"""Tests for tools/sim/pattern_world.py: the pattern world (sold-price plan §1d items 22, 23 and 27)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from sim.inference import START_MS  # noqa: E402
from sim.market import DAY_MS, HOUR_MS  # noqa: E402
from sim.pattern_world import (CRASH_TO, EARLY_CRASH_DAY, PREMIUM, SOLD_BY_LOCAL_HOUR, UTC_OFFSET_HOURS,  # noqa: E402
                               Planted, afternoon_premium, buyer_rhythm, by_slot, calibration, crash, factor_at,
                               kind_shifts, local_hour, pattern_world, slide)


def test_local_hours_are_five_behind_utc():
    assert local_hour(START_MS) == 19                     # Fri 00:00 UTC is Thu 19:00 local
    assert by_slot(list(range(24)))[:2] == (19, 20)       # Monday 00:00 UTC takes local 19:00


def test_the_buyers_rhythm_keeps_the_measured_hours_with_a_mean_of_one():
    assert sum(buyer_rhythm()) / 168 == pytest.approx(1.0)
    assert max(SOLD_BY_LOCAL_HOUR) / min(SOLD_BY_LOCAL_HOUR) == pytest.approx(2.0)   # 5.6% against 2.8%


def test_the_premium_is_dearest_in_the_afternoon():
    by_local = [afternoon_premium()[(h - UTC_OFFSET_HOURS) % 24] for h in range(24)]   # Monday, local hour order
    assert max(by_local) == by_local[16] and min(by_local) == by_local[4]
    assert max(by_local) / min(by_local) == pytest.approx((1 + PREMIUM / 2) / (1 - PREMIUM / 2))


def test_a_slide_and_a_crash_step_hourly():
    falling = slide(START_MS, START_MS + 2 * DAY_MS, 0.025)
    assert len(falling) == 48 and factor_at(falling, START_MS + DAY_MS - 1) == pytest.approx(0.975)
    assert factor_at(falling, START_MS - 1) == 1.0
    fall = crash(START_MS)
    assert factor_at(fall, START_MS + 3 * DAY_MS) == pytest.approx(CRASH_TO)
    assert factor_at(fall, START_MS + 30 * HOUR_MS) == pytest.approx(CRASH_TO ** (31 / 72))
    crashed = Planted("early crash", 200, kind_shifts("early crash"))
    assert crashed.worth_at(START_MS + EARLY_CRASH_DAY * DAY_MS - 1) == 200
    assert crashed.worth_at(START_MS + (EARLY_CRASH_DAY + 3) * DAY_MS) == pytest.approx(20)
    assert kind_shifts("steady") == kind_shifts("premium") == ()


def test_the_world_plants_every_kind():
    world, planted = pattern_world(1, days=2, kinds=(("steady", 1), ("premium", 1), ("slide", 1)))
    assert [planted[i.key].kind for i in world.items] == ["steady", "premium", "slide"]
    assert all(len(i.weekly) == 168 for i in world.items)
    assert [bool(i.hourly) for i in world.items] == [False, True, False]


def test_the_world_matches_the_measured_rhythm_and_premium():
    found = calibration(None, 1, days=10, kinds=(("steady", 2), ("premium", 3)))
    measures = {m.what.split(" (")[0]: m for m in found}
    assert all(m.band == "reported" for m in found)
    assert 1.6 < measures["world: the busiest hour's sales ÷ the quietest's"].engine < 2.6
    assert 0.98 < measures["world: steady items' sold price, afternoon ÷ night"].engine < 1.02
    assert measures["world: premium items' sold price, afternoon ÷ night"].engine > 1.02
