"""Storage (SPEC §3.2, §5.2): scenarios S7, S9, FM10."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ahsync.collect.ahf import AhfApi
from ahsync.collect.client import ApiClient
from ahsync.clock import DAY_MS, HOUR_MS
from ahsync.db import sqlite as db_module
from ahsync.db.sqlite import BookScan, Database, DatabaseError, backup_files, is_healthy
from ahsync.collect.catchup import run_catchup
from tests.support.builders import AhfWorld, copper, item_key, ms
from tests.support.fake_clock import FakeClock
from tests.support.fake_transport import DEFAULT_MARKET

MARKET = DEFAULT_MARKET
T0 = FakeClock().now_ms()
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def db(tmp_path):
    """A fresh database."""
    database = Database.open(tmp_path / "ahsync.db", T0, tmp_path / "backups")
    yield database
    database.close()


def candle(key: int, t: int, close: int | None) -> tuple:
    """One candle row whose prices all equal close."""
    return (key, t, close, close, close, close, 1)


def test_S7_cur_uses_the_newer_of_ahf_and_local_and_keeps_its_src(db):
    linen = item_key("Linen Cloth")
    db.upsert_current(MARKET, "ahf", [(linen, copper("50c"), 20, ms("T0-2h", T0))])
    db.upsert_current(MARKET, "local", [(linen, copper("48c"), 5, ms("T0-1h", T0))])
    cur = db.current_price(MARKET, linen)
    assert (cur.price, cur.qty, cur.src) == (48, 5, "local")

    db.upsert_current(MARKET, "ahf", [(linen, copper("52c"), 20, ms("T0", T0))])
    cur = db.current_price(MARKET, linen)
    assert (cur.price, cur.src, cur.at) == (52, "ahf", T0)
    assert db.current_price(MARKET, item_key("Copper Bar")) is None


def test_S9_ahf_close_50c_and_local_close_48c_in_one_hour_give_one_close_48c(db):
    linen = item_key("Linen Cloth")
    hour_ms = (T0 // HOUR_MS) * HOUR_MS
    db.upsert_candles("candles_h1", MARKET, [candle(linen, hour_ms, copper("50c"))], "ahf")
    db.upsert_candles("candles_h1", MARKET, [candle(linen, hour_ms, copper("48c"))], "local")
    db.upsert_candles("candles_h1", MARKET, [candle(linen, hour_ms - HOUR_MS, copper("51c"))], "ahf")
    db.upsert_candles("candles_h1", MARKET, [candle(linen, hour_ms - 2 * HOUR_MS, None)], "ahf")

    closes = db.hourly_closes(MARKET, linen, hour_ms - 2 * HOUR_MS, hour_ms)

    assert closes == [(hour_ms - HOUR_MS, 51), (hour_ms, 48)]


def test_FM10_quick_check_fails_on_start_restores_newest_backup_and_refetches_later_hours(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    path, backups = tmp_path / "ahsync.db", tmp_path / "backups"
    db = Database.open(path, clock.now_ms(), backups)
    run_catchup(AhfApi(ApiClient(world.transport, clock)), db, clock, (MARKET,))
    backed_up_hour = db.get_sync_state(MARKET).last_hour
    assert db.daily_backup(backups, clock.now_ms()) is not None
    world.advance(5 * HOUR_MS)
    run_catchup(AhfApi(ApiClient(world.transport, clock)), db, clock, (MARKET,))
    db.close()
    with open(path, "r+b") as handle:                   # damage the file header
        handle.write(b"\0" * 100)
    assert not is_healthy(path)

    db = Database.open(path, clock.now_ms(), backups)
    assert db.get_sync_state(MARKET).last_hour == backed_up_hour
    restore = db.status("restore")
    assert (restore.kind, restore.data) == ("restored", {"backup": "ahsync-2026-10-16.db"})
    assert list(tmp_path.glob("ahsync.db.corrupt-*"))
    world.transport.requests.clear()
    run_catchup(AhfApi(ApiClient(world.transport, clock)), db, clock, (MARKET,))
    prefix = f"candles/v1/h1/{MARKET}/"
    hours = [int(p.removeprefix(prefix).split(".")[0]) for p in world.transport.paths() if p.startswith(prefix)]
    assert hours == list(range(backed_up_hour + 1, world.sealed_h1 + 1))
    db.close()


def test_damaged_database_without_backups_starts_fresh_or_refuses(tmp_path):
    path = tmp_path / "ahsync.db"
    path.write_bytes(b"not a database at all" * 10)
    with pytest.raises(DatabaseError):
        Database.open(path, T0, None)
    db = Database.open(path, T0, tmp_path / "backups")
    assert db.status("restore").data == {"backup": None}
    db.close()


def test_backup_replaces_a_leftover_temp_file(tmp_path, db):
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "ahsync-2026-10-16.db.tmp").write_bytes(b"half a backup")
    target = db.daily_backup(backups, T0)
    assert is_healthy(target) and not list(backups.glob("*.tmp"))


def test_backups_keep_the_newest_three_one_per_day(tmp_path, db):
    backups = tmp_path / "backups"
    for day in range(5):
        db.daily_backup(backups, T0 + day * DAY_MS)
    assert db.daily_backup(backups, T0 + 4 * DAY_MS) is None
    assert [p.name for p in backup_files(backups)] == [
        "ahsync-2026-10-20.db", "ahsync-2026-10-19.db", "ahsync-2026-10-18.db"]


def test_schema_version_is_recorded_and_newer_files_are_refused(tmp_path, db):
    assert db.get_meta("schema_version") == str(db_module.SCHEMA_VERSION) and db.quick_check()
    db.set_meta("schema_version", "99")
    db.close()
    with pytest.raises(DatabaseError, match="newer"):
        Database.open(tmp_path / "ahsync.db", T0, None)


def test_migrations_run_step_by_step(tmp_path, db, monkeypatch):
    db.close()
    step = db_module.SCHEMA_VERSION + 1
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", step)
    monkeypatch.setattr(db_module, "MIGRATIONS", {**db_module.MIGRATIONS,
                                                  step: "CREATE TABLE extra(x INTEGER); INSERT INTO extra VALUES(1)"})
    reopened = Database.open(tmp_path / "ahsync.db", T0, None)
    assert reopened.get_meta("schema_version") == str(step) and reopened.count("extra") == 1
    reopened.close()


def test_staging_tables_are_emptied_on_open(tmp_path, db):
    db.conn.execute("INSERT INTO intel_new(market, schema_version, body, built) VALUES('m', 1, '{}', 0)")
    db.close()
    reopened = Database.open(tmp_path / "ahsync.db", T0, None)
    assert reopened.count("intel_new") == 0
    reopened.close()


def test_transactions_roll_back_as_a_unit_and_nest(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.set_meta("a", "1")
            with db.transaction():
                db.set_meta("b", "2")
            raise RuntimeError("fail")
    assert db.get_meta("a") is None and db.get_meta("b") is None


def test_upserts_are_idempotent(db):
    rows = [candle(1, 0, 10), candle(2, 0, 20)]
    for _ in range(2):
        db.upsert_candles("candles_d1", MARKET, rows, "ahf")
        db.store_book(BookScan("s1", MARKET, "local", "A-B", 5, "partial", 0.4), [(1, 10, 1, 3, 2)])
        db.upsert_items([(1, 1, "X", 1, 1, 1, 1, 1, 1, 1)], 5)
    assert db.count("candles_d1") == 2 and db.count("book_entries") == 1 and db.count("items") == 1
    assert db.has_scan("s1") and not db.has_scan("s2")
    db.replace_current(MARKET, "ahf", [(1, None, 0, 5)])
    db.replace_current(MARKET, "ahf", [(2, 30, 1, 6)])
    assert db.current_price(MARKET, 1) is None and db.current_price(MARKET, 2).price == 30
    with pytest.raises(ValueError):
        db.upsert_candles("items", MARKET, rows, "ahf")


def test_sync_state_status_and_failures(db):
    assert db.get_sync_state(MARKET).last_hour is None
    db.update_sync_state(MARKET, last_hour=5)
    db.update_sync_state(MARKET, last_run=9)
    state = db.get_sync_state(MARKET)
    assert (state.last_hour, state.last_run) == (5, 9)
    with pytest.raises(ValueError):
        db.update_sync_state(MARKET, bogus=1)
    db.set_status("b", "info", "two", {"n": 2}, 1)
    db.set_status("a", "warn", "one", {}, 1)
    db.set_status("a", "error", "one_again", {"hour": 5, "ranges_ms": [[0, 3_600_000]]}, 2)
    assert [(s.topic, s.level, s.kind, s.data) for s in db.statuses()] == [
        ("a", "error", "one_again", {"hour": 5, "ranges_ms": [[0, 3_600_000]]}), ("b", "info", "two", {"n": 2})]
    db.clear_status("a")
    assert [s.topic for s in db.statuses()] == ["b"] and db.status("a") is None
    assert db.record_fetch_failure(MARKET, 7, "bad", 1) == 1
    assert db.record_fetch_failure(MARKET, 7, "bad", 2) == 2
    db.clear_fetch_failure(MARKET, 7)
    assert db.fetch_failures(MARKET) == []


def test_sync_facts_for_the_status_page(db):
    assert db.sync_facts(MARKET, T0 - 7 * DAY_MS) == (None, 0, None, None, 0, 0)
    db.upsert_current(MARKET, "ahf", [(1, 50, 20, T0 - HOUR_MS), (2, None, 0, T0 - 3 * HOUR_MS)])
    db.upsert_current(MARKET, "local", [(3, 40, 5, T0)])
    db.upsert_candles("candles_h1", MARKET, [candle(1, T0 - 5 * HOUR_MS, 50), candle(1, T0 - 2 * HOUR_MS, 51)], "ahf")
    db.store_book(BookScan("s1", MARKET, "local", "A-R", T0 - DAY_MS), [])
    db.store_book(BookScan("s0", MARKET, "local", "A-R", T0 - 8 * DAY_MS), [])
    db.store_book(BookScan(f"ahf:{MARKET}:1", MARKET, "ahf", None, T0 - HOUR_MS), [])
    # newest AHF price (the local one doesn't count), 2 items priced, the hourly span, 1 recent own scan, 1 AHF book
    assert db.sync_facts(MARKET, T0 - 7 * DAY_MS) == (T0 - HOUR_MS, 2, T0 - 5 * HOUR_MS, T0 - 2 * HOUR_MS, 1, 1)


def test_pruning_primitives_guard_their_tables(db):
    with pytest.raises(ValueError):
        db.delete_before("items", 0)
    db.vacuum()


def schema_objects(conn) -> dict[str, str]:
    """Every table and index, by name, with its SQL in one normalised spacing."""
    return {name: " ".join((sql or "").replace("IF NOT EXISTS ", "").split()) for name, sql in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name")}


def test_a_version_1_database_migrates_to_exactly_the_current_schema_and_keeps_its_data(tmp_path):
    # The schema a database was created with on 2026-10-16 (commit 3158183). Every later change to schema.sql
    # needs a migration, or this test fails.
    old = sqlite3.connect(tmp_path / "old.db")
    old.executescript((ROOT / "tests" / "fixtures" / "schema_v1.sql").read_text(encoding="utf-8"))
    old.execute("INSERT OR REPLACE INTO meta(name, value) VALUES ('schema_version', '1')")
    old.execute("INSERT INTO candles_h1(market, key, t, close, src) VALUES ('m', 1, 0, 50, 'ahf')")
    old.commit()
    old.close()
    migrated = Database.open(tmp_path / "old.db", T0, None)
    fresh = Database.open(tmp_path / "fresh.db", T0, None)
    assert schema_objects(migrated.conn).keys() == schema_objects(fresh.conn).keys()
    for table in schema_objects(fresh.conn):                   # and every table has the same columns
        columns = f"PRAGMA table_info({table})"
        assert migrated.conn.execute(columns).fetchall() == fresh.conn.execute(columns).fetchall(), table
    assert migrated.get_meta("schema_version") == str(db_module.SCHEMA_VERSION)
    assert migrated.count("candles_h1") == 1
    migrated.close()
    fresh.close()

def test_V38_book_rows_move_to_integer_scan_ids_and_read_the_same(tmp_path):
    old = sqlite3.connect(tmp_path / "old.db")
    old.executescript((ROOT / "tests" / "fixtures" / "schema_v1.sql").read_text(encoding="utf-8"))
    old.execute("INSERT OR REPLACE INTO meta(name, value) VALUES ('schema_version', '1')")
    old.executemany("INSERT INTO scans(scan_id, market, src, at, kind, coverage) VALUES (?, 'm', 'local', ?, 'full', 1)",
                    [("late", 2000), ("early", 1000)])
    old.executemany("INSERT INTO book_entries(scan_id, key, price, stack, band, count) VALUES (?, ?, ?, ?, ?, ?)",
                    [("late", 2589, 50, 20, 3, 2), ("early", 2589, 45, 20, 4, 1), ("early", 2840, 500, 5, 2, 3)])
    old.commit()
    old.close()
    db = Database.open(tmp_path / "old.db", T0, None)
    assert db.conn.execute("SELECT sid, scan_id FROM scans ORDER BY sid").fetchall() == [(1, "early"), (2, "late")]
    assert db.scan_entries("early") == [(2589, 45, 20, 4, 1), (2840, 500, 5, 2, 3)]
    assert db.scan_entries("late") == [(2589, 50, 20, 3, 2)]
    db.store_book(BookScan("late", "m", "local", None, 2000), [(2589, 49, 20, 3, 1)])   # a re-read replaces in place
    assert db.scan_entries("late") == [(2589, 49, 20, 3, 1)] and db.count("scans") == 2
    db.close()


def test_schema_22_adds_positions_and_each_actions_candidate_and_keeps_the_plan(tmp_path):
    db = Database.open(tmp_path / "v21.db", T0, None)                  # a schema-21 database: before positions (M14)
    db.conn.executescript("DROP TABLE positions; DROP TABLE position_lots; ALTER TABLE actions DROP COLUMN cand_id; "
                          "ALTER TABLE plan_new DROP COLUMN cand_id; "
                          "INSERT INTO actions(action_id, kind, status) VALUES ('a1', 'buy', 'pending');")
    db.set_meta("schema_version", "21")
    db.close()
    migrated = Database.open(tmp_path / "v21.db", T0, None)
    assert migrated.get_meta("schema_version") == str(db_module.SCHEMA_VERSION)          # through 22 to the latest
    assert migrated.conn.execute("SELECT action_id, cand_id FROM actions").fetchall() == [("a1", None)]
    assert migrated.count("positions") == 0 and migrated.count("position_lots") == 0
    migrated.close()


def test_schema_23_adds_the_gathering_tables_and_keeps_the_events(tmp_path):
    db = Database.open(tmp_path / "v22.db", T0, None)                  # a schema-22 database: before Gathering (M17)
    db.conn.executescript("DROP TABLE gather_events; DROP TABLE farming_sessions; DROP TABLE farming_session_items; "
                          "DROP TABLE manual_rates; "
                          "INSERT INTO crafts(event_id, char_id, spell_id, count, at) VALUES ('e1', 'A-R', 1, 1, 5);")
    db.set_meta("schema_version", "22")
    db.close()
    migrated = Database.open(tmp_path / "v22.db", T0, None)
    assert migrated.get_meta("schema_version") == "23" and migrated.count("crafts") == 1
    assert [migrated.count(t) for t in ("gather_events", "farming_sessions", "farming_session_items",
                                        "manual_rates")] == [0, 0, 0, 0]
    indexes = {name for (name,) in migrated.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"gather_events_char_at", "farming_sessions_char"} <= indexes
    migrated.close()
