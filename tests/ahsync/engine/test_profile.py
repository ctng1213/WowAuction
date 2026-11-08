"""Tests for ahsync/engine/profile.py."""
from __future__ import annotations

import pytest

from ahsync.engine import profile
from ahsync.engine.prices import weekday_hour
from ahsync.engine.profile import Profile, _slot, pack, price_profile, unpack, sales_profile, seasonality_strength, strength, supply_profile

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


TUE = 24


def test_W8_seasonality_strength_scales_with_weeks_of_data():
    assert strength(20, 50, 6) == 60            # 100 × (1 − 20/50) × 1
    assert strength(20, 50, 3) == 30            # × 3/6
    assert strength(60, 50, 6) == 0             # worse than flat is 0, never negative
    assert strength(20, 0, 6) is None           # nothing to beat
    two_weeks = history(2, lambda s: 150 if s < 84 else 50)
    assert seasonality_strength(two_weeks, 100, NOW) is None


def test_W8_a_repeating_pattern_is_predictable():
    pattern = history(6, lambda s: 150 if s % 24 < 12 else 50)
    # Smoothing blurs each of the 2 daily edges: the 4 hours around one miss by 20, 40, 40, 20c,
    # so the profile's mean error is 240c / 24 h = 10c against the flat 50c → 100 × (1 − 10/50) = 80.
    assert seasonality_strength(pattern, 100, NOW) == 80


def test_W11_a_premium_seen_once_is_not_predictable():
    def noisy(week: int):
        """A close that wobbles differently each week, plus a premium in one training week."""
        return lambda s: 140 if (week == 2 and s == SAT + 18) else 100 + 10 * ((s + week) % 3 - 1)
    closes = sorted(c for w in range(4) for c in history(4, noisy(w)) if (NOW - c[0]) // WEEK_MS == 3 - w)
    assert seasonality_strength(closes, 100, NOW) <= 10


def test_seasonality_needs_a_full_held_out_day_and_a_median():
    sparse = [c for c in history(4, lambda s: 100 + s % 2) if c[0] <= NOW - WEEK_MS or c[0] > NOW - 10 * HOUR_MS]
    assert seasonality_strength(sparse, 100, NOW) is None          # 10 held-out closes < 24
    assert seasonality_strength(history(4, lambda s: 100), None, NOW) is None


def test_W12_supply_profile():
    busy_tuesday = supply_profile(history(4, lambda s: 200 if TUE + 16 <= s <= TUE + 20 else 100), NOW)
    assert busy_tuesday.values[TUE + 18] == pytest.approx(2.0)
    assert busy_tuesday.values[TUE + 15] == pytest.approx(1.4)
    assert busy_tuesday.confident[TUE + 18]
    two_weeks = supply_profile(history(2, lambda s: 200 if TUE <= s < TUE + 24 else 100), NOW)
    assert (two_weeks.values[TUE + 12], two_weeks.values[12]) == (pytest.approx(2.0), pytest.approx(1.0))
    assert not two_weeks.confident[TUE + 12]
    assert supply_profile([], NOW).values == tuple([1.0] * 168)
    assert supply_profile(history(4, lambda s: 0), NOW).values == tuple([1.0] * 168)


def test_slots_follow_the_weekday_rule():
    for t in range(0, 3 * WEEK_MS, 7 * HOUR_MS + 123_457):
        weekday, hour = weekday_hour(t)
        assert _slot(t) == weekday * 24 + hour, t


def test_W13_profiles_pack_in_thousandths_half_up():
    values = (1.2346, 0.0625, 0.8) + (1.0,) * 165
    prof = Profile(values, (7,) * 168, (True,) * 168)
    text, weeks = pack(prof)
    assert (text, weeks) == ("1235,63,800,1000*165", "7*168")                     # run-length coded (BZ46)
    back = unpack(text, weeks)
    assert back.values[:3] == (1.235, 0.063, 0.8) and back.confident[0]
    assert pack(back) == (text, weeks)
    assert pack(Profile(values, (0,) * 168, (False,) * 168)) == ("", "")
    assert unpack("", "") == Profile((1.0,) * 168, (0,) * 168, (False,) * 168)
    with pytest.raises(ValueError, match="168 slots"):
        unpack("1000,1000", "7,7")


def test_unpack_values_reads_the_same_ratios_without_the_weeks():
    values, weeks = profile.pack(profile.Profile(tuple(0.5 + i / 1000 for i in range(168)), tuple([3] * 168),
                                                 tuple([True] * 168)))
    assert profile.unpack_values(values) == profile.unpack(values, weeks).values
    assert profile.unpack_values("") is None
    with pytest.raises(ValueError):
        profile.unpack_values("1,2,3")


def test_BZ46_profiles_are_run_length_coded_and_read_in_either_form():
    plain_text = ",".join(["760", "880"] + ["1000"] * 166)
    assert profile.expand("760,880,1000*166") == plain_text.split(",")
    assert profile.plain("760,880,1000*166") == plain_text and profile.plain("") == ""
    assert profile.unpack("760,880,1000*166", "7*168") == profile.unpack(plain_text, ",".join(["7"] * 168))
    assert profile.unpack_values("1000*168") == (1.0,) * 168
    for bad in ("1000*169", "1000*0", "1000*-2", "1000*x", "1000*100,1000*100", "1000*99999999999", "1000*167"):
        with pytest.raises(ValueError):
            profile.expand(bad)                                     # a run never grows past 168 slots (SEC16)
