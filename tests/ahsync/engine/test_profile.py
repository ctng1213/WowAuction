"""Tests for ahsync/engine/profile.py."""
from __future__ import annotations

import pytest

from ahsync.engine.profile import price_profile, sales_profile

HOUR_MS = 3_600_000
WEEK_MS = 7 * 24 * HOUR_MS
MONDAY = 345_600_000                       # 1970-01-05 00:00 UTC, a Monday
NOW = MONDAY + 101 * WEEK_MS - HOUR_MS     # a Sunday 23:00: the current week is complete
SAT = 5 * 24                               # slot of Saturday 00:00


def history(weeks: int, value_of_slot) -> list[tuple[int, float]]:
    """One value per hour for the last `weeks` whole weeks, oldest first.

    Args:
        weeks: Whole weeks of history ending with the current one.
        value_of_slot: slot (0 = Mon 00:00) -> value.

    Returns:
        (hour start ms, value) pairs.
    """
    this_monday = NOW - (NOW - MONDAY) % WEEK_MS
    return [(this_monday - w * WEEK_MS + s * HOUR_MS, value_of_slot(s))
            for w in reversed(range(weeks)) for s in range(168)]


def saturday_rise(slot: int) -> int:
    """100 c everywhere, 120 c on Saturday 16:00-20:00."""
    return 120 if SAT + 16 <= slot <= SAT + 20 else 100


def test_P3_a_slot_with_four_weeks_of_a_saturday_rise_reads_1_2_with_high_confidence():
    profile = price_profile(history(4, saturday_rise), med30=100, now_ms=NOW)
    assert profile.values[SAT + 18] == pytest.approx(1.2)
    assert profile.confident[SAT + 18] is True
    assert profile.weeks[SAT + 18] == 4


def test_P4_one_week_of_data_falls_back_to_the_weekday_with_low_confidence():
    profile = price_profile(history(1, saturday_rise), med30=100, now_ms=NOW)
    # Saturday's 24 closes: 19 at 100 c and 5 at 120 c → weekday median 100 c → 1.0
    assert profile.values[SAT + 18] == pytest.approx(1.0)
    assert profile.confident[SAT + 18] is False


def test_price_profile_without_data_or_median_is_flat():
    assert set(price_profile([], med30=100, now_ms=NOW).values) == {1.0}
    assert set(price_profile(history(4, saturday_rise), med30=None, now_ms=NOW).values) == {1.0}


def test_future_observations_are_ignored():
    future = [(NOW + HOUR_MS, 500)]
    assert price_profile(future, med30=100, now_ms=NOW).weeks == tuple([0] * 168)


def test_sales_profile_has_mean_one_and_rises_where_buyers_are():
    busy_evenings = history(4, lambda s: 6.0 if 18 <= s % 24 <= 22 else 1.0)
    profile = sales_profile(busy_evenings, now_ms=NOW)
    assert sum(profile.values) / 168 == pytest.approx(1.0)
    assert profile.values[20] > 2 * profile.values[8]


def test_sales_profile_is_flat_without_sales():
    assert set(sales_profile([], now_ms=NOW).values) == {1.0}
    assert set(sales_profile(history(4, lambda s: 0.0), now_ms=NOW).values) == {1.0}


def test_sales_slots_without_any_weekday_data_take_the_average_hour():
    mondays_only = [(t, v) for t, v in history(4, lambda s: 2.0) if (t - MONDAY) % WEEK_MS < 24 * HOUR_MS]
    profile = sales_profile(mondays_only, now_ms=NOW)
    assert profile.values[3 * 24 + 12] == pytest.approx(1.0)      # a Thursday: no data at all
