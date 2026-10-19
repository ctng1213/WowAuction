"""Own full scans as local data: prices, hourly closes, book volume and hourly sales (SPEC §4.2, §2.2, §3.3.1).

Called inside the SavedVariables ingest transaction for each newly stored local scan
(and, for hourly sales, after AHF books are stored). Every write is idempotent:
`current` only moves forward in time, candles are upserts, and a day's `books` volume
and an hour's sales rate are recomputed whole from the stored books.
"""
from __future__ import annotations

from ahsync.engine.volume import Book, BookEntry, MAX_GAP_MS, SALES_PAIR_MAX_GAP_MS, VOLUME_METHOD_VERSION, estimate_volume, hourly_sales
from ahsync.db.sqlite import Database

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def prices_from_book(entries: list[tuple[int, int, int, int, int]]) -> dict[int, tuple[int, int, int]]:
    """Each item's lowest unit price, the units at it, and all units listed (SPEC §4.2).

    Args:
        entries: (key, unit price, stack, band, count) rows of one book.

    Returns:
        key -> (lowest price copper, units at that price, units listed in total).
    """
    out: dict[int, list[int]] = {}
    for key, price, stack, _, count in entries:
        units = stack * count
        row = out.setdefault(key, [price, 0, 0])
        if price < row[0]:
            row[0], row[1] = price, 0
        if price == row[0]:
            row[1] += units
        row[2] += units
    return {key: (low, at_low, total) for key, (low, at_low, total) in sorted(out.items())}


def store_scan_prices(db: Database, market: str, scan_id: str, at_ms: int) -> set[int]:
    """Write a scan as local `current` rows and local hourly closes (V21).

    Args:
        db: Database, inside the caller's transaction.
        market: Market id.
        scan_id: The stored scan.
        at_ms: Its time.

    Returns:
        The item keys it priced.
    """
    prices = prices_from_book(db.scan_entries(scan_id))
    db.upsert_local_current(market, [(key, low, at_low, at_ms) for key, (low, at_low, _) in prices.items()])
    hour = at_ms // HOUR_MS * HOUR_MS
    db.upsert_candles("candles_h1", market, [(key, hour, low, low, low, low, total)
                                             for key, (low, _, total) in prices.items()], "local")
    return set(prices)


def _book(db: Database, scan_id: str, at_ms: int) -> Book:
    """A stored scan as an engine Book.

    Args:
        db: Database.
        scan_id: The scan.
        at_ms: Its time.

    Returns:
        The Book.
    """
    return Book(at_ms, tuple(BookEntry(*row) for row in db.scan_entries(scan_id)))


def recompute_book_volume(db: Database, market: str, day: int) -> dict[int, tuple[float, float, float]]:
    """A day's `books` volume from consecutive complete local scans at most 6 h apart (V19, V20).

    A pair counts for the day of its later scan. Partial scans are never part of a pair.

    Args:
        db: Database, inside the caller's transaction.
        market: Market id.
        day: Day number.

    Returns:
        key -> (sold, expired, listed) written for that day (the day's rows are replaced whole).
    """
    scans = db.local_full_scans(market, day * DAY_MS - MAX_GAP_MS, (day + 1) * DAY_MS - 1)
    totals: dict[int, list[float]] = {}
    for (prev_id, prev_at), (next_id, next_at) in zip(scans, scans[1:]):
        if next_at // DAY_MS != day:
            continue
        volume = estimate_volume(_book(db, prev_id, prev_at), _book(db, next_id, next_at))
        for key, v in (volume or {}).items():
            row = totals.setdefault(key, [0.0, 0.0, 0.0])
            row[0] += v.sold
            row[1] += v.expired
            row[2] += v.listed
    rows = {key: (sold, expired, listed) for key, (sold, expired, listed) in sorted(totals.items())}
    db.replace_volume_day(market, day, "books", rows, VOLUME_METHOD_VERSION)
    return rows


def recompute_stale_volume(db: Database, market: str) -> int:
    """Recompute `books` volume from an older method where its order books are still stored (SPEC §3.3.6, V24).

    Args:
        db: Database, inside the caller's transaction.
        market: Market id.

    Returns:
        Days left on an older method, because their books were pruned; Status reports them.
    """
    left = 0
    for day in db.stale_volume_days(market, "books", VOLUME_METHOD_VERSION):
        if db.local_full_scans(market, day * DAY_MS - MAX_GAP_MS, (day + 1) * DAY_MS - 1):
            recompute_book_volume(db, market, day)
        else:
            left += 1
    return left


def _hour_ranges(times_ms: list[int]) -> list[tuple[int, int]]:
    """The hours whose sales rate a new book can change, merged into ranges.

    A book at T is the later book of pairs starting up to 2 h before it, and the earlier
    book of the pair starting in its own hour.

    Args:
        times_ms: Times of newly stored complete books.

    Returns:
        (first hour start, last hour start) ranges in ms, ascending, not overlapping.
    """
    ranges: list[list[int]] = []
    for at_ms in sorted(times_ms):
        first = (at_ms - SALES_PAIR_MAX_GAP_MS) // HOUR_MS * HOUR_MS
        last = at_ms // HOUR_MS * HOUR_MS
        if ranges and first <= ranges[-1][1] + HOUR_MS:
            ranges[-1][1] = max(ranges[-1][1], last)
        else:
            ranges.append([first, last])
    return [(first, last) for first, last in ranges]


def recompute_sales_hours(db: Database, market: str, times_ms: list[int]) -> None:
    """Recompute `sales_h1` for the hours newly stored complete books touch (V25-V27).

    Books of either source pair, partial books never do. Each affected hour is rewritten whole.

    Args:
        db: Database, inside the caller's transaction.
        market: Market id.
        times_ms: Times of the newly stored complete books.
    """
    for first, last in _hour_ranges(times_ms):
        scans = db.complete_scans(market, first, last + HOUR_MS - 1 + SALES_PAIR_MAX_GAP_MS)
        rates = hourly_sales([_book(db, scan_id, at) for scan_id, at in scans])
        db.replace_sales_hours(market, first, last, rates)


def derive_from_scans(db: Database, new_scans: list[tuple[str, str, int, bool]]) -> set[int]:
    """Prices and volume for newly stored local scans (SPEC §4.2).

    Args:
        db: Database, inside the caller's transaction.
        new_scans: (market, scan id, time ms, complete) for each scan stored by this file.

    Returns:
        The item keys whose prices or volume changed.
    """
    dirty: set[int] = set()
    days: set[tuple[str, int]] = set()
    complete_times: dict[str, list[int]] = {}
    for market, scan_id, at_ms, complete in new_scans:
        dirty |= store_scan_prices(db, market, scan_id, at_ms)
        if complete:
            complete_times.setdefault(market, []).append(at_ms)
            days.add((market, at_ms // DAY_MS))
            later = db.local_full_scans(market, at_ms + 1, at_ms + MAX_GAP_MS)
            if later:
                days.add((market, later[0][1] // DAY_MS))     # an older file read late pairs with the next scan
    for market, day in sorted(days):
        dirty |= set(recompute_book_volume(db, market, day))
    for market, times in sorted(complete_times.items()):
        recompute_sales_hours(db, market, times)
    return dirty
