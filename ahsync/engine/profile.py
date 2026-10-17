"""Hour-of-week profiles: price ratio `how` and sales ratio `sales_how` (SPEC §3.3, §3.3.4).

Each slot's value is a recency-weighted median over the weeks of history. A slot
with data from at least 3 different weeks is used as is (confidence high); with
1-2 weeks it falls back to its weekday's value (confidence low); with none it is
flat. Values are then smoothed as the mean of the slot and the 2 slots on each side.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ahsync.engine.patterns import SLOTS, recency_weight, weighted_median
from ahsync.engine.prices import weekday_hour

WEEK_MS = 7 * 86_400_000
MIN_WEEKS = 3          # weeks of data a slot needs to stand on its own (SPEC §3.3)
SMOOTH_RADIUS = 2      # slots on each side in the smoothing mean


@dataclass(frozen=True)
class Profile:
    """A 168-slot hour-of-week profile."""

    values: tuple[float, ...]     # 168 ratios after fallback and smoothing
    weeks: tuple[int, ...]        # 168 counts of distinct weeks of data per slot
    confident: tuple[bool, ...]   # 168 flags: True when the slot had ≥ 3 weeks (confidence high)


def _observations(series: Sequence[tuple[int, float]], now_ms: int, half_life_weeks: float
                  ) -> tuple[list[list[tuple[float, float]]], list[set[int]]]:
    """Bucket a time series into hour-of-week slots with recency weights.

    Args:
        series: (time ms, value) pairs, at most one per hour.
        now_ms: Now, UTC ms; observations after it are ignored.
        half_life_weeks: Recency half-life in weeks.

    Returns:
        (per-slot (value, weight) lists, per-slot sets of week ages).
    """
    by_slot: list[list[tuple[float, float]]] = [[] for _ in range(SLOTS)]
    week_sets: list[set[int]] = [set() for _ in range(SLOTS)]
    for t, value in series:
        if t > now_ms:
            continue
        weekday, hour = weekday_hour(t)
        slot = weekday * 24 + hour
        age = (now_ms - t) // WEEK_MS
        by_slot[slot].append((float(value), recency_weight(age, half_life_weeks)))
        week_sets[slot].add(age)
    return by_slot, week_sets


def _raw_slots(by_slot: list[list[tuple[float, float]]], week_sets: list[set[int]]) -> list[float | None]:
    """Each slot's weighted median, or its weekday's when the slot has fewer than 3 weeks.

    Args:
        by_slot: Per-slot (value, weight) lists.
        week_sets: Per-slot sets of week ages.

    Returns:
        168 values; None for a slot whose weekday has no data either.
    """
    weekday_values = [weighted_median([o for h in range(24) for o in by_slot[d * 24 + h]]) for d in range(7)]
    raw: list[float | None] = []
    for slot in range(SLOTS):
        if len(week_sets[slot]) >= MIN_WEEKS:
            raw.append(weighted_median(by_slot[slot]))
        else:
            raw.append(weekday_values[slot // 24])
    return raw


def _smooth(values: list[float]) -> tuple[float, ...]:
    """Circular mean of each slot and the 2 slots on each side.

    Args:
        values: 168 values.

    Returns:
        168 smoothed values.
    """
    width = 2 * SMOOTH_RADIUS + 1
    return tuple(sum(values[(s + k) % SLOTS] for k in range(-SMOOTH_RADIUS, SMOOTH_RADIUS + 1)) / width
                 for s in range(SLOTS))


def price_profile(hourly: Sequence[tuple[int, int]], med30: int | None, now_ms: int,
                  half_life_weeks: float = 4) -> Profile:
    """`how`: weighted median close per slot ÷ med30, with fallbacks and smoothing (SPEC §3.3).

    Args:
        hourly: (hour start ms, close copper), one per item and hour, any history length.
        med30: The 30-day median in copper.
        now_ms: Now, UTC ms.
        half_life_weeks: pattern_half_life_weeks.

    Returns:
        The profile; flat 1.0 everywhere when there is no data or no med30.
    """
    by_slot, week_sets = _observations(hourly, now_ms, half_life_weeks)
    weeks = tuple(len(w) for w in week_sets)
    confident = tuple(w >= MIN_WEEKS for w in weeks)
    if not med30:
        return Profile(tuple([1.0] * SLOTS), weeks, confident)
    ratios = [1.0 if v is None else v / med30 for v in _raw_slots(by_slot, week_sets)]
    return Profile(_smooth(ratios), weeks, confident)


def sales_profile(sold_per_hour: Sequence[tuple[int, float]], now_ms: int, half_life_weeks: float = 4) -> Profile:
    """`sales_how`: units sold per slot ÷ the average hour, mean 1.0 (SPEC §3.3).

    Args:
        sold_per_hour: (hour start ms, units sold in that hour) from consecutive hourly books.
        now_ms: Now, UTC ms.
        half_life_weeks: pattern_half_life_weeks.

    Returns:
        The profile; a slot without data (nor weekday data) takes the average hour,
        so it is 1.0 before smoothing; flat 1.0 everywhere with no sales at all.
    """
    by_slot, week_sets = _observations(sold_per_hour, now_ms, half_life_weeks)
    weeks = tuple(len(w) for w in week_sets)
    confident = tuple(w >= MIN_WEEKS for w in weeks)
    raw = _raw_slots(by_slot, week_sets)
    known = [v for v in raw if v is not None]
    mean = sum(known) / len(known) if known else 0.0
    if mean <= 0:
        return Profile(tuple([1.0] * SLOTS), weeks, confident)
    filled = [mean if v is None else v for v in raw]
    smoothed = _smooth(filled)
    overall = sum(smoothed) / SLOTS
    return Profile(tuple(v / overall for v in smoothed), weeks, confident)
