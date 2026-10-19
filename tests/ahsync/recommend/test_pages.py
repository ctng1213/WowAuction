"""Tests for ahsync/recommend/pages.py: pages from intelligence plus state only (AR3)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel
from ahsync.recommend.pages import craft_page, disenchant_page
from tests.support.fake_clock import FakeClock

M = "forever.pvp.horde.us"
NOW = 20_000 * 86_400_000
BAR, ROD, DUST, GLOVES = 2840, 6217, 10940, 4239
ITEMS = {BAR: ItemInfo(BAR, "Copper Bar", 1, 10, 7, None, 10, 20), ROD: ItemInfo(ROD, "Copper Rod", 1, 15, 7, None, 50, 20),
         DUST: ItemInfo(DUST, "Strange Dust", 1, 20, 7, None, 20, 20),
         GLOVES: ItemInfo(GLOVES, "Embossed Leather Gloves", 2, 25, 4, None, 100, 1)}


def intel(key: int, cur: int, qty: int, sold: float | None, sell_value: float | None) -> ItemIntel:
    """A minimal item intelligence record."""
    return ItemIntel(key, cur, qty, 3_600_000, cur, cur, cur, cur, 0.0, sold, 0.0 if sold else None, sold,
                     "ahf" if sold else None, {"ahf": 7} if sold else {}, cur, 1, 1.0 if sold else 0.25,
                     sold is None, sell_value, 80, qty, None, ())


STATS = {BAR: intel(BAR, 1000, 6, 500.0, 950.0), ROD: intel(ROD, 4001, 3, 60.0, 3800.0),
         DUST: intel(DUST, 601, 200, 2000.0, 570.0), GLOVES: intel(GLOVES, 800, 1, None, None)}


@pytest.fixture
def state(tmp_path):
    """Character state: Brugan knows Copper Rod; Amaya has Enchanting 142."""
    db = Database.open(tmp_path / "s.db", FakeClock().now_ms(), None)
    with db.transaction():
        db.conn.executemany("INSERT INTO characters(id, market, gold) VALUES (?, ?, ?)",
                            [("Brugan-G", M, 400_000), ("Amaya-G", M, 100_000)])
        db.conn.executemany("INSERT INTO professions(char_id, profession, skill, max_skill) VALUES (?, ?, ?, ?)",
                            [("Brugan-G", "Blacksmithing", 112, 150), ("Amaya-G", "Enchanting", 142, 150)])
        db.conn.execute("INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) "
                        "VALUES ('Blacksmithing', 2663, ?, 1, 1)", (ROD,))
        db.conn.execute("INSERT INTO reagents(profession, spell_id, key, count) VALUES ('Blacksmithing', 2663, ?, 2)", (BAR,))
        db.conn.execute("INSERT INTO known_recipes(char_id, spell_id) VALUES ('Brugan-G', 2663)")
    yield db
    db.close()


def test_AR3_pages_use_the_published_intelligence_not_history(state):
    before = craft_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 500_000, NOW, Config(markets=(M,)))
    with state.transaction():                           # history moves on: bars get dear
        state.conn.execute("INSERT INTO current(market, key, price, qty, at, src) VALUES (?, ?, 99999, 99, ?, 'ahf')",
                           (M, BAR, NOW))
    after = craft_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 500_000, NOW, Config(markets=(M,)))
    assert after == before
    [rod] = before["rows"]
    assert (rod["product"], rod["source"], rod["mats_each"]) == ("Copper Rod", "AH", "20s")


def test_disenchant_page_from_intelligence(state):
    page = disenchant_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 500_000, NOW, Config(markets=(M,)))
    [gloves] = page["rows"]
    assert (gloves["item"], gloves["buy"]) == ("Embossed Leather Gloves", "8s")


def test_a_disenchant_buy_that_risks_too_much_of_your_gold_is_left_out(state):
    page = disenchant_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 2_000, NOW, Config(markets=(M,)))
    assert (page["rows"], page["hidden"]) == ([], 1)     # the 8s gloves are over 25% of 20s


def test_FM5_a_suspect_price_is_no_mat_source_and_no_disenchant_buy(state):
    suspect = {**STATS, BAR: replace(STATS[BAR], cur_suspect=True), GLOVES: replace(STATS[GLOVES], cur_suspect=True)}
    crafts = craft_page(state, ["Amaya-G", "Brugan-G"], suspect, ITEMS, 500_000, NOW, Config(markets=(M,)))
    assert all(r["source"] != "AH" for r in crafts["rows"])                    # the bars' book is not used
    assert disenchant_page(state, ["Amaya-G", "Brugan-G"], suspect, ITEMS, 500_000, NOW, Config(markets=(M,)))["rows"] == []


def test_D45_crafts_stop_at_what_the_market_takes_less_what_you_have_and_within_the_risk_limit(state):
    cfg = Config(markets=(M,))
    [rod] = craft_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 500_000, NOW, cfg)["rows"]
    assert rod["crafts"] == 1                                    # others list 3 rods: half of that, at least a stack
    with state.transaction():
        state.conn.execute("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES ('Brugan-G', 'bags', ?, 1, 0)", (ROD,))
    page = craft_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 500_000, NOW, cfg)
    assert (page["rows"], page["footer"]) == ([], "0 recipes worth crafting · 1 not shown: 1 already held or listed")
    with state.transaction():
        state.conn.execute("DELETE FROM inventory")
    page = craft_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 4_000, NOW, cfg)   # 20s of bars is over 25% of 40s
    assert (page["rows"], page["footer"]) == ([], "0 recipes worth crafting · 1 not shown: 1 over your risk limit")


def test_D46_only_recipes_worth_crafting_are_rows_and_the_footer_says_why_the_rest_are_not():
    from ahsync.engine.views import craft_footer
    assert craft_footer(1, {"pay": 1, "mats": 1, "risk": 1, "held": 1}) == (
        "1 recipe worth crafting · 4 not shown: 1 doesn't pay now · 1 lacks mats · 1 over your risk limit · "
        "1 already held or listed")
    assert craft_footer(2, {"pay": 3}) == "2 recipes worth crafting · 3 not shown: 3 don't pay now"
    assert craft_footer(2, {}) == "2 recipes worth crafting"


def test_D44_owned_mats_on_another_character_are_mailed_to_the_crafter(state):
    with state.transaction():
        state.conn.execute("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES ('Amaya-G', 'bags', ?, 2, 0)", (BAR,))
    [rod] = craft_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 500_000, NOW, Config(markets=(M,)))["rows"]
    assert (rod["lines"], rod["crafter"]) == (["use 2 owned Copper Bar · mail 2 from Amaya"], "Brugan (Blacksmithing 112)")
