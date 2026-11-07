"""Tests for ahcloud/daily.py: the day's snapshots into history, then the baselines (SPEC §2.4, BZ10-BZ16, FM72)."""
from __future__ import annotations

import pytest

from ahcloud.daily import DB_KEY, LAST_INGESTED, REBUILD_REPLAY_MS, REFUSED, carry_state, compact_prefix, key_time_ms, run_daily
from ahcloud.hourly import run_hourly
from ahcloud.publish import read_latest
from ahcloud.store import LocalStore, PreconditionFailed
from ahsync.collect.blizzard import raw_key
from ahsync.db.sqlite import Database, DatabaseError

DAY_MS = 86_400_000
from tests.support.cloud_world import (A_MS, B, B_MS, C_MS, HOUR, LINEN, M, SNAP_A, SNAP_B, SNAP_C, SPAULDERS,
                                       FakeCatalog, house, linen)


def stores(tmp_path, *snaps):
    """A data store with raw snapshots, and an empty intel store."""
    data, intel = LocalStore(tmp_path / "data"), LocalStore(tmp_path / "intel")
    for at_ms, envelope in snaps:
        data.put(raw_key(M, at_ms), envelope)
    return data, intel


def history(data, tmp_path) -> Database:
    """The uploaded history database, opened from a copy."""
    path = tmp_path / "check.db"
    path.write_bytes(data.get(DB_KEY).data)
    return Database.open(path, 0, None)


def test_BZ10_the_daily_run_ingests_every_new_snapshot_and_publishes_the_baselines(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    catalog = FakeCatalog()
    outcome = run_daily(data, intel, catalog, M, tmp_path / "run")
    assert (outcome.state, outcome.ingested, outcome.built_ms) == ("published", 2, B_MS)
    doc, pointer = read_latest(intel, M)
    assert (doc.built_ms, doc.observations_until_ms, pointer.built_ms) == (B_MS, B_MS, B_MS)
    linen = doc.items[LINEN]
    assert (linen.cur, linen.cur_qty, linen.book) == (125, 10, ((125, 10, 4, 1),))
    assert doc.catalog[LINEN] == ("Linen Cloth", 1, 7, 13, None, "forever") and doc.catalog[SPAULDERS][2] == 4
    assert doc.coverage == ((A_MS // HOUR, B_MS // HOUR),) and doc.settings["default_sell_prob"] == 0.25
    assert linen.recent_sold.split(",")[-1] == "4" and linen.last_hour == (4.0, 0.0, 0.0)   # the state layered once
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


def test_BZ11_a_second_run_with_nothing_new_republishes_the_same_baselines(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    first = read_latest(intel, M)
    catalog = FakeCatalog()
    outcome = run_daily(data, intel, catalog, M, tmp_path / "run")
    assert (outcome.state, outcome.ingested) == ("republished", 0) and catalog.ranges == []   # catalog already known
    assert read_latest(intel, M)[1].sha256 == first[1].sha256                 # same snapshots, same bytes


def test_BZ11_a_run_that_lost_the_race_fails_without_overwriting(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A))
    run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    data.put(raw_key(M, B_MS), SNAP_B)
    original = data.put

    def another_run_wrote_first(key, value, if_match=None):
        if key == DB_KEY:
            original(DB_KEY, b"someone else's database")
        return original(key, value, if_match)

    data.put = another_run_wrote_first
    with pytest.raises(PreconditionFailed):
        run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    assert data.get(DB_KEY).data == b"someone else's database"               # never overwritten blind
    assert read_latest(intel, M)[0].built_ms == A_MS                          # nothing published


def test_BZ12_a_crash_before_the_pointer_is_repaired_by_the_next_run(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))

    class Crash(Exception):
        """The run dies after the database upload."""

    def dying(key, value, if_match=None):
        raise Crash()

    original = intel.put
    intel.put = dying
    with pytest.raises(Crash):
        run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    assert read_latest(intel, M) is None and data.get(DB_KEY) is not None
    intel.put = original
    outcome = run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    assert (outcome.state, outcome.ingested) == ("republished", 0) and read_latest(intel, M)[0].built_ms == B_MS


def test_BZ13_a_late_snapshot_is_ingested_in_time_order(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (C_MS, SNAP_C))
    run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    data.put(raw_key(M, B_MS), SNAP_B)                                       # B arrives after C was ingested
    assert run_daily(data, intel, FakeCatalog(), M, tmp_path / "run").ingested == 1
    db = history(data, tmp_path)
    try:
        assert db.conn.execute("SELECT COUNT(*) FROM scans").fetchone() == (3,)
    finally:
        db.close()


def test_BZ14_books_go_after_two_days_and_history_after_ninety_and_the_raw_stays(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    later = A_MS + 3 * 86_400_000
    data.put(raw_key(M, later), _shifted(SNAP_B, later))
    run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    db = history(data, tmp_path)
    try:
        assert {r[0] for r in db.conn.execute("SELECT scan_id FROM scans")} == {f"blizzard:{M}:{later}"}
    finally:
        db.close()
    assert len(data.list(f"raw/{M}/")) == 3                                  # raw is kept forever


def _shifted(envelope: bytes, at_ms: int) -> bytes:
    """The same snapshot under a later Last-Modified."""
    import gzip
    import json
    from email.utils import format_datetime
    from datetime import datetime, timezone
    body = json.loads(gzip.decompress(envelope))
    body["last_modified"] = format_datetime(datetime.fromtimestamp(at_ms / 1000, timezone.utc), usegmt=True)
    return gzip.compress(json.dumps(body).encode(), mtime=0)


def test_BZ16_what_forever_lacks_comes_from_classic_era_marked_as_such(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    full = FakeCatalog().records
    catalog = FakeCatalog({LINEN: full[LINEN]}, fallback={SPAULDERS: full[SPAULDERS]})
    run_daily(data, intel, catalog, M, tmp_path / "run")
    doc = read_latest(intel, M)[0]
    assert doc.catalog[LINEN][5] == "forever" and doc.catalog[SPAULDERS] == ("Fortified Spaulders", 2, 4, 412, None, "classic1x")
    assert catalog.fallback_ranges == [(SPAULDERS, SPAULDERS + 999)]         # only what Forever lacked


def test_BZ16_an_item_the_search_does_not_know_is_asked_again_after_a_week(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    catalog = FakeCatalog({LINEN: FakeCatalog().records[LINEN]})             # Spaulders unknown to both searches
    run_daily(data, intel, catalog, M, tmp_path / "run")
    doc = read_latest(intel, M)[0]
    assert LINEN in doc.catalog and SPAULDERS not in doc.catalog and SPAULDERS in doc.items
    again = FakeCatalog()
    run_daily(data, intel, again, M, tmp_path / "run")
    assert again.ranges == []                                                # not asked again within the week


def test_FM74_a_refused_snapshot_is_set_aside_and_the_rest_is_published(tmp_path, caplog):
    bad = house(B, [], [{**linen(2455, 10), "time_left": "EXPIRED"}])
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, bad), (C_MS, SNAP_C))
    outcome = run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    assert (outcome.state, outcome.ingested, outcome.refused, outcome.built_ms) == ("published", 2, 1, C_MS)
    assert read_latest(intel, M)[0].built_ms == C_MS
    assert raw_key(M, B_MS) in caplog.text and "unknown time_left" in caplog.text
    db = history(data, tmp_path)
    try:
        assert db.get_meta(REFUSED) == f"[{B_MS}]"
        assert {r[0] for r in db.conn.execute("SELECT scan_id FROM scans")} == {f"blizzard:{M}:{A_MS}", f"blizzard:{M}:{C_MS}"}
    finally:
        db.close()
    again = run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    assert (again.state, again.ingested, again.refused) == ("republished", 0, 0)       # B is not tried again


def test_BZ23_the_daily_document_carries_each_items_ended_shares(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    doc = read_latest(intel, M)[0]
    assert doc.items[SPAULDERS].expired_share == 1.0                                               # short, gone: expired
    assert doc.items[LINEN].expired_share is None                                                  # nothing ended
    db = history(data, tmp_path)
    try:
        assert db.conn.execute("SELECT ended, expired FROM lifetimes_d1 WHERE key = ?", (SPAULDERS,)).fetchall() == [(1.0, 1.0)]
    finally:
        db.close()


def test_FM72_a_damaged_database_fails_the_run(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A))
    data.put(DB_KEY, b"this is not a database" * 200)
    with pytest.raises(DatabaseError):
        run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    assert read_latest(intel, M) is None


def test_the_daily_run_keeps_the_hourly_rings_it_did_not_build(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    data.put(raw_key(M, C_MS), SNAP_C)
    run_hourly(data, intel, M)
    hourly = read_latest(intel, M)[0].items[LINEN].recent_closes
    run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")                # C is now in history too
    daily = read_latest(intel, M)[0]
    assert daily.items[LINEN].recent_closes == hourly and daily.observations_until_ms == C_MS == daily.built_ms
    assert carry_state(daily, None).observations_until_ms is None


def test_raw_keys_carry_their_snapshot_time():
    assert key_time_ms(raw_key(M, B_MS)) == B_MS - B_MS % 1000


class RacingStore(LocalStore):
    """A data store where another run uploads the database the first time the rebuild lists the raw."""

    raced = False

    def list(self, prefix: str, start_after: str = "") -> list[str]:
        if prefix.startswith("raw/") and not self.raced:
            self.raced = True
            self.put(DB_KEY, b"the daily run's database")
        return super().list(prefix, start_after)


def test_FM76_a_rebuild_replays_only_the_retention_window_and_never_overwrites_blind(tmp_path):
    old_ms = A_MS - 100 * DAY_MS
    data, intel = stores(tmp_path, (old_ms, _shifted(SNAP_A, old_ms)), (A_MS, SNAP_A), (B_MS, SNAP_B))
    run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    outcome = run_daily(data, intel, FakeCatalog(), M, tmp_path / "run", fresh=True)
    assert (outcome.state, outcome.ingested) == ("published", 2)                 # the 100-day-old snapshot is left out
    assert REBUILD_REPLAY_MS == 90 * DAY_MS + DAY_MS + 36 * HOUR
    racing = RacingStore(tmp_path / "data")
    with pytest.raises(PreconditionFailed):
        run_daily(racing, intel, FakeCatalog(), M, tmp_path / "run", fresh=True)
    assert racing.get(DB_KEY).data == b"the daily run's database"               # kept, not overwritten
    assert run_daily(racing, intel, FakeCatalog(), M, tmp_path / "run", fresh=True).state == "published"


def test_FM72_a_rebuild_from_raw_gives_the_byte_identical_document(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B), (C_MS, SNAP_C))
    run_daily(data, intel, FakeCatalog(), M, tmp_path / "run")
    incremental = read_latest(intel, M)[1]
    data.put(DB_KEY, b"this is not a database" * 200)                       # the state is damaged
    outcome = run_daily(data, intel, FakeCatalog(), M, tmp_path / "run", fresh=True)
    assert (outcome.state, outcome.ingested) == ("published", 3)
    assert read_latest(intel, M)[1].sha256 == incremental.sha256              # raw is the source of truth
