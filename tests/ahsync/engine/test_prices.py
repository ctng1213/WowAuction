"""Tests for ahsync/engine/prices.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahsync.engine.prices import (
    DAY_MS, HOUR_MS, cur_others, median, percentile, post_price, suspect_price, trend7, weekday_hour, window_median,
)

SHARED = json.loads((Path(__file__).resolve().parents[3] / "tests" / "scenarios" / "prices.json").read_text(encoding="utf-8"))

G, S = 10_000, 100
NOW = 100 * DAY_MS          # a fixed "now": day 100 at 00:00 UTC


def hours_back(prices: list[int]) -> list[tuple[int, int]]:
    """Hourly closes ending at NOW, one hour apart, oldest first.

    Args:
        prices: Close prices in copper, oldest first.

    Returns:
        (hour start ms, close) pairs.
    """
    n = len(prices)
    return [(NOW - (n - 1 - i) * HOUR_MS, price) for i, price in enumerate(prices)]


def test_P1_seven_day_median_of_hourly_closes():
    assert window_median(hours_back([g * G for g in range(1, 10)]), [], NOW, 7) == 5 * G


def test_P2_median_falls_back_to_daily_closes_when_hourly_are_sparse():
    hourly = hours_back([100 * G, 100 * G])                        # only 2 hourly closes
    daily = [(NOW - d * DAY_MS, (d + 1) * G) for d in range(7)]    # 1..7 g
    assert window_median(hourly, daily, NOW, 7) == 4 * G


def test_P10_even_count_median_floors_the_mean_of_the_middle_two():
    assert median([1 * G, 2 * G, 3 * G, 4 * G]) == 2 * G + 50 * S
    assert median([1, 2]) == 1


def test_P11_weekday_and_hour_use_the_utc_date():
    assert weekday_hour(345_600_000) == (0, 0)          # 1970-01-05 00:00 UTC, a Monday


def test_R12_post_price_does_not_undercut_your_own_lowest_auction():
    book = [(50, 1), (55, 2), (70, 1)]
    own = [(50, 1)]
    assert cur_others(book, own, cur=50) == 55
    assert post_price(55, 60) == 54


def test_median_and_percentile_of_nothing_is_none():
    assert median([]) is None
    assert percentile([], 0.1) is None


def test_nearest_rank_percentiles():
    values = list(range(1, 11))                  # 1..10
    assert percentile(values, 0.1) == 1          # ceil(1.0) = rank 1
    assert percentile(values, 0.9) == 9          # ceil(9.0) = rank 9
    assert percentile(values, 0.95) == 10        # ceil(9.5) = rank 10
    assert percentile([5], 0.01) == 5


def test_window_median_uses_hourly_closes_when_there_are_enough():
    hourly = hours_back([1 * G] * 20 + [3 * G] * 10)             # 30 closes in the last 30 h
    assert window_median(hourly, [(NOW, 99 * G)], NOW, 7) == 1 * G


def test_window_median_ignores_closes_outside_the_window_and_is_none_without_data():
    old = [(NOW - 8 * DAY_MS, 5 * G)]
    assert window_median(old, old, NOW, 7) is None
    assert window_median([(NOW, 2 * G)], [], NOW, 7) == 2 * G    # sparse hourly, no daily


def test_trend_is_the_daily_median_slope_as_percent_of_med7():
    # daily medians 100, 102, 104, 106 c over 4 days: slope 2 c/day; med7 100 → 2 %/day
    hourly = [(NOW - (3 - d) * DAY_MS, 100 + 2 * d) for d in range(4)]
    assert trend7(hourly, NOW, 100) == 2.0


def test_trend_needs_four_days_and_a_median():
    three_days = [(NOW - d * DAY_MS, 100) for d in range(3)]
    assert trend7(three_days, NOW, 100) == 0.0
    four_days = [(NOW - d * DAY_MS, 100) for d in range(4)]
    assert trend7(four_days, NOW, None) == 0.0
    assert trend7(four_days + [(NOW - 9 * DAY_MS, 1)], NOW, 100) == 0.0   # old close ignored; flat


def test_cur_others_cases():
    assert cur_others([], [], None) is None                     # nothing listed
    assert cur_others([], [(60, 1)], 50) == 50                  # someone else holds the lowest
    assert cur_others([], [(50, 1)], 50) is None                # you hold it and there's no book
    assert cur_others([(50, 2)], [(50, 1)], 50) == 50           # you hold only one of two at 50 c
    assert cur_others([(50, 1)], [(50, 1)], 50) is None         # the whole book is yours


def test_post_price_with_missing_inputs():
    assert post_price(None, 60) == 60
    assert post_price(55, None) == 54
    assert post_price(None, None) is None


@pytest.mark.parametrize("s", SHARED, ids=[s["name"] for s in SHARED])
def test_PP1_post_price_shared_with_the_addon(s):
    assert post_price(s["args"]["cur_others"], s["args"]["med7"]) == s["expect"], s["why"]


def test_FM5_a_price_far_out_of_range_is_suspect_until_a_second_hour_confirms_it():
    assert suspect_price(1, 30, 40, [1])                  # 1c < 10% of a 30c p10 (3c); one hour of it is no proof
    assert not suspect_price(1, 30, 40, [1, 1])           # two hours at 1c: it is the market now
    assert suspect_price(401, 30, 40, [45])               # 401c > 10 × a 40c p90
    assert not suspect_price(3, 30, 40, [])               # 3c is not below 3c
    assert not suspect_price(None, 30, 40, []) and not suspect_price(1, None, 40, []) and not suspect_price(1, 30, None, [])
