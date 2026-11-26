"""Hour-of-week profiles: `how`, `sales_how`, `supply_how`, and seasonality strength (SPEC §3.3, §3.3.4, §3.3.6).

`how` and `supply_how` take each slot's recency-weighted median over the weeks of history: a slot with data from
at least 3 different weeks is used as is (confidence high); with fewer, `how` reads 1.0 (P29) and `supply_how` its
weekday's value. `sales_how` is a recency-weighted rate shrunk toward its hour of day and the item's average (P30).
Values are then smoothed as the mean of the slot and the 2 slots on each side.
`how`'s observations are each hourly close against the item's own centred week (W17), so a price that moved and
stayed is not read as a pattern.
"""
from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass

from ahsync.engine.patterns import SLOTS, recency_weight, weighted_median

HOUR_MS = 3_600_000
WEEK_MS = 7 * 86_400_000
MIN_WEEKS = 3          # weeks of data a slot needs to stand on its own (SPEC §3.3)
SMOOTH_RADIUS = 2      # slots on each side in the smoothing mean
SALES_PRIOR_HOURS = 30.0   # pseudo-hours pulling a sales rate toward the level above it (P30; measured, plan §1d 40)
PACK_SCALE = 1000                # packed profiles keep ratios in thousandths (SPEC §5.0 contents)
STRENGTH_MIN_HELD_OUT = 24      # held-out closes seasonality strength needs (SPEC §3.3.6)
STRENGTH_FULL_WEEKS = 6         # weeks of data at which strength is no longer scaled down
CENTRED_BEFORE, CENTRED_AFTER = 84, 83   # hours before and after a close in the week it is set against (W17)
CENTRED_MIN_CLOSES = 84                  # closes that week needs (half of it)


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
        hour_number = t // HOUR_MS                # _slot(t), inline: this loop runs for every hour of every item
        slot = (hour_number // 24 + 3) % 7 * 24 + hour_number % 24
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


def centred_ratios(hourly: Sequence[tuple[int, int]], now_ms: int) -> list[tuple[int, float]]:
    """Each hourly close against the median of the item's closes in the week centred on it (SPEC §3.3 `how`, W17).

    The week is the 168 hours from 84 before the close's hour to 83 after it. An hour gives an observation only
    when that whole week lies inside the item's history (from its first close to the last hour that has ended by
    now) and holds at least
    CENTRED_MIN_CLOSES closes, so a price that moved and stayed is no pattern and the newest 84 hours give none.

    Args:
        hourly: (hour start ms, close copper), at most one per hour, any order.
        now_ms: Now, UTC ms; closes after it are ignored.

    Returns:
        (hour start ms, close ÷ the week's median by SPEC §3.3's rule), ascending; an hour whose median is 0 is left
        out.
    """
    points = sorted((t, c) for t, c in hourly if t <= now_ms)
    if not points:
        return []
    first, window, out = points[0][0], [], []
    right = left = 0
    for t, close in points:
        start, end = t - CENTRED_BEFORE * HOUR_MS, t + CENTRED_AFTER * HOUR_MS
        while right < len(points) and points[right][0] <= end:
            bisect.insort(window, points[right][1])
            right += 1
        while points[left][0] < start:
            del window[bisect.bisect_left(window, points[left][1])]
            left += 1
        if start < first or end + HOUR_MS > now_ms or len(window) < CENTRED_MIN_CLOSES:   # its last hour ended
            continue
        mid = len(window) // 2
        middle = window[mid] if len(window) % 2 else (window[mid - 1] + window[mid]) // 2
        if middle > 0:
            out.append((t, close / middle))
    return out


def price_profile(hourly: Sequence[tuple[int, int]], med30: int | None, now_ms: int,
                  half_life_weeks: float = 4) -> Profile:
    """`how`: weighted median per slot of each close against its centred week, 1.0 for a slot with under 3 weeks,
    then smoothing (SPEC §3.3, W17, P29; until SP2b each slot's close ÷ med30; until P29 a thin slot took its
    weekday's median).

    Args:
        hourly: (hour start ms, close copper), one per item and hour, any history length.
        med30: The 30-day median in copper; only its absence matters (no recent close: flat).
        now_ms: Now, UTC ms.
        half_life_weeks: pattern_half_life_weeks.

    Returns:
        The profile, not normalised; flat 1.0 everywhere when there is no observation or no med30. A slot's weeks
        count the weeks it has an observation in.
    """
    return price_profile_from(centred_ratios(hourly, now_ms), med30, now_ms, half_life_weeks)


def price_profile_from(ratios: Sequence[tuple[int, float]], med30: int | None, now_ms: int,
                       half_life_weeks: float = 4) -> Profile:
    """`how` from centred ratios already computed (price_profile's second half; SPEC §3.3, W17, P29).

    Args:
        ratios: centred_ratios(hourly, now_ms).
        med30: The 30-day median in copper; only its absence matters (no recent close: flat).
        now_ms: Now, UTC ms.
        half_life_weeks: pattern_half_life_weeks.

    Returns:
        The profile, as price_profile gives it.
    """
    by_slot, week_sets = _observations(ratios, now_ms, half_life_weeks)
    weeks = tuple(len(w) for w in week_sets)
    confident = tuple(w >= MIN_WEEKS for w in weeks)
    if not med30:
        return Profile(tuple([1.0] * SLOTS), weeks, confident)
    # No weekday fallback for prices (P29): with under 3 weeks a weekday's median is one crash week, and that
    # fallback priced worse than a flat pattern on the real market (plan sold-price.md §1d item 39).
    ratios = [weighted_median(by_slot[s]) if confident[s] else 1.0 for s in range(SLOTS)]
    return Profile(_smooth(ratios), weeks, confident)


def sales_profile(sold_per_hour: Sequence[tuple[int, float]], now_ms: int, half_life_weeks: float = 4) -> Profile:
    """`sales_how`: each slot's sales rate ÷ the average hour, mean 1.0 (SPEC §3.3, P30).

    A slot's rate is its recency-weighted units ÷ its weighted hours observed, pulled toward its hour of day by
    SALES_PRIOR_HOURS pseudo-hours; the hour of day pools its 7 weekdays and is pulled toward the item's average
    hour the same way. Then smoothed as the other profiles.

    Args:
        sold_per_hour: (hour start ms, units sold in that hour) from consecutive hourly books.
        now_ms: Now, UTC ms.
        half_life_weeks: pattern_half_life_weeks.

    Returns:
        The profile; a slot never observed takes its hour of day's rate, an hour of day never observed the
        average; flat 1.0 everywhere with no sales at all. Weeks and confidence count the slot's weeks as before.
    """
    by_slot, week_sets = _observations(sold_per_hour, now_ms, half_life_weeks)
    weeks = tuple(len(w) for w in week_sets)
    confident = tuple(w >= MIN_WEEKS for w in weeks)
    units = [sum(value * weight for value, weight in obs) for obs in by_slot]
    hours = [sum(weight for _, weight in obs) for obs in by_slot]
    if sum(units) <= 0:
        return Profile(tuple([1.0] * SLOTS), weeks, confident)
    average = sum(units) / sum(hours)
    # A median of mostly-zero hours made one lucky hour a thin item's whole week (P30); a rate pooled over the
    # weekdays and pulled toward the average needs evidence before an hour stands out.
    by_hour = [(sum(units[d * 24 + h] for d in range(7)) + SALES_PRIOR_HOURS * average)
               / (sum(hours[d * 24 + h] for d in range(7)) + SALES_PRIOR_HOURS) for h in range(24)]
    rates = [(units[s] + SALES_PRIOR_HOURS * by_hour[s % 24]) / (hours[s] + SALES_PRIOR_HOURS) for s in range(SLOTS)]
    smoothed = _smooth(rates)
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
                         half_life_weeks: float = 4, centred: Sequence[tuple[int, float]] | None = None) -> int | None:
    """How predictable the weekly price pattern is, tested out of sample (SPEC §3.3.6).

    Builds `how` from every week but the latest, then predicts the latest week's
    closes with it (med30 × how[slot]) and with a flat med30.

    Args:
        hourly: (hour start ms, close copper), any history length.
        med30: The 30-day median in copper.
        now_ms: Now, UTC ms; the held-out week is the 7 days up to it.
        half_life_weeks: pattern_half_life_weeks.
        centred: centred_ratios(hourly, now_ms) when the caller has them. The held-out pattern's ratios are then
            those whose centred week ends by the held-out week's start: their windows hold the same closes with or
            without the latest week, so the result is the one built from scratch, bit for bit.

    Returns:
        Strength 0-100, or None for insufficient evidence: fewer than 3 weeks of
        data, fewer than 24 held-out closes, no med30, or a flat error of 0.
    """
    past = [(t, c) for t, c in hourly if t <= now_ms]
    weeks = len({(now_ms - t) // WEEK_MS for t, _ in past})
    held_out = [(t, c) for t, c in past if t > now_ms - WEEK_MS]
    if weeks < MIN_WEEKS or len(held_out) < STRENGTH_MIN_HELD_OUT or not med30:
        return None
    cut = now_ms - WEEK_MS
    if centred is None:
        how = price_profile([(t, c) for t, c in past if t <= cut], med30, cut, half_life_weeks).values
    else:
        last = cut - (CENTRED_AFTER + 1) * HOUR_MS          # a centred week must have ended by the cut
        how = price_profile_from([r for r in centred if r[0] <= last], med30, cut, half_life_weeks).values
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
    """A profile as the intelligence stores it: ratios in thousandths and weeks, run-length coded (SPEC §5.0, W13, BZ47).

    Args:
        prof: The profile.

    Returns:
        (values text, weeks text), each 168 comma-separated integers with a run of n equal ones written `v*n`
        (`1000*168` for a flat profile); ("", "") when no slot has any data.
    """
    if not any(prof.weeks):
        return "", ""
    return (_runs([str(math.floor(v * PACK_SCALE + 0.5)) for v in prof.values]),
            _runs([str(w) for w in prof.weeks]))


def _runs(slots: Sequence[str]) -> str:
    """Slot texts run-length coded (BZ47).

    Args:
        slots: One text per slot.

    Returns:
        Comma-separated, a run of n > 1 equal texts written `v*n`.
    """
    out = []
    run, count = slots[0], 0
    for text in slots:
        if text == run:
            count += 1
            continue
        out.append(run if count == 1 else f"{run}*{count}")
        run, count = text, 1
    out.append(run if count == 1 else f"{run}*{count}")
    return ",".join(out)


def expand(text: str) -> list[str]:
    """A packed profile string's 168 slot texts, from either form: plain (schema 13 and older) or run-length (BZ47).

    Args:
        text: From pack, or a plain list of 168 comma-separated integers; not "".

    Returns:
        168 texts, one per slot.

    Raises:
        ValueError: When the text is not 168 slots, or a run's count is not a positive integer. At most 169
            tokens are split off and a run is never expanded past 168, so a hostile string costs nothing (SEC16).
    """
    if "*" not in text:                        # the plain form: a split is enough
        slots = text.split(",", SLOTS)
        if len(slots) != SLOTS:
            raise ValueError(f"a packed profile needs {SLOTS} slots, got {len(slots)}")
        return slots
    slots = []
    for token in text.split(",", SLOTS):
        value, star, count = token.partition("*")
        n = int(count) if star else 1
        if n < 1 or len(slots) + n > SLOTS:
            raise ValueError(f"a packed profile needs {SLOTS} slots, got a run of {n} after {len(slots)}")
        slots.extend([value] * n)
    if len(slots) != SLOTS:
        raise ValueError(f"a packed profile needs {SLOTS} slots, got {len(slots)}")
    return slots


def plain(text: str) -> str:
    """A packed profile string in the plain form schema 13 and older readers expect (BZ47).

    Args:
        text: Plain or run-length coded; "" for no profile.

    Returns:
        168 comma-separated integers; "" for "".

    Raises:
        ValueError: As expand.
    """
    return ",".join(expand(text)) if text else text


def unpack(values_text: str, weeks_text: str) -> Profile:
    """A packed profile back as a Profile, from either form (W13, BZ47).

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
    values = tuple(int(n) / PACK_SCALE for n in expand(values_text))
    weeks = tuple(int(n) for n in expand(weeks_text))
    return Profile(values, weeks, tuple(w >= MIN_WEEKS for w in weeks))


def unpack_values(values_text: str) -> tuple[float, ...] | None:
    """Only the ratios of a packed profile, without its weeks: what the plan's facts read (W13, SPEC §3.1.2 re-plan).

    Args:
        values_text: From pack: 168 integers in thousandths, or "".

    Returns:
        The ratios n ÷ 1000, the same floats as unpack(...).values; None for "".

    Raises:
        ValueError: When the text is not 168 integers.
    """
    if not values_text:
        return None
    return tuple([n / PACK_SCALE for n in map(int, expand(values_text))])
