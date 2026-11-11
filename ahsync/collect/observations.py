"""Normalized observations into the Historical Store: snapshots with auction ids (SPEC §5.0, §3.3.1, §2.4, V36).

A snapshot is kept raw on disk (`collect/snapshots.py`) and, grouped into an order book, in the database, where it
prices items and feeds hourly sales and group diffs like any complete book. Its id diffs are the most trusted daily
volume (kind "ids"), from auction lifetimes carried one snapshot at a time (SPEC §2.4 "One snapshot at a time"):
each snapshot steps the stored state (`engine.lifetimes.step`), its gap's record goes into the ledger, and the open
day's and the last hours' rows are folded from the ledger. Only a late snapshot, a missing or damaged state (FM86)
or the cutover (BZ67) replays from the snapshot store. Every step is idempotent and commits in one transaction, so
a crash between the file and the commit is mended by delivering the same snapshot again (FM32).
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, ExitStack, nullcontext

from ahsync.collect.local_scans import (HOUR_MS, book_days, recompute_book_volume, recompute_sales_hours,
                                       store_scan_prices)
from ahsync.collect.provider import SnapshotObservation, snapshot_problems
from ahsync.collect.snapshots import SnapshotStore
from ahsync.db.sqlite import BookScan, Database
from ahsync.engine.lifetime_prices import new_listing_positions, sold_prices
from ahsync.engine.lifetimes import (LIFETIME_LOOKBACK_MS, GapRecord, Lifetimes, LifeState, gap_flows, gap_hours,
                                     sold_by_hour, step)
from ahsync.engine.volume import ID_METHOD_VERSION, SALES_PAIR_MAX_GAP_MS, IdBook, covered_seconds, group_book
from ahsync.history.lifetime_store import GapSummary

DAY_MS = 86_400_000
RECENT_HOURS = 6        # a snapshot rewrites the id hours [floor(S) − 6 h, floor(S)]: its gap's and the pairs' (§2.4)
CREDIT_REACH_MS = 12 * HOUR_MS    # gaps crediting hour H end after H − 5 h and before H + 7 h (`gap_hours`)

log = logging.getLogger(__name__)


def _no_stage(_: str) -> AbstractContextManager[None]:
    """A stage timer that times nothing.

    Args:
        _: The stage's name.

    Returns:
        An empty context.
    """
    return nullcontext()


def ingest_snapshot(db: Database, store: SnapshotStore, obs: SnapshotObservation,
                    stage: Callable[[str], AbstractContextManager[None]] = _no_stage) -> set[int]:
    """Store a provider's snapshot and everything derived from it, in one transaction (V36, SPEC §2.4).

    Args:
        db: Database.
        store: The snapshot store (the replay's input).
        obs: The snapshot.
        stage: Times a block by name: the file and the book are "ingest", the lifetimes and the commit "recompute"
            (BZ64).

    Returns:
        Item keys whose prices or volume changed.

    Raises:
        ValueError: The snapshot is malformed (provider.snapshot_problems); nothing is stored.
    """
    problems = snapshot_problems(obs)
    if problems:
        raise ValueError(f"{obs.src} snapshot at {obs.at_ms} refused: " + "; ".join(problems))
    scan_id = f"{obs.src}:{obs.market}:{obs.at_ms}"
    with ExitStack() as unit:                # one transaction across both stages; its commit is timed in the second
        with stage("ingest"):
            store.put(obs)
            unit.enter_context(db.transaction())
            newest, seen_before = db.newest_scan_at(obs.market, obs.src), db.has_scan(scan_id)
            dirty = _store_book(db, obs, scan_id)
        with stage("recompute"):
            dirty |= advance_lifetimes(db, store, obs, newest, seen_before)
            unit.close()
    return dirty


def _store_book(db: Database, obs: SnapshotObservation, scan_id: str) -> set[int]:
    """A snapshot as an order book: prices, observed hours, book-pair hours and group volume (V36).

    Args:
        db: Database, inside the caller's transaction.
        obs: The snapshot.
        scan_id: Its scan id.

    Returns:
        Item keys whose prices or book volume changed.
    """
    entries = [(e.key, e.price, e.stack, e.band, e.count) for e in group_book(IdBook(obs.at_ms, obs.auctions)).entries]
    db.store_book(BookScan(scan_id, obs.market, obs.src, None, obs.at_ms), entries)
    dirty = store_scan_prices(db, obs.market, scan_id, obs.at_ms, obs.src)
    db.add_observed_hours(obs.market, obs.src, [obs.at_ms // HOUR_MS])   # a snapshot is always complete (SRC16)
    recompute_sales_hours(db, obs.market, [obs.at_ms])                   # the ids rewrite their hours after (§3.3.1)
    for day in sorted(book_days(db, obs.market, [obs.at_ms])):
        dirty |= set(recompute_book_volume(db, obs.market, day))
    return dirty


def advance_lifetimes(db: Database, store: SnapshotStore, obs: SnapshotObservation, newest: int | None,
                      seen_before: bool) -> set[int]:
    """Carry the lifetimes over one stored snapshot: step, or replay when it is late or the state is off (§2.4).

    Args:
        db: Database, inside the caller's transaction.
        store: The snapshot store, holding the snapshot.
        obs: The snapshot.
        newest: The history's newest snapshot before this one was stored, UTC ms; None for a new history.
        seen_before: Whether this snapshot was stored before (a repeat, FM32).

    Returns:
        Item keys whose id volume was written.
    """
    market, src, at = obs.market, obs.src, obs.at_ms
    state = db.load_life_state(market, src)
    if newest is not None and (state is None or state.prev_at != newest):
        log.warning("lifetimes of %s %s: the state is at %s, the history at %s; rebuilding it from the snapshot store",
                    market, src, None if state is None else state.prev_at, newest)       # BZ67, FM86
        state = bootstrap(db, store, market, src, newest)
    state = state if state is not None else LifeState()
    if state.prev_at is not None and at <= state.prev_at:
        if seen_before or at == state.prev_at:
            return set()                                           # already in the state (FM32)
        log.warning("lifetimes of %s %s: snapshot %s is older than the state's %s; replaying", market, src, at,
                    state.prev_at)                                 # BZ66
        return replay_late(db, store, market, src, at, state.prev_at)
    return advance_to(db, store, (market, src, at), state)


def advance_to(db: Database, store: SnapshotStore, where: tuple[str, str, int], state: LifeState) -> set[int]:
    """Step the stored state over one snapshot, add its gap to the ledger and fold the open day and hours (§2.4).

    Args:
        db: Database, inside the caller's transaction.
        store: The snapshot store, holding the snapshot.
        where: (market id, source, the snapshot's time UTC ms), later than the state's.
        state: The state as stored.

    Returns:
        Item keys whose id volume was written.
    """
    market, src, at = where
    new, record = step(state, store.load(market, src, at))
    db.save_life_state(market, src, state, new)
    if record is not None:
        db.write_gaps(market, src, [summary(record)])
    dirty = fold_day(db, market, src, at // DAY_MS)
    fold_hours(db, market, src, recent_hours(at))
    return dirty


def bootstrap_window(newest_ms: int) -> tuple[int, int]:
    """Where a rebuilt state's replay starts and from which gap it writes the ledger (BZ67, FM86).

    The ledger must hold every gap of the day before the newest snapshot's and of its own: a late snapshot is at most
    a day older than the newest (BZ14) and refolds its whole day (BZ66), and that also covers every gap crediting the
    hours the next snapshots rewrite (they end after floor(newest) − 11 h). Each written gap needs
    LIFETIME_LOOKBACK_MS before it, so the replay reads at most 48 h + 66 h back (COMPACT_DAYS holds 120 h).

    Args:
        newest_ms: The history's newest snapshot, UTC ms.

    Returns:
        (the first snapshot replayed, the first gap end written), UTC ms.
    """
    write_from = (newest_ms - DAY_MS) // DAY_MS * DAY_MS
    return write_from - LIFETIME_LOOKBACK_MS, write_from


def bootstrap(db: Database, store: SnapshotStore, market: str, src: str, newest_ms: int) -> LifeState:
    """Rebuild the state at the history's newest snapshot from the snapshot store (the cutover BZ67, FM86).

    Args:
        db: Database, inside the caller's transaction.
        store: The snapshot store.
        market: Market id.
        src: Source name.
        newest_ms: The history's newest snapshot, UTC ms.

    Returns:
        The state, as stored.
    """
    from_ms, write_from = bootstrap_window(newest_ms)
    state, _, _ = _replay(db, store, (market, src), (from_ms, newest_ms), write_from)
    fold_day(db, market, src, newest_ms // DAY_MS)
    fold_hours(db, market, src, recent_hours(newest_ms))
    return state


def replay_late(db: Database, store: SnapshotStore, market: str, src: str, late_ms: int, prev_ms: int) -> set[int]:
    """Link a snapshot older than the state where it belongs (BZ66): replay, rewrite later gaps, re-fold.

    Every gap ending at or after it depends on it, nothing earlier does (SPEC §2.4), so the state is rebuilt from
    LIFETIME_LOOKBACK_MS before it and the ledger rewritten from it on. Then its days are folded whole, and the hours
    its old and new gaps credit, with those its own book pairs rewrote (gaps ending before it credit some of them);
    an hour no gap credits any more gets its book-pair rate back, as a rebuild would give it.

    Args:
        db: Database, inside the caller's transaction.
        store: The snapshot store, holding the late snapshot.
        market: Market id.
        src: Source name.
        late_ms: The late snapshot's time, UTC ms.
        prev_ms: The state's newest snapshot, UTC ms.

    Returns:
        Item keys whose id volume was written.
    """
    _, gone, written = _replay(db, store, (market, src), (late_ms - LIFETIME_LOOKBACK_MS, prev_ms), late_ms)
    dirty: set[int] = set()
    for day in sorted({late_ms // DAY_MS} | {s.gap[1] // DAY_MS for s in written}):
        dirty |= fold_day(db, market, src, day)
    hours = recent_hours(late_ms) | {h for gap in gone + [s.gap for s in written] for h in gap_hours(gap)}
    stale = hours - fold_hours(db, market, src, hours)
    if stale:                     # each rewrites [h, h + 2 h] from book pairs; the ids then take their hours back
        recompute_sales_hours(db, market, [h + SALES_PAIR_MAX_GAP_MS for h in sorted(stale)])
        fold_hours(db, market, src, {h + k * HOUR_MS for h in stale for k in range(3)})
    return dirty


def _replay(db: Database, store: SnapshotStore, where: tuple[str, str], span: tuple[int, int],
            write_from_ms: int) -> tuple[LifeState, list[tuple[int, int]], list[GapSummary]]:
    """Step a fresh state over the stored snapshots of a span, then rewrite the ledger from a gap end on.

    Args:
        db: Database, inside the caller's transaction.
        store: The snapshot store.
        where: (market id, source).
        span: (first, last) snapshot times replayed, UTC ms, inclusive.
        write_from_ms: Gaps ending at or after this replace the ledger's.

    Returns:
        (the state after the last snapshot, as stored; the ledger's gaps it replaced; the gaps it wrote).
    """
    market, src = where
    state, written = LifeState(), []
    for at in store.times(market, src, *span):
        state, record = step(state, store.load(market, src, at))
        if record is not None and record.gap[1] >= write_from_ms:
            written.append(summary(record))
    gone = db.delete_gaps_from(market, src, write_from_ms)
    db.write_gaps(market, src, written)
    if state.prev_at is None:
        db.clear_life_state(market, src)
    else:
        db.save_life_state(market, src, None, state)
    return state, gone, written


def summary(record: GapRecord) -> GapSummary:
    """A gap's record as its ledger entry: flows per item, new listings by position, sold units by price (§2.4).

    Args:
        record: From `engine.lifetimes.step`.

    Returns:
        The summary, with the same floats a pass over the gap gives.
    """
    gap = record.gap
    lt = Lifetimes((gap,), {gap: record.seen}, record.endings, record.fills, record.listed, record.clashes,
                   record.commodities, record.first_seen, (record,))
    return GapSummary(gap, gap_flows(record), new_listing_positions(lt).get(gap, {}), sold_prices(lt).get(gap, {}))


def recent_hours(at_ms: int) -> set[int]:
    """The hours a snapshot's id rates can change: its gap's and its book pairs' (SPEC §2.4).

    Args:
        at_ms: The snapshot's time, UTC ms.

    Returns:
        Hour starts, ms: [floor(at) − RECENT_HOURS h, floor(at)].
    """
    last = at_ms // HOUR_MS * HOUR_MS
    return {last - k * HOUR_MS for k in range(RECENT_HOURS + 1)}


def fold_day(db: Database, market: str, src: str, day: int) -> set[int]:
    """One day's id volume, ended listings, new-listing positions and sold prices, folded from the ledger (§2.4).

    The day's gaps (those ending in it; each counts for the day of its later snapshot, its cover too) summed in
    gap order from zero, the same arithmetic as the pass the ledger replaced. Its rows are replaced whole.

    Args:
        db: Database, inside the caller's transaction.
        market: Market id.
        src: Source name.
        day: Day number.

    Returns:
        The item keys written.
    """
    gaps = db.read_gaps(market, src, day * DAY_MS, day * DAY_MS + DAY_MS - 1)
    volume: dict[int, list[float]] = {}
    ended: dict[int, list[float]] = {}
    unseen: dict[int, float] = {}
    for g in gaps:
        for key, f in g.flows.items():
            row = volume.setdefault(key, [0.0, 0.0, 0.0])
            row[0] += f.sold
            row[1] += f.expired
            row[2] += f.listed
            unseen[key] = unseen.get(key, 0.0) + f.unseen
            if f.ended:
                tally = ended.setdefault(key, [0.0] * 8)
                for i, value in enumerate((f.ended, f.expired_listings, f.matched, f.cancels, f.cancelled,
                                           f.t1_ended, f.t1_left)):
                    tally[i] += value
                tally[7] = max(tally[7], float(f.commodity))
    spans = [g.gap for g in gaps]
    db.replace_volume_day(market, day, src, {k: tuple(v) for k, v in sorted(volume.items())}, ID_METHOD_VERSION,
                          ("ids", covered_seconds(spans)) if spans else None, unseen)
    db.replace_lifetimes_day(market, day, src, {k: tuple(v) for k, v in sorted(ended.items())})
    db.replace_new_listings_day(market, day, src, merge_positions(g.positions for g in gaps))
    db.replace_sold_prices_day(market, day, src, merge_positions(g.prices for g in gaps))   # BZ52
    return set(volume)


def fold_hours(db: Database, market: str, src: str, hours: set[int]) -> set[int]:
    """Some hours' id sales rates, folded from every ledger gap crediting them (SPEC §2.4 item 4, BZ30).

    An hour no gap credits keeps its book-pair rate; one that some gap credits is replaced whole by the ids' rates
    (ids rank above groups, §3.3.1), pooled over all its gaps in gap order.

    Args:
        db: Database, inside the caller's transaction, after the snapshot's book pairs.
        market: Market id.
        src: Source name.
        hours: Hour starts, ms.

    Returns:
        The hours some gap credits, rewritten.
    """
    if not hours:
        return set()
    near = db.read_gaps(market, src, min(hours) - CREDIT_REACH_MS, max(hours) + CREDIT_REACH_MS, units=False)
    flows = {g.gap: g.flows for g in near}
    credited = {h for gap in flows for h in gap_hours(gap) if h in hours}
    rates = sold_by_hour(flows, credited)
    for first, last in _runs(sorted(credited)):
        db.replace_sales_hours(market, first, last, rates)
    return credited


def merge_positions(per_gap: Iterable[dict[int, dict[int, float]]]) -> dict[int, dict[int, float]]:
    """Several gaps' new-listing positions (or sold prices, BZ52) summed per item and position (or price).

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
