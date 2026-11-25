"""Tests for ahsync/engine/sales_profile.py: the sales profile and the 3-day hold (P32, P33, PI22)."""
from __future__ import annotations

import pytest

from ahsync.engine import sales_profile as sp
from ahsync.engine.lifetime_prices import SoldHour, price_level
from tests.support.gen import cases

HOUR, DAY = 3_600_000, 86_400_000
NOW = 1_795_000_000_000 // HOUR * HOUR
SEED = 25_112_026


def steady(until_ms: int, hours: int = 40 * 24, units: float = 1.0) -> tuple[SoldHour, ...]:
    """One clean unit sold every hour for `hours` hours up to `until_ms`."""
    return tuple(SoldHour(until_ms - h * HOUR, 100, units, units) for h in range(hours))


def test_P32_the_sales_profile_names_commodities_busy_thin_and_rare_gear():
    assert sp.classify(True, True, "sold", 900.0) == sp.COMMODITY        # a commodity with a curve
    assert sp.classify(False, True, "sold", 34.0) == sp.BUSY
    assert sp.classify(False, False, "sold", 2.0) == sp.THIN
    assert sp.classify(False, False, "sold", 0.5) == sp.RARE              # under 1 a day
    assert sp.classify(False, False, "asks", 5.0) == sp.RARE              # an ask level
    assert sp.classify(False, False, None, None) == sp.RARE
    assert sp.classify(False, False, "sold", 1.0) == sp.THIN              # the bound is in


def test_P33_the_busy_line_holds_3_days():
    sold = steady(NOW - 30 * HOUR)                                       # 1 clean unit an hour until 30 h ago
    weighted = [sum(0.5 ** ((at - s.hour_ms) // HOUR / 24) for s in sold if 0 <= (at - s.hour_ms) // HOUR < 720)
                for at in (NOW, NOW - DAY, NOW - 2 * DAY)]
    assert weighted == pytest.approx([14.77, 29.54, 35.13], abs=0.01)    # the why: under 20 now, over it before
    assert [sp.has_curve(sold, NOW - d * DAY) for d in range(4)] == [False, True, True, True]
    assert sp.held_busy(sold, NOW)                                        # the last run of 3 was a curve
    assert sp.held_busy(sold, NOW + DAY)                                  # no, no, yes, yes: still busy
    assert not sp.held_busy(sold, NOW + 2 * DAY)                          # no, no, no: thin, 2 days late
    assert not sp.held_busy((), NOW)
    assert sp.held_busy(sold, NOW, now_status=False) and not sp.held_busy(sold, NOW + 2 * DAY, now_status=False)


@pytest.mark.parametrize("statuses, held", [
    ([False, True, True, True], True),                     # P33's day: the run of 3 before now
    ([True, False] * 6, True),                             # alternating: no run of 3 in the 9 days, the status now
    ([False, True] * 6, False),
    ([True, True, False, False, False], False),            # a new status that held only 2 days doesn't count yet
])
def test_P33_the_hold_reads_the_most_recent_run_of_3(monkeypatch, statuses, held):
    monkeypatch.setattr(sp, "has_curve", lambda sold, at: statuses[(NOW - at) // DAY])
    assert sp.held_busy((), NOW) is held


def random_sales(rng):
    """Random clean sales over 20 days, bursty enough to cross the curve line."""
    rate = rng.choice((0.2, 0.5, 0.8, 1.5))
    return tuple(SoldHour(NOW - h * HOUR, 100, u, u) for h in range(20 * 24)
                 if (u := rate * rng.random() * 2 * (1 if rng.random() < 0.7 else 0)) > 0)


SALES = cases(SEED, random_sales, n=60)


def test_PI22_the_held_status_agrees_with_three_equal_days_and_only_changes_to_a_status_that_held():
    for i, sold in enumerate(SALES):
        raw = [sp.has_curve(sold, NOW - d * DAY) for d in range(sp.HOLD_LOOKBACK_DAYS + sp.HOLD_DAYS + 3)]
        held = sp.held_busy(sold, NOW)
        if raw[0] == raw[1] == raw[2]:
            assert held == raw[0], f"seed {SEED} case {i}"
        runs = [raw[d] for d in range(sp.HOLD_LOOKBACK_DAYS) if raw[d] == raw[d + 1] == raw[d + 2]]
        assert held == (runs[0] if runs else raw[0]), f"seed {SEED} case {i}"
        assert sp.held_busy(sold, NOW) == held                             # the same history, the same answer
        level = price_level(sold, NOW, None, (None, None, None), None)       # today's status is price_level's curve
        assert raw[0] == (level.curve is not None), f"seed {SEED} case {i}"
        assert sp.held_busy(sold, NOW, level.curve is not None) == held
