"""Pricing primitives per item and market (SPEC §3.3): medians, percentiles, trend, post price.

Pure functions over closes the caller has already read; one close per (item, hour),
local preferred (SPEC §2.2), is the store's job. Numeric rules (SPEC §3.3) are exact:
even-count medians floor the mean of the middle two, percentiles are nearest rank,
and the hour of week uses the UTC date.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
MIN_HOURLY_CLOSES = 24      # fewer than this in a window → use daily closes (SPEC §3.3 med7/med30)
MIN_TREND_DAYS = 4          # trend7 needs at least this many daily medians (SPEC §3.3)
SUSPECT_LOW, SUSPECT_HIGH = 0.1, 10.0   # below 10% of p10_30 or above 10× p90_30 (SPEC §5.2, FM5)


def median(values: Sequence[int]) -> int | None:
    """Median by the SPEC §3.3 rule.

    Args:
        values: Integer prices in copper, any order.

    Returns:
        The middle value; for an even count, floor of the mean of the two middle
        values; None when values is empty.
    """
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def percentile(values: Sequence[int], p: float) -> int | None:
    """Nearest-rank percentile (SPEC §3.3).

    Args:
        values: Integer prices in copper, any order.
        p: The percentile as a fraction in (0, 1], e.g. 0.1.

    Returns:
        The value at 1-based rank ceil(p × n) of the sorted values; None when empty.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p * len(ordered)))
    return ordered[rank - 1]


def weekday_hour(t_ms: int) -> tuple[int, int]:
    """Hour-of-week slot of a time, on the UTC date (SPEC §3.3).

    Args:
        t_ms: UTC Unix time in ms.

    Returns:
        (weekday with 0 = Monday, hour 0-23). 1970-01-01 was a Thursday.
    """
    return (t_ms // DAY_MS + 3) % 7, (t_ms // HOUR_MS) % 24


def _in_window(closes: Sequence[tuple[int, int]], now_ms: int, days: int) -> list[int]:
    """Close prices whose time lies in the last `days` days up to now.

    Args:
        closes: (time in ms, close in copper) pairs.
        now_ms: Window end, UTC ms (inclusive).
        days: Window length in days.

    Returns:
        The close prices with now − days < t ≤ now.
    """
    start = now_ms - days * DAY_MS
    return [close for t, close in closes if start < t <= now_ms]


def window_median(hourly: Sequence[tuple[int, int]], daily: Sequence[tuple[int, int]],
                  now_ms: int, days: int) -> int | None:
    """med7 / med30: median of hourly closes in the window, falling back to daily closes (SPEC §3.3).

    Args:
        hourly: (hour start ms, close copper), one per item and hour.
        daily: (day start ms, close copper), one per item and day.
        now_ms: Window end, UTC ms.
        days: 7 for med7, 30 for med30.

    Returns:
        The median of the hourly closes, or of the daily closes when there are fewer
        than 24 hourly ones; None when there are neither.
    """
    hours = _in_window(hourly, now_ms, days)
    if len(hours) >= MIN_HOURLY_CLOSES:
        return median(hours)
    day_closes = _in_window(daily, now_ms, days)
    # SPEC GAP: fewer than 24 hourly closes and no daily ones → the hourly median, not None.
    return median(day_closes) if day_closes else median(hours)


def trend7(hourly: Sequence[tuple[int, int]], now_ms: int, med7: int | None) -> float:
    """Price trend in % per day: least-squares slope of daily medians ÷ med7 × 100 (SPEC §3.3).

    Args:
        hourly: (hour start ms, close copper) pairs.
        now_ms: Window end, UTC ms; the last 7 days are used.
        med7: The 7-day median in copper.

    Returns:
        The slope as a percentage of med7 per day; 0.0 with fewer than 4 days of
        data or no med7.
    """
    by_day: dict[int, list[int]] = {}
    for t, close in hourly:
        if now_ms - 7 * DAY_MS < t <= now_ms:
            by_day.setdefault(t // DAY_MS, []).append(close)
    if len(by_day) < MIN_TREND_DAYS or not med7:
        return 0.0
    days = sorted(by_day)
    xs = [float(d - days[0]) for d in days]
    ys = [float(median(by_day[d])) for d in days]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = (sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
             / sum((x - mean_x) ** 2 for x in xs))
    return slope / med7 * 100


def cur_others(book: Sequence[tuple[int, int]], own: Sequence[tuple[int, int]], cur: int | None) -> int | None:
    """Lowest buyout that is not one of your own auctions (SPEC §3.3 curOthers).

    Args:
        book: (unit price copper, auctions) from the newest local book; may be empty.
        own: (unit price copper, auctions) of your own live auctions of the item.
        cur: Newest lowest buyout in copper, or None when nothing is listed.

    Returns:
        The lowest price with auctions left after removing yours; cur when you don't
        hold it; None when cur is None, or you hold cur and there is no book to look past it.
    """
    if cur is None:
        return None
    own_count: dict[int, int] = {}
    for price, count in own:
        own_count[price] = own_count.get(price, 0) + count
    if cur not in own_count:
        return cur
    book_count: dict[int, int] = {}
    for price, count in book:
        book_count[price] = book_count.get(price, 0) + count
    others = [price for price, count in book_count.items() if count > own_count.get(price, 0)]
    return min(others) if others else None


def suspect_price(cur: int | None, p10: int | None, p90: int | None, recent: Sequence[int]) -> bool:
    """Whether the lowest buyout looks like a mistake rather than the market (SPEC §5.2, FM5).

    Args:
        cur: The lowest buyout now, copper; None when there is none.
        p10: The 30-day 10th percentile of hourly closes, copper.
        p90: The 30-day 90th percentile, copper.
        recent: Hourly closes of the last 24 h, copper (one per hour).

    Returns:
        True when cur is below 10% of p10 or above 10× p90 and fewer than two of the recent hours
        closed out of that range too (a second observation confirms it); False without the numbers.
    """
    if cur is None or p10 is None or p90 is None:
        return False

    def outside(price: int) -> bool:
        """Whether a price is out of the item's usual range.

        Args:
            price: Copper.

        Returns:
            True below 10% of p10 or above 10× p90.
        """
        return price < SUSPECT_LOW * p10 or price > SUSPECT_HIGH * p90
    return outside(cur) and sum(1 for c in recent if outside(c)) < 2


def drop_suspect(closes: Sequence[tuple[int, int]], p10: int | None, p90: int | None,
                 confirm_ms: int = DAY_MS) -> list[tuple[int, int]]:
    """Hourly closes without the unconfirmed suspect ones, for the statistics (SPEC §5.2, FM31).

    Args:
        closes: (hour start ms, close copper), any order.
        p10: The 30-day 10th percentile of all closes.
        p90: The 30-day 90th percentile.
        confirm_ms: A second out-of-range close this close in time confirms one.

    Returns:
        The closes in their order, less each one below 10% of p10 or above 10× p90 that no other out-of-range
        close within confirm_ms confirms; all of them without both percentiles.
    """
    if p10 is None or p90 is None:
        return list(closes)
    out_times = sorted(t for t, c in closes if c < SUSPECT_LOW * p10 or c > SUSPECT_HIGH * p90)
    lone = {t for i, t in enumerate(out_times)
            if not ((i > 0 and t - out_times[i - 1] <= confirm_ms) or
                    (i + 1 < len(out_times) and out_times[i + 1] - t <= confirm_ms))}
    return [(t, c) for t, c in closes if t not in lone]


def post_price(cur_others_copper: int | None, med7: int | None) -> int | None:
    """Post price p = min(curOthers − 1, med7) (SPEC §3.4).

    Args:
        cur_others_copper: Lowest competing buyout in copper, or None.
        med7: 7-day median in copper, or None.

    Returns:
        The unit price in copper; the available one when the other is None; None
        when both are None (the item is flagged "no price").
    """
    undercut = None if cur_others_copper is None else cur_others_copper - 1
    candidates = [v for v in (undercut, med7) if v is not None]
    return min(candidates) if candidates else None
