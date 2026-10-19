"""Tests for ahsync/engine/confidence.py."""
from __future__ import annotations

import pytest

from ahsync.engine.confidence import level, percent, score
from ahsync.engine.volume import Cover, Volume, daily_volume

HOUR_MS = 3_600_000


def volume(ids_days: int, book_days: int) -> float:
    """The volume component of a week with whole days of id diffs, then whole days of book estimates.

    Args:
        ids_days: Days of id diffs.
        book_days: Days of book estimates after them.

    Returns:
        DailyVolume.volume_score.
    """
    rows = {d: {"ids": Volume(5, 0, 0)} for d in range(ids_days)}
    rows.update({d: {"books": Volume(5, 0, 0)} for d in range(ids_days, ids_days + book_days)})
    return daily_volume(rows, range(7), {d: {"ids": Cover("ids")} for d in range(ids_days)}).volume_score


def test_CF1_every_component_full_is_100_percent():
    c = score(60, volume(7, 0), 1 * HOUR_MS, pattern_weeks=7, tier_n=30)
    assert percent(c) == 100 and level(percent(c)) == "high"


def test_CF2_thin_data_for_a_planned_post():
    # 0.25×0.25 + 0.30×0.8 + 0.15×0.5 + 0 + 0 = 0.3775 ÷ 1.00 → 38 %
    c = score(15, volume(0, 7), 12 * HOUR_MS, pattern_weeks=0, tier_n=0)
    assert (percent(c), level(percent(c))) == (38, "low")


def test_CF3_posting_now_leaves_the_pattern_out():
    c = score(15, volume(0, 7), 12 * HOUR_MS, pattern_weeks=None, tier_n=0)
    assert percent(c) == 44          # 0.3775 ÷ 0.85 = 0.444


def test_CF4_mixed_volume_sources_score_by_how_each_day_was_observed():
    assert volume(4, 3) == pytest.approx((4 + 3 * 0.8) / 7)
    c = score(60, volume(4, 3), 1 * HOUR_MS, pattern_weeks=None, tier_n=30)
    assert (percent(c), level(percent(c))) == (97, "high")


def test_old_or_missing_books_and_unknown_volume():
    assert score(0, 0.0, None, None, 0) == 0.3 * 0.15 / 0.85
    assert score(0, 0.0, 30 * HOUR_MS, None, 0) == score(0, 0.0, None, None, 0)
    assert level(45) == "medium" and level(44) == "low"
