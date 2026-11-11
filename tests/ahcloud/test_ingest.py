"""Tests for ahcloud/ingest.py: raw snapshots into the history, its one writer (SPEC §2.4 Ingest; BZ10-BZ16,
BZ56-BZ58, BZ61, FM72-FM76, FM82, FM83)."""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import pytest

from ahcloud import ingest
from ahcloud.daily import run_daily
from ahcloud.ingest import (COMPACT_DAYS, DB_KEY, LAST_INGESTED, REBUILD_REPLAY_MS, REBUILDING, REFUSED, STATE_KEY,
                            compact_prefix, key_time_ms, read_state, run_ingest)
from ahcloud.publish import read_latest
from ahcloud.store import LocalStore, PreconditionFailed
from ahsync.collect.blizzard import raw_key
from ahsync.db.sqlite import Database, DatabaseError
from ahsync.engine.volume import LIFETIME_LOOKBACK_MS, SALES_PAIR_MAX_GAP_MS
from tests.support.cloud_world import (A_MS, B, B_MS, C_MS, HOUR, LINEN, M, SNAP_A, SNAP_B, SNAP_C, SPAULDERS,
                                       FakeCatalog, house, linen)
from tests.support.fake_clock import FakeClock

DAY_MS = 86_400_000
NOW = C_MS + 10 * 60_000                       # the runs' clock: only the state file reads it


def stores(tmp_path, *snaps):
    """A data store with raw snapshots, and an empty intel store."""
    data, intel = LocalStore(tmp_path / "data"), LocalStore(tmp_path / "intel")
    for at_ms, envelope in snaps:
        data.put(raw_key(M, at_ms), envelope)
    return data, intel


def ingest_run(data, tmp_path, catalog=None, **kwargs):
    """One Ingest run with a fake catalog and clock."""
    return run_ingest(data, catalog or FakeCatalog(), M, tmp_path / "ingest", FakeClock(NOW), **kwargs)


def cycle(data, intel, tmp_path, catalog=None, **kwargs):
    """An Ingest run, then the daily analysis: what the old single daily run did. Returns the Ingest outcome."""
    outcome = ingest_run(data, tmp_path, catalog, **kwargs)
    run_daily(data, intel, M, tmp_path / "run")
    return outcome


def history(data, tmp_path) -> Database:
    """The uploaded history database, opened from a copy."""
    path = tmp_path / "check.db"
    path.write_bytes(data.get(DB_KEY).data)
    return Database.open(path, 0, None)


def shifted(envelope: bytes, at_ms: int) -> bytes:
    """The same snapshot under another Last-Modified."""
    body = json.loads(gzip.decompress(envelope))
    body["last_modified"] = format_datetime(datetime.fromtimestamp(at_ms / 1000, timezone.utc), usegmt=True)
    return gzip.compress(json.dumps(body).encode(), mtime=0)


def hourly_snaps(first_ms: int, count: int) -> list[tuple[int, bytes]]:
    """`count` snapshots an hour apart from first_ms, alternating B's and C's books."""
    return [(first_ms + i * HOUR, shifted(SNAP_B if i % 2 == 0 else SNAP_C, first_ms + i * HOUR)) for i in range(count)]


def test_BZ10_ingest_then_the_analysis_publish_every_new_snapshot_as_the_baselines(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    catalog = FakeCatalog()
    outcome = cycle(data, intel, tmp_path, catalog)
    assert (outcome.state, outcome.ingested, outcome.last_ingested_at_ms) == ("ingested", 2, B_MS)
    doc, pointer = read_latest(intel, M)
    assert (doc.built_ms, doc.observations_until_ms, pointer.built_ms) == (B_MS, B_MS, B_MS)
    linen_intel = doc.items[LINEN]
    assert (linen_intel.cur, linen_intel.cur_qty, linen_intel.book) == (125, 10, ((125, 10, 4, 1),))
    assert doc.catalog[LINEN] == ("Linen Cloth", 1, 7, 13, None, "forever") and doc.catalog[SPAULDERS][2] == 4
    assert doc.coverage == ((A_MS // HOUR, B_MS // HOUR),) and doc.settings["default_sell_prob"] == 0.25
    assert linen_intel.recent_sold.split(",")[-1] == "4" and linen_intel.last_hour == (4.0, 0.0, 0.0)
    db = history(data, tmp_path)
    try:
        assert db.get_meta(LAST_INGESTED) == str(B_MS)
        scans = {r[0] for r in db.conn.execute("SELECT scan_id FROM scans")}
        assert scans == {f"blizzard:{M}:{A_MS}", f"blizzard:{M}:{B_MS}"}
        sold = db.conn.execute("SELECT sold FROM volume_d1 WHERE key = ? AND src = 'blizzard'", (LINEN,)).fetchone()
        assert sold == (4.0,)                                                    # the id diff, A to B
    finally:
        db.close()
    assert sorted(k.rsplit("/", 1)[1] for k in data.list(compact_prefix(M))) == [
        f"{A_MS}.json.gz", f"{B_MS}.json.gz", "index.json"]
    assert catalog.ranges == [(LINEN, LINEN + 999), (SPAULDERS, SPAULDERS + 999)]


def test_BZ56_an_ingest_run_writes_the_history_then_its_state_and_publishes_nothing(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A))
    ingest_run(data, tmp_path)
    for at_ms, envelope in ((B_MS, SNAP_B), (C_MS, SNAP_C)):
        data.put(raw_key(M, at_ms), envelope)
    order = []
    original = data.put
    data.put = lambda key, value, if_match=None: (order.append(key), original(key, value, if_match))[1]
    outcome = ingest_run(data, tmp_path)
    assert (outcome.state, outcome.ingested, outcome.pending, outcome.lag_ms) == ("ingested", 2, 0, 0)
    assert order[-2:] == [DB_KEY, STATE_KEY]                                     # the state only after the history
    assert all(k.startswith(compact_prefix(M)) for k in order[:-2])
    state = read_state(data)
    assert (state["last_ingested_at_ms"], state["newest_seen_ms"], state["pending"]) == (C_MS, C_MS, 0)
    assert (state["outcome"], state["ingested"], state["refused"], state["finished_ms"]) == ("ingested", 2, 0, NOW)
    assert read_latest(intel, M) is None                                          # the analysis publishes, not Ingest


def test_BZ57_a_backlog_drains_twelve_snapshots_a_run_and_ends_where_one_run_would(tmp_path):
    snaps = hourly_snaps(A_MS, 30)
    data, intel = stores(tmp_path / "capped", *snaps)
    runs = [ingest_run(data, tmp_path / "capped") for _ in range(3)]
    assert [(r.ingested, r.pending) for r in runs] == [(12, 18), (12, 6), (6, 0)]
    assert runs[0].lag_ms == (30 - 12) * HOUR and runs[2].lag_ms == 0
    assert read_state(data)["newest_seen_ms"] == snaps[-1][0]
    run_daily(data, intel, M, tmp_path / "capped" / "run")
    whole_data, whole_intel = stores(tmp_path / "whole", *snaps)
    cycle(whole_data, whole_intel, tmp_path / "whole", cap=None)
    assert read_latest(intel, M)[1].sha256 == read_latest(whole_intel, M)[1].sha256      # SM8: byte for byte


def test_BZ58_a_run_with_nothing_new_reads_only_the_state_and_the_listing(tmp_path):
    data, _ = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    ingest_run(data, tmp_path)
    reads = []
    original = data.get
    data.get = lambda key: (reads.append(key), original(key))[1]
    outcome = ingest_run(data, tmp_path)
    assert (outcome.state, outcome.ingested, outcome.last_ingested_at_ms) == ("up_to_date", 0, B_MS)
    assert reads == [STATE_KEY]                                                    # neither the history nor the compact files
    assert read_state(data)["outcome"] == "up_to_date"


def test_BZ58_a_lost_state_file_costs_one_full_run_and_nothing_else(tmp_path):
    data, _ = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    ingest_run(data, tmp_path)
    data.put(STATE_KEY, b"{not json")
    before = data.get(DB_KEY).etag
    assert ingest_run(data, tmp_path).state == "up_to_date"
    assert data.get(DB_KEY).etag == before and read_state(data)["newest_seen_ms"] == B_MS


def test_BZ11_a_second_cycle_with_nothing_new_republishes_the_same_baselines(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    cycle(data, intel, tmp_path)
    first = read_latest(intel, M)
    catalog = FakeCatalog()
    assert cycle(data, intel, tmp_path, catalog).state == "up_to_date" and catalog.ranges == []
    assert read_latest(intel, M)[1].sha256 == first[1].sha256                 # same snapshots, same bytes


def test_FM83_a_run_that_lost_the_race_fails_without_overwriting_or_moving_its_state(tmp_path):
    data, _ = stores(tmp_path, (A_MS, SNAP_A))
    ingest_run(data, tmp_path)
    state = read_state(data)
    data.put(raw_key(M, B_MS), SNAP_B)
    original = data.put

    def another_writer_first(key, value, if_match=None):
        if key == DB_KEY:
            original(DB_KEY, b"a local rebuild's database")
        return original(key, value, if_match)

    data.put = another_writer_first
    with pytest.raises(PreconditionFailed):
        ingest_run(data, tmp_path)
    assert data.get(DB_KEY).data == b"a local rebuild's database"            # never overwritten blind
    assert read_state(data) == state                                           # the state file not written


def test_BZ13_a_late_snapshot_is_ingested_in_time_order(tmp_path):
    data, _ = stores(tmp_path, (A_MS, SNAP_A), (C_MS, SNAP_C))
    ingest_run(data, tmp_path)
    data.put(raw_key(M, B_MS), SNAP_B)                                       # B arrives after C was ingested
    data.put(STATE_KEY, b"{}")                                               # (a real collector never writes out of order)
    assert ingest_run(data, tmp_path).ingested == 1
    db = history(data, tmp_path)
    try:
        assert db.conn.execute("SELECT COUNT(*) FROM scans").fetchone() == (3,)
    finally:
        db.close()


def test_BZ14_books_go_after_two_days_and_history_after_ninety_and_the_raw_stays(tmp_path):
    data, _ = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    ingest_run(data, tmp_path)
    later = A_MS + 3 * DAY_MS
    data.put(raw_key(M, later), shifted(SNAP_B, later))
    ingest_run(data, tmp_path)
    db = history(data, tmp_path)
    try:
        assert {r[0] for r in db.conn.execute("SELECT scan_id FROM scans")} == {f"blizzard:{M}:{later}"}
    finally:
        db.close()
    assert len(data.list(f"raw/{M}/")) == 3                                  # raw is kept forever


def test_BZ16_what_forever_lacks_comes_from_classic_era_marked_as_such(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    full = FakeCatalog().records
    catalog = FakeCatalog({LINEN: full[LINEN]}, fallback={SPAULDERS: full[SPAULDERS]})
    cycle(data, intel, tmp_path, catalog)
    doc = read_latest(intel, M)[0]
    assert doc.catalog[LINEN][5] == "forever" and doc.catalog[SPAULDERS] == ("Fortified Spaulders", 2, 4, 412, None, "classic1x")
    assert catalog.fallback_ranges == [(SPAULDERS, SPAULDERS + 999)]         # only what Forever lacked


def test_BZ16_an_item_the_search_does_not_know_is_asked_again_after_a_week(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    catalog = FakeCatalog({LINEN: FakeCatalog().records[LINEN]})             # Spaulders unknown to both searches
    cycle(data, intel, tmp_path, catalog)
    doc = read_latest(intel, M)[0]
    assert LINEN in doc.catalog and SPAULDERS not in doc.catalog and SPAULDERS in doc.items
    again = FakeCatalog()
    data.put(raw_key(M, C_MS), SNAP_C)
    ingest_run(data, tmp_path, again)
    assert again.ranges == []                                                # not asked again within the week


def test_FM74_a_refused_snapshot_is_set_aside_and_the_rest_is_ingested(tmp_path, caplog):
    bad = house(B, [], [{**linen(2455, 10), "time_left": "EXPIRED"}])
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, bad), (C_MS, SNAP_C))
    outcome = cycle(data, intel, tmp_path)
    assert (outcome.state, outcome.ingested, outcome.refused, outcome.last_ingested_at_ms) == ("ingested", 2, 1, C_MS)
    assert read_latest(intel, M)[0].built_ms == C_MS and read_state(data)["refused"] == 1
    assert raw_key(M, B_MS) in caplog.text and "unknown time_left" in caplog.text
    db = history(data, tmp_path)
    try:
        assert db.get_meta(REFUSED) == f"[{B_MS}]"
        assert {r[0] for r in db.conn.execute("SELECT scan_id FROM scans")} == {f"blizzard:{M}:{A_MS}", f"blizzard:{M}:{C_MS}"}
    finally:
        db.close()
    data.put(STATE_KEY, b"{}")                                               # force the full path: B is still set aside
    again = ingest_run(data, tmp_path)
    assert (again.state, again.ingested, again.refused) == ("up_to_date", 0, 0)


def test_FM74_a_first_batch_refused_whole_is_kept_so_the_next_run_moves_on(tmp_path):
    bad = [(A_MS + i * HOUR, house(B, [], [{**linen(2455, 10), "time_left": "EXPIRED"}])) for i in range(2)]
    bad = [(at, shifted(envelope, at)) for at, envelope in bad]
    data, _ = stores(tmp_path, *bad, (A_MS + 2 * HOUR, shifted(SNAP_B, A_MS + 2 * HOUR)))
    first = ingest_run(data, tmp_path, cap=2)
    assert (first.state, first.refused, first.pending) == ("empty", 2, 1)
    second = ingest_run(data, tmp_path, cap=2)
    assert (second.state, second.ingested, second.pending) == ("ingested", 1, 0)


def test_BZ23_the_daily_document_carries_each_items_ended_shares(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    cycle(data, intel, tmp_path)
    doc = read_latest(intel, M)[0]
    assert doc.items[SPAULDERS].expired_share == 1.0                                               # short, gone: expired
    assert doc.items[LINEN].expired_share is None                                                  # nothing ended
    db = history(data, tmp_path)
    try:
        assert db.conn.execute("SELECT ended, expired FROM lifetimes_d1 WHERE key = ?", (SPAULDERS,)).fetchall() == [(1.0, 1.0)]
    finally:
        db.close()


def test_FM72_a_damaged_database_fails_the_ingest_run(tmp_path):
    data, _ = stores(tmp_path, (A_MS, SNAP_A))
    data.put(DB_KEY, b"this is not a database" * 200)
    with pytest.raises(DatabaseError):
        ingest_run(data, tmp_path)
    assert data.get(STATE_KEY) is None


def test_FM82_a_run_that_dies_mid_batch_leaves_the_history_and_the_next_run_redoes_it(tmp_path):
    data, _ = stores(tmp_path, (A_MS, SNAP_A))
    ingest_run(data, tmp_path)
    before, state = data.get(DB_KEY).etag, read_state(data)
    data.put(raw_key(M, B_MS), SNAP_B)
    data.put(raw_key(M, C_MS), SNAP_C)

    class Timeout(Exception):
        """Lambda stops the run."""

    real = ingest.snapshot
    calls = []

    def dying(store, key, market):
        calls.append(key)
        if len(calls) == 2:
            raise Timeout()
        return real(store, key, market)

    ingest.snapshot = dying
    try:
        with pytest.raises(Timeout):
            ingest_run(data, tmp_path)
    finally:
        ingest.snapshot = real
    assert data.get(DB_KEY).etag == before and read_state(data) == state       # nothing uploaded, nothing moved
    assert ingest_run(data, tmp_path).ingested == 2                              # the same snapshots, next run


def test_raw_keys_carry_their_snapshot_time():
    assert key_time_ms(raw_key(M, B_MS)) == B_MS - B_MS % 1000


class RacingStore(LocalStore):
    """A data store where another writer uploads the database the first time the rebuild lists the raw."""

    raced = False

    def list(self, prefix: str, start_after: str = "") -> list[str]:
        if prefix.startswith("raw/") and not self.raced:
            self.raced = True
            self.put(DB_KEY, b"the scheduled run's database")
        return super().list(prefix, start_after)


def test_FM76_a_rebuild_replays_only_the_retention_window_and_never_overwrites_blind(tmp_path):
    old_ms = A_MS - 100 * DAY_MS
    data, _ = stores(tmp_path, (old_ms, shifted(SNAP_A, old_ms)), (A_MS, SNAP_A), (B_MS, SNAP_B))
    ingest_run(data, tmp_path)
    outcome = ingest_run(data, tmp_path, fresh=True)
    assert (outcome.state, outcome.ingested) == ("ingested", 2)                 # the 100-day-old snapshot is left out
    assert REBUILD_REPLAY_MS == 90 * DAY_MS + DAY_MS + 66 * HOUR   # the lookback grew from 36 h with BZ44's rate window
    racing = RacingStore(tmp_path / "data")
    with pytest.raises(PreconditionFailed):
        ingest_run(racing, tmp_path, fresh=True)
    assert racing.get(DB_KEY).data == b"the scheduled run's database"           # kept, not overwritten
    assert ingest_run(racing, tmp_path, fresh=True).state == "ingested"


def test_FM72_a_rebuild_from_raw_gives_the_byte_identical_document(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B), (C_MS, SNAP_C))
    cycle(data, intel, tmp_path)
    incremental = read_latest(intel, M)[1]
    data.put(DB_KEY, b"this is not a database" * 200)                       # the state is damaged
    outcome = cycle(data, intel, tmp_path, fresh=True)
    assert (outcome.state, outcome.ingested, outcome.rebuilding_until_ms) == ("ingested", 3, None)
    assert read_latest(intel, M)[1].sha256 == incremental.sha256              # raw is the source of truth


def test_BZ61_a_rebuild_runs_over_several_runs_and_the_analysis_waits_for_it(tmp_path, monkeypatch):
    snaps = hourly_snaps(A_MS, 9)
    data, intel = stores(tmp_path, *snaps)
    cycle(data, intel, tmp_path, cap=None)
    incremental = read_latest(intel, M)[1]
    monkeypatch.setattr(ingest, "REBUILD_SNAPSHOTS_PER_RUN", 4)
    first = ingest_run(data, tmp_path, fresh=True)
    newest = snaps[-1][0]
    assert (first.ingested, first.pending, first.rebuilding_until_ms) == (4, 5, newest)
    assert run_daily(data, intel, M, tmp_path / "run").state == "rebuilding"     # nothing published meanwhile
    assert read_latest(intel, M)[1] == incremental
    second = ingest_run(data, tmp_path)                                          # a scheduled run, at the rebuild cap
    assert (second.ingested, second.pending, second.rebuilding_until_ms) == (4, 1, newest)
    third = ingest_run(data, tmp_path)
    assert (third.ingested, third.pending, third.rebuilding_until_ms) == (1, 0, None)
    db = history(data, tmp_path)
    try:
        assert db.get_meta(REBUILDING) is None
    finally:
        db.close()
    assert run_daily(data, intel, M, tmp_path / "run").state == "published"
    assert read_latest(intel, M)[1].sha256 == incremental.sha256              # the incremental history's document


def test_the_handler_reports_its_outcome_and_the_historys_lag(tmp_path, monkeypatch):
    data = LocalStore(tmp_path / "data")
    for at_ms, envelope in hourly_snaps(A_MS, 14):
        data.put(raw_key(M, at_ms), envelope)
    for name, value in {"DATA_BUCKET": "data", "MARKET": M, "SECRET_NAME": "wowauction/blizzard-api"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(ingest.aws, "S3Store", lambda bucket: data)
    monkeypatch.setattr(ingest, "from_secret", lambda name, get: SimpleNamespace(client_id="a", secret="b"))
    monkeypatch.setattr(ingest, "BlizzardClient", lambda *args: FakeCatalog())
    monkeypatch.setattr(ingest, "WORKDIR", tmp_path / "lambda-tmp")
    metrics = []
    monkeypatch.setattr(ingest.aws, "metric", lambda name, value, dims, now: metrics.append((name, value)))
    result = ingest.handler({}, None)
    assert (result["mode"], result["state"], result["ingested"], result["pending"]) == ("scheduled", "ingested", 12, 2)
    assert metrics == [("ingest_ingested", 1), ("history_lag_minutes", 120)]
    assert ingest.handler({"mode": "rebuild"}, None)["mode"] == "rebuild"


def test_the_compact_store_keeps_every_snapshot_a_late_snapshots_recompute_reads():
    # a snapshot a day late recomputes from the day 2 h before it; that day can start a further day earlier
    worst = DAY_MS + SALES_PAIR_MAX_GAP_MS + DAY_MS + LIFETIME_LOOKBACK_MS
    assert COMPACT_DAYS * DAY_MS >= worst
