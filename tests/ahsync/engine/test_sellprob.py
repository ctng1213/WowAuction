"""Tests for ahsync/engine/sellprob.py: shared scenarios, Python-only scenarios, invariants PI1-PI17, LP1."""
from __future__ import annotations

import json
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
