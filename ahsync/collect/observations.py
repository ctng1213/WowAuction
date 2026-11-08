"""Normalized observations into the Historical Store: snapshots with auction ids (SPEC §5.0, §3.3.1, V36).

A snapshot is kept raw on disk (`collect/snapshots.py`) and, grouped into an order book, in the database, where it
prices items and feeds hourly sales and group diffs like any complete book. Its id diffs are the most trusted
daily volume (kind "ids"). Every step is idempotent, so a crash between the file and the database commit is
mended by delivering the same snapshot again (FM32).
"""
from __future__ import annotations

from collections.abc import Iterable

from ahsync.collect.local_scans import (HOUR_MS, book_days, recompute_book_volume, recompute_sales_hours,
                                       store_scan_prices)
from ahsync.collect.provider import SnapshotObservation, snapshot_problems
from ahsync.collect.snapshots import SnapshotStore
from ahsync.db.sqlite import BookScan, Database
from ahsync.engine.volume import (ID_METHOD_VERSION, LIFETIME_LOOKBACK_MS, MAX_GAP_MS, SALES_PAIR_MAX_GAP_MS, IdBook, covered_seconds,
                                  gap_hours, group_book, life_flows, lifetimes, new_listing_positions,
                                  sold_by_hour)

DAY_MS = 86_400_000


def ingest_snapshot(db: Database, store: SnapshotStore, obs: SnapshotObservation, id_volume: bool = True) -> set[int]:
    """Store a provider's snapshot and everything derived from it (V36).

    Args:
        db: Database.
        store: The raw snapshot store.
        obs: The snapshot.
        id_volume: Recompute the id volume of the days it touches now; False when the caller ingests a batch and
            calls `recompute_id_days` once for all of them (the cloud's daily run: each day's lifetimes read 36 h before it).

    Returns:
        Item keys whose prices or volume changed (without the id volume when id_volume is False).

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
        db.add_observed_hours(obs.market, obs.src, [obs.at_ms // HOUR_MS])   # a snapshot is always complete (SRC16)
        recompute_sales_hours(db, obs.market, [obs.at_ms])
        for day in sorted(book_days(db, obs.market, [obs.at_ms])):
            dirty |= set(recompute_book_volume(db, obs.market, day))
        if id_volume:
            dirty |= recompute_id_days(db, store, obs.market, obs.src, id_days(store, obs.market, obs.src, obs.at_ms))
    return dirty


def id_days(store: SnapshotStore, market: str, src: str, at_ms: int) -> set[int]:
    """The days whose id volume a new snapshot changes.

    Args:
        store: The snapshot store.
        market: Market id.
        src: Source name.
        at_ms: The new snapshot's time.

    Returns:
        From the day 2 h before it (its book pairs rewrite those hours of `sales_h1`, which the ids own) to the day of
        the last snapshot up to LIFETIME_LOOKBACK_MS after it (its sightings shape the windows of auctions ending
        that late, M33a review).
    """
    later = store.times(market, src, at_ms + 1, at_ms + LIFETIME_LOOKBACK_MS)
    last = later[-1] if later else at_ms
    return set(range((at_ms - SALES_PAIR_MAX_GAP_MS) // DAY_MS, last // DAY_MS + 1))


def recompute_id_days(db: Database, store: SnapshotStore, market: str, src: str, days: set[int]) -> set[int]:
    """Some days' id volume, ended listings and hourly sales of one source, from auction lifetimes (SPEC §2.4).

    One `lifetimes` pass per day, over the snapshots from LIFETIME_LOOKBACK_MS (66 h: an ending's life and posting gaps,
    plus the day of gaps its early-leave rate reads, BZ44) before it to MAX_GAP_MS
    after it, keeping only the records of gaps that end from MAX_GAP_MS before the day on, so memory is one day's.
    A gap counts for the day of its later snapshot (its cover too); its sold units are credited to its clock hours,
    which replace those hours' book-pair rates in `sales_h1` (ids rank above groups, §3.3.1); every gap crediting
    a written hour is in the pass, so an hour pooled across midnight comes out the same in any order. A day's
    result does not depend on which other days are recomputed (an ending in it needs at most 36 h before it).

    Args:
        db: Database, inside the caller's transaction.
        store: The snapshot store.
        market: Market id.
        src: Source name.
        days: Day numbers.

    Returns:
        The item keys written (each day's rows are replaced whole).
    """
    written: set[int] = set()
    for day in sorted(days):
        day_ms = day * DAY_MS
        times = store.times(market, src, day_ms - LIFETIME_LOOKBACK_MS, day_ms + DAY_MS - 1 + MAX_GAP_MS)
        lt = lifetimes((store.load(market, src, at) for at in times), keep_from_ms=day_ms - MAX_GAP_MS)
        flows, positions = life_flows(lt), new_listing_positions(lt)
        gaps = [g for g in sorted(flows) if g[1] // DAY_MS == day]
        volume: dict[int, list[float]] = {}
        ended: dict[int, list[float]] = {}
        for gap in gaps:
            for key, f in flows[gap].items():
                row = volume.setdefault(key, [0.0, 0.0, 0.0])
                row[0] += f.sold
                row[1] += f.expired
                row[2] += f.listed
                if f.ended:
                    tally = ended.setdefault(key, [0.0] * 8)
                    for i, value in enumerate((f.ended, f.expired_listings, f.matched, f.cancels, f.cancelled,
                                               f.t1_ended, f.t1_left)):
                        tally[i] += value
                    tally[7] = max(tally[7], float(f.commodity))
        db.replace_volume_day(market, day, src, {k: tuple(v) for k, v in sorted(volume.items())}, ID_METHOD_VERSION,
                              ("ids", covered_seconds(gaps)) if gaps else None)
        db.replace_lifetimes_day(market, day, src, {k: tuple(v) for k, v in sorted(ended.items())})
        db.replace_new_listings_day(market, day, src, merge_positions(positions.get(g, {}) for g in gaps))
        hours = {h for gap in gaps for h in gap_hours(gap)}
        rates = sold_by_hour(flows, hours)
        for first, last in _runs(sorted(hours)):
            db.replace_sales_hours(market, first, last, rates)
        written |= set(volume)
    return written


def merge_positions(per_gap: Iterable[dict[int, dict[int, float]]]) -> dict[int, dict[int, float]]:
    """Several gaps' new-listing positions summed per item and position.

    Args:
        per_gap: key -> position -> units, one mapping per gap.

    Returns:
        key -> position -> units.
    """
    out: dict[int, dict[int, float]] = {}
    for by_key in per_gap:
        for key, by_position in by_key.items():
            mine = out.setdefault(key, {})
            for position, units in by_position.items():
                mine[position] = mine.get(position, 0.0) + units
    return out


def _runs(hours: list[int]) -> list[tuple[int, int]]:
    """Consecutive hour starts merged into ranges.

    Args:
        hours: Hour starts in ms, ascending.

    Returns:
        (first, last) hour starts of each run of consecutive hours.
    """
    runs: list[list[int]] = []
    for hour in hours:
        if runs and hour == runs[-1][1] + HOUR_MS:
            runs[-1][1] = hour
        else:
            runs.append([hour, hour])
    return [(a, b) for a, b in runs]


def recompute_id_volume(db: Database, store: SnapshotStore, market: str, src: str, day: int) -> set[int]:
    """One day's id volume (V33, V36): `recompute_id_days` for that day alone.

    Args:
        db: Database, inside the caller's transaction.
        store: The snapshot store.
        market: Market id.
        src: Source name.
        day: Day number.

    Returns:
        The item keys written for that day.
    """
    return recompute_id_days(db, store, market, src, {day})
