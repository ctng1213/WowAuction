"""Storage (SPEC §3.2, §5.2): scenarios S7, S9, FM10."""
from __future__ import annotations

import pytest

from ahsync.api.ahf import AhfApi
from ahsync.api.client import ApiClient
from ahsync.clock import DAY_MS, HOUR_MS
from ahsync.store import db as db_module
from ahsync.store.db import BookScan, Database, DatabaseError, backup_files, is_healthy
from ahsync.sync.catchup import run_catchup
from tests.support.builders import AhfWorld, copper, item_key, ms
from tests.support.fake_clock import FakeClock
from tests.support.fake_transport import DEFAULT_MARKET

MARKET = DEFAULT_MARKET
T0 = FakeClock().now_ms()


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
    assert db.get_meta("schema_version") == "1" and db.quick_check()
    db.set_meta("schema_version", "99")
    db.close()
    with pytest.raises(DatabaseError, match="newer"):
        Database.open(tmp_path / "ahsync.db", T0, None)


def test_migrations_run_step_by_step(tmp_path, db, monkeypatch):
    db.close()
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(db_module, "MIGRATIONS", {2: "CREATE TABLE extra(x INTEGER); INSERT INTO extra VALUES(1)"})
    reopened = Database.open(tmp_path / "ahsync.db", T0, None)
    assert reopened.get_meta("schema_version") == "2" and reopened.count("extra") == 1
    reopened.close()


def test_staging_tables_are_emptied_on_open(tmp_path, db):
    db.conn.execute("INSERT INTO item_stats_new(market, key, stats, updated) VALUES('m', 1, '{}', 0)")
    db.close()
    reopened = Database.open(tmp_path / "ahsync.db", T0, None)
    assert reopened.count("item_stats_new") == 0
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


def test_pruning_primitives_guard_their_tables(db):
    with pytest.raises(ValueError):
        db.delete_before("items", 0)
    db.vacuum()


# ---- SavedVariables ingest

def test_savepoint_undoes_only_the_failing_rows_writes(db):
    with db.transaction():
        db.add_vendor_reagent(2320)
        with pytest.raises(ValueError):
            with db.savepoint():
                db.add_vendor_reagent(2321)
                raise ValueError("bad row")
        db.add_vendor_reagent(2324)
    assert [r[0] for r in db.conn.execute("SELECT key FROM vendor_reagents ORDER BY key")] == [2320, 2324]


def test_add_only_writes_report_whether_the_row_was_new(db):
    assert db.add_known_recipe("A-R", 2663) and not db.add_known_recipe("A-R", 2663)
    assert db.add_vendor_reagent(2320) and not db.add_vendor_reagent(2320)


def test_replace_snapshot_applies_only_newer_snapshots_and_reports_changed_keys(db):
    assert db.replace_snapshot("A-R", "bags", 10, {(2589, 0): 20, (6948, 1): 1}) == {2589, 6948}
    assert db.replace_snapshot("A-R", "bags", 10, {(2589, 0): 5}) is None          # not newer
    assert db.replace_snapshot("A-R", "bags", 11, {(2589, 0): 20, (6948, 1): 1}) == set()
    assert db.replace_snapshot("A-R", "bags", 12, {(2589, 0): 19, (6948, 1): 1}) == {2589}
    assert db.replace_snapshot("A-R", "auctions", 5, {(2857, 4100, 5, 3): 2}) == {2857}
    assert db.conn.execute("SELECT key, qty FROM inventory").fetchall() == [(2589, 19), (6948, 1)]
    assert db.conn.execute("SELECT count, seen FROM own_auctions").fetchall() == [(2, 5)]


def test_upsert_event_rejects_other_tables_and_never_changes_unconfirmable_events(db):
    with pytest.raises(ValueError):
        db.upsert_event("items", {})
    row = {"event_id": "A-R:1000:0", "char_id": "A-R", "spell_id": 2663, "count": 1, "at": 1000}
    assert db.upsert_event("crafts", row)
    assert not db.upsert_event("crafts", row | {"count": 9})
    assert db.conn.execute("SELECT count FROM crafts").fetchall() == [(1,)]


def test_item_rule_ties_are_settled_by_rule_name_whatever_the_order(db):
    db.upsert_item_rule(1, "ignore", 5, "addon")
    assert db.upsert_item_rule(1, "keep", 5, "dashboard")
    assert not db.upsert_item_rule(1, "ignore", 5, "addon")
    assert not db.upsert_item_rule(1, "no_buy", 4, "addon")
    assert db.conn.execute("SELECT rule, set_at, source FROM item_rules").fetchall() == [("keep", 5, "dashboard")]


def test_character_market_and_recipe_item_keys_lookups(db):
    assert db.character_market("A-R") is None and db.recipe_item_keys(2663) == set()
    db.upsert_character({"id": "A-R", "name": "A", "realm": "R", "faction": "Horde", "market": MARKET,
                         "class": "MAGE", "level": 1, "gold": 0, "updated": 1})
    db.upsert_recipe({"profession": "Blacksmithing", "spell_id": 2663, "product_key": 6217, "yield_min": 1,
                      "yield_max": 1, "skill_req": None, "cooldown_s": None}, {(2840,): 2})
    assert db.character_market("A-R") == MARKET and db.recipe_item_keys(2663) == {6217, 2840}
