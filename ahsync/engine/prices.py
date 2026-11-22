"""Pricing primitives per item and market (SPEC §3.3, §3.4): medians, percentiles, trend, the post price and
the forecast of an hour from the price level.

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


def clamp_bounds(price: int, low: int | None, high: int | None) -> int:
    """A forecast kept within the level's bounds (SPEC §3.3 Bounds, §3.3.4, TT51).

    Args:
        price: Copper.
        low: The low bound, copper; None for none.
        high: The high bound, copper; None for none. Never below low when both are known.

    Returns:
        The price raised to low, then lowered to high, as far as they are known.
    """
    if low is not None:
        price = max(price, low)
    if high is not None:
        price = min(price, high)
    return price


def forecast(level: int, how: float, factor: float, low: int | None, high: int | None) -> int:
    """The expected price of an hour: level × its pattern × a trend factor, within the bounds (SPEC §3.3.4, TT51, PP5).

    Args:
        level: The price level (SPEC §3.3), copper.
        how: The hour's price ratio.
        factor: The trend factor (1 for the current hour).
        low: The level's low bound, copper; None for none.
        high: The level's high bound, copper; None for none.

    Returns:
        floor(level × how × factor + 0.5), then clamp_bounds.
    """
    return clamp_bounds(math.floor(level * how * factor + 0.5), low, high)


def post_price(cur_others_copper: int | None, level: int | None, how_now: float, low: int | None,
               high: int | None) -> int | None:
    """Post price p = min(curOthers − 1, ceiling), the ceiling being this hour's forecast (SPEC §3.4, PP1, PP5).

    You undercut the cheapest competitor, up to what the item sells for at this hour: the level × the hour's
    pattern, within the level's bounds. An ask level's high bound is itself, so asks never earn a premium (§3.3).

    Args:
        cur_others_copper: Lowest competing buyout in copper, or None.
        level: The price level (SPEC §3.3), copper, or None.
        how_now: The current hour's price ratio (1.0 without a pattern).
        low: The level's low bound, copper; None without a level.
        high: The level's high bound, copper; None without a level.

    Returns:
        The unit price in copper: with a competitor, the undercut capped at the ceiling (uncapped without a level);
        without one, the ceiling; None when there is neither (the item is flagged "no price").
    """
    ceiling = None if level is None else forecast(level, how_now, 1.0, low, high)
    if cur_others_copper is None:
        return ceiling
    undercut = cur_others_copper - 1
    return undercut if ceiling is None else min(undercut, ceiling)


def relist_price(cur_others_copper: int | None, level: int | None, how_now: float, low: int | None,
                 high: int | None, closes: int | None = None) -> int | None:
    """The price to relist bought units now: the post price, only when it is grounded (SPEC §3.4C.2, R28).

    With another listing left, the undercut grounds it. With none, the price is the ceiling alone, which is
    grounded only by MIN_HOURLY_CLOSES hourly closes in 30 days: a level of four closes is not a price you can
    sell at. Stock you already own may still post at post_price.

    Args:
        cur_others_copper: Lowest competing buyout left after the buy, copper, or None.
        level: The price level, copper, or None.
        how_now: The current hour's price ratio.
        low: The level's low bound, copper, or None.
        high: The level's high bound, copper, or None.
        closes: Hourly closes in the last 30 days; None when unknown (treated as enough).

    Returns:
        post_price(...), or None when nothing is listed and closes < MIN_HOURLY_CLOSES.
    """
    if cur_others_copper is None and closes is not None and closes < MIN_HOURLY_CLOSES:
        return None
    return post_price(cur_others_copper, level, how_now, low, high)
