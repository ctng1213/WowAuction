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
    missing = [r for r in craft["rows"] if r["spell"] == 9999][0]
    assert (missing["crafts"], missing["source"], missing["note"]) == (0, "—", "missing: 3 Rare Ore")
    assert missing["product"] == "778"
