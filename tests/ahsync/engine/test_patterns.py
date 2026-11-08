"""Tests for ahsync/engine/patterns.py."""
from __future__ import annotations

import math
from dataclasses import replace

import pytest

from ahsync.engine.patterns import (found_share, 
    Forecast, ForecastFacts, Window, buy_value, buy_windows, recency_weight, sell_net_price, sell_scores, sell_windows,
    slot_label, weighted_median, window_forecast, window_label,
)

from ahsync.engine.capacity import liquidity_cap

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


def test_W16_a_slot_without_data_never_joins_a_window():
    tue = 24
    weeks = [0] * 168
    for h in (18, 19, 20):
        weeks[tue + h] = 1
    how = [1.0] * 168
    for h in range(24):
        how[tue + h] = 1.5                     # the weekday fallback fills Tuesday's other hours as high
    how[tue + 18], how[tue + 19] = 1.0, 1.6     # one data hour is weak, so the observed hours aren't flat
    sell = sell_windows(how, [1.0] * 168, weeks)
    buy = buy_windows([2.0 - x for x in how], weeks)
    data = {tue + 18, tue + 19, tue + 20}
    for w in sell + buy:
        assert {(w.start_slot + i) % 168 for i in range(w.hours)} <= data and w.weeks >= 1
    assert sell and buy
    assert sell_windows(how, [1.0] * 168, [0] * 168) == [] and buy_windows(how, [0] * 168) == []
    flat = [1.5 if weeks[s] else 9.9 for s in range(168)]            # every hour with data alike: no best time
    assert sell_windows(flat, [1.0] * 168, weeks) == [] and buy_windows(flat, weeks) == []


MON_MS = 1_728_259_200_000             # Mon 2024-10-07 00:00 UTC: slot 0


def facts(**changes) -> ForecastFacts:
    """W15's item: level 100c, no trend or clamps, 93 units listed by others with a flat supply pattern, stack 20.

    Args:
        changes: Field overrides.

    Returns:
        The ForecastFacts.
    """
    base = ForecastFacts(MON_MS, 100, 0.0, None, None, None, 720, 93, FLAT, 20, 0.5, 0.5, 0, 0.15)
    return replace(base, **changes)


def test_W14_buy_windows_rank_by_actionable_value_not_by_price():
    how = ratios(1.0, {**{MON + h: 0.85 for h in (3, 4, 5)}, THU + 4: 0.93})
    listed = {MON + 3: 10, THU + 4: 100}

    def forecast(w: Window) -> Forecast:
        """Level 100c × the window's ratio, a stack of 5 or half of what others list, confidence 80%."""
        return Forecast(math.floor(100 * w.how_avg + 0.5), liquidity_cap(listed[w.start_slot], 0.5, 5), 80)

    top_sell = [replace(Window(TUE + 18, 3, 3.6, 1.2, 6, TUE + 18), forecast=Forecast(120, 46, 80))]
    net = sell_net_price(top_sell, 100)
    assert net == pytest.approx(114.0)                                        # 120c after the 5% cut
    windows = buy_windows(how, SIX_WEEKS, forecast=forecast, sell_net=net)
    assert [window_label(w) for w in windows] == ["Thu 04:00–05:00", "Mon 03:00–06:00"]   # the built ranking: Mon
    assert [buy_value(net, w.forecast) for w in windows] == [pytest.approx(840.0), pytest.approx(116.0)]
    assert [(w.forecast.price, w.forecast.exec_qty) for w in windows] == [(93, 50), (85, 5)]
    assert [window_label(w) for w in buy_windows(how, SIX_WEEKS, top=1, forecast=forecast, sell_net=net)] == \
        ["Thu 04:00–05:00"]                                                   # top_buy_windows
    # Selling at 90c (85.5c after the cut) leaves Thu at 0.93 worth 0: dropped; Mon still pays 0.5c a unit.
    assert [window_label(w) for w in buy_windows(how, SIX_WEEKS, forecast=forecast, sell_net=85.5)] == \
        ["Mon 03:00–06:00"]
    # Without a price level there is no forecast: the windows keep the cheapest-first ranking (W10).
    assert [w.start_slot for w in buy_windows(how, SIX_WEEKS)] == [MON + 3, THU + 4]


def test_W14_equal_values_go_to_the_lower_price_then_the_earlier_start():
    how = ratios(1.0, {10: 0.85, 30: 0.85, 50: 0.86})
    prices = {10: 90, 30: 90, 50: 85}
    windows = buy_windows(how, SIX_WEEKS, forecast=lambda w: Forecast(prices[w.start_slot], 2 if w.start_slot != 50 else 1, 100),
                          sell_net=100.0)
    # 10 and 30: (100 − 90) × 2 = 20; 50: (100 − 85) × 1 = 15.
    assert [w.start_slot for w in windows] == [10, 30, 50]
    tied = buy_windows(how, SIX_WEEKS, forecast=lambda w: Forecast(prices[w.start_slot] - (5 if w.start_slot == 30 else 0),
                                                                   1, 100), sell_net=95.0)
    # 10: 5; 30: (95 − 85) = 10; 50: 10 at 85 too: 30 and 50 tie on value and price, the earlier start first.
    assert [w.start_slot for w in tied] == [30, 50, 10]


def test_W15_every_window_carries_its_expected_price_executable_quantity_and_confidence():
    f = facts()
    sell = sell_windows(W1_HOW, FLAT, SIX_WEEKS, forecast=lambda w: window_forecast(w, f))
    assert [window_label(w) for w in sell] == ["Tue 18:00–21:00", "Wed 19:00–22:00"]        # sell_score order kept
    # 100c × 1.15 and × 1.12; max(20, floor(0.5 × 93)) = 46; confidence 0.25 + 0.3 × 0.5 + 0.15 (book) + 0.15 (6 weeks)
    # + 0 (no tier history) = 70%.
    assert [w.forecast for w in sell] == [Forecast(115, 46, 70), Forecast(112, 46, 70)]
    assert [w.strength for w in sell] == ["established", "established"]
    assert [w.start_slot for w in sell_windows(W1_HOW, FLAT, SIX_WEEKS, top=1)] == [TUE + 18]   # top_sell_windows
    buy = buy_windows(W1_HOW, SIX_WEEKS, forecast=lambda w: window_forecast(w, f), sell_net=sell_net_price(sell, 100))
    assert buy and all(w.forecast == Forecast(100, 46, 70) for w in buy)


def test_window_forecasts_follow_supply_trend_and_clamps():
    w = Window(TUE + 18, 3, 3.45, 1.15, 6, TUE + 19)
    # Supply unknown: one stack. Half the supply in the window's hours as now: the cap halves.
    assert window_forecast(w, facts(supply=None)).exec_qty == 20
    pattern = ratios(1.0, {TUE + h: 0.5 for h in (18, 19, 20)})
    assert window_forecast(w, facts(supply=400, supply_how=pattern)).exec_qty == 100     # 400 × 0.5 × 0.5
    # A 10%/day trend counts as 5%/day: 1.79 days to Tue 19:00 → × 1.0896; a smaller max_trend_total caps it, and the
    # forecast stays under p90.
    assert window_forecast(w, facts(trend7=10.0)).price == 125                           # 100 × 1.15 × 1.0896
    assert window_forecast(w, facts(trend7=10.0, max_trend_total=0.05)).price == 121     # 100 × 1.15 × 1.05
    assert window_forecast(w, facts(trend7=10.0, p90=120)).price == 120
    assert window_forecast(w, facts(closes_30d=4)).price == 100                          # thin: capped at the level
    assert window_forecast(w, facts(closes_30d=None)).conf == 45                          # unknown count: 0 closes
    assert sell_net_price([], 100) == pytest.approx(95.0)


def test_found_share_weighs_the_weeks_a_buy_window_offered_its_price():
    tue_0700 = (20_396 * 24 + 7) * 3_600_000            # day 20396 is a Tuesday: slot 31
    week = 7 * 86_400_000
    now = tue_0700 + 2 * 3_600_000
    closes = [(tue_0700, 17), (tue_0700 + 3_600_000, 25),          # this week: lowest 17, at the price
              (tue_0700 - week, 19), (tue_0700 - week + 3_600_000, 18),   # a week ago: lowest 18, above it
              (tue_0700 - 2 * week + 3_600_000, 16),                   # two weeks ago: 16
              (tue_0700 + 5 * 3_600_000, 1), (tue_0700 - 8 * 3_600_000, 1)]   # outside the window: ignored
    # weights 1, 0.7071, 0.5 (half-life 2 weeks): (1 + 0.5) / 2.2071
    assert found_share(closes, 31, 3, 17, now, 2) == pytest.approx(1.5 / (1 + 0.5 ** 0.5 + 0.5))
    assert found_share(closes, 31, 3, None, now, 2) is None
    assert found_share(closes, 100, 3, 17, now, 2) is None         # no close in the window yet: "—"
    assert found_share([(now + 1, 1)], 31, 3, 17, now, 2) is None  # the future is ignored


def test_found_share_counts_a_window_in_progress_as_its_own_week():
    tue_0700 = (20_396 * 24 + 7) * 3_600_000            # slot 31; the window runs 07:00-10:00
    week = 7 * 86_400_000
    now = tue_0700 + 90 * 60_000                         # Tue 08:30: the window is in progress
    closes = [(tue_0700, 30), (tue_0700 + 3_600_000, 30),                                  # this week so far: 30
              (tue_0700 - week, 30), (tue_0700 - week + 3_600_000, 30), (tue_0700 - week + 7_200_000, 10)]   # last: 10
    # last week's 09:00 hour belongs to last week's occurrence, not this one: (0 + 0.7071) / 1.7071
    assert found_share(closes, 31, 3, 17, now, 2) == pytest.approx(0.5 ** 0.5 / (1 + 0.5 ** 0.5))
