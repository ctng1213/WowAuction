"""Tests for ahsync/engine/plan.py: the sell plan for what you own (SPEC §3.4C.1), invariant PL2."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine import plan, sellprob
from ahsync.engine.plan import Lot, PlanSettings, PlanWindow, SellFacts
from tests.support.gen import cases

HOUR, DAY = 3_600_000, 86_400_000
MONDAY = 345_600_000 + 3000 * 7 * DAY        # a Monday 00:00 UTC
FRI, SAT, SUN, WED = 4, 5, 6, 2
NOW = MONDAY + FRI * DAY + 14 * HOUR + 30 * 60_000      # Fri 14:30
LINEN, GLOVES = 2589, 4239
S = PlanSettings()
SEED_PL2 = 4022


def at(day: int, hour: int, week: int = 0) -> int:
    """ms of a weekday and hour in this plan week (Mon 0 … Sun 6)."""
    return MONDAY + (week * 7 + day) * DAY + hour * HOUR


def window(day: int, start: int, hours: int, best: int) -> PlanWindow:
    """A selling window on a weekday, as hours of the week."""
    return PlanWindow(day * 24 + start, hours, day * 24 + best)


def linen(**changes) -> SellFacts:
    """Linen Cloth: 45c now, level 49c, sells 40/day in stacks of 20, a Saturday 17-21 peak at 18:00."""
    how = [1.0] * 168
    how[SAT * 24 + 18] = 1.12
    base = SellFacts(LINEN, level=49, trend7=0.0, post_price=45, sold_per_day=40.0, listed_per_day=40.0, ref_stack=20,
                     how=tuple(how), sales_how=tuple([1.0] * 168), windows=(window(SAT, 17, 4, 18),), book=(),
                     cur=45, cur_qty=40, vendor_sell=10, supply_now=93)
    return replace(base, **changes)


def test_R14_each_window_takes_half_of_what_others_list_for_200_linen():
    f = linen(sold_per_day=200.0)                  # busy enough that the supply cap, not demand, is the limit
    assert [o.cap for o in plan.post_options(f, NOW, S)] == [46, 46]  # max(1 stack, floor(0.5 × 93) = 46)
    actions = plan.sell_plan([Lot("Amaya-G", LINEN, 200)], {LINEN: f}, NOW, S)
    posts = [a for a in actions if a.kind == "post"]
    assert [a.qty for a in posts] == [40, 40] and sum(a.qty for a in actions) == 200   # 2 stacks fit in 46, a 3rd doesn't
    assert [a.kind for a in actions if a.kind != "post"] == ["hold"]   # the rest waits for later windows (> 7 days)


def test_R6_owned_linen_is_spread_over_windows_within_their_caps_saturday_first():
    f = linen(windows=(window(SAT, 17, 4, 18), window(SUN, 19, 3, 20), window(WED, 20, 2, 21)))
    actions = plan.sell_plan([Lot("Amaya-G", LINEN, 200)], {LINEN: f}, NOW, S)
    posts = [a for a in actions if a.kind == "post"]
    by_day: dict[int, int] = {}
    for a in posts:
        by_day[a.at_ms // DAY] = by_day.get(a.at_ms // DAY, 0) + a.qty
    assert len(by_day) >= 3 and all(a.qty <= 46 for a in posts)        # each window within its supply cap
    sat = [a for a in posts if a.peak_ms == at(SAT, 18)]
    assert sat and sat[0].price == 55                            # 49 × 1.12 = 54.88 → 55c, the best value
    # 40 sell a day: a second stack in a window would mostly not sell, so the rest waits, never vendored at 10c.
    assert [a.kind for a in actions if a.kind != "post"] == ["hold"]


def test_R7_an_owned_green_worth_more_disenchanted_is_planned_for_the_character_who_can():
    green = SellFacts(GLOVES, level=800, trend7=0.0, post_price=800, sold_per_day=2.0, listed_per_day=3.0, ref_stack=1,
                      how=tuple([1.0] * 168), sales_how=tuple([1.0] * 168), windows=(), book=(), cur=800, cur_qty=1,
                      vendor_sell=100, de_value=1425.0, de_char="Amaya-G")
    [action] = plan.sell_plan([Lot("Brugan-G", GLOVES, 1)], {GLOVES: green}, NOW, S)
    assert (action.kind, action.char_id, action.expected) == ("de", "Amaya-G", 1425.0)


def test_D12_a_post_is_planned_8_hours_before_its_peak_and_lasts_through_it():
    actions = plan.sell_plan([Lot("Amaya-G", LINEN, 20)], {LINEN: linen(post_price=30)}, NOW, S)
    [post] = [a for a in actions if a.kind == "post"]
    assert (post.at_ms, post.peak_ms, post.duration_h) == (at(SAT, 10), at(SAT, 18), 24)   # 9 h to cover → 24 h


def test_D13_a_post_whose_lead_time_passed_is_planned_now_over_the_rest_of_the_window():
    f = linen(windows=(window(FRI, 18, 3, 19),))
    [_, fri] = plan.post_options(f, NOW, S)
    assert fri.at_ms == NOW                                           # 19:00 − 8 h = 11:00 has passed
    assert [h % 24 for h in fri.hours] == list(range(14, 21))         # Fri 14:00-20:59: the current hour counts in full
    assert fri.window == (at(FRI, 18), at(FRI, 21), at(FRI, 19))


def test_window_occurrences_and_prices():
    running = window(FRI, 13, 3, 14)                                  # Fri 13-16, running now
    # Still running now, and again next Friday at 13:00, which starts before the horizon ends (Fri 14:30).
    assert plan.occurrences([running], NOW, 7) == [(at(FRI, 13), at(FRI, 16), at(FRI, 14)),
                                                   (at(FRI, 13, 1), at(FRI, 16, 1), at(FRI, 14, 1))]
    assert plan.occurrences([window(0, 0, 2, 1)], NOW, 7) == [(at(0, 0, 1), at(0, 2, 1), at(0, 1, 1))]
    assert plan.slot_price(100, 1.1, 10.0, 2) == 121                  # trend clamped to 5%/day: 100 × 1.1 × 1.1
    assert plan.slot_of(NOW // HOUR) == FRI * 24 + 14


def test_without_sales_or_a_price_the_plan_falls_back():
    unknown = linen(sold_per_day=None, level=None)
    assert plan.post_chance(unknown, 45, 20, [1, 2], 0, S) == S.default_sell_prob
    [only] = plan.post_options(unknown, NOW, S)                       # no level: posting now only
    assert only.window is None and only.cap == 46
    nothing = linen(post_price=None, level=None, vendor_sell=0)
    assert [a.kind for a in plan.sell_plan([Lot("A", LINEN, 5)], {LINEN: nothing}, NOW, S)] == ["hold"]
    vendor = linen(post_price=None, level=None, vendor_sell=10)
    [sale] = plan.sell_plan([Lot("A", LINEN, 5)], {LINEN: vendor}, NOW, S)
    assert (sale.kind, sale.price, sale.expected) == ("vendor", 10, 50.0)
    assert plan.sell_plan([Lot("A", 1, 5)], {LINEN: vendor}, NOW, S) == []   # no facts: left out


def test_your_own_planned_units_are_ahead_of_you_and_the_book_counts():
    f = linen(book=((40, 5, 4, 1),))
    # 24 h: 40 buyers, 20 go to undercutters (40 × 0.5 listed), 5 to the stack ahead: 15 of your 20 sell.
    assert plan.post_chance(f, 45, 20, list(range(24)), 0, S) == 0.75
    assert plan.post_chance(f, 45, 20, list(range(24)), 20, S) == 0.0     # 20 more of your own units ahead
    assert sellprob.ahead_of([(40, 5.0, 4)], 45, None, None)


def random_world(rng):
    """Random lots of one item on up to three characters, and its facts."""
    stack = rng.choice([1, 5, 20])
    sold = rng.choice([None, 2.0, 12.0, 40.0, 200.0])
    lots = [Lot(char, LINEN, rng.randint(1, 120)) for char in ("A", "B", "C")[:rng.randint(1, 3)]]
    windows = tuple(window(rng.randint(0, 6), rng.randint(0, 20), rng.randint(1, 4), 0) for _ in range(rng.randint(0, 3)))
    windows = tuple(PlanWindow(w.start_slot, w.hours, w.start_slot + rng.randint(0, w.hours - 1)) for w in windows)
    now = MONDAY + rng.randint(0, 7 * 24 - 1) * HOUR + rng.randint(0, 59) * 60_000
    return lots, linen(ref_stack=stack, sold_per_day=sold, windows=windows, post_price=rng.choice([None, 30, 45]),
                       vendor_sell=rng.choice([0, 5, 60]), supply_now=rng.choice([None, 0, 10, 93, 400])), now


def test_PL2_posts_are_at_least_a_stack_and_never_more_than_you_hold():
    for i, (lots, f, now) in enumerate(cases(SEED_PL2, random_world)):
        actions = plan.sell_plan(lots, {LINEN: f}, now, S)
        for lot in lots:
            mine = [a for a in actions if a.kind != "de" and a.char_id == lot.char_id]
            assert sum(a.qty for a in mine) == lot.qty, (SEED_PL2, i)          # every unit planned exactly once
            short = [a for a in mine if a.kind == "post" and a.qty < f.ref_stack]
            assert len(short) <= 1 and all(a.qty == lot.qty % f.ref_stack for a in short), (SEED_PL2, i)
        assert all(a.at_ms >= now for a in actions), (SEED_PL2, i)


def test_R18_gear_may_double_the_market_and_always_gets_two_pieces():
    green = SellFacts(GLOVES, level=800, trend7=0.0, post_price=800, sold_per_day=2.0, listed_per_day=3.0, ref_stack=1,
                      how=tuple([1.0] * 168), sales_how=tuple([1.0] * 168), windows=(), book=(), cur=800, cur_qty=1,
                      vendor_sell=100, supply_now=0, gear=True)
    assert [o.cap for o in plan.post_options(green, NOW, S)] == [2]          # nothing else listed: still 2 pieces
    assert [o.cap for o in plan.post_options(replace(green, supply_now=3), NOW, S)] == [3]   # match the 3 listed
    assert [o.cap for o in plan.post_options(replace(green, supply_now=3, gear=False), NOW, S)] == [1]   # mats: 0.5


def test_O12_a_losing_streak_lowers_the_planned_price_and_posts_a_quarter_of_the_stacks():
    from ahsync.engine.sell_record import Record
    busy = linen(sold_per_day=400.0, supply_now=2000)          # room for every stack, so only the record limits it
    record = Record(0, 2, 2, 2, False, False)
    plain = [o.price for o in plan.post_options(busy, NOW, S)]
    cut = [o.price for o in plan.post_options(replace(busy, record=record), NOW, S)]
    assert cut == [int(p * 0.95 ** 2) for p in plain]                                   # 45c → 40c (45 × 0.9025)
    posted = [a for a in plan.sell_plan([Lot("A", LINEN, 160)], {LINEN: replace(busy, record=record)}, NOW, S)
              if a.kind == "post"]
    assert sum(a.qty for a in posted) == 40                                             # 8 stacks × ¼ = 2 stacks


def test_O13_O11_the_price_never_drops_below_the_vendor_floor_and_an_undercut_caps_it():
    from ahsync.engine.sell_record import Record
    f = linen(vendor_sell=44, post_price=50, record=Record(0, 2, 2, 2, False, False))
    assert plan.post_options(f, NOW, S)[0].price == 47                                  # ceil(44 ÷ 0.95)
    undercut = linen(record=Record(0, 1, 0, 0, True, False))                            # cur 45: cap 44
    assert plan.post_options(undercut, NOW, S)[0].price == 44
    assert plan.post_options(linen(record=Record(3, 0, 0, 0, False, False)), NOW, S)[0].price == 45   # a clean record


def test_TT6_price_at_best_time_includes_the_clamped_trend():
    assert plan.slot_price(49, 1.12, 2.0, 2) == 57                      # floor(49 × 1.12 × 1.04 + 0.5)
    assert plan.slot_price(49, 1.12, 9.0, 2) == 60                      # clamped to 5%: floor(49 × 1.12 × 1.1 + 0.5)
