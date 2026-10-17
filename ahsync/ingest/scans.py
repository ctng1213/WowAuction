"""Own full scans as local data: prices, hourly closes and book volume (SPEC §4.2, §2.2, §3.3.1).

Called inside the SavedVariables ingest transaction for each newly stored local scan.
Every write is idempotent: `current` only moves forward in time, candles are upserts,
and a day's `books` volume is recomputed whole from that day's scan pairs.
"""
from __future__ import annotations

from ahsync.engine.volume import MAX_GAP_MS, Book, BookEntry, estimate_volume
from ahsync.store.db import Database

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
    db.replace_volume_day(market, day, "books", rows)
    return rows


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
    for market, scan_id, at_ms, complete in new_scans:
        dirty |= store_scan_prices(db, market, scan_id, at_ms)
        if complete:
            days.add((market, at_ms // DAY_MS))
            later = db.local_full_scans(market, at_ms + 1, at_ms + MAX_GAP_MS)
            if later:
                days.add((market, later[0][1] // DAY_MS))     # an older file read late pairs with the next scan
    for market, day in sorted(days):
        dirty |= set(recompute_book_volume(db, market, day))
    return dirty
