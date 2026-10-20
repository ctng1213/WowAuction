"""Tests for ahsync/export/addon_files.py: atomic writes, the shortlist and the export (SPEC §3.5, FM14, EX1)."""
from __future__ import annotations

import pytest

from ahsync.config import Config
from ahsync.db.sqlite import BookScan, Database
from ahsync.export import addon_files
from ahsync.export import market_lua as market_module
from ahsync.intel.contract import ItemIntel, MarketIntelligence
from tests.support.fake_clock import FakeClock

HOUR, DAY = 3_600_000, 86_400_000
M = "forever.pvp.horde.us"
BAR, ROD, LINEN, GLOVES, SILK, WOOL, ORE = 2840, 6217, 2589, 6585, 4306, 2592, 2771


@pytest.fixture
def db(tmp_path):
    """A fresh database."""
    database = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    yield database
    database.close()


def sql(db: Database, statement: str, *rows: tuple) -> None:
    """Insert rows with one statement."""
    with db.transaction():
        db.conn.executemany(statement, rows)


def item(key: int, cur: int | None = 45) -> ItemIntel:
    """An item's intelligence with a price."""
    return ItemIntel(key, cur, 20, HOUR, 49, 49, 40, 60, 0.0, 48.0, 0.0, 48.0, "ahf", {"ahf": 7}, 45, 20, 0.9, False,
                     42.0, 86, 93, None, ())


def publish(db: Database, now: int, *keys: int) -> None:
    """Publish intelligence for these items, as a full refresh does."""
    with db.transaction():
        db.stage_intel(MarketIntelligence(M, now, now - HOUR, 1, {k: item(k) for k in keys}))
        db.stage_view("disenchant", M, {"rows": [{"key": GLOVES, "item": "Scouting Gloves"}, {"item": "no key"}]}, now)
    db.publish_refresh([M])


def test_EX1_the_shortlist_is_holdings_recipes_plan_lists_and_recent_targeted_scans(db):
    now = FakeClock().now_ms()
    sql(db, "INSERT INTO characters(id, market, gold) VALUES (?, ?, ?)", ("Brugan-G", M, 100))
    sql(db, "INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, ?, ?, ?, ?)", ("Brugan-G", "bags", BAR, 12, 0))
    sql(db, "INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) VALUES (?, ?, ?, ?, ?)",
        ("Blacksmithing", 2663, ROD, 1, None), ("Enchanting", 7418, None, 1, None))
    sql(db, "INSERT INTO reagents(profession, spell_id, key, count) VALUES (?, ?, ?, ?)", ("Blacksmithing", 2663, BAR, 2))
    sql(db, "INSERT INTO known_recipes(char_id, spell_id) VALUES (?, ?)", ("Brugan-G", 2663), ("Brugan-G", 7418))
    sql(db, "INSERT INTO actions(action_id, char_id, kind, key, qty, slot_ms, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("a1", "Brugan-G", "post", LINEN, 20, now, "pending"), ("a0", "Brugan-G", "post", WOOL, 5, now, "done"))
    db.store_book(BookScan("p1", M, "local", "Brugan-G", now - 2 * DAY, "partial", 0.1), [(SILK, 70, 20, 3, 1)])
    db.store_book(BookScan("f1", M, "local", "Brugan-G", now - DAY, "full"), [(WOOL, 30, 20, 3, 1)])
    db.store_book(BookScan("p0", M, "local", "Brugan-G", now - 9 * DAY, "partial", 0.1), [(ORE, 50, 20, 3, 1)])
    assert addon_files.shortlist(db, M, now) == {BAR, ROD, LINEN, SILK}      # no Disenchant page yet
    publish(db, now, LINEN)                  # the refresh's plan replaces the open actions: Linen's post is gone
    assert addon_files.shortlist(db, M, now) == {BAR, ROD, GLOVES, SILK}


def test_FM14_a_rename_blocked_three_times_keeps_the_old_file_and_warns(db, tmp_path):
    now = FakeClock().now_ms()
    folder = tmp_path / "wow" / "Interface" / "AddOns" / "AHProfit_Data"
    folder.mkdir(parents=True)
    (folder / "MarketIntelligence.lua").write_text("-- old", encoding="utf-8")
    publish(db, now, LINEN)
    cfg, waits = Config(markets=(M,), wow_path=str(tmp_path / "wow")), []

    def locked(src, dst):
        """A rename that antivirus blocks."""
        raise PermissionError("locked")

    assert addon_files.export_market(db, cfg, now, waits.append, locked) is False
    assert waits == [5, 5] and (folder / "MarketIntelligence.lua").read_text(encoding="utf-8") == "-- old"
    assert not (folder / "MarketIntelligence.lua.tmp").exists()
    assert db.status("addon_file:MarketIntelligence.lua").kind == "addon_file_blocked"

    assert addon_files.export_market(db, cfg, now, waits.append) is True             # the next refresh succeeds
    assert "AHProfitMarket = {" in (folder / "MarketIntelligence.lua").read_text(encoding="utf-8")
    assert db.status("addon_file:MarketIntelligence.lua") is None and db.status("addon_file_size:MarketIntelligence.lua") is None


def test_a_temp_file_that_reads_back_wrong_is_not_renamed(tmp_path, monkeypatch):
    target = tmp_path / "x.lua"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(addon_files.Path, "read_bytes", lambda self: b"garbled")
    assert addon_files.write_atomic(target, "new", lambda s: None) is False
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "old"


def test_nothing_is_written_without_intelligence_or_an_addons_folder(db, tmp_path, caplog):
    now = FakeClock().now_ms()
    cfg = Config(markets=(M,), wow_path=str(tmp_path / "nowow"))
    assert addon_files.export_market(db, cfg, now, lambda s: None) is False         # no refresh yet
    publish(db, now, LINEN)
    assert addon_files.export_market(db, cfg, now, lambda s: None) is False and "run tools/deploy.py" in caplog.text


def test_a_file_over_its_budget_is_written_with_a_warning(db, tmp_path, monkeypatch):
    now = FakeClock().now_ms()
    (tmp_path / "Interface" / "AddOns" / "AHProfit_Data").mkdir(parents=True)
    publish(db, now, LINEN)
    monkeypatch.setattr(addon_files, "MAX_MARKET_BYTES", 10)
    assert addon_files.export_market(db, Config(markets=(M,), wow_path=str(tmp_path)), now, lambda s: None) is True
    assert db.status("addon_file_size:MarketIntelligence.lua").data["limit"] == 10


def test_the_plan_file_holds_open_actions_rules_and_ingested_until(db, tmp_path):
    now = FakeClock().now_ms()
    folder = tmp_path / "Interface" / "AddOns" / "AHProfit_Data"
    cfg = Config(markets=(M,), wow_path=str(tmp_path))
    assert addon_files.export_plan(db, cfg, now, lambda s: None) is False             # no AddOns folder yet
    folder.mkdir(parents=True)
    sql(db, "INSERT INTO characters(id, market, gold) VALUES (?, ?, ?)", ("Brugan-G", M, 100), ("Other-X", None, 1))
    sql(db, "INSERT INTO actions(action_id, char_id, kind, key, qty, price, slot_ms, expected_net, status, conf) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("a1", "Brugan-G", "post", LINEN, 20, 55, now, 1045.5, "pending", 0.8),
        ("a2", "Brugan-G", "buy", SILK, 5, 70, now - DAY, 10.0, "missed", None),
        ("a3", "Other-X", "post", WOOL, 1, 1, now, 1.0, "pending", 1.0))
    sql(db, "INSERT INTO item_rules(key, rule, set_at, source) VALUES (?, ?, ?, ?)", (LINEN, "none", now, "dashboard"))
    db.set_meta("savedvars_ingested_until:Brugan-G", str(now - HOUR))
    assert addon_files.export_plan(db, cfg, now, lambda s: None) is True
    text = (folder / "PortfolioPlan.lua").read_text(encoding="utf-8")
    assert f'"a1;post;{LINEN};20;55;{now};1046;;;pending;80;;;;"' in text and "a2;" not in text and "a3;" not in text
    assert f'[{LINEN}] = "none,{now}"' in text and f'["Brugan-G"] = {now - HOUR},' in text
    assert db.addon_actions([]) == []


def test_A23_disenchantable_items_from_item_metadata(db):
    db.upsert_items([(6585, 6585, "Scouting Gloves", 2, 18, 4, 0, 0, 100, 1), (2589, 2589, "Linen Cloth", 1, 5, 7, 0, 0, 10, 20),
                     (9000, 9000, "Odd Sword", 2, 18, 2, 0, 0, 100, 1), (9001, 9001, "No Level", 2, None, 4, 0, 0, 1, 1)],
                    FakeClock().now_ms())
    assert addon_files.disenchantable(db) == {6585: "1,2,18,a", 9000: "1,2,18,w"}


def test_EX6_vendor_prices_of_vendor_sold_items_only(db):
    # (key, item id, name, quality, ilvl, class, subclass, vendor buy, vendor sell, stack)
    db.upsert_items([(4470, 4470, "Simple Wood", 1, 5, 7, 0, 38, 9, 20), (2320, 2320, "Coarse Thread", 1, 5, 7, 0, 10, 2, 20),
                     (2589, 2589, "Linen Cloth", 1, 5, 7, 0, 0, 10, 20), (2880, 2880, "Weak Flux", 1, 5, 7, 0, None, 0, 20)],
                    FakeClock().now_ms())
    with db.transaction():
        for key in (4470, 2320, 2880, 1):            # Weak Flux: no known price; 1: no item row
            db.add_vendor_reagent(key)
    assert addon_files.vendor_prices(db) == {2320: 10, 4470: 38}



def plan_with_one_action(db: Database, tmp_path, now: int):
    """A WoW folder and a database with Linen published and Brugan's one open post; returns (cfg, folder)."""
    folder = tmp_path / "Interface" / "AddOns" / "AHProfit_Data"
    folder.mkdir(parents=True)
    publish(db, now, LINEN)
    sql(db, "INSERT INTO characters(id, market, gold) VALUES (?, ?, ?)", ("Brugan-G", M, 100))
    sql(db, "INSERT INTO actions(action_id, char_id, kind, key, qty, price, slot_ms, expected_net, status, conf) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("a1", "Brugan-G", "post", LINEN, 20, 55, now, 1045.5, "pending", 0.8))
    return Config(markets=(M,), wow_path=str(tmp_path)), folder


def test_EX14_both_addon_files_carry_export_version_2_after_schema_version_outside_the_checksum(db, tmp_path):
    now = FakeClock().now_ms()
    cfg, folder = plan_with_one_action(db, tmp_path, now)
    assert addon_files.export_market(db, cfg, now, lambda s: None) and addon_files.export_plan(db, cfg, now, lambda s: None)
    assert addon_files.EXPORT_VERSION == 3                              # 3: closes30 (TT23); 2: a recipient (EX16)
    for name in ("MarketIntelligence.lua", "PortfolioPlan.lua"):
        header = (folder / name).read_text(encoding="utf-8").splitlines()
        at = header.index("  schemaVersion = 1,")
        assert header[at + 1] == "  exportVersion = 3,"
    plan = (folder / "PortfolioPlan.lua").read_text(encoding="utf-8")
    action = f"a1;post;{LINEN};20;55;{now};1046;;;pending;80;;;;"
    assert f'checksum = "1:{len(action)}"' in plan                       # the action string only
    market = (folder / "MarketIntelligence.lua").read_text(encoding="utf-8")
    linen = market_module.item_text(item(LINEN))
    assert f'checksum = "1:{len(linen)}"' in market                       # Linen's item string only


def test_EX15_each_written_addon_file_is_logged_with_its_size_time_and_contents(db, tmp_path, caplog):
    now = FakeClock().now_ms()
    cfg, folder = plan_with_one_action(db, tmp_path, now)
    with caplog.at_level("INFO", logger="ahsync.export"):
        addon_files.export_market(db, cfg, now, lambda s: None)
        addon_files.export_plan(db, cfg, now, lambda s: None)
    market_bytes = (folder / "MarketIntelligence.lua").stat().st_size
    plan_bytes = (folder / "PortfolioPlan.lua").stat().st_size
    assert [r.getMessage() for r in caplog.records if r.levelname == "INFO"] == [
        f"wrote MarketIntelligence.lua: {market_bytes} bytes, generatedAt {now}, exportVersion 3, 1 items",
        f"wrote PortfolioPlan.lua: {plan_bytes} bytes, generatedAt {now}, exportVersion 3, 1 open actions"]

    caplog.clear()

    def locked(src, dst):
        """A rename that antivirus blocks."""
        raise PermissionError("locked")

    with caplog.at_level("INFO", logger="ahsync.export"):
        assert addon_files.export_market(db, cfg, now, lambda s: None, locked) is False
    assert [r.levelname for r in caplog.records] == ["WARNING"] * 3
