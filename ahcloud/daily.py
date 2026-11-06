"""The analyzer's daily mode: the day's snapshots into history, then the baselines (SPEC §2.4, BZ10-BZ14).

One run, in a fresh folder: download `state/history.db` (AHSync's own schema) and the compact snapshot store's
window; ingest every raw snapshot without a `scans` row, in time order (a repeated, late or dropped event comes
out the same); fill the catalog for new items from Blizzard's item search; prune (books 2 days, history 90);
analyze with `now_ms` = the newest ingested snapshot, so a rebuild from raw gives the same bytes; upload a
`VACUUM INTO` copy of the database with If-Match on the ETag downloaded; publish the base document.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from ahcloud.hourly import advance, snapshot
from ahcloud.publish import publish, read_latest, settings_to_dict
from ahcloud.store import Store
from ahsync.analysis.market import analyze_market
from ahsync.collect.blizzard import SEARCH_SPAN, BlizzardClient, catalog_row, raw_key
from ahsync.collect.observations import ingest_snapshot
from ahsync.collect.snapshots import SnapshotStore
from ahsync.db.sqlite import Database
from ahsync.engine.item_key import KEY_SUFFIX_FACTOR
from ahsync.engine.sellprob import MarketSettings
from ahsync.engine.volume import MAX_GAP_MS
from ahsync.history.prune import prune
from ahsync.intel.contract import MarketIntelligence

DB_KEY = "state/history.db"
SRC = "blizzard"
DAY_MS, HOUR_MS = 86_400_000, 3_600_000
CLOUD_BOOK_DAYS = 2                     # the cloud needs only the newest book and the day's pairs (SPEC §2.4)
COMPACT_DAYS = 3                        # compact snapshots kept: a day's id volume reads 6 h before it; lifetimes 25 h
MISSING_RETRY_MS = 7 * DAY_MS           # an id the item search didn't know is asked again after a week (BZ16)
LAST_INGESTED = "last_ingested_at_ms"   # meta key: the newest snapshot in history.db (BZ12)


@dataclass(frozen=True)
class DailyOutcome:
    """What a daily run did."""

    state: str                          # "published", "republished" (no new snapshot, BZ12) or "empty"
    ingested: int = 0                   # snapshots ingested this run
    built_ms: int | None = None
    items: int = 0                      # items in the document


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


def download_compact(data: Store, root: Path, market: str, from_ms: int) -> None:
    """Fetch the compact store's index and its files from a time on, into a local SnapshotStore folder.

    Args:
        data: The data store.
        root: The local snapshot store's root.
        market: Market id.
        from_ms: The oldest snapshot a recompute could read.
    """
    folder = root / market / SRC
    folder.mkdir(parents=True, exist_ok=True)
    for key in data.list(compact_prefix(market)):
        name = key.rsplit("/", 1)[1]
        if name == "index.json" or int(name.split(".")[0]) >= from_ms:
            (folder / name).write_bytes(data.get(key).data)


def upload_compact(data: Store, root: Path, market: str, keep_from_ms: int) -> None:
    """Upload the compact files the store holds and its index; delete the remote files the index dropped.

    Args:
        data: The data store.
        root: The local snapshot store's root.
        market: Market id.
        keep_from_ms: Remote files older than this are deleted (the local prune already dropped them).
    """
    folder, prefix = root / market / SRC, compact_prefix(market)
    remote = set(data.list(prefix))
    for path in sorted(folder.glob("*.json.gz")):
        if prefix + path.name not in remote:
            data.put(prefix + path.name, path.read_bytes())
    data.put(prefix + "index.json", (folder / "index.json").read_bytes())
    for key in sorted(remote):
        name = key.rsplit("/", 1)[1]
        if name != "index.json" and int(name.split(".")[0]) < keep_from_ms:
            data.delete(key)


def fill_catalog(db: Database, client: BlizzardClient, keys: list[int], now_ms: int) -> int:
    """Fetch the metadata of priced items the catalog lacks, in id ranges (SPEC §2.4, BZ16).

    A random-suffix key gets its base item's row under its own key, as AHF listed variants. An id the search
    doesn't know is remembered and asked again after a week.

    Args:
        db: The history database.
        client: The Blizzard client.
        keys: Item keys with prices.
        now_ms: Now, UTC ms.

    Returns:
        Rows written.
    """
    known = set(db.items())
    missing = json.loads(db.get_meta("catalog_missing") or "{}")
    wanted = sorted({k % KEY_SUFFIX_FACTOR for k in keys if k > 0 and k not in known}
                    - {int(i) for i, at in missing.items() if now_ms - at < MISSING_RETRY_MS})
    rows: dict[int, tuple] = {}
    start = 0
    while start < len(wanted):
        first = wanted[start]
        span = [i for i in wanted[start:] if i < first + SEARCH_SPAN]
        rows.update({r[0]: r for r in map(catalog_row, client.search_items(first, first + SEARCH_SPAN - 1))})
        start += len(span)
    for item_id in wanted:
        if item_id not in rows:
            missing[str(item_id)] = now_ms
        else:
            missing.pop(str(item_id), None)
    variants = [(k, *rows[k % KEY_SUFFIX_FACTOR][1:]) for k in keys if k > KEY_SUFFIX_FACTOR and k % KEY_SUFFIX_FACTOR in rows]
    written = db.upsert_items(sorted(rows.values()) + sorted(variants), now_ms)
    with db.transaction():
        db.set_meta("catalog_missing", json.dumps(missing, sort_keys=True))
    return written


def coverage(db: Database, market: str, now_ms: int) -> tuple[tuple[int, int], ...]:
    """The hours the cloud observed in the last 90 days, as ranges (SPEC §2.4, D99).

    Args:
        db: The history database.
        market: Market id.
        now_ms: Now, UTC ms.

    Returns:
        (first hour, last hour) runs of consecutive observed hours, ascending.
    """
    hours = db.observed_hours(market, (now_ms - 90 * DAY_MS) // HOUR_MS, now_ms // HOUR_MS).get(SRC, [])
    runs: list[list[int]] = []
    for hour in hours:
        if runs and hour == runs[-1][1] + 1:
            runs[-1][1] = hour
        else:
            runs.append([hour, hour])
    return tuple((a, b) for a, b in runs)


def catalog_of(db: Database, keys: list[int]) -> dict[int, tuple]:
    """The document's catalog: each priced item's name, quality, class, vendor price and stack.

    Args:
        db: The history database.
        keys: The document's item keys.

    Returns:
        Key -> (name, quality, item_class, vendor_sell, stack), for the keys the catalog knows.
    """
    items = db.items()
    return {k: (items[k].name, items[k].quality, items[k].item_class, items[k].vendor_sell, items[k].stack)
            for k in sorted(keys) if k in items}


def run_daily(data: Store, intel_store: Store, client: BlizzardClient, market: str, workdir: Path,
              settings: MarketSettings = MarketSettings(), fresh: bool = False) -> DailyOutcome:
    """One daily run (SPEC §2.4 daily mode).

    Args:
        data: The data store (raw/, state/).
        intel_store: The intel store.
        client: The Blizzard client (the catalog's item search).
        market: Market id.
        workdir: A folder for this run only (emptied first).
        settings: The analysis settings (the code's defaults; they travel in the document).
        fresh: Rebuild (FM72): ignore the stored database and compact snapshots and replay every raw snapshot;
            the new database replaces the old one unconditionally.

    Returns:
        What it did.

    Raises:
        ahcloud.store.PreconditionFailed: Another run uploaded the database meanwhile; nothing published.
        ahsync.db.sqlite.DatabaseError: The downloaded database is damaged (FM72: run a rebuild).
    """
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    found = None if fresh else data.get(DB_KEY)
    if found:
        (workdir / "history.db").write_bytes(found.data)
    db = Database.open(workdir / "history.db", 0, None)
    try:
        return _ingest_and_publish(db, data, intel_store, client, market, workdir, settings, found.etag if found else None)
    finally:
        db.close()


def _ingest_and_publish(db: Database, data: Store, intel_store: Store, client: BlizzardClient, market: str,
                        workdir: Path, settings: MarketSettings, etag: str | None) -> DailyOutcome:
    """The run's body, with the database open (see run_daily).

    Args:
        db: The downloaded (or new) history database.
        data: The data store.
        intel_store: The intel store.
        client: The Blizzard client.
        market: Market id.
        workdir: This run's folder.
        settings: The analysis settings.
        etag: The database's ETag as downloaded; None for a new database.

    Returns:
        What it did.
    """
    last = int(db.get_meta(LAST_INGESTED) or 0)
    after = raw_key(market, last - DAY_MS) if last else ""            # older raw is ingested or pruned (BZ14)
    pending = [k for k in data.list(f"raw/{market}/", start_after=after)
               if db.conn.execute("SELECT 1 FROM scans WHERE scan_id = ?", (f"{SRC}:{market}:{key_time_ms(k)}",)).fetchone() is None]
    if not pending and not last:
        return DailyOutcome("empty")
    store = SnapshotStore(workdir / "snapshots")
    if pending and etag is not None:                                 # a rebuild starts the compact store afresh
        download_compact(data, store.root, market, (key_time_ms(pending[0]) // DAY_MS) * DAY_MS - MAX_GAP_MS)
    for key in pending:
        observation = snapshot(data, key, market)
        if observation is not None:                                 # an empty house ingests nothing (BZ5)
            ingest_snapshot(db, store, observation)
        last = max(last, key_time_ms(key))
    keys = sorted({k for (k,) in db.conn.execute("SELECT DISTINCT key FROM current WHERE market = ?", (market,))})
    fill_catalog(db, client, keys, last)
    prune(db, last, 90, CLOUD_BOOK_DAYS)
    store.prune(market, SRC, last - COMPACT_DAYS * DAY_MS)
    with db.transaction():
        db.set_meta(LAST_INGESTED, str(last))
    intel, _failed = analyze_market(db, market, last, settings, (SRC,))
    intel = replace(intel, catalog=catalog_of(db, list(intel.items)), coverage=coverage(db, market, last),
                    settings=settings_to_dict(settings))
    try:
        prior = read_latest(intel_store, market)
    except ValueError:                                              # a torn pointer: rebuild the rings from now
        prior = None
    carried = carry_state(intel, prior[0] if prior else None)
    intel = advance(data, carried, market) or replace(carried, observations_until_ms=last)
    if pending:
        upload_compact(data, store.root, market, last - COMPACT_DAYS * DAY_MS)
    copy = workdir / "upload.db"
    db.conn.execute("VACUUM INTO ?", (str(copy),))
    data.put(DB_KEY, copy.read_bytes(), if_match=etag)
    publish(intel_store, intel)
    return DailyOutcome("published" if pending else "republished", len(pending), intel.built_ms, len(intel.items))


def carry_state(base: MarketIntelligence, prior: MarketIntelligence | None) -> MarketIntelligence:
    """A fresh analysis with the previous document's hourly fields carried over (SPEC §2.4, BZ33).

    The analysis knows nothing of the last 24 hours' rings; the previous document does. Its observations time
    is kept, so `advance` then applies only the snapshots it had not seen.

    Args:
        base: The new analysis (no hourly fields).
        prior: The latest published document, or None.

    Returns:
        The analysis with last_hour and the recent rings of every item the prior knew, and the prior's
        observations_until_ms (None without a prior).
    """
    if prior is None:
        return replace(base, observations_until_ms=None)
    items = {}
    for key, i in base.items.items():
        p = prior.items.get(key)
        items[key] = i if p is None else replace(
            i, last_hour=p.last_hour, recent_closes=p.recent_closes, recent_sold=p.recent_sold,
            recent_listed=p.recent_listed, recent_units=p.recent_units)
    return replace(base, items=items, observations_until_ms=prior.observations_until_ms)
