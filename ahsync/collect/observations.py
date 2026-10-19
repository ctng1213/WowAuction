"""Normalized observations into the Historical Store: snapshots with auction ids (SPEC §5.0, §3.3.1, V36).

A snapshot is kept raw on disk (`collect/snapshots.py`) and, grouped into an order book, in the database, where it
prices items and feeds hourly sales and group diffs like any complete book. Its id diffs are the most trusted
daily volume (kind "ids"). Every step is idempotent, so a crash between the file and the database commit is
mended by delivering the same snapshot again (FM32).
"""
from __future__ import annotations

from ahsync.collect.local_scans import book_days, recompute_book_volume, recompute_sales_hours, store_scan_prices
from ahsync.collect.provider import SnapshotObservation, snapshot_problems
from ahsync.collect.snapshots import SnapshotStore
from ahsync.db.sqlite import BookScan, Database
from ahsync.engine.volume import (MAX_GAP_MS, VOLUME_METHOD_VERSION, IdBook, covered_seconds, estimate_volume_ids,
                                  group_book)

DAY_MS = 86_400_000


def ingest_snapshot(db: Database, store: SnapshotStore, obs: SnapshotObservation) -> set[int]:
    """Store a provider's snapshot and everything derived from it (V36).

    Args:
        db: Database.
        store: The raw snapshot store.
        obs: The snapshot.

    Returns:
        Item keys whose prices or volume changed.

    Raises:
        ValueError: The snapshot is malformed (provider.snapshot_problems); nothing is stored.
    """
    problems = snapshot_problems(obs)
    if problems:
        raise ValueError(f"{obs.src} snapshot at {obs.at_ms} refused: " + "; ".join(problems))
    store.put(obs)
    entries = [(e.key, e.price, e.stack, e.band, e.count) for e in group_book(IdBook(obs.at_ms, obs.auctions)).entries]
    scan_id = f"{obs.src}:{obs.market}:{obs.at_ms}"
    with db.transaction():
        db.store_book(BookScan(scan_id, obs.market, obs.src, None, obs.at_ms), entries)
        dirty = store_scan_prices(db, obs.market, scan_id, obs.at_ms, obs.src)
        recompute_sales_hours(db, obs.market, [obs.at_ms])
        for day in sorted(book_days(db, obs.market, [obs.at_ms])):
            dirty |= set(recompute_book_volume(db, obs.market, day))
        for day in sorted(id_days(store, obs.market, obs.src, obs.at_ms)):
            dirty |= recompute_id_volume(db, store, obs.market, obs.src, day)
    return dirty


def id_days(store: SnapshotStore, market: str, src: str, at_ms: int) -> set[int]:
    """The days whose id volume a new snapshot changes.

    Args:
        store: The snapshot store.
        market: Market id.
        src: Source name.
        at_ms: The new snapshot's time.

    Returns:
        Its day, and the day of the next snapshot after it (an older one delivered late pairs with it).
    """
    later = store.times(market, src, at_ms + 1, at_ms + MAX_GAP_MS)
    return {at_ms // DAY_MS} | ({later[0] // DAY_MS} if later else set())


def recompute_id_volume(db: Database, store: SnapshotStore, market: str, src: str, day: int) -> set[int]:
    """A day's id-diff volume of one source, from its consecutive snapshots at most 6 h apart (V33, V36).

    A pair counts for the day of its later snapshot; the day's cover records the hours its pairs observed.

    Args:
        db: Database, inside the caller's transaction.
        store: The snapshot store.
        market: Market id.
        src: Source name.
        day: Day number.

    Returns:
        The item keys written for that day (the day's rows are replaced whole).
    """
    times = store.times(market, src, day * DAY_MS - MAX_GAP_MS, (day + 1) * DAY_MS - 1)
    loaded: dict[int, IdBook] = {}
    totals: dict[int, list[float]] = {}
    pairs: list[tuple[int, int]] = []
    for prev_at, next_at in zip(times, times[1:]):
        if next_at // DAY_MS != day:
            continue
        prev = loaded.get(prev_at) or store.load(market, src, prev_at)
        loaded[next_at] = store.load(market, src, next_at)
        volume = estimate_volume_ids(prev, loaded[next_at])
        if volume is None:
            continue
        pairs.append((prev_at, next_at))
        for key, v in volume.items():
            row = totals.setdefault(key, [0.0, 0.0, 0.0])
            row[0] += v.sold
            row[1] += v.expired
            row[2] += v.listed
    rows = {key: (sold, expired, listed) for key, (sold, expired, listed) in sorted(totals.items())}
    db.replace_volume_day(market, day, src, rows, VOLUME_METHOD_VERSION,
                          ("ids", covered_seconds(pairs)) if pairs else None)
    return set(rows)
