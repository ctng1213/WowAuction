"""Confidence percentage: how much the data behind a result can be trusted (SPEC §3.3.5)."""
from __future__ import annotations

import math
from collections.abc import Mapping

HOUR_MS = 3_600_000
VOLUME_SCORE = {"ahf": 1.0, "books": 0.7, "mail": 0.6}   # anything else (none, listing turnover) scores 0
WEIGHTS = {"price": 0.25, "volume": 0.30, "book": 0.15, "pattern": 0.15, "tier": 0.15}
HIGH, MEDIUM = 75, 45          # level thresholds in percent, only for the dot colour


def score(closes_30d: int, days_by_source: Mapping[str, int], book_age_ms: int | None,
          pattern_weeks: int | None, tier_n: int, days: int = 7) -> float:
    """Weighted mean of the components that apply (SPEC §3.3.5).

    Args:
        closes_30d: n, hourly closes in the last 30 days.
        days_by_source: How many of the last `days` days each volume source covered.
        book_age_ms: Age of the order book used, or None when there is none.
        pattern_weeks: Weeks of data in the window used, for planned posts only;
            None when posting now (the pattern component then doesn't apply).
        tier_n: Auctions behind the tier sell-through.
        days: The volume window, 7.

    Returns:
        Confidence in [0, 1].
    """
    parts = {
        "price": min(1.0, closes_30d / 60),
        "volume": sum(VOLUME_SCORE.get(src, 0.0) * n for src, n in sorted(days_by_source.items())) / days,
        "book": 1.0 if book_age_ms is not None and book_age_ms <= 2 * HOUR_MS
        else 0.5 if book_age_ms is not None and book_age_ms <= 24 * HOUR_MS else 0.3,
        "tier": min(1.0, tier_n / 30),
    }
    if pattern_weeks is not None:
        parts["pattern"] = min(1.0, pattern_weeks / 6)
    weight = sum(WEIGHTS[name] for name in parts)
    return sum(parts[name] * WEIGHTS[name] for name in sorted(parts)) / weight


def percent(confidence: float) -> int:
    """Confidence as a whole percent, rounded half up (SPEC §3.3 numeric rules).

    Args:
        confidence: In [0, 1].

    Returns:
        floor(confidence × 100 + 0.5).
    """
    return math.floor(confidence * 100 + 0.5)


def level(confidence_percent: int) -> str:
    """The dot colour's level (SPEC §3.3).

    Args:
        confidence_percent: 0-100.

    Returns:
        "high" (≥ 75), "medium" (≥ 45) or "low".
    """
    if confidence_percent >= HIGH:
        return "high"
    return "medium" if confidence_percent >= MEDIUM else "low"
