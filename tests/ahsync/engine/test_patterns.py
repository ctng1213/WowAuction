"""Tests for ahsync/engine/patterns.py."""
from __future__ import annotations

import pytest

from ahsync.engine.patterns import (
    Window, buy_windows, recency_weight, sell_scores, sell_windows, slot_label, weighted_median, window_label,
)

TUE, WED, THU, SUN, MON = 1 * 24, 2 * 24, 3 * 24, 6 * 24, 0


def ratios(base: float, overrides: dict[int, float]) -> list[float]:
    """168 values equal to base except the given slots.

    Args:
        base: Value for every other slot.
        overrides: slot -> value.

    Returns:
        The list.
    """
    return [overrides.get(s, base) for s in range(168)]


W1_HOW = ratios(1.0, {**{TUE + h: 1.15 for h in (18, 19, 20)}, **{WED + h: 1.12 for h in (19, 20, 21)}})
FLAT = [1.0] * 168
SIX_WEEKS = [6] * 168


def test_W1_top_windows_rank_by_score_sum():
    windows = sell_windows(W1_HOW, FLAT, SIX_WEEKS)
    assert [window_label(w) for w in windows] == ["Tue 18:00–21:00", "Wed 19:00–22:00"]
    assert [round(w.score_sum, 2) for w in windows] == [3.45, 3.36]


def test_W2_a_high_price_with_few_buyers_loses():
    sales = ratios(1.0, {**{TUE + h: 0.8 for h in (18, 19, 20)}, **{WED + h: 2.0 for h in (19, 20, 21)}})
    assert sell_scores(W1_HOW, sales)[TUE + 19] == pytest.approx(0.46)
    windows = sell_windows(W1_HOW, sales, SIX_WEEKS)
    assert [window_label(w) for w in windows] == ["Wed 19:00–22:00"]


def test_W3_a_window_can_cross_midnight_and_the_week_boundary():
    how = ratios(1.0, {SUN + 23: 1.3, MON + 0: 1.3, MON + 1: 1.3})
    windows = sell_windows(how, FLAT, SIX_WEEKS)
    assert len(windows) == 1
    assert window_label(windows[0]) == "Sun 23:00–Mon 02:00"
    assert (windows[0].start_slot, windows[0].hours) == (SUN + 23, 3)


def test_W4_recency_weighted_median():
    weights = [recency_weight(age, 4) for age in (0, 1, 4)]
    assert [round(w, 2) for w in weights] == [1.0, 0.84, 0.5]
    assert weighted_median(list(zip([1.0, 1.0, 1.2], weights))) == 1.0


def test_W5_two_weeks_of_data_is_an_early_pattern():
    window = sell_windows(W1_HOW, FLAT, [2] * 168)[0]
    assert window.strength == "early pattern"


def test_W6_seven_weeks_of_data_is_established():
    window = sell_windows(W1_HOW, FLAT, [7] * 168)[0]
    assert window.strength == "established"


def test_W7_no_slot_data_means_no_windows():
    assert sell_windows(W1_HOW, FLAT, [0] * 168) == []


def test_three_to_five_weeks_is_a_pattern_and_weeks_are_the_fewest_in_the_window():
    weeks = ratios(5, {TUE + 20: 3})
    assert weeks[0] == 5
    window = sell_windows(W1_HOW, FLAT, weeks)[0]
    assert (window.weeks, window.strength) == (3, "pattern")


def test_a_flat_week_has_no_best_window():
    assert sell_windows(FLAT, FLAT, SIX_WEEKS) == []


def test_at_most_three_windows_and_ties_go_to_the_earlier_start():
    how = ratios(1.0, {10: 2.0, 30: 2.0, 50: 2.0, 70: 2.0})
    windows = sell_windows(how, FLAT, SIX_WEEKS)
    assert [w.start_slot for w in windows] == [10, 30, 50]


def test_best_slot_is_the_highest_score_in_the_window():
    how = ratios(1.0, {TUE + 18: 1.10, TUE + 19: 1.15, TUE + 20: 1.15})
    window = sell_windows(how, FLAT, SIX_WEEKS)[0]
    assert window.best_slot == TUE + 19          # ties go to the earliest
    assert window.how_avg == pytest.approx((1.10 + 1.15 + 1.15) / 3)


def test_weighted_median_of_nothing_is_none_and_labels():
    assert weighted_median([]) is None
    assert slot_label(TUE + 9) == "Tue 09:00"
    assert window_label(Window(TUE + 21, 3, 3.0, 1.0, 6, TUE + 21)) == "Tue 21:00–Wed 00:00"


def test_W10_buying_windows_are_the_cheapest_hours():
    how = ratios(1.0, {**{MON + h: 0.85 for h in (3, 4, 5)}, THU + 4: 0.93})
    windows = buy_windows(how, SIX_WEEKS)
    assert [window_label(w) for w in windows] == ["Mon 03:00–06:00", "Thu 04:00–05:00"]
    assert (windows[0].best_slot, windows[0].how_avg) == (MON + 3, pytest.approx(0.85))


def test_buying_windows_need_data_and_a_cheaper_hour_and_keep_the_top_three():
    assert buy_windows(W1_HOW, [0] * 168) == []
    assert buy_windows(FLAT, SIX_WEEKS) == []
    how = ratios(1.0, {10: 0.9, 30: 0.8, 50: 0.8, 70: 0.85})
    assert [w.start_slot for w in buy_windows(how, SIX_WEEKS)] == [30, 50, 70]     # 10 is above 0.8 ÷ 0.9
