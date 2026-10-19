"""Tests for ahsync/sync/refresh.py: the full refresh end to end, and its failure modes (FM7-FM9)."""
from __future__ import annotations

import sqlite3

import pytest

from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.app import refresh
from ahsync.intel.contract import INTEL_SCHEMA_VERSION
from tests.support.fake_clock import FakeClock

M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
NOW = 20_000 * DAY + 12 * HOUR
BAR, ROD, DUST, ESSENCE, GLOVES = 2840, 6217, 10940, 10938, 4239
BRUGAN, AMAYA = "Brugan-Grommash", "Amaya-Grommash"
CFG = Config(markets=(M,))


@pytest.fixture
def db(tmp_path):
    """A database seeded with a small world: bars, rods, dust, a disenchantable green, two characters."""
    database = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    seed(database)
    yield database
    database.close()


def rows(db: Database, statement: str, *values: tuple) -> None:
    """Insert rows with one statement."""
    with db.transaction():
        db.conn.executemany(statement, values)


def seed(db: Database) -> None:
    """Items, prices, volume, characters, professions, a recipe and some stock."""
    rows(db, "INSERT INTO items(key, item_id, name, quality, ilvl, class, vendor_buy, vendor_sell, stack) "
             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
         (BAR, BAR, "Copper Bar", 1, 10, 7, None, 10, 20), (ROD, ROD, "Copper Rod", 1, 15, 7, None, 50, 20),
         (DUST, DUST, "Strange Dust", 1, 20, 7, None, 20, 20), (ESSENCE, ESSENCE, "Lesser Magic Essence", 2, 20, 7,
                                                                  None, 30, 10),
         (GLOVES, GLOVES, "Embossed Leather Gloves", 2, 25, 4, None, 100, 1))
    rows(db, "INSERT INTO current(market, key, price, qty, at, src) VALUES (?, ?, ?, ?, ?, ?)",
         (M, BAR, 1000, 6, NOW - HOUR, "ahf"), (M, ROD, 4001, 3, NOW - HOUR, "ahf"),
         (M, DUST, 601, 200, NOW - HOUR, "ahf"), (M, ESSENCE, 900, 50, NOW - HOUR, "ahf"),
         (M, GLOVES, 800, 1, NOW - 2 * HOUR, "ahf"))
    closes = [(M, key, NOW - h * HOUR, price, "ahf") for key, price in ((ROD, 4000), (DUST, 600), (BAR, 1000))
              for h in range(30)]
    rows(db, "INSERT INTO candles_h1(market, key, t, close, src) VALUES (?, ?, ?, ?, ?)", *closes)
    volume = [(M, key, NOW // DAY - d, listed, sold, 0, "ahf") for key, sold, listed in
              ((ROD, 60, 60), (DUST, 2000, 2000), (BAR, 500, 500), (ESSENCE, 300, 300)) for d in range(7)]
    rows(db, "INSERT INTO volume_d1(market, key, day, listed, sold, expired, src) VALUES (?, ?, ?, ?, ?, ?, ?)",
         *volume)
    rows(db, "INSERT INTO characters(id, name, market, gold) VALUES (?, ?, ?, ?)",
         (BRUGAN, "Brugan", M, 400_000), (AMAYA, "Amaya", M, 100_000))
    rows(db, "INSERT INTO professions(char_id, profession, skill, max_skill) VALUES (?, ?, ?, ?)",
         (BRUGAN, "Blacksmithing", 112, 150), (AMAYA, "Enchanting", 142, 150))
    rows(db, "INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) VALUES (?, ?, ?, ?, ?)",
         ("Blacksmithing", 2663, ROD, 1, 1), ("Enchanting", 7418, None, 1, 1))
    rows(db, "INSERT INTO reagents(profession, spell_id, key, count) VALUES (?, ?, ?, ?)",
         ("Blacksmithing", 2663, BAR, 2), ("Enchanting", 7418, DUST, 1))
    rows(db, "INSERT INTO known_recipes(char_id, spell_id) VALUES (?, ?)", (BRUGAN, 2663), (AMAYA, 7418))


def test_a_full_refresh_publishes_intelligence_and_both_pages(db):
    result = refresh.refresh_all(db, NOW, CFG)
    assert (result.error, result.failed, result.items) == (None, {}, 5)
    intel = db.read_intel(M)
    assert (len(intel.items), intel.built_ms, intel.schema_version) == (5, NOW, INTEL_SCHEMA_VERSION)
    craft, built = db.view("craft", M)
    assert built == NOW
    [rod] = craft["rows"]                                   # the enchant makes no item: not on the Craft tab
    assert (rod["product"], rod["crafter"], rod["source"]) == ("Copper Rod", "Brugan (Blacksmithing 112)", "AH")
    assert rod["crafts"] >= 1 and rod["mats_each"] == "20s" and rod["profit_class"] == "pos"
    disenchant, _ = db.view("disenchant", M)
    [gloves] = disenchant["rows"]
    assert (gloves["item"], gloves["buy"], gloves["price_age"]) == ("Embossed Leather Gloves", "8s", "2 h")
    assert "Strange Dust" in gloves["mats"] and gloves["chance"] != "—"


def test_stats_use_the_engine_rules(db):
    refresh.refresh_all(db, NOW, CFG)
    s = db.read_intel(M).items[ROD]
    assert (s.med7, s.post_price, s.ref_stack, s.volume_source) == (4000, 4000, 1, "ahf")
    assert s.sold_per_day == 60 and s.days_by_source == {"ahf": 7} and not s.volume_unknown
    assert 0 < s.chance <= 1 and s.sell_value is not None


def test_FM7_a_crash_mid_refresh_leaves_the_old_numbers_and_the_refresh_reruns(db, monkeypatch, tmp_path):
    refresh.refresh_all(db, NOW, CFG)
    before = db.view("craft", M)

    def crash(*_args, **_kwargs):
        """Simulate the process dying while staging."""
        raise KeyboardInterrupt
    monkeypatch.setattr(db, "stage_view", crash)
    with pytest.raises(KeyboardInterrupt):
        refresh.refresh_all(db, NOW + HOUR, CFG)
    monkeypatch.undo()
    db.close()
    reopened = Database.open(tmp_path / "ahsync.db", NOW, None)      # start-up drops the staging tables
    assert reopened.view("craft", M) == before and reopened.count("intel_new") == 0
    assert refresh.refresh_all(reopened, NOW + HOUR, CFG).error is None
    assert reopened.view("craft", M)[1] == NOW + HOUR
    reopened.close()


def test_FM8_one_failing_item_is_listed_and_the_rest_refresh(db, monkeypatch):
    from ahsync.analysis import market as analysis
    original = analysis.analyze_item

    def flaky(item, info, now_ms, settings):
        """Fail for the gloves only."""
        if item.key == GLOVES:
            raise ValueError("bad candle")
        return original(item, info, now_ms, settings)
    monkeypatch.setattr(analysis, "analyze_item", flaky)
    result = refresh.refresh_all(db, NOW, CFG)
    assert result.failed == {f"{M}:{GLOVES}": "ValueError: bad candle"} and result.items == 4
    assert db.status("refresh_items").data == {"count": 1, "items": [f"{M}:{GLOVES}"]}
    assert db.view("disenchant", M)[0]["rows"] == []
    monkeypatch.undo()
    refresh.refresh_all(db, NOW, CFG)
    assert db.status("refresh_items") is None


def test_FM9_disk_full_on_the_third_write_publishes_nothing(db, monkeypatch):
    refresh.refresh_all(db, NOW, CFG)
    before = db.view("craft", M)
    writes = {"n": 0}

    def counted(original):
        """Wrap a staging write so the third one overall fails."""
        def write(*args, **kwargs):
            """Count, and fail on the third."""
            writes["n"] += 1
            if writes["n"] == 3:
                raise sqlite3.OperationalError("database or disk is full")
            return original(*args, **kwargs)
        return write
    monkeypatch.setattr(db, "stage_intel", counted(db.stage_intel))
    monkeypatch.setattr(db, "stage_view", counted(db.stage_view))
    result = refresh.refresh_all(db, NOW + HOUR, CFG)
    assert result.error == "OperationalError: database or disk is full"
    assert db.view("craft", M) == before and db.count("intel_new") == 0
    assert db.status("refresh").kind == "refresh_failed"
    monkeypatch.undo()
    refresh.refresh_all(db, NOW + 2 * HOUR, CFG)
    assert db.status("refresh") is None


def test_unknown_volume_and_missing_mats(db):
    rows(db, "INSERT INTO items(key, item_id, name, quality, ilvl, class, vendor_sell, stack) "
             "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (777, 777, "Rare Ore", 1, 30, 7, 5, 20))
    rows(db, "INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) VALUES (?, ?, ?, ?, ?)",
         ("Blacksmithing", 9999, 778, 1, 1))
    rows(db, "INSERT INTO reagents(profession, spell_id, key, count) VALUES (?, ?, ?, ?)",
         ("Blacksmithing", 9999, 777, 3))
    rows(db, "INSERT INTO known_recipes(char_id, spell_id) VALUES (?, ?)", (BRUGAN, 9999))
    refresh.refresh_all(db, NOW, CFG)
    craft, _ = db.view("craft", M)
    assert not [r for r in craft["rows"] if r["spell"] == 9999]                  # not craftable: no row (D46)
    assert "1 lacks mats" in craft["footer"]


def test_a_refresh_publishes_the_plan_and_the_next_one_keeps_what_was_done(db):
    rows(db, "INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, ?, ?, ?, ?)", (BRUGAN, "bags", BAR, 6, 0))
    refresh.refresh_all(db, NOW, CFG)
    first = db.conn.execute("SELECT action_id, kind, status, char_id FROM actions ORDER BY action_id").fetchall()
    assert first and {r[2] for r in first} == {"pending"} and {r[3] for r in first} <= {BRUGAN, AMAYA}
    assert db.conn.execute("SELECT COUNT(*) FROM plan_new").fetchone()[0] == 0          # staging emptied
    assert all(db.view(page, M) for page in ("today", "week", "flip"))                  # the plan's pages, published
    with db.transaction():
        db.conn.execute("UPDATE actions SET status = 'done' WHERE action_id = ?", (first[0][0],))
    refresh.refresh_all(db, NOW + HOUR, CFG)
    later = db.conn.execute("SELECT action_id, status, run_at FROM actions").fetchall()
    assert (first[0][0], "done", NOW) in later                                         # history is kept
    assert all(run == NOW + HOUR for _, status, run in later if status == "pending")    # pending re-planned


def test_IU13_a_replan_follows_new_stock_from_the_published_intelligence_and_keeps_it(db, monkeypatch):
    refresh.refresh_all(db, NOW, CFG)
    intel_before = db.conn.execute("SELECT built FROM intel WHERE market = ?", (M,)).fetchone()
    assert not db.conn.execute("SELECT 1 FROM actions WHERE kind = 'post' AND key = ?", (DUST,)).fetchall()
    rows(db, "INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, ?, ?, ?, ?)", (AMAYA, "bags", DUST, 40, 0))
    monkeypatch.setattr(refresh, "analyze_market", lambda *a: pytest.fail("a re-plan reads no history"))
    assert refresh.replan_all(db, NOW + HOUR, CFG) == [M]
    sells = db.conn.execute("SELECT kind, run_at FROM actions WHERE key = ? AND char_id = ?", (DUST, AMAYA)).fetchall()
    assert sells and all(run == NOW + HOUR for _, run in sells)                     # Amaya's new dust gets a plan
    assert db.conn.execute("SELECT built FROM intel WHERE market = ?", (M,)).fetchone() == intel_before
    _, built = db.view("today", M)
    assert built == NOW + HOUR                                                      # the pages follow


def test_IU13_a_failed_replan_keeps_the_last_plan_and_a_market_without_intelligence_is_skipped(db, monkeypatch):
    assert refresh.replan_all(db, NOW, CFG) == []                                   # nothing published yet
    refresh.refresh_all(db, NOW, CFG)
    plan = db.conn.execute("SELECT action_id FROM actions ORDER BY 1").fetchall()

    def boom(*_args):
        """Fail like a full disk."""
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(refresh, "_stage_plan", boom)
    assert refresh.replan_all(db, NOW + HOUR, CFG) == []
    assert db.conn.execute("SELECT action_id FROM actions ORDER BY 1").fetchall() == plan
    assert db.conn.execute("SELECT COUNT(*) FROM plan_new").fetchone()[0] == 0


# ---------------------------------------------------------------- FI1: a crash at any commit point

def _pipeline(folder, clock) -> None:
    """One AHSync run: catch-up, a SavedVariables ingest, tracking, a full refresh and both addon files."""
    from ahsync.collect.ahf import AhfApi
    from ahsync.collect.catchup import run_catchup
    from ahsync.collect.client import ApiClient
    from ahsync.export.addon_files import export_market, export_plan
    from ahsync.recommend.tracking_pass import tracking_pass
    from ahsync.state.importer import apply_data
    from ahsync.state.savedvars import read_ahprofit
    from tests.support.builders import AhfWorld
    from tests.support.savedvars_builder import REALMS, savedvars_text

    now = clock.now_ms()
    cfg = Config(markets=(M,), wow_path=folder / "wow")
    db = Database.open(folder / "ahsync.db", now, None)
    try:
        run_catchup(AhfApi(ApiClient(AhfWorld(clock).transport, clock, "")), db, clock, (M,))
        character = {"name": "Amaya", "realm": "Grommash", "faction": "Horde", "class": "MAGE", "level": 60,
                     "gold": 100_000, "updated": now}
        buy = {"id": f"{AMAYA}:{now - HOUR}:0", "type": "purchase", "char": AMAYA, "at": now - HOUR, "key": BAR,
               "qty": 4, "price": 900, "source": "ah", "confirmed": True}
        data = read_ahprofit(savedvars_text({"characters": {AMAYA: character}, "events": [buy],
                                             "inventory": {AMAYA: {"bags": f"{BAR},4,0", "bagsAt": now}}}))
        apply_data(db, data, REALMS, now)
        tracking_pass(db)
        refresh.refresh_all(db, now, cfg)
        export_market(db, cfg, now, clock.sleep)
        export_plan(db, cfg, now, clock.sleep)
    finally:
        db.close()


def _prepare(folder, clock) -> None:
    """The world before the run: the seeded database, three hours behind AHF, and an AddOns folder."""
    from ahsync.export.addon_files import data_folder
    from tests.support.builders import AhfWorld

    db = Database.open(folder / "ahsync.db", clock.now_ms(), None)
    seed(db)
    db.update_sync_state(M, last_hour=AhfWorld(clock).sealed_h1 - 3, last_run=clock.now_ms() - 4 * HOUR)
    db.close()
    data_folder(Config(markets=(M,), wow_path=folder / "wow")).mkdir(parents=True)


def _snapshot(folder) -> tuple[dict, dict]:
    """Every table's rows, sorted, and every addon file's bytes."""
    conn = sqlite3.connect(folder / "ahsync.db")
    try:
        tables = [t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                             "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        rows_by_table = {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall(), key=repr) for t in tables}
    finally:
        conn.close()
    files = {p.name: p.read_bytes() for p in sorted((folder / "wow").rglob("*.lua"))}
    return rows_by_table, files


def test_FI1_a_crash_at_any_commit_point_then_a_restart_gives_the_same_database_and_addon_files(tmp_path, monkeypatch):
    import contextlib

    from tests.support.fake_transport import SimulatedCrash

    real = Database.transaction
    commits = {"n": 0, "crash_at": 0}

    @contextlib.contextmanager
    def counted(self):
        """A transaction that counts its commits and crashes just before the chosen one."""
        outermost = not self._depth
        with real(self):
            yield
            if outermost:
                commits["n"] += 1
                if commits["n"] == commits["crash_at"]:
                    raise SimulatedCrash(f"crash before commit {commits['n']}")

    clock = FakeClock(NOW)
    _prepare(tmp_path / "ref", clock)
    monkeypatch.setattr(Database, "transaction", counted)
    _pipeline(tmp_path / "ref", clock)
    reference, total = _snapshot(tmp_path / "ref"), commits["n"]
    assert total > 10 and reference[1]                       # the run has many commit points and writes both files
    for crash_at in range(1, total + 1):
        folder = tmp_path / f"crash{crash_at}"
        monkeypatch.setattr(Database, "transaction", real)
        _prepare(folder, clock)
        monkeypatch.setattr(Database, "transaction", counted)
        commits.update(n=0, crash_at=crash_at)
        with pytest.raises(SimulatedCrash):
            _pipeline(folder, clock)
        commits.update(n=0, crash_at=0)
        _pipeline(folder, clock)                             # the restart
        assert _snapshot(folder) == reference, f"crash before commit {crash_at}"


def _plan_rows(db: Database, *exclude: int) -> list[tuple]:
    """The live plan's actions as (char, kind, key, qty, price, slot), without run ids, for comparing plans."""
    return sorted(r for r in db.conn.execute(
        "SELECT char_id, kind, key, qty, price, slot_ms FROM actions WHERE status IN ('pending', 'late')").fetchall()
        if r[2] not in exclude)


def test_IU1_a_buy_re_plans_without_history_and_leaves_items_whose_inputs_did_not_change(db, monkeypatch):
    from ahsync.history.reads import HistoryReads
    refresh.refresh_all(db, NOW, CFG)
    before = _plan_rows(db, ESSENCE)
    rows(db, "INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, ?, ?, ?, ?)", (AMAYA, "bags", ESSENCE, 20, 0))
    for name in ("market_history", "hourly_closes", "daily_closes", "volume_rows", "newest_book"):
        monkeypatch.setattr(HistoryReads, name, lambda *a, **k: pytest.fail("a re-plan read history"))
    refresh.replan_all(db, NOW, CFG)
    assert db.conn.execute("SELECT COUNT(*) FROM actions WHERE key = ? AND status = 'pending'", (ESSENCE,)).fetchone()[0]
    assert _plan_rows(db, ESSENCE) == before                    # the other items' rows are the same


def test_IU6_your_newer_in_game_scan_reaches_the_plan_and_pages_on_the_next_re_plan(db):
    refresh.refresh_all(db, NOW, CFG)
    [rod] = db.view("craft", M)[0]["rows"]
    assert rod["mats_each"] == "20s"                            # 2 bars at 10s
    rows(db, "INSERT INTO scans(scan_id, market, src, char_id, at, kind, coverage) VALUES (?, ?, ?, ?, ?, ?, ?)",
         ("scan", M, "local", AMAYA, NOW + HOUR, "full", 1.0))
    rows(db, "INSERT INTO book_entries(scan_id, key, price, stack, band, count) VALUES (?, ?, ?, ?, ?, ?)",
         ("scan", BAR, 500, 1, 4, 6))
    rows(db, "INSERT INTO current(market, key, price, qty, at, src) VALUES (?, ?, ?, ?, ?, ?)",
         (M, BAR, 500, 6, NOW + HOUR, "local"))
    refresh.replan_all(db, NOW + 2 * HOUR, CFG)
    [rod] = db.view("craft", M)[0]["rows"]
    assert rod["mats_each"] == "10s"                            # the scan's 5s bars
    assert db.read_intel(M).items[BAR].cur == 1000              # the stored intelligence is analysis' own
