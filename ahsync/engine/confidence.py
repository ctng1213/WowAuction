"""Confidence percentage: how much the data behind a result can be trusted (SPEC §3.3.5)."""
from __future__ import annotations

import math
HOUR_MS = 3_600_000
WEIGHTS = {"price": 0.25, "volume": 0.30, "book": 0.15, "pattern": 0.15, "tier": 0.15}
HIGH, MEDIUM = 75, 45          # level thresholds in percent, only for the dot colour


def score(closes_30d: int, volume_score: float, book_age_ms: int | None,
          pattern_weeks: int | None, tier_n: int) -> float:
    """Weighted mean of the components that apply (SPEC §3.3.5).

    Args:
        closes_30d: n, hourly closes in the last 30 days.
        volume_score: The volume component, 0-1: each day's score by how it was observed, averaged over the
            window (engine.volume.DailyVolume.volume_score; 0 without a source).
        book_age_ms: Age of the order book used, or None when there is none.
        pattern_weeks: Weeks of data in the window used, for planned posts only;
            None when posting now (the pattern component then doesn't apply).
        tier_n: Auctions behind the tier sell-through.

    Returns:
        Confidence in [0, 1].
    """
    parts = {
        "price": min(1.0, closes_30d / 60),
        "volume": volume_score,
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
