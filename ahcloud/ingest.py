"""The Ingest Lambda: raw snapshots into the history, its one writer (SPEC §2.4 Ingest; BZ56-BZ58, BZ61, FM83, FM85).

A run reads `state/ingest.json` and the raw listing, and stops when nothing is newer. Otherwise, in a fresh folder,
it downloads `state/history.db` (AHSync's own schema) and the compact snapshot store's window; ingests the oldest
pending raw snapshots, at most `MAX_SNAPSHOTS_PER_RUN` (a snapshot is one raw object, one picture of the house at one
Blizzard refresh), in time order, so a repeated, late or dropped event comes out the same; fills the catalog for new
items from Blizzard's item search; prunes (books 2 days, history 90); uploads the compact files and a `VACUUM INTO`
copy of the database with If-Match on the ETag downloaded; and only then writes `state/ingest.json`, which can
therefore lag the database but never lead it. A backlog drains over successive scheduled runs. The daily analysis
(`ahcloud.daily`) only reads what this writes.

A rebuild (FM72) starts a fresh history from the raw of the retention window and marks it rebuilding until the
newest raw at its start; while the mark is set each run takes REBUILD_SNAPSHOTS_PER_RUN and the daily analysis
publishes nothing, so half a history is never published (BZ61).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ahcloud import aws
from ahcloud.credentials import from_secret
from ahcloud.hourly import snapshot
from ahcloud.profile import Profile
from ahcloud.store import Store
from ahsync.clock import Clock, SystemClock
from ahsync.collect.blizzard import (FALLBACK_NAMESPACE, SEARCH_SPAN, STATIC_NAMESPACE, BlizzardClient, BlizzardError,
                                     catalog_row, raw_key)
from ahsync.collect.client import UrllibTransport
from ahsync.collect.observations import id_days, ingest_snapshot, recompute_id_days
from ahsync.collect.provider import snapshot_problems
from ahsync.collect.snapshots import SnapshotStore
from ahsync.db.sqlite import Database
from ahsync.engine.item_key import KEY_SUFFIX_FACTOR
from ahsync.engine.volume import SALES_PAIR_MAX_GAP_MS
from ahsync.engine.lifetimes import LIFETIME_LOOKBACK_MS
from ahsync.history.prune import prune

DB_KEY = "state/history.db"
STATE_KEY = "state/ingest.json"
SRC = "blizzard"
DAY_MS, HOUR_MS, MINUTE_MS = 86_400_000, 3_600_000, 60_000
MAX_SNAPSHOTS_PER_RUN = 12              # raw objects per scheduled run, about 12 h of market time (plan decision 3)
# A rebuild's runs take more: provisional until IS0 measures a snapshot's cost on Lambda (plan §3 IS0).
REBUILD_SNAPSHOTS_PER_RUN = 60
CLOUD_BOOK_DAYS = 2                     # the cloud needs only the newest book and the day's pairs (SPEC §2.4)
COMPACT_DAYS = 5                        # compact snapshots kept: a late snapshot (a day old) recomputes from the day 2 h
                                        # before it, whose pass reads LIFETIME_LOOKBACK_MS (66 h) earlier: 116 h at worst
MISSING_RETRY_MS = 7 * DAY_MS           # an id the item search didn't know is asked again after a week (BZ16)
LAST_INGESTED = "last_ingested_at_ms"   # meta key: the newest snapshot in history.db (BZ12)
REBUILDING = "rebuilding_until_ms"      # meta key: a rebuild is under way until the history reaches this (BZ61)
RETENTION_DAYS = 90                     # history kept in the cloud (SPEC §2.4); the raw stays forever (BZ14)
# A first run or a rebuild replays only this much raw before the newest snapshot: what the retention keeps, plus the
# oldest kept day's own 24 h and the 36 h its lifetimes read before it (FM76). Older raw would be pruned anyway.
REBUILD_REPLAY_MS = RETENTION_DAYS * DAY_MS + DAY_MS + LIFETIME_LOOKBACK_MS
REFUSED = "refused_snapshots"           # meta key: snapshot times refused whole, never tried again (FM74)
EMPTY = "empty_snapshots"               # meta key: empty-house snapshot times (BZ5), ingested as nothing, never pending
                                        # again (BZ62; they leave no scans row)
WORKDIR = Path("/tmp/ahcloud-ingest")   # Lambda's ephemeral disk; emptied by every run that downloads

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestOutcome:
    """What an Ingest run did."""

    state: str                          # "ingested", "up_to_date" (nothing new, BZ58) or "empty" (no raw yet)
    ingested: int = 0                   # snapshots ingested this run
    refused: int = 0                    # snapshots refused whole this run (FM74)
    pending: int = 0                    # snapshots still waiting after the run (BZ57)
    last_ingested_at_ms: int | None = None
    lag_ms: int = 0                     # the newest raw minus the newest snapshot ingested or refused (FM84)
    rebuilding_until_ms: int | None = None
    profile: str = field(default="", compare=False)   # the run's profile line (BZ64), printed by the caller


@dataclass(frozen=True)
class _Run:
    """One run's fixed facts, carried into its body."""

    etag: str | None                    # the stored database's ETag at the start; None without one
    fresh: bool                         # a rebuild: ignore the stored database and compact snapshots
    cap: int | None                     # snapshots this run may take; None uncapped (a local command)
    started_ms: int                     # the run's start, UTC ms (for the state file's duration)
    workdir: Path                       # this run's folder
    clock: Clock                        # the state file's times only; the history's times are the snapshots'
    profile: Profile                    # the run's stage times (BZ64)


def key_time_ms(key: str) -> int:
    """A raw key's snapshot time.

    Args:
        key: "raw/<market>/<YYYY>/<MM>/<DD>/<HHMMSS>.json.gz".

    Returns:
        UTC ms.
    """
    stamp = "".join(key.rsplit("/", 4)[1:]).removesuffix(".json.gz")
    return int(datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp() * 1000)


def compact_prefix(market: str) -> str:
    """Where the compact snapshot store lives in the data store.

    Args:
        market: Market id.

    Returns:
        "state/snapshots/<market>/blizzard/".
    """
    return f"state/snapshots/{market}/{SRC}/"


def read_state(data: Store) -> dict:
    """The last run's `state/ingest.json`, or {} when there is none or it can't be read.

    Args:
        data: The data store.

    Returns:
        The state: `last_ingested_at_ms`, `newest_seen_ms`, `pending`, `finished_ms`, `duration_ms`, `outcome`,
        `ingested`, `refused`, `rebuilding_until_ms`.
    """
    found = data.get(STATE_KEY)
    if found is None:
        return {}
    try:
        state = json.loads(found.data)
    except ValueError:                                              # a damaged file only costs one full run
        return {}
    return state if isinstance(state, dict) else {}


def write_state(data: Store, outcome: IngestOutcome, newest_seen_ms: int | None, finished_ms: int,
                started_ms: int) -> None:
    """Write `state/ingest.json` (SPEC §2.4 Ingest): only after the database is uploaded, or when nothing changed.

    Args:
        data: The data store.
        outcome: The run's outcome.
        newest_seen_ms: Every raw snapshot up to this time is ingested or refused; None before any.
        finished_ms: The run's end, UTC ms.
        started_ms: The run's start, UTC ms.
    """
    state = {"last_ingested_at_ms": outcome.last_ingested_at_ms, "newest_seen_ms": newest_seen_ms,
             "pending": outcome.pending, "finished_ms": finished_ms, "duration_ms": finished_ms - started_ms,
             "outcome": outcome.state, "ingested": outcome.ingested, "refused": outcome.refused,
             "rebuilding_until_ms": outcome.rebuilding_until_ms}
    data.put(STATE_KEY, json.dumps(state, sort_keys=True).encode("utf-8"))


def run_ingest(data: Store, client: BlizzardClient, market: str, workdir: Path, clock: Clock,
               cap: int | None = MAX_SNAPSHOTS_PER_RUN, fresh: bool = False) -> IngestOutcome:
    """One Ingest run (SPEC §2.4 Ingest).

    Args:
        data: The data store (raw/, state/).
        client: The Blizzard client (the catalog's item search).
        market: Market id.
        workdir: A folder for this run only (emptied first).
        clock: The clock, for the state file's times only; the history's times are the snapshots'.
        cap: The most snapshots to ingest; None takes every pending one (a local command has no time limit).
            While the history is marked rebuilding, a cap is replaced by REBUILD_SNAPSHOTS_PER_RUN.
        fresh: Rebuild (FM72): ignore the stored database and compact snapshots, replay the raw of the last
            REBUILD_REPLAY_MS (FM76) and mark the history rebuilding; the upload is still conditional on the
            database's ETag at the start, so a run that uploaded meanwhile makes the rebuild fail.

    Returns:
        What it did, with its profile line (BZ64).

    Raises:
        ahcloud.store.PreconditionFailed: Another writer uploaded the database meanwhile; the state file is not
            written (FM83).
        ahsync.db.sqlite.DatabaseError: The downloaded database is damaged (FM72: run a rebuild).
    """
    profile, started_ms = Profile("ingest"), clock.now_ms()
    with profile.stage("state"):
        state = {} if fresh else read_state(data)
        seen = state.get("newest_seen_ms")
        nothing_new = isinstance(seen, int) and not data.list(f"raw/{market}/", start_after=raw_key(market, seen))
    if nothing_new:
        outcome = IngestOutcome("up_to_date", 0, 0, 0, state.get("last_ingested_at_ms"), 0,
                                state.get("rebuilding_until_ms"))
        write_state(data, outcome, seen, clock.now_ms(), started_ms)
        return _profiled(outcome, profile)
    with profile.stage("download"):
        shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True)
        found = data.get(DB_KEY)
        if found and not fresh:
            (workdir / "history.db").write_bytes(found.data)
        db = Database.open(workdir / "history.db", 0, None)
    try:
        run = _Run(found.etag if found else None, fresh, cap, started_ms, workdir, clock, profile)
        return _profiled(_ingest(db, data, client, market, run), profile)
    finally:
        db.close()


def _profiled(outcome: IngestOutcome, profile: Profile) -> IngestOutcome:
    """The outcome with the run's profile line.

    Args:
        outcome: What the run did.
        profile: Its stage times.

    Returns:
        The outcome, its `profile` set to the line (outcome and snapshot count only; no key or name).
    """
    return replace(outcome, profile=profile.line(outcome=outcome.state, snapshots=outcome.ingested,
                                                 refused=outcome.refused, pending=outcome.pending))


def _pending(db: Database, data: Store, market: str, last: int) -> tuple[list[str], int | None]:
    """The raw keys still to ingest, in time order, and the newest raw key's time.

    Args:
        db: The history database.
        data: The data store.
        market: Market id.
        last: The newest snapshot in the history (0 for a first run or a rebuild).

    Returns:
        (keys without a `scans` row, not refused and not empty, oldest first; the newest listed key's time, None
        when none).
    """
    refused = set(json.loads(db.get_meta(REFUSED) or "[]")) | set(json.loads(db.get_meta(EMPTY) or "[]"))
    after = raw_key(market, last - DAY_MS) if last else ""            # older raw is ingested or pruned (BZ14)
    listed = data.list(f"raw/{market}/", start_after=after)
    if not last and listed:                                           # a first run or a rebuild: the window only (FM76)
        listed = [k for k in listed if key_time_ms(k) >= key_time_ms(listed[-1]) - REBUILD_REPLAY_MS]
    pending = [k for k in listed if key_time_ms(k) not in refused
               and db.conn.execute("SELECT 1 FROM scans WHERE scan_id = ?", (f"{SRC}:{market}:{key_time_ms(k)}",)).fetchone() is None]
    return pending, (key_time_ms(listed[-1]) if listed else None)


def _ingest(db: Database, data: Store, client: BlizzardClient, market: str, run: _Run) -> IngestOutcome:
    """The run's body, with the database open (see run_ingest).

    Args:
        db: The downloaded (or new) history database.
        data: The data store.
        client: The Blizzard client.
        market: Market id.
        run: The run's fixed facts, its folder, clock and profile.

    Returns:
        What it did.
    """
    last = int(db.get_meta(LAST_INGESTED) or 0)
    with run.profile.stage("state"):
        pending, newest = _pending(db, data, market, last)
    if (run.fresh or run.etag is None) and not last and newest is not None:     # a history from nothing (BZ61, BZ63)
        db.set_meta(REBUILDING, str(newest))
    mark = int(db.get_meta(REBUILDING) or 0) or None
    limit = None if run.cap is None else REBUILD_SNAPSHOTS_PER_RUN if mark else run.cap
    batch = pending if limit is None else pending[:limit]
    left = len(pending) - len(batch)
    seen = key_time_ms(batch[-1]) if left else newest
    if not batch:
        outcome = IngestOutcome("up_to_date" if last else "empty", 0, 0, 0, last or None, 0, mark)
        if last:
            write_state(data, outcome, seen, run.clock.now_ms(), run.started_ms)
        return outcome
    store = SnapshotStore(run.workdir / "snapshots")
    refused, empty = _ingest_batch(db, data, market, store, (run, batch, last))
    last = max([last] + [key_time_ms(k) for k in batch if key_time_ms(k) not in refused])
    written = [key_time_ms(k) for k in batch if key_time_ms(k) not in set(refused) | set(empty)]
    if last:
        _finish_history(db, client, market, store, (run, last))
        mark = _clear_mark(db, mark, last, left)
    lag = (newest or 0) - (seen or 0)
    outcome = (IngestOutcome("ingested", len(written), len(refused), left, last, lag, mark) if last
               else IngestOutcome("empty", 0, len(refused), left, None, lag, mark))   # all refused: keep the refusals
    with run.profile.stage("upload"):
        _write_back(db, data, market, store, run, written, last)
        write_state(data, outcome, seen, run.clock.now_ms(), run.started_ms)
    return outcome


def _ingest_batch(db: Database, data: Store, market: str, store: SnapshotStore,
                  work: tuple[_Run, list[str], int]) -> tuple[list[int], list[int]]:
    """Download the compact window, ingest the batch, then recompute the days it touched, each as a stage.

    Args:
        db: The history database.
        data: The data store.
        market: Market id.
        store: The local compact snapshot store.
        work: (the run, the batch's raw keys oldest first, the history's newest snapshot before it, UTC ms).

    Returns:
        (the times refused, the times of empty houses), both remembered in meta.
    """
    run, batch, last = work
    if run.etag is not None and not run.fresh:                       # a rebuild starts the compact store afresh
        with run.profile.stage("download"):
            first_day = (key_time_ms(batch[0]) - SALES_PAIR_MAX_GAP_MS) // DAY_MS       # id_days' first day
            download_compact(data, store.root, market, first_day * DAY_MS - LIFETIME_LOOKBACK_MS, last)
    with run.profile.stage("ingest"):
        refused, empty, days = ingest_pending(db, data, store, market, batch)
        remember(db, REFUSED, refused)
        remember(db, EMPTY, empty)
    with run.profile.stage("recompute"):
        with db.transaction():
            recompute_id_days(db, store, market, SRC, days)
    return refused, empty


def _write_back(db: Database, data: Store, market: str, store: SnapshotStore, run: _Run, written: list[int],
                last: int) -> None:
    """Upload the run's work in the order that keeps a lost race harmless (FM83).

    This run's new compact files first (named by their time; any run writes the same bytes for one), then the
    database with If-Match, and only after that succeeds the compact index and the deletion of old files.

    Args:
        db: The history database.
        data: The data store.
        market: Market id.
        store: The local compact snapshot store.
        run: The run's fixed facts.
        written: Times of the snapshots this run stored, UTC ms.
        last: The newest snapshot in the history, UTC ms; 0 when there is none yet.

    Raises:
        ahcloud.store.PreconditionFailed: Another writer uploaded the database meanwhile.
    """
    upload_compact_files(data, store.root, market, written)
    copy = run.workdir / "upload.db"
    db.conn.execute("VACUUM INTO ?", (str(copy),))
    data.put(DB_KEY, copy.read_bytes(), if_match=run.etag)
    if last:
        finish_compact(data, store.root, market, last - COMPACT_DAYS * DAY_MS)


def remember(db: Database, name: str, times: list[int]) -> None:
    """Add snapshot times to a meta list (refused or empty), so they are never pending again.

    Args:
        db: The history database.
        name: The meta key, REFUSED or EMPTY.
        times: Snapshot times, UTC ms; nothing is written when empty.
    """
    if times:
        known = set(json.loads(db.get_meta(name) or "[]"))
        db.set_meta(name, json.dumps(sorted(known | set(times))))


def _finish_history(db: Database, client: BlizzardClient, market: str, store: SnapshotStore,
                    work: tuple[_Run, int]) -> None:
    """After a batch: the catalog for new items, the prune, and the newest snapshot recorded, each as a stage.

    Args:
        db: The history database.
        client: The Blizzard client.
        market: Market id.
        store: The local compact snapshot store.
        work: (the run, the newest snapshot now in the history, UTC ms).
    """
    run, last = work
    with run.profile.stage("catalog"):
        keys = sorted({k for (k,) in db.conn.execute("SELECT DISTINCT key FROM current WHERE market = ?", (market,))})
        fill_catalog(db, client, keys, last)
    with run.profile.stage("prune"):
        prune(db, last, RETENTION_DAYS, CLOUD_BOOK_DAYS)
        store.prune(market, SRC, last - COMPACT_DAYS * DAY_MS)
    db.set_meta(LAST_INGESTED, str(last))


def _clear_mark(db: Database, mark: int | None, last: int, left: int) -> int | None:
    """Clear the rebuilding mark once the history has reached it, or nothing is left to take (BZ61).

    Args:
        db: The history database.
        mark: The mark, UTC ms, or None.
        last: The newest snapshot now in the history, UTC ms.
        left: Snapshots still pending after this run (0 also when the newest raw was refused or empty).

    Returns:
        The mark still set, or None.
    """
    if mark is not None and (last >= mark or left == 0):
        with db.transaction():
            db.conn.execute("DELETE FROM meta WHERE name = ?", (REBUILDING,))
        return None
    return mark


def download_compact(data: Store, root: Path, market: str, from_ms: int, to_ms: int) -> None:
    """Fetch the compact files of a time range into a local SnapshotStore folder, and index exactly those.

    The index is built from the files fetched, never read from the remote `index.json`: a run that lost a race may
    have written that (FM83), and files after the history's newest snapshot may be a lost run's or, during a
    rebuild, the old history's.

    Args:
        data: The data store.
        root: The local snapshot store's root.
        market: Market id.
        from_ms: The oldest snapshot a recompute could read.
        to_ms: The history's newest snapshot; later files are left out.
    """
    folder = root / market / SRC
    folder.mkdir(parents=True, exist_ok=True)
    times = []
    for key in data.list(compact_prefix(market)):
        name = key.rsplit("/", 1)[1]
        if name != "index.json" and from_ms <= int(name.split(".")[0]) <= to_ms:
            (folder / name).write_bytes(data.get(key).data)
            times.append(int(name.split(".")[0]))
    (folder / "index.json").write_bytes(json.dumps(sorted(times)).encode("utf-8"))


def upload_compact_files(data: Store, root: Path, market: str, times: list[int]) -> None:
    """Upload the compact files this run wrote, replacing any copy (an old history's, during a rebuild).

    Args:
        data: The data store.
        root: The local snapshot store's root.
        market: Market id.
        times: The snapshot times this run stored, UTC ms; any the local prune already dropped are skipped.
    """
    folder, prefix = root / market / SRC, compact_prefix(market)
    for at_ms in sorted(times):
        path = folder / f"{at_ms}.json.gz"
        if path.exists():
            data.put(prefix + path.name, path.read_bytes())


def finish_compact(data: Store, root: Path, market: str, keep_from_ms: int) -> None:
    """After the database upload: the compact index (for reading by hand; runs build their own) and the old files.

    Args:
        data: The data store.
        root: The local snapshot store's root.
        market: Market id.
        keep_from_ms: Remote files older than this are deleted (the local prune already dropped them).
    """
    folder, prefix = root / market / SRC, compact_prefix(market)
    if (folder / "index.json").exists():
        data.put(prefix + "index.json", (folder / "index.json").read_bytes())
    for key in sorted(data.list(prefix)):
        name = key.rsplit("/", 1)[1]
        if name != "index.json" and int(name.split(".")[0]) < keep_from_ms:
            data.delete(key)


def search_ranges(client: BlizzardClient, ids: list[int], namespace: str) -> dict[int, tuple]:
    """Catalog rows for some item ids, one item search per 1,000-id range that holds any of them.

    Args:
        client: The Blizzard client.
        ids: Item ids, ascending.
        namespace: The static namespace to search.

    Returns:
        Item id -> items row, for the ids (and their range neighbours) the search knows.
    """
    rows: dict[int, tuple] = {}
    start = 0
    while start < len(ids):
        first = ids[start]
        rows.update({r[0]: r for r in map(catalog_row, client.search_items(first, first + SEARCH_SPAN - 1, namespace))})
        start += sum(1 for i in ids[start:] if i < first + SEARCH_SPAN)
    return rows


def fill_catalog(db: Database, client: BlizzardClient, keys: list[int], now_ms: int) -> int:
    """Fetch the metadata of priced items the catalog lacks, in id ranges (SPEC §2.4, BZ16).

    Forever's static data first; what it lacks from Classic Era's (`static-classic1x-us`, the user's decision
    2026-11-06), remembered in meta `catalog_fallback` so the document marks those rows. A random-suffix key gets
    its base item's row under its own key, as AHF listed variants. An id neither knows is asked again after a
    week.

    Args:
        db: The history database.
        client: The Blizzard client.
        keys: Item keys with prices.
        now_ms: Now, UTC ms (the newest snapshot's time, so a rebuild asks the same questions).

    Returns:
        Rows written.
    """
    known = set(db.items())
    missing = json.loads(db.get_meta("catalog_missing") or "{}")
    wanted = sorted({k % KEY_SUFFIX_FACTOR for k in keys if k > 0 and k not in known}
                    - {int(i) for i, at in missing.items() if now_ms - at < MISSING_RETRY_MS})
    rows = search_ranges(client, wanted, STATIC_NAMESPACE)
    fallback = search_ranges(client, [i for i in wanted if i not in rows], FALLBACK_NAMESPACE)
    fallback = {i: r for i, r in fallback.items() if i in wanted and i not in rows}
    rows.update(fallback)
    sources = set(json.loads(db.get_meta("catalog_fallback") or "[]")) - {i for i in rows if i not in fallback}
    sources |= set(fallback)
    for item_id in wanted:
        if item_id not in rows:
            missing[str(item_id)] = now_ms
        else:
            missing.pop(str(item_id), None)
    variants = [(k, *rows[k % KEY_SUFFIX_FACTOR][1:]) for k in keys if k > KEY_SUFFIX_FACTOR and k % KEY_SUFFIX_FACTOR in rows]
    written = db.upsert_items(sorted(rows.values()) + sorted(variants), now_ms)
    with db.transaction():
        db.set_meta("catalog_missing", json.dumps(missing, sort_keys=True))
        db.set_meta("catalog_fallback", json.dumps(sorted(sources)))
    return written


def ingest_pending(db: Database, data: Store, store: SnapshotStore, market: str,
                   pending: list[str]) -> tuple[list[int], list[int], set[int]]:
    """Ingest raw snapshots in order, setting aside any that is refused whole (FM33, FM74).

    A refused snapshot is stored nowhere (`ingest_snapshot` checks before writing) and logged with its key and
    reason; the rest go on, so one bad answer from Blizzard never stops the cloud. The caller recomputes the days
    the batch touches once, not once per snapshot (each day reads 66 h before it, SPEC §2.4).

    Args:
        db: The history database.
        data: The data store (raw/).
        store: The local compact snapshot store.
        market: Market id.
        pending: Raw keys without a `scans` row, in time order.

    Returns:
        (the times of the snapshots refused, those of empty houses or vanished objects (BZ5, BZ62), ascending;
        the days whose id volume and lifetimes need recomputing).
    """
    refused, empty, days = [], [], set()
    for key in pending:
        try:
            observation = snapshot(data, key, market)
        except BlizzardError as error:
            problems = [str(error)]
        else:
            problems = snapshot_problems(observation) if observation is not None else []
        if problems:
            log.warning("refused %s: %s", key, "; ".join(problems))
            refused.append(key_time_ms(key))
        elif observation is None:                                   # an empty house ingests nothing (BZ5)
            empty.append(key_time_ms(key))
        else:
            ingest_snapshot(db, store, observation, id_volume=False)
            days |= id_days(store, market, SRC, observation.at_ms)
    return refused, empty, days


def emit_outcome(market: str, outcome: IngestOutcome, now_ms: int) -> None:
    """The run's metrics: its outcome, the history's lag behind the raw in minutes (FM84) and any refusal.

    Args:
        market: Market id.
        outcome: The run's outcome.
        now_ms: Now, UTC ms.
    """
    dims = {"market": market}
    aws.metric(f"ingest_{outcome.state}", 1, dims, now_ms)
    aws.metric("history_lag_minutes", outcome.lag_ms // MINUTE_MS, dims, now_ms)
    if outcome.refused:
        aws.metric("ingest_refused", outcome.refused, dims, now_ms)


def handler(event: Any, context: Any) -> dict:
    """The Lambda entry point: a scheduled run, or a rebuild when invoked with {"mode": "rebuild"}.

    The environment holds names only (DATA_BUCKET, MARKET, SECRET_NAME); the Blizzard secret is read for the
    catalog's item search.

    Args:
        event: The invocation's event.
        context: The Lambda context (unused).

    Returns:
        The outcome as a dict, for the invocation log.
    """
    logging.getLogger().setLevel(logging.INFO)          # Lambda's root logger starts at WARNING
    env, clock = os.environ, SystemClock()
    market = env["MARKET"]
    found = from_secret(env["SECRET_NAME"], aws.get_secret)
    client = BlizzardClient(UrllibTransport(), clock, found.client_id, found.secret)
    fresh = isinstance(event, dict) and event.get("mode") == "rebuild"
    outcome = run_ingest(aws.S3Store(env["DATA_BUCKET"]), client, market, WORKDIR, clock, fresh=fresh)
    print(outcome.profile)                              # one JSON log line for Logs Insights (BZ64)
    emit_outcome(market, outcome, clock.now_ms())
    return {"mode": "rebuild" if fresh else "scheduled", "state": outcome.state, "ingested": outcome.ingested,
            "refused": outcome.refused, "pending": outcome.pending}
