"""Tests for ahsync/engine/timing.py: the timing premium (SPEC §3.4D.2)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahsync.engine import timing
from ahsync.engine.views import round_half_up
from tests.support.gen import cases

SHARED = json.loads((Path(__file__).resolve().parents[3] / "tests" / "scenarios" / "timing.json").read_text(encoding="utf-8"))
SEED_PF26 = 3426
MON = 1_728_259_200_000             # Mon 2024-10-07 00:00 UTC
NOW = MON + 10 * 3_600_000 + 38 * 60_000


def run(s: dict) -> object:
    """Run one shared scenario's call.

    Args:
        s: The scenario.

    Returns:
        What the engine returns.
    """
    return getattr(timing, s["call"])(**s["args"])


@pytest.mark.parametrize("s", SHARED, ids=lambda s: f"{s['id']} {s['name']}")
def test_shared_timing_scenarios(s):
    assert run(s) == s["expect"], s["why"]


def test_PF17_profit_later_is_worth_less_and_the_premium_alone_decides():
    later = {d: timing.adjusted_profit(5500, NOW, NOW + d * 86_400_000, 0.15, 0.05, 8) for d in (1, 3, 7)}
    assert [round_half_up(later[d]) for d in (1, 3, 7)] == [4583, 4231, 3667]   # 45s 83c, 42s 31c, 36s 67c
    assert later[3] > 4000 > later[7]                                          # waiting 3 days wins, 7 days loses
    assert timing.adjusted_profit(4000, NOW, None, 0.15, 0.05, 8) == 4000


def test_PF18_the_weeks_funded_rows_adjust_by_their_waits():
    day, hour = 86_400_000, 3_600_000
    rows = [(7342, 5 * day + 18 * hour), (5500, 3 * day + 19 * hour), (1855, 3 * day + 19 * hour),
            (1630, 6 * day + 18 * hour), (730, 5 * day + 19 * hour), (194, 6 * day + 19 * hour), (134, 5 * day + 19 * hour)]
    shown = [round_half_up(timing.adjusted_profit(e, NOW, MON + at, 0.15, 0.05, 8)) for e, at in rows]
    assert shown[:2] == [5187, 4175]                                          # 51s 87c, 41s 75c
    assert sum(shown) == 12624                                                # +1g 26s 24c in all
    assert timing.wait_days(NOW, MON + 3 * day + 19 * hour) == 3.35


def test_PF19_a_wait_inside_the_immediate_window_is_today_with_no_premium():
    tonight, tuesday = MON + 18 * 3_600_000, MON + 86_400_000 + 8 * 3_600_000
    assert timing.immediate(timing.wait_hundredths(NOW, tonight), 8)
    assert not timing.immediate(timing.wait_hundredths(NOW, tuesday), 8)
    assert timing.immediate(timing.wait_hundredths(NOW, tuesday), 24)
    assert timing.adjusted_profit(1000, NOW, tuesday, 0.15, 0.05, 8) == 1000 * 1e6 / 1_194_500


def random_timing(rng):
    """One generated PF26 case.

    Args:
        rng: The seeded Random.

    Returns:
        (net copper, a shorter wait in ms, a longer one, base, per day, immediate hours).
    """
    short = rng.randint(0, 8 * 86_400_000)
    return (rng.choice([-1, 1]) * rng.uniform(0, 1e6), short, short + rng.randint(0, 3 * 86_400_000),
            rng.choice([0.0, 0.15, 0.3]), rng.choice([0.0, 0.05, 0.1]), rng.choice([8, 24]))


def test_PF26_adjusted_equals_net_when_immediate_and_never_rises_with_the_wait():
    for n, (net, short, long, base, per_day, hours) in enumerate(cases(SEED_PF26, random_timing)):
        near = timing.adjusted_profit(net, NOW, NOW + short, base, per_day, hours)
        far = timing.adjusted_profit(net, NOW, NOW + long, base, per_day, hours)
        if timing.immediate(timing.wait_hundredths(NOW, NOW + short), hours):
            assert near == net, (SEED_PF26, n)
        if net > 0:
            assert 0 < far <= near <= net, (SEED_PF26, n)
        else:
            assert net <= near <= far <= 0, (SEED_PF26, n)
