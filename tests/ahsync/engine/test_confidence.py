"""Tests for ahsync/engine/confidence.py."""
from __future__ import annotations

from ahsync.engine.confidence import level, percent, score

HOUR_MS = 3_600_000


def test_CF1_every_component_full_is_100_percent():
    c = score(60, {"ahf": 7}, 1 * HOUR_MS, pattern_weeks=7, tier_n=30)
    assert percent(c) == 100 and level(percent(c)) == "high"


def test_CF2_thin_data_for_a_planned_post():
    # 0.25×0.25 + 0.30×0.7 + 0.15×0.5 + 0 + 0 = 0.3475 ÷ 1.00 → 35 %
    c = score(15, {"books": 7}, 12 * HOUR_MS, pattern_weeks=0, tier_n=0)
    assert (percent(c), level(percent(c))) == (35, "low")


def test_CF3_posting_now_leaves_the_pattern_out():
    c = score(15, {"books": 7}, 12 * HOUR_MS, pattern_weeks=None, tier_n=0)
    assert percent(c) == 41          # 0.3475 ÷ 0.85 = 0.409


def test_CF4_mixed_volume_sources_score_by_share_of_days():
    c = score(60, {"ahf": 4, "books": 3}, 1 * HOUR_MS, pattern_weeks=None, tier_n=30)
    assert (percent(c), level(percent(c))) == (95, "high")


def test_old_or_missing_books_and_unknown_volume():
    assert score(0, {}, None, None, 0) == 0.3 * 0.15 / 0.85
    assert score(0, {}, 30 * HOUR_MS, None, 0) == score(0, {}, None, None, 0)
    assert level(45) == "medium" and level(44) == "low"
