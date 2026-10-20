"""Tests for ahsync/export/addon_files.py: atomic writes, the shortlist and the export (SPEC §3.5, FM14, EX1)."""
from __future__ import annotations

import pytest

from dataclasses import replace

from ahsync.config import Config
from ahsync.db.sqlite import BookScan, Database
from ahsync.export import addon_files
from ahsync.engine.portfolio import candidate_to_json, export_subset
from ahsync.export import market_lua as market_module
from ahsync.export.portfolio_lua import candidate_text
from ahsync.intel.contract import ItemIntel, MarketIntelligence
from tests.support import mockup_export as mx
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


def test_EX14_both_addon_files_carry_export_version_4_after_schema_version_outside_the_checksum(db, tmp_path):
    now = FakeClock().now_ms()
    cfg, folder = plan_with_one_action(db, tmp_path, now)
    assert addon_files.export_market(db, cfg, now, lambda s: None) and addon_files.export_plan(db, cfg, now, lambda s: None)
    assert addon_files.EXPORT_VERSION == 4                  # 4: K, G, the portfolio (EX17, EX18); 3: closes30 (TT23)
    for name in ("MarketIntelligence.lua", "PortfolioPlan.lua"):
        header = (folder / name).read_text(encoding="utf-8").splitlines()
        at = header.index("  schemaVersion = 1,")
        assert header[at + 1] == "  exportVersion = 4,"
    plan = (folder / "PortfolioPlan.lua").read_text(encoding="utf-8")
    action = f"a1;post;{LINEN};20;55;{now};1046;;;pending;80;;;;"
    # The action string and Brugan's gold ("100," with no snapshot time): the export version counts in neither.
    assert f'checksum = "2:{len(action) + len("100,")}"' in plan
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
        f"wrote MarketIntelligence.lua: {market_bytes} bytes, generatedAt {now}, exportVersion 4, 1 items",
        f"wrote PortfolioPlan.lua: {plan_bytes} bytes, generatedAt {now}, exportVersion 4, 1 open actions"]

    caplog.clear()

    def locked(src, dst):
        """A rename that antivirus blocks."""
        raise PermissionError("locked")

    with caplog.at_level("INFO", logger="ahsync.export"):
        assert addon_files.export_market(db, cfg, now, lambda s: None, locked) is False
    assert [r.levelname for r in caplog.records] == ["WARNING"] * 3


def publish_portfolio(db: Database, now: int, rows: list | None = None) -> None:
    """Publish Linen and the mockup world's exported candidates and snapshot, with the copper chain known by Brugan.

    Args:
        db: Database.
        now: Now.
        rows: The exported candidates to stage; the mockup's 16 by default.
    """
    cands, result, adjusted = mx.selection()
    status = dict(result.reasons)
    picked = export_subset(cands, result.funded, adjusted, 150)
    staged = rows if rows is not None else [(c.cand_id, status.get(c.cand_id, ""), adjusted[c.cand_id],
                                             candidate_to_json(c)) for c in picked]
    fields = mx.portfolio_fields()
    snap = {"snapshot_ms": now, "settings": [300, 150, 50, 8, 20, 20, 7], "config_version": 7, "capital_base": 355800,
            "committed": 41800, "capacity": 64940, "allocated": 62500, "reserved": fields.reserved,
            "positions": [["stock:Brugan-G:2840", "Brugan-G", BAR, "Holding", 6, 6, 3000, 3157.0, now, "post"]]}
    sql(db, "INSERT INTO characters(id, market, gold, updated) VALUES (?, ?, ?, ?)", ("Brugan-G", M, 104000, now - HOUR))
    sql(db, "INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) VALUES (?, ?, ?, ?, ?)",
        ("Mining", 2657, BAR, 1, 1), ("Blacksmithing", 7818, ROD, 1, 1), ("Enchanting", 7421, 6218, 1, 1),
        ("Enchanting", 7795, None, 1, 1))
    sql(db, "INSERT INTO reagents(profession, spell_id, key, count) VALUES (?, ?, ?, ?)",
        ("Mining", 2657, 2770, 1), ("Blacksmithing", 7818, BAR, 1), ("Enchanting", 7421, ROD, 1),
        ("Enchanting", 7421, 10940, 1), ("Enchanting", 7795, 10940, 1))
    sql(db, "INSERT INTO known_recipes(char_id, spell_id) VALUES (?, ?)",
        *[("Brugan-G", s) for s in (2657, 7818, 7421, 7795)])
    with db.transaction():
        db.stage_intel(MarketIntelligence(M, now, now - HOUR, 1, {LINEN: item(LINEN), GLOVES: item(GLOVES)}))
        db.stage_export(M, staged, snap)
    db.publish_refresh([M])


def test_EX17_EX18_the_published_candidates_graph_and_snapshot_reach_both_files(db, tmp_path):
    now = FakeClock().now_ms()
    folder = tmp_path / "Interface" / "AddOns" / "AHProfit_Data"
    folder.mkdir(parents=True)
    publish_portfolio(db, now)
    db.upsert_items([(GLOVES, GLOVES, "Scouting Gloves", 2, 18, 4, 0, 0, 100, 1)], now)
    cfg = Config(markets=(M,), wow_path=str(tmp_path))
    assert addon_files.export_market(db, cfg, now, lambda s: None) and addon_files.export_plan(db, cfg, now, lambda s: None)
    market = (folder / "MarketIntelligence.lua").read_text(encoding="utf-8")
    assert all(f'K[{n}]="{t}"' in market for n, t in enumerate(mx.k_strings(), start=1)) and "K[17]" not in market
    assert 'R[7818]="6217:1000:2840*1"' in market and 'U[2840]="7818"' in market and "R[7795]" not in market
    assert f'G.DE="{GLOVES}"' in market and 'G.order="2770,2840,6217,10940,6218,' + str(GLOVES) + '"' in market
    assert db.status("candidate_export") is None
    plan = (folder / "PortfolioPlan.lua").read_text(encoding="utf-8")
    assert f'["{M}"] = "{now};300;150;50;8;20;20;7;7;355800;41800;64940;62500",' in plan
    assert f'["Brugan-G"] = "104000,{now - HOUR}",' in plan and '["Amaya-Grommash"] = 25200,' in plan
    assert f'"stock:Brugan-G:2840;{BAR};Holding;6;6;3000;3157;{now};post",' in plan


def test_FM40_the_export_drops_the_lowest_alternatives_until_the_file_fits_2_MB():
    cands, result, adjusted = mx.selection()
    status = dict(result.reasons)
    base = [(candidate_text(c, status.get(c.cand_id, "")), status.get(c.cand_id, ""), adjusted[c.cand_id])
            for c in export_subset(cands, result.funded, adjusted, 16)]
    extra = [(candidate_text(replace(cands[0], key=20_000 + i), "Exceeds risk tolerance (needs 1s, 0c left)"),
              "Exceeds risk tolerance (needs 1s, 0c left)", 70.0 - i * 0.25) for i in range(134)]
    rows = {M: base + extra}                                         # 12 funded, then 138 alternatives: 150
    intel = MarketIntelligence(M, mx.NOW, None, 1, {})

    def build(pad: int):
        """The file built with a character name of `pad` letters: padding outside the checksum."""
        return lambda kept: market_module.market_lua([intel], {}, {"x" * pad: M}, mx.NOW, candidates=kept,
                                                     graphs={M: mx.graph()})
    limit = addon_files.MAX_MARKET_BYTES
    unpadded = build(0)({M: [t for t, _s, _a in rows[M][:112]]})
    pad = limit - len(unpadded.encode("utf-8"))
    full = build(pad)({M: [t for t, _s, _a in rows[M]]})
    assert 2.0 * 1024 * 1024 < len(full) < 2.11 * 1024 * 1024           # "would be 2.1 MB"
    text, kept = addon_files.fit_market(build(pad), rows, limit)
    assert len(kept[M]) == 112 and len(text.encode("utf-8")) <= limit
    assert kept[M] == [t for t, _s, _a in rows[M][:112]]               # the 38 lowest alternatives went, no funded row
    over, kept_over = addon_files.fit_market(build(limit), rows, limit)  # the funded rows alone don't fit
    assert kept_over[M] == [t for t, _s, _a in rows[M][:12]] and len(over.encode("utf-8")) > limit


def test_FM40_the_trimmed_file_is_written_with_a_status_line_and_over_budget_with_its_warning(db, tmp_path, monkeypatch):
    now = FakeClock().now_ms()
    (tmp_path / "Interface" / "AddOns" / "AHProfit_Data").mkdir(parents=True)
    publish_portfolio(db, now)
    cfg = Config(markets=(M,), wow_path=str(tmp_path))
    assert addon_files.export_market(db, cfg, now, lambda s: None)
    full = (tmp_path / "Interface" / "AddOns" / "AHProfit_Data" / "MarketIntelligence.lua").stat().st_size
    lowest = mx.k_strings()[-2:]                                       # Grunt's Bracers, Lesser Mana Potion
    monkeypatch.setattr(addon_files, "MAX_MARKET_BYTES", full - 1)
    assert addon_files.export_market(db, cfg, now, lambda s: None)
    text = (tmp_path / "Interface" / "AddOns" / "AHProfit_Data" / "MarketIntelligence.lua").read_text(encoding="utf-8")
    assert lowest[0] in text and lowest[1] not in text
    line = db.status("candidate_export")
    assert (line.kind, line.data["kept"], line.data["total"]) == ("candidate_export_trimmed", 15, 16)
    assert db.status("addon_file_size:MarketIntelligence.lua") is None
    monkeypatch.setattr(addon_files, "MAX_MARKET_BYTES", 100)
    assert addon_files.export_market(db, cfg, now, lambda s: None)
    text = (tmp_path / "Interface" / "AddOns" / "AHProfit_Data" / "MarketIntelligence.lua").read_text(encoding="utf-8")
    assert all(t in text for t in mx.k_strings()[:12]) and "K[13]" not in text
    assert db.status("candidate_export").data["kept"] == 12
    assert db.status("addon_file_size:MarketIntelligence.lua").data["limit"] == 100


def test_a_recipe_loop_is_cut_in_the_exported_graph_and_logged(db, tmp_path, caplog):
    now = FakeClock().now_ms()
    (tmp_path / "Interface" / "AddOns" / "AHProfit_Data").mkdir(parents=True)
    publish_portfolio(db, now, rows=[])
    sql(db, "INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) VALUES (?, ?, ?, ?, ?)",
        ("Mining", 9001, 2770, 1, 1))
    sql(db, "INSERT INTO reagents(profession, spell_id, key, count) VALUES (?, ?, ?, ?)", ("Mining", 9001, BAR, 1))
    sql(db, "INSERT INTO known_recipes(char_id, spell_id) VALUES (?, ?)", ("Brugan-G", 9001))
    with caplog.at_level("INFO", logger="ahsync.export"):
        assert addon_files.export_market(db, Config(markets=(M,), wow_path=str(tmp_path)), now, lambda s: None)
    assert f"recipe loop on {M}: item 2770 is not followed back into spell 2657 (C13)" in caplog.text
    text = (tmp_path / "Interface" / "AddOns" / "AHProfit_Data" / "MarketIntelligence.lua").read_text(encoding="utf-8")
    assert 'U[2770]="2657"' not in text and 'U[2840]="7818,9001"' in text and "K[1]" not in text
