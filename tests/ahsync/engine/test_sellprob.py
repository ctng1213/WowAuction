"""Tests for ahsync/engine/sellprob.py: shared scenarios, Python-only scenarios, invariants PI1-PI21, LP1."""
from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

import pytest

from ahsync.engine import sellprob as sp
from ahsync.engine.fees import AH_CUT, shortest_duration_covering
from ahsync.engine.sellprob import Competitor, QueueInputs
from tests.support.gen import cases

ROOT = Path(__file__).resolve().parents[3]
SHARED = json.loads((ROOT / "tests" / "scenarios" / "sellprob.json").read_text(encoding="utf-8"))
HOUR_MS = 3_600_000
FLAT8 = (1.0,) * 8
SHORT, MEDIUM, LONG, VERY_LONG = 1, 2, 3, 4


def queue(sold=24.0, listed=0.0, u=0.0, demand=FLAT8, qty=5, ahead=()) -> QueueInputs:
    """Simulation inputs with the scenario defaults: S = 24/day, flat, 8 h, no undercutters.

    Returns:
        The QueueInputs.
    """
    return QueueInputs(sold, listed, u, tuple(demand), qty, tuple(ahead))


# ---------------------------------------------------------------- shared scenarios (also run in Lua)

@pytest.mark.parametrize("scenario", SHARED, ids=[s["id"] for s in SHARED])
def test_shared_scenarios(scenario):
    result = getattr(sp, scenario["call"])(**scenario["args"])
    assert result == pytest.approx(scenario["expect"], abs=1e-9), scenario["why"]


# ---------------------------------------------------------------- Python-only scenarios (the simulation)

def test_P6_ahead_units_take_their_share_then_you_sell_everything():
    q = queue(sold=120, qty=20, ahead=[Competitor(100, 10, VERY_LONG)])
    assert sp.simulate(q) == 1.0      # demand 40: the 10 ahead take 10, you get 20 of the remaining 30


def test_P7_slow_items_get_a_low_chance():
    assert sp.simulate(queue(sold=2 / 7, qty=1)) == pytest.approx(2 / 7 / 3)      # ≈ 0.095


def test_P8_no_volume_data_uses_the_default_and_flags_it():
    assert sp.chance(None, None, 0, 0, 0) == (0.25, True)


def test_P12_a_long_band_competitor_expires_and_frees_buyers():
    assert sp.simulate(queue(ahead=[Competitor(100, 6, LONG)])) == pytest.approx(0.6)


def test_P13_a_very_long_competitor_stays_the_whole_time():
    assert sp.simulate(queue(ahead=[Competitor(100, 6, VERY_LONG)])) == pytest.approx(0.4)


def test_P14_undercutters_take_buyers_first():
    q = queue(listed=12, u=1.0, ahead=[Competitor(100, 6, LONG)])
    assert sp.simulate(q) == pytest.approx(0.3)


def test_P17_market_sell_through_and_days_of_supply():
    through, supply = sp.market_facts(30, 10, 93)
    assert (through, round(supply, 1)) == (0.75, 3.1)


def test_P18_without_a_book_nothing_is_ahead_when_you_post_below_cur():
    assert sp.ahead_of(None, 49, cur=50, cur_qty=8) == ()


def test_P19_without_a_book_cur_qty_is_ahead_all_window():
    assert sp.ahead_of(None, 50, cur=50, cur_qty=8) == (Competitor(50, 8.0, 0),)


def test_P20_undercut_share_from_new_listings_relative_to_the_lowest_then():
    listings = [(40, 50, 50), (60, 60, 50)]      # 40 units at the then-lowest, 60 above it
    assert sp.undercut_share(listings, price=45, lowest_now=45) == 0.4


def test_P21_a_morning_post_misses_the_afternoon_rush():
    demand = (1.0,) * 4 + (0.5,) * 4               # 06:00-09:00 then 10:00-13:00
    assert sp.simulate(queue(demand=demand, qty=10)) == pytest.approx(0.6)


def test_P22_the_same_post_at_ten_catches_the_rush():
    demand = (0.5,) * 4 + (2.0,) * 4               # 10:00-13:00 then 14:00-17:00
    assert sp.simulate(queue(demand=demand, qty=10)) == pytest.approx(1.0)


SAT_10 = (2 * 24 + 10) * HOUR_MS        # 1970-01-03 was a Saturday


def test_P23_a_planned_post_covers_its_window_and_picks_the_shortest_duration():
    hours = sp.planned_window(post_ms=SAT_10, window_end_ms=SAT_10 + 11 * HOUR_MS, now_ms=SAT_10 - 5 * HOUR_MS)
    assert len(hours) == 11                       # Sat 10:00-20:59
    assert shortest_duration_covering(len(hours)) == 24


def test_P24_a_flip_sale_uses_its_planned_window_not_the_next_eight_hours():
    now = SAT_10 - 30 * HOUR_MS
    planned = sp.planned_window(SAT_10, SAT_10 + 11 * HOUR_MS, now)
    assert planned != sp.posting_now_window(now)
    assert planned[0] == SAT_10 // HOUR_MS


# ---------------------------------------------------------------- unit tests

def test_window_helpers():
    now = 10 * HOUR_MS + 1234
    assert sp.posting_now_window(now) == list(range(10, 18))         # the current hour counts in full
    assert sp.planned_window(5 * HOUR_MS, 12 * HOUR_MS, now) == [10, 11]   # post time passed: from now
    assert sp.planned_window(5 * HOUR_MS, 9 * HOUR_MS, now) == []           # window ended: missed
    assert sp.demand_for([0, 1], [float(s) for s in range(168)]) == (72.0, 73.0)   # Thursday 00:00


def test_curve_build_encode_decode_round_trip():
    grid = sp.build_curve(24, 0, 0.5, [1.0] * 168, refresh_ms=0, qty=5)
    assert len(grid) == 24 and len(grid[0]) == len(sp.CURVE_AHEAD)
    assert grid[0][0] == 1.0 and grid[0][-1] == 0.0                     # 100 ahead absorb all 8 buyers
    decoded = sp.decode_curve(sp.encode_curve(grid), len(sp.CURVE_AHEAD))
    assert decoded[0][0] == 1.0 and len(decoded) == 24
    assert set(sp.encode_curve(grid)) <= {chr(c) for c in range(35, 86)}


def test_curve_points_are_bit_identical_to_the_simulation():
    def make(rng):
        """S, L, u, an 8-hour demand profile and the posted units."""
        return (rng.uniform(0, 200), rng.uniform(0, 200), rng.random(),
                tuple(rng.uniform(0, 3) for _ in range(8)), rng.randint(0, 40))
    for i, (s, l, u, demand, qty) in enumerate(cases(CURVE_SEED, make)):
        row = sp._block_row(s, l, u, demand, qty, sp.CURVE_AHEAD)
        expected = [sp.simulate(sp.QueueInputs(s, l, u, demand, qty, (sp.Competitor(0, float(a), 0),) if a else ()))
                    for a in sp.CURVE_AHEAD]
        assert row == expected, f"seed {CURVE_SEED} case {i}"


def test_curve_rows_follow_the_demand_of_each_posting_hour():
    busy = [3.0 if s == 24 + 18 else 0.5 for s in range(168)]         # Tue 18:00 is busy
    tuesday_10 = (5 * 24 + 10) * HOUR_MS                              # day 5 (1970-01-06) is a Tuesday
    grid = sp.build_curve(24, 0, 0.5, busy, refresh_ms=tuesday_10, qty=5)
    # Rows 1-8 (posting 11:00-18:00) catch the 18:00 hour: 7 × 0.5 + 3 = 6.5 buyers, so all 5 units sell.
    # Rows 0 and 9 miss it: 8 × 0.5 = 4 buyers, 4 of 5 units → 0.8.
    assert (grid[0][0], grid[1][0], grid[8][0], grid[9][0]) == (0.8, 1.0, 1.0, 0.8)
    flat = sp.build_curve(24, 0, 0.5, [1.0] * 168, refresh_ms=tuesday_10, qty=5)
    assert all(row == flat[0] for row in flat) and flat[0] is not flat[1]   # reused values, separate lists


def test_zero_units_and_edge_inputs():
    assert sp.simulate(queue(qty=0)) == 0.0
    assert sp.tier_of(100, None) == 1 and sp.tier_of(104, 100) == 2 and sp.tier_of(110, 100) == 3
    assert sp.tier_of(120, 100) == 4
    assert sp.market_facts(0, 0, 10) == (None, None)
    assert sp.undercut_share([], 10, 10) is None
    assert sp.ahead_of([(40, 2, LONG), (60, 1, LONG)], 50, None, None) == (Competitor(40, 2, LONG),)
    assert sp.ahead_of(None, 50, None, None) == ()
    assert sp.from_curve([0.9, 0.5], -1, [0, 5]) == 0.9 and sp.from_curve([0.9, 0.5], 9, [0, 5]) == 0.5
    assert sp.chance(queue(), 0.8, 30, 0, 0) == (pytest.approx(0.5 * 1.0 + 0.5 * 0.8), False)


# ---------------------------------------------------------------- invariants (SPEC §8.7)

SEED = 9_102_026
CURVE_SEED = 17_102_026


def random_queue(rng) -> QueueInputs:
    """A random but valid simulation input."""
    ahead = tuple(Competitor(rng.randint(1, 100), float(rng.randint(0, 30)), rng.randint(0, 4))
                  for _ in range(rng.randint(0, 4)))
    hours = rng.randint(1, 12)
    return QueueInputs(rng.random() * 200, rng.random() * 100, rng.random(),
                       tuple(rng.random() * 3 for _ in range(hours)), rng.randint(1, 40), ahead)


QUEUES = cases(SEED, random_queue)


def replace(q: QueueInputs, **changes) -> QueueInputs:
    """A copy of q with some fields changed."""
    return QueueInputs(**{**q.__dict__, **changes})


def check(condition: bool, i: int, q) -> None:
    """Assert with the seed and case printed."""
    assert condition, f"seed {SEED} case {i}: {q}"


def test_PI1_probabilities_stay_in_0_to_1():
    for i, q in enumerate(QUEUES):
        p, _ = sp.chance(q, 0.5, i % 40, i % 5, i % 7)
        check(0 <= sp.simulate(q) <= 1 and 0 <= p <= 1, i, q)


def test_PI2_more_sales_or_a_longer_window_never_lowers_P():
    for i, q in enumerate(QUEUES):
        check(sp.simulate(replace(q, sold_per_day=q.sold_per_day * 1.5)) >= sp.simulate(q) - 1e-12, i, q)
        check(sp.simulate(replace(q, demand_profile=q.demand_profile + (1.0,))) >= sp.simulate(q) - 1e-12, i, q)


def test_PI3_more_competition_or_more_units_never_raises_P():
    for i, q in enumerate(QUEUES):
        more_ahead = replace(q, ahead=q.ahead + (Competitor(1, 5.0, 4),))
        check(sp.simulate(more_ahead) <= sp.simulate(q) + 1e-12, i, q)
        check(sp.simulate(replace(q, qty=q.qty + 5)) <= sp.simulate(q) + 1e-12, i, q)


def test_PI4_lowering_your_price_never_lowers_P():
    for i, q in enumerate(QUEUES):
        book = [(c.price, c.units, c.band) for c in q.ahead] + [(55, 10.0, 3)]
        high = replace(q, ahead=sp.ahead_of(book, 60, None, None))
        low = replace(q, ahead=sp.ahead_of(book, 50, None, None))
        check(sp.simulate(low) >= sp.simulate(high) - 1e-12, i, q)


def test_PI5_record_blend_moves_P_toward_your_own_rate():
    for i in range(1000):
        p, s, x = (i % 97) / 96, i % 6, (i // 6) % 6
        check(sp.with_record(p, 0, 0) == pytest.approx(p), i, (p, s, x))
        if s + x:
            rate = s / (s + x)
            once, twice = sp.with_record(p, s, x), sp.with_record(p, 2 * s, 2 * x)
            check(min(p, rate) - 1e-12 <= once <= max(p, rate) + 1e-12, i, (p, s, x))
            check(abs(twice - rate) <= abs(once - rate) + 1e-12, i, (p, s, x))


def random_post(rng) -> tuple[int, int, float, int]:
    """(qty, price, fallback, deposit) with the price above the fallback floor (SPEC §3.3.3)."""
    fallback = rng.random() * 500
    return rng.randint(1, 50), int(fallback / (1 - AH_CUT)) + 1 + rng.randint(0, 5000), fallback, rng.randint(0, 3000)


POSTS = cases(SEED + 1, random_post)


def test_PI6_a_certain_sale_never_loses_the_deposit():
    for i, (qty, price, fallback, deposit) in enumerate(POSTS):
        check(sp.expected_post(1, qty, price, fallback, deposit) == pytest.approx(qty * price * (1 - AH_CUT)), i, qty)


def test_PI7_a_certain_expiry_is_the_fallback_minus_the_deposit():
    for i, (qty, price, fallback, deposit) in enumerate(POSTS):
        check(sp.expected_post(0, qty, price, fallback, deposit) == pytest.approx(qty * fallback - deposit), i, qty)


def test_PI8_a_higher_chance_never_lowers_a_worthwhile_post():
    for i, (qty, price, fallback, deposit) in enumerate(POSTS):
        values = [sp.expected_post(p / 10, qty, price, fallback, deposit) for p in range(11)]
        check(all(b >= a - 1e-9 for a, b in zip(values, values[1:])), i, (qty, price, fallback, deposit))


def test_PI9_expected_never_beats_selling_everything():
    for i, (qty, price, fallback, deposit) in enumerate(POSTS):
        for p in (0.0, 0.3, 0.7, 1.0):
            e = sp.expected_post(p, qty, price, fallback, deposit)
            check(e <= qty * price * (1 - AH_CUT) + 1e-9, i, (p, qty, price, fallback, deposit))


def test_PI10_the_old_formula_is_a_special_case():
    for i, q in enumerate(QUEUES):
        ahead_units = sum(c.units for c in q.ahead)
        flat = replace(q, listed_per_day=0.0, demand_profile=(1.0,) * len(q.demand_profile),
                       ahead=(Competitor(1, ahead_units, 0),) if ahead_units else ())
        old = min(1.0, max(0.0, (q.sold_per_day * len(q.demand_profile) / 24 - ahead_units) / q.qty))
        check(sp.simulate(flat) == pytest.approx(old, abs=1e-9), i, q)


def test_PI11_more_undercutting_never_raises_P():
    for i, q in enumerate(QUEUES):
        check(sp.simulate(replace(q, listed_per_day=q.listed_per_day * 2)) <= sp.simulate(q) + 1e-12, i, q)
        check(sp.simulate(replace(q, undercut_share=min(1.0, q.undercut_share + 0.3))) <= sp.simulate(q) + 1e-12, i, q)


def test_PI12_a_competitor_expiring_sooner_never_lowers_P():
    for i, q in enumerate(QUEUES):
        sooner = replace(q, ahead=tuple(Competitor(c.price, c.units, max(1, c.band - 1) if c.band else 4)
                                        for c in q.ahead))
        check(sp.simulate(sooner) >= sp.simulate(q) - 1e-12, i, q)


def test_PI13_adding_hours_at_the_end_never_lowers_P():
    for i, q in enumerate(QUEUES):
        check(sp.simulate(replace(q, demand_profile=q.demand_profile + (0.5, 2.0))) >= sp.simulate(q) - 1e-12, i, q)


def test_PI14_the_blend_lies_between_the_queue_and_the_tier_history():
    for i, q in enumerate(QUEUES):
        pq, st = sp.simulate(q), (i % 11) / 10
        p = sp.blend(pq, st, i % 45)
        check(min(pq, st) - 1e-12 <= p <= max(pq, st) + 1e-12, i, q)
        check(sp.blend(pq, None, i % 45) == pq, i, q)


def test_PI15_nobody_sells_more_than_the_buyers_buy():
    for i, q in enumerate(QUEUES):
        r = sp.simulate_detail(q)
        check(r.yours + r.competitors + r.undercutters <= r.demand + 1e-9, i, q)


def test_PI16_more_buyers_in_any_hour_never_lowers_P():
    for i, q in enumerate(QUEUES):
        k = i % len(q.demand_profile)
        busier = q.demand_profile[:k] + (q.demand_profile[k] + 1.0,) + q.demand_profile[k + 1:]
        check(sp.simulate(replace(q, demand_profile=busier)) >= sp.simulate(q) - 1e-12, i, q)


def test_PI17_each_context_uses_its_own_window():
    for i in range(1000):
        now = 1_000 * HOUR_MS + (i * 7919) % HOUR_MS * 3
        post, end = now + ((i % 9) - 4) * HOUR_MS, now + (i % 13 + 1) * HOUR_MS
        check(sp.posting_now_window(now) == list(range(now // HOUR_MS, now // HOUR_MS + 8)), i, now)
        planned = sp.planned_window(post, end, now)
        check(planned == list(range(max(post, now) // HOUR_MS, -(-end // HOUR_MS))), i, (post, end, now))


def test_LP1_committed_property_cases_match_python():
    import tools.gen_properties as gen                      # noqa: PLC0415
    committed = json.loads((ROOT / "tests" / "properties" / "sellprob.json").read_text(encoding="utf-8"))
    seed, fresh = gen.build()["sellprob"]
    assert committed == json.loads(json.dumps({"seed": seed, "cases": fresh})), \
        "tests/properties/sellprob.json is stale: run python tools/gen_properties.py"


def test_P26_posting_now_follows_the_items_hourly_sales_profile():
    fri_18 = (20_000 * 24 + 18) * HOUR_MS                  # day 20000 is a Friday
    supply = sp.Supply(24.0, 0.0, (), None, None)
    settings = sp.MarketSettings(default_undercut_share=0.0)
    flat, unknown = sp.posting_chance(supply, 50, 30, fri_18, settings)
    assert (flat, unknown) == (pytest.approx(8 / 30), False)                       # demand 8 of 30 units
    assert sp.posting_chance(supply, 50, 30, fri_18, settings, sp.FLAT_HOW)[0] == flat
    busy = [1.0] * 168
    for hour in range(8):                                                          # Fri 18:00 - Sat 01:59
        slot = (4 * 24 + 18 + hour) % 168
        busy[slot] = 3.0
    assert sp.posting_chance(supply, 50, 30, fri_18, settings, busy)[0] == pytest.approx(24 / 30)   # 0.80


def test_P25_posting_now_without_volume_uses_the_default_for_its_position():
    settings = sp.MarketSettings()
    book = ((45, 20, 4, 1), (50, 20, 4, 1))
    assert sp.posting_chance(sp.Supply(None, None, book, 45, 20), 44, 20, 0, settings) == (0.5, True)
    assert sp.posting_chance(sp.Supply(None, None, book, 45, 20), 55, 20, 0, settings) == (0.25, True)
    assert sp.posting_chance(sp.Supply(None, None, None, 45, 20), 46, 20, 0, settings) == (0.25, True)   # no book: cur
    assert sp.posting_chance(sp.Supply(None, None, (), None, None), None, 20, 0, settings) == (0.5, True)
    assert sp.cheapest_other(book, 99) == 45 and sp.cheapest_other(None, 45) == 45 and sp.cheapest_other((), 45) is None


def test_market_settings_round_trip_through_a_cloud_document():
    import json
    from ahsync.engine.sellprob import MarketSettings
    from ahsync.engine.volume import VolumePrior
    settings = MarketSettings(default_sell_prob=0.3, volume_prior=VolumePrior(2.0, 4))
    assert MarketSettings.from_dict(json.loads(json.dumps(settings.to_dict()))) == settings
    assert MarketSettings.from_dict({}) == MarketSettings()                     # a desktop-built document
    assert MarketSettings.from_dict({"default_sell_prob": 0.4}).default_sell_prob == 0.4   # no prior given


# ---------------------------------------------------------------- price acceptance (SPEC §3.3.2, P28, PI18-PI20)

SOLD_CURVE = (60, 80, 90, 100, 120, 150, 200)           # P28's curve: copper at the 0, 10, 25, 50, 75, 90, 100th
ACCEPT_SEED = 22_112_026


def hour_demand(price: int, acc: sp.Acceptance | None, how: float = 1.0) -> float:
    """The units one hour's buyers take at a price: S = 240/day, a flat profile, one window hour (P28)."""
    acc = acc if acc is None else sp.Acceptance(acc.level, acc.curve, (how,) * 168)
    profile = sp.accepted_demand([0], sp.FLAT_HOW, price, acc)
    return sp.simulate_detail(QueueInputs(240.0, 0.0, 0.0, profile, 1, ())).demand


def test_P28_price_acceptance_in_the_sell_chance():
    acc = sp.Acceptance(100, SOLD_CURVE)
    assert [hour_demand(p, acc) for p in (100, 120, 135, 150, 175, 200)] == pytest.approx([10, 5, 3.5, 2, 1, 0])
    assert hour_demand(132, acc, how=1.10) == pytest.approx(5)          # 132 c is the scaled curve's 75th
    assert hour_demand(500, sp.Acceptance(500, None)) == 10             # no curve: no acceptance
    assert hour_demand(500, None) == 10
    # The continuous form (the user, 2026-11-22): the level above the curve's 50th reads 1 at the level, not a jump.
    above = sp.Acceptance(120, SOLD_CURVE)
    assert sp.acceptance(120, 120, SOLD_CURVE, 1.0) == 1.0 and sp.acceptance(150, 120, SOLD_CURVE, 1.0) == pytest.approx(0.4)
    assert hour_demand(121, above) < 10 and hour_demand(121, above) > hour_demand(135, above)


def random_acceptance(rng) -> tuple[int, sp.Acceptance]:
    """A random post price and a random but valid level, curve and pattern."""
    curve = tuple(sorted(rng.randint(10, 400) for _ in range(len(SOLD_CURVE))))
    level = rng.randint(curve[0], curve[-1]) if rng.random() < 0.9 else rng.randint(curve[-1], curve[-1] + 50)
    how = tuple(0.5 + 1.5 * rng.random() for _ in range(168)) if rng.random() < 0.7 else None
    return rng.randint(1, 500), sp.Acceptance(level, curve, how)


ACCEPTANCES = cases(ACCEPT_SEED, random_acceptance)


def test_PI18_the_acceptance_is_1_at_the_hours_level_falls_with_the_price_and_is_0_from_the_curves_100th():
    for i, (price, acc) in enumerate(ACCEPTANCES):
        how = (acc.how or sp.FLAT_HOW)[i % 168]
        hour_level, top = math.floor(acc.level * how + 0.5), math.floor(acc.curve[-1] * how + 0.5)
        a, higher = sp.acceptance(price, acc.level, acc.curve, how), sp.acceptance(price + 1 + i % 40, acc.level, acc.curve, how)
        check(0 <= a <= 1 and higher <= a + 1e-12, i, (price, acc))
        check(sp.acceptance(hour_level, acc.level, acc.curve, how) == 1.0, i, (price, acc))
        check(sp.acceptance(hour_level - 1, acc.level, acc.curve, how) == 1.0, i, (price, acc))
        check(sp.acceptance(max(top, hour_level + 1), acc.level, acc.curve, how) == 0.0, i, (price, acc))
        check(sp.acceptance(price, None, acc.curve, how) == 1.0 and sp.acceptance(price, acc.level, None, how) == 1.0, i, acc)


def test_PI19_a_higher_price_never_raises_P_at_fixed_competition():
    settings = sp.MarketSettings()
    for i, ((price, acc), q) in enumerate(zip(ACCEPTANCES, QUEUES)):
        supply = sp.Supply(q.sold_per_day, q.listed_per_day, None, None, None)      # nothing ahead, whatever the price
        lower = sp.posting_chance(supply, price, q.qty, i * HOUR_MS, settings, None, q.undercut_share, acc)[0]
        higher = sp.posting_chance(supply, price + 1 + i % 60, q.qty, i * HOUR_MS, settings, None, q.undercut_share, acc)[0]
        check(higher <= lower + 1e-12, i, (price, acc, q))
        same = sp.posting_chance(supply, price, q.qty, i * HOUR_MS, settings, None, q.undercut_share,
                                 sp.Acceptance(None, acc.curve, acc.how))[0]          # no level: the spread, no acceptance
        check(lower <= same + 1e-12, i, (price, acc, q))                           # the acceptance only ever cuts


def test_PI20_the_curves_rows_never_rise_with_the_price():
    for i, (price, acc) in enumerate(ACCEPTANCES[:300]):
        q = QUEUES[i]
        args = (q.sold_per_day, q.listed_per_day, q.undercut_share, acc.how or sp.FLAT_HOW, i * HOUR_MS, q.qty)
        kw = dict(acc=acc, spread=sp.demand_spread(acc))
        lower, higher = sp.build_curve(*args, price=price, **kw), sp.build_curve(*args, price=price + 1 + i % 80, **kw)
        plain = sp.build_curve(*args)
        spread = sp.build_curve(*args, spread=sp.demand_spread(acc))             # the same spread, no acceptance
        for row_lo, row_hi, row_plain in zip(lower, higher, spread):
            check(all(h <= lo + 1e-12 for lo, h in zip(row_lo, row_hi)), i, (price, acc, q))
            check(all(lo <= p + 1e-12 for lo, p in zip(row_lo, row_plain)), i, (price, acc, q))
        check(sp.build_curve(*args, price=price, acc=sp.Acceptance(acc.level, None, acc.how)) == plain, i, acc)


# ---------------------------------------------------------------- the demand spread (SPEC §3.3.2, P31, PI21)

def test_P31_the_chance_averages_over_the_spread_of_demand():
    assert sum(sp.DEMAND_SPREAD) / len(sp.DEMAND_SPREAD) == pytest.approx(1.0)
    acc, bare = sp.Acceptance(100, SOLD_CURVE, None, "busy"), sp.Acceptance(100, None, None, "thin")
    supply, settings = sp.Supply(24.0, 0.0, None, None, None), sp.MarketSettings()
    # S 24 a day over a flat 8 h: demand 8 × m units for 10 posted, min(8m, 10) ÷ 10 averaged over the ten m
    assert sp.posting_chance(supply, 100, 10, 0, settings, None, 0.0, acc)[0] == pytest.approx(0.6552)
    assert sp.posting_chance(supply, 100, 10, 0, settings, None, 0.0, bare)[0] == pytest.approx(0.8)    # no curve
    assert sp.posting_chance(supply, 100, 10, 0, settings, None, 0.0, None)[0] == pytest.approx(0.8)
    assert sp.demand_spread(acc) == sp.DEMAND_SPREAD and sp.demand_spread(bare) == sp.demand_spread(None) == (1.0,)
    assert sp.demand_spread(sp.Acceptance(100, SOLD_CURVE)) == (1.0,)            # the profile gates it, not the curve
    commodity = sp.Acceptance(100, SOLD_CURVE, None, "commodity")              # a commodity with a curve: spread too
    assert sp.demand_spread(commodity) == sp.DEMAND_SPREAD
    assert sp.posting_chance(supply, 100, 10, 0, settings, None, 0.0, commodity)[0] == pytest.approx(0.6552)
    thin_commodity = sp.Acceptance(100, None, None, "commodity")                 # without a curve: once at S
    assert sp.posting_chance(supply, 100, 10, 0, settings, None, 0.0, thin_commodity)[0] == pytest.approx(0.8)
    grid = sp.build_curve(24.0, 0.0, 0.0, sp.FLAT_HOW, 0, 10, price=100, acc=acc, spread=sp.demand_spread(acc))
    assert grid[0][0] == pytest.approx(0.6552)                                  # the exported curve reads it too
    assert sp.build_curve(24.0, 0.0, 0.0, sp.FLAT_HOW, 0, 10, price=100, acc=bare)[0][0] == pytest.approx(0.8)


def random_spread_case(rng):
    """A random queue, a random busy item with a curve and a raised S."""
    return random_queue(rng), dataclasses.replace(random_acceptance(rng)[1], profile="busy"), 1 + rng.random() * 3


SPREAD_CASES = cases(SEED + 31, random_spread_case)


def test_PI21_the_spread_chance_stays_in_range_rises_with_S_and_is_the_plain_chance_without_a_curve():
    for i, (q, acc, more) in enumerate(SPREAD_CASES):
        spread = replace(q, spread=sp.demand_spread(acc))
        p, higher = sp.simulate(spread), sp.simulate(replace(spread, sold_per_day=q.sold_per_day * more))
        check(0 <= p <= 1 and higher >= p - 1e-12, i, (q, acc))
        check(sp.simulate(replace(q, spread=sp.demand_spread(dataclasses.replace(acc, profile="thin")))) == sp.simulate(q), i, q)
        quiet = replace(spread, ahead=(), undercut_share=0.0)                   # concave in S: never above the mean's
        check(sp.simulate(quiet) <= sp.simulate(replace(quiet, spread=sp.NO_SPREAD)) + 1e-12, i, q)
    for i, (q, acc, _) in enumerate(SPREAD_CASES[:200]):                        # the curve's rows are simulate's
        row = sp._spread_row(q.sold_per_day, q.listed_per_day, q.undercut_share, q.demand_profile, q.qty,
                             sp.CURVE_AHEAD, sp.DEMAND_SPREAD)
        expected = [sp.simulate(QueueInputs(q.sold_per_day, q.listed_per_day, q.undercut_share, q.demand_profile, q.qty,
                                            (Competitor(0, float(a), 0),) if a else (), sp.DEMAND_SPREAD))
                    for a in sp.CURVE_AHEAD]
        check(row == expected, i, q)


def test_P34_a_curve_commoditys_competitors_stay_in_the_queue_for_the_whole_window():
    ahead = (sp.Competitor(100, 6.0, LONG),)                     # 6 tied units, LONG: its midpoint is 5 h
    curve_commodity = sp.Acceptance(100, SOLD_CURVE, None, "commodity")
    assert sp.queue_ahead(ahead, 100, curve_commodity) == (sp.Competitor(100, 6.0, 0),)
    cheaper = (sp.Competitor(90, 6.0, LONG),)                    # strictly cheaper: keeps its midpoint (unmeasured)
    assert sp.queue_ahead(cheaper, 100, curve_commodity) == cheaper
    for other in (sp.Acceptance(100, None, None, "commodity"), sp.Acceptance(100, SOLD_CURVE, None, "busy"), None):
        assert sp.queue_ahead(ahead, 100, other) == ahead          # gear and commodities without a curve: midpoints
    # S 24 a day, 1 unit posted, the spread's m: leaving at 5 h it gets min(1, 3m), staying min(1, max(0, 8m - 6))
    leave = [sp.simulate(QueueInputs(24.0 * m, 0.0, 0.0, FLAT8, 1, ahead)) for m in sp.DEMAND_SPREAD]
    stay = [sp.simulate(QueueInputs(24.0 * m, 0.0, 0.0, FLAT8, 1, sp.queue_ahead(ahead, 100, curve_commodity)))
            for m in sp.DEMAND_SPREAD]
    assert sum(leave) / 10 == pytest.approx(0.954) and sum(stay) / 10 == pytest.approx(0.508)
    book = ((100, 6, LONG, 1),)
    supply = sp.Supply(24.0, 0.0, book, 100, 6)
    assert sp.posting_chance(supply, 100, 1, 0, sp.MarketSettings(), None, 0.0, curve_commodity)[0] == pytest.approx(0.508)

