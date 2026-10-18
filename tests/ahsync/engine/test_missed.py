"""Tests for ahsync/engine/missed.py: late and missed actions, the missed-peak re-plan (SPEC §3.4.0), invariant PL3."""
from __future__ import annotations

from dataclasses import replace

import pytest

from ahsync.engine import missed, plan, sellprob
from ahsync.engine.plan import Lot, PlanSettings, PlanWindow, SellFacts
from tests.support.gen import cases

HOUR, DAY = 3_600_000, 86_400_000
MONDAY = 345_600_000 + 3000 * 7 * DAY
TUE, WED, THU = 1, 2, 3
WEEK_END = MONDAY + 7 * DAY
S = PlanSettings()
ANCHOR = 1000.0                                   # the original Tue peak's expected net: 10s
SEED_PL3 = 4033


def at(day: int, hour: int, week: int = 0) -> int:
    """ms of a weekday and hour of the plan week."""
    return MONDAY + (week * 7 + day) * DAY + hour * HOUR


def raid_nights(wed: float, thu: float, **changes) -> SellFacts:
    """An item with Tue, Wed and Thu 20:00-23:00 windows peaking at 20:00; med30 10s; no vendor value or deposit."""
    how = [1.0] * 168
    how[WED * 24 + 20], how[THU * 24 + 20] = wed, thu
    windows = tuple(PlanWindow(d * 24 + 20, 3, d * 24 + 20) for d in (TUE, WED, THU))
    base = SellFacts(2589, med30=1000, trend7=0.0, post_price=1000, sold_per_day=40.0, listed_per_day=10.0,
                     ref_stack=1, how=tuple(how), sales_how=tuple([1.0] * 168), windows=windows, book=(), cur=1000,
                     cur_qty=1, vendor_sell=0, supply_now=40)
    return replace(base, **changes)


@pytest.fixture
def chances(monkeypatch):
    """Fix each window's chance to sell by weekday, so every expected value is hand-checkable."""
    by_day = {WED: 1.0, THU: 1.0}

    def chance(f, price, qty, hours, ahead, s):
        """The chance of the window these hours end in."""
        return by_day[(hours[-1] // 24 + 3) % 7]
    monkeypatch.setattr(missed, "post_chance", chance)
    return by_day


def test_M1_a_missed_tuesday_peak_moves_to_wednesday(chances):
    chances[WED] = 0.88
    f = raid_nights(wed=1.1364, thu=1.0)                           # 0.88 × 11s 36c × 0.95 = 9s 50c: 95% of 10s
    r = missed.replan_missed(ANCHOR, at(TUE, 23), 1, f, at(TUE, 23, 0) + 30 * 60_000, WEEK_END, S, 0.5, 0.10)
    assert (r.kind, r.at_ms, r.window[0]) == ("post", at(WED, 12), at(WED, 20))   # posted 8 h before the 20:00 peak
    assert round(r.net) == 950 and r.p == 0.88 and r.tried[0].failed == ""


def test_M2_wednesday_missed_too_and_thursday_is_20_percent_below_so_it_holds(chances):
    f = raid_nights(wed=1.0, thu=0.8421)                           # Thu: 8s 42c × 0.95 = 8s: 80% of the Tue anchor
    r = missed.replan_missed(ANCHOR, at(WED, 23), 1, f, at(WED, 23) + 60_000, WEEK_END, S, 0.5, 0.10)
    assert (r.kind, [t.failed for t in r.tried]) == ("hold", ["below the original"])


def test_M3_a_replacement_is_always_compared_with_the_original_not_the_last_replacement(chances):
    f = raid_nights(wed=0.9684, thu=0.9053)                        # Wed 9s 20c (92%), Thu 8s 60c (86% of Tue, 93% of Wed)
    r = missed.replan_missed(ANCHOR, at(WED, 23), 1, f, at(WED, 23) + 60_000, WEEK_END, S, 0.5, 0.10)
    assert r.kind == "hold" and round(r.tried[0].net) == 860


def test_M4_a_close_window_that_is_unlikely_to_sell_is_skipped_for_the_next(chances):
    chances[WED] = 0.40
    f = raid_nights(wed=2.5263, thu=1.0)                           # Wed: 0.40 × 25s 26c × 0.95 = 9s 60c (96%)
    r = missed.replan_missed(ANCHOR, at(TUE, 23), 1, f, at(TUE, 23) + 60_000, WEEK_END, S, 0.5, 0.10)
    assert [t.failed for t in r.tried] == ["unlikely to sell", ""] and r.window[0] == at(THU, 20)


def test_M5_a_window_below_the_vendor_value_is_not_worth_it(chances):
    f = raid_nights(wed=1.0105, thu=1.0, vendor_sell=1000)         # Wed 9s 60c, but the vendor pays 10s
    r = missed.replan_missed(ANCHOR, at(TUE, 23), 1, f, at(TUE, 23) + 60_000, WEEK_END, S, 0.5, 0.10)
    assert r.kind == "hold" and [t.failed for t in r.tried] == ["not worth it", "not worth it"]


def test_M6_a_new_week_is_a_fresh_slate():
    # Mon 00:00: the plan doesn't know last week's misses; the item is planned for this week's best window.
    f = raid_nights(wed=1.3, thu=1.0)
    posts = [a for a in plan.sell_plan([Lot("A", 2589, 1)], {2589: f}, at(0, 0, 1), S) if a.kind == "post"]
    assert [a.peak_ms for a in posts] == [at(WED, 20, 1)]


def test_M7_a_post_whose_post_time_passed_before_its_window_is_late_not_missed():
    assert missed.status_now("post", at(TUE, 12), at(TUE, 23), at(TUE, 18)) == "late"
    assert [h % 24 for h in sellprob.planned_window(at(TUE, 12), at(TUE, 23), at(TUE, 18))] == list(range(18, 23))
    assert missed.status_now("post", at(TUE, 12), at(TUE, 23), at(TUE, 23)) == "missed"
    assert missed.status_now("post", at(TUE, 12), at(TUE, 23), at(TUE, 11)) == "pending"


def test_other_actions_are_missed_24_hours_after_their_slot_and_holds_wait():
    assert missed.status_now("buy", at(TUE, 12), None, at(WED, 11)) == "pending"
    assert missed.status_now("buy", at(TUE, 12), None, at(WED, 12)) == "missed"
    assert missed.status_now("post", at(TUE, 12), None, at(WED, 12)) == "missed"      # posting now: 24 h too
    assert missed.status_now("hold", at(TUE, 12), None, at(THU, 12)) == "pending"


def random_miss(rng):
    """A random missed Tuesday peak with random later windows and chances."""
    f = raid_nights(wed=rng.uniform(0.5, 1.5), thu=rng.uniform(0.5, 1.5), vendor_sell=rng.choice([0, 300, 900]))
    return f, rng.uniform(200, 1500), {WED: rng.random(), THU: rng.random()}


def test_PL3_a_replacement_is_close_to_the_original_likely_to_sell_and_this_week_or_it_is_a_hold(monkeypatch):
    for i, (f, anchor, by_day) in enumerate(cases(SEED_PL3, random_miss, 500)):
        monkeypatch.setattr(missed, "post_chance", lambda f, price, qty, hours, ahead, s: by_day[(hours[-1] // 24 + 3) % 7])
        r = missed.replan_missed(anchor, at(TUE, 23), 1, f, at(TUE, 23) + 60_000, WEEK_END, S, 0.5, 0.10)
        if r.kind == "post":
            assert r.net >= 0.9 * anchor and r.p >= 0.5 and at(TUE, 23) <= r.window[0] < WEEK_END, (SEED_PL3, i)
        else:
            assert r.tried == () or all(t.failed for t in r.tried), (SEED_PL3, i)
