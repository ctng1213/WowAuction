"""Hour-of-week profiles: `how`, `sales_how`, `supply_how`, and seasonality strength (SPEC §3.3, §3.3.4, §3.3.6).

Each slot's value is a recency-weighted median over the weeks of history. A slot
with data from at least 3 different weeks is used as is (confidence high); with
1-2 weeks it falls back to its weekday's value (confidence low); with none it is
flat. Values are then smoothed as the mean of the slot and the 2 slots on each side.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ahsync.engine.patterns import SLOTS, recency_weight, weighted_median

HOUR_MS = 3_600_000
WEEK_MS = 7 * 86_400_000
MIN_WEEKS = 3          # weeks of data a slot needs to stand on its own (SPEC §3.3)
SMOOTH_RADIUS = 2      # slots on each side in the smoothing mean
PACK_SCALE = 1000                # packed profiles keep ratios in thousandths (SPEC §5.0 contents)
STRENGTH_MIN_HELD_OUT = 24      # held-out closes seasonality strength needs (SPEC §3.3.6)
STRENGTH_FULL_WEEKS = 6         # weeks of data at which strength is no longer scaled down


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
    weight_of_age: dict[int, float] = {}       # at most ~13 ages; the same value recency_weight gives
    for t, value in series:
        if t > now_ms:
            continue
        slot = _slot(t)
        age = (now_ms - t) // WEEK_MS
        weight = weight_of_age.get(age)
        if weight is None:
            weight = weight_of_age[age] = recency_weight(age, half_life_weeks)
        by_slot[slot].append((float(value), weight))
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


def supply_profile(hourly_qty: Sequence[tuple[int, int]], now_ms: int, half_life_weeks: float = 4) -> Profile:
    """`supply_how`: median listed units per slot ÷ the overall median listed units (SPEC §3.3.6).

    Args:
        hourly_qty: (hour start ms, units listed), one per item and hour; hours never observed are absent.
        now_ms: Now, UTC ms.
        half_life_weeks: pattern_half_life_weeks.

    Returns:
        The profile, with the same weekday fallback and smoothing as `how`; flat 1.0
        everywhere when there is no data or the overall median is 0.
    """
    by_slot, week_sets = _observations(hourly_qty, now_ms, half_life_weeks)
    weeks = tuple(len(w) for w in week_sets)
    confident = tuple(w >= MIN_WEEKS for w in weeks)
    overall = weighted_median([o for slot in by_slot for o in slot])
    if not overall:
        return Profile(tuple([1.0] * SLOTS), weeks, confident)
    ratios = [1.0 if v is None else v / overall for v in _raw_slots(by_slot, week_sets)]
    return Profile(_smooth(ratios), weeks, confident)


def strength(error_profile: float, error_flat: float, weeks: int) -> int | None:
    """Seasonality strength from the held-out errors (SPEC §3.3.6).

    Args:
        error_profile: Mean absolute error of the weekly profile's prediction, copper.
        error_flat: Mean absolute error of a flat med30 prediction, copper.
        weeks: Weeks of data behind the item.

    Returns:
        floor(100 × max(0, 1 − error_profile ÷ error_flat) × min(1, weeks ÷ 6) + 0.5), 0-100;
        None when error_flat is 0 (nothing to beat).
    """
    if error_flat == 0:
        return None
    gain = max(0.0, 1 - error_profile / error_flat)
    return math.floor(100 * gain * min(1.0, weeks / STRENGTH_FULL_WEEKS) + 0.5)


def seasonality_strength(hourly: Sequence[tuple[int, int]], med30: int | None, now_ms: int,
                         half_life_weeks: float = 4) -> int | None:
    """How predictable the weekly price pattern is, tested out of sample (SPEC §3.3.6).

    Builds `how` from every week but the latest, then predicts the latest week's
    closes with it (med30 × how[slot]) and with a flat med30.

    Args:
        hourly: (hour start ms, close copper), any history length.
        med30: The 30-day median in copper.
        now_ms: Now, UTC ms; the held-out week is the 7 days up to it.
        half_life_weeks: pattern_half_life_weeks.

    Returns:
        Strength 0-100, or None for insufficient evidence: fewer than 3 weeks of
        data, fewer than 24 held-out closes, no med30, or a flat error of 0.
    """
    past = [(t, c) for t, c in hourly if t <= now_ms]
    weeks = len({(now_ms - t) // WEEK_MS for t, _ in past})
    held_out = [(t, c) for t, c in past if t > now_ms - WEEK_MS]
    if weeks < MIN_WEEKS or len(held_out) < STRENGTH_MIN_HELD_OUT or not med30:
        return None
    how = price_profile([(t, c) for t, c in past if t <= now_ms - WEEK_MS], med30, now_ms - WEEK_MS,
                        half_life_weeks).values
    error_profile = sum(abs(c - med30 * how[_slot(t)]) for t, c in held_out) / len(held_out)
    error_flat = sum(abs(c - med30) for _, c in held_out) / len(held_out)
    return strength(error_profile, error_flat, weeks)


def _slot(t_ms: int) -> int:
    """The hour-of-week slot of a time.

    Args:
        t_ms: UTC ms.

    Returns:
        weekday × 24 + hour, 0 = Monday 00:00 UTC: prices.weekday_hour's rule (SPEC §3.3), from the hour number.
    """
    hour_number = t_ms // HOUR_MS
    return (hour_number // 24 + 3) % 7 * 24 + hour_number % 24


def pack(prof: Profile) -> tuple[str, str]:
    """A profile as the intelligence stores it: ratios in thousandths and weeks, comma-separated (SPEC §5.0, W13).

    Args:
        prof: The profile.

    Returns:
        (values text, weeks text); ("", "") when no slot has any data.
    """
    if not any(prof.weeks):
        return "", ""
    values = ",".join(str(math.floor(v * PACK_SCALE + 0.5)) for v in prof.values)
    return values, ",".join(str(w) for w in prof.weeks)


def unpack(values_text: str, weeks_text: str) -> Profile:
    """A packed profile back as a Profile (W13).

    Args:
        values_text: From pack: 168 integers in thousandths, or "".
        weeks_text: From pack: 168 week counts, or "".

    Returns:
        The profile with ratios n ÷ 1000; flat 1.0 with 0 weeks for "".

    Raises:
        ValueError: When the text is not 168 integers.
    """
    if not values_text:
        return Profile(tuple([1.0] * SLOTS), tuple([0] * SLOTS), tuple([False] * SLOTS))
    values = tuple(int(n) / PACK_SCALE for n in values_text.split(","))
    weeks = tuple(int(n) for n in weeks_text.split(","))
    if len(values) != SLOTS or len(weeks) != SLOTS:
        raise ValueError(f"a packed profile needs {SLOTS} slots, got {len(values)} and {len(weeks)}")
    return Profile(values, weeks, tuple(w >= MIN_WEEKS for w in weeks))
