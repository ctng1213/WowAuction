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


def test_a_disenchant_buy_bigger_than_the_remaining_capacity_is_left_out(state):
    page = disenchant_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 500_000, NOW, Config(markets=(M,)), 600)
    assert (page["rows"], page["hidden"]) == ([], 1)     # the 8s gloves are over the 6s of capacity left


def test_FM5_a_suspect_price_is_no_mat_source_and_no_disenchant_buy(state):
    suspect = {**STATS, BAR: replace(STATS[BAR], cur_suspect=True), GLOVES: replace(STATS[GLOVES], cur_suspect=True)}
    crafts = craft_page(state, ["Amaya-G", "Brugan-G"], suspect, ITEMS, 500_000, NOW, Config(markets=(M,)))
    assert all(r["source"] != "AH" for r in crafts["rows"])                    # the bars' book is not used
    assert disenchant_page(state, ["Amaya-G", "Brugan-G"], suspect, ITEMS, 500_000, NOW, Config(markets=(M,)))["rows"] == []


def test_D45_crafts_stop_at_what_the_market_takes_less_what_you_have_and_within_the_exposure_budget(state):
    cfg = Config(markets=(M,))
    [rod] = craft_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 500_000, NOW, cfg)["rows"]
    assert rod["crafts"] == 1                                    # others list 3 rods: half of that, at least a stack
    with state.transaction():
        state.conn.execute("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES ('Brugan-G', 'bags', ?, 1, 0)", (ROD,))
    page = craft_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 500_000, NOW, cfg)
    assert (page["rows"], page["footer"]) == ([], "0 recipes worth crafting · 1 not shown: 1 already held or listed")
    with state.transaction():
        state.conn.execute("DELETE FROM inventory")
    # 40s of gold, nothing committed: risk tolerance 30% leaves 12s of capacity, less than the 20s of bars.
    page = craft_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 4_000, NOW, cfg, room=1_200)
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


def test_D24_a_craft_row_using_mats_from_old_bags_says_how_old(state):
    with state.transaction():
        state.conn.execute("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES ('Brugan-G', 'bags', ?, 2, 0)", (BAR,))
        state.conn.execute("INSERT INTO snapshots(char_id, location, at) VALUES ('Brugan-G', 'bags', ?)", (NOW - 3 * 86_400_000,))
    [rod] = craft_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 500_000, NOW, Config(markets=(M,)))["rows"]
    assert rod["note"] == "based on Brugan's bags from 3 d ago"
    with state.transaction():
        state.conn.execute("UPDATE snapshots SET at = ?", (NOW - 3_600_000,))
    [rod] = craft_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 500_000, NOW, Config(markets=(M,)))["rows"]
    assert rod["note"] == ""


def test_V23_with_no_current_supply_the_craft_cap_uses_the_median_daily_supply(state):
    cfg = Config(markets=(M,))
    unseen = {**STATS, ROD: replace(STATS[ROD], units_on_ah=None)}
    [rod] = craft_page(state, ["Amaya-G", "Brugan-G"], unseen, ITEMS, 500_000, NOW, cfg)["rows"]
    assert rod["crafts"] == 1                                    # no supply known: one stack
    typical = {**STATS, ROD: replace(STATS[ROD], units_on_ah=None, median_daily_supply=4)}
    [rod] = craft_page(state, ["Amaya-G", "Brugan-G"], typical, ITEMS, 500_000, NOW, cfg)["rows"]
    assert rod["crafts"] == 2                                    # half of the usual 4 listed


def test_D56_the_plans_craft_chains_come_first_and_their_products_get_no_own_row(state):
    from ahsync.engine.plan import PlanAction
    chain = [PlanAction("buy", "Brugan-G", BAR, 2, 1000, NOW, None, None, None, None, 0.0, 0.0, chain="craft:2663",
                        source="ah"),
             PlanAction("craft", "Brugan-G", ROD, 1, None, NOW, None, None, None, None, 3600.0, 1600.0,
                            chain="craft:2663"),                   # carries the profit: 36s - 20s (D59)
             PlanAction("post", "Brugan-G", ROD, 1, 4000, NOW, None, None, None, 0.9, 3600.0, 3600.0,
                        chain="craft:2663")]
    page = craft_page(state, ["Amaya-G", "Brugan-G"], STATS, ITEMS, 500_000, NOW, Config(markets=(M,)), chain, 0)
    [rod] = page["rows"]                                          # the plan's: no own Copper Rod row besides it
    assert (rod["product"], rod["source"], rod["crafter"], rod["profit"]) == (
        "Copper Rod", "Plan", "Brugan (Blacksmithing 112)", "+16s")
    assert rod["lines"] == ["buy 2 Copper Bar on the AH · 20s", "post Fri 00:00"]   # day 20000 is a Friday
    assert page["footer"] == "1 planned · 0 recipes worth crafting"


def test_D71_with_no_enchanter_every_disenchantable_item_is_listed_as_research_with_its_calculations(state):
    scouting, greater_magic = 7005, 10939
    items = {**ITEMS, scouting: ItemInfo(scouting, "Scouting Gloves of the Monkey", 2, 18, 4, None, 100, 1),
             greater_magic: ItemInfo(greater_magic, "Greater Magic Essence", 2, 25, 7, None, 100, 10)}
    stats = {DUST: replace(STATS[DUST], sell_value=600.0), greater_magic: intel(greater_magic, 1100, 20, 50.0, 1000.0),
             scouting: intel(scouting, 800, 1, None, None)}
    with_enchanter = disenchant_page(state, ["Amaya-G", "Brugan-G"], stats, items, 500_000, NOW, Config(markets=(M,)))
    with state.transaction():
        state.conn.execute("DELETE FROM professions WHERE profession = 'Enchanting'")
    page = disenchant_page(state, ["Amaya-G", "Brugan-G"], stats, items, 500_000, NOW, Config(markets=(M,)))
    [gloves] = page["rows"]
    # EV = 0.75 × 2.5 × 6s + 0.2 × 1.5 × 10s = 14s 25c; bought at 8s.
    assert (gloves["item"], gloves["buy"], gloves["ev"], gloves["profit"]) == \
        ("Scouting Gloves of the Monkey", "8s", "14s 25c", "+6s 25c")
    assert f"{page['notice']['title']}: {page['notice']['text']}" == \
        "Research only: no character has Enchanting yet. These rows enter the funded plan once one does."
    # Rows, their sort values and filter fields are the same as with an enchanter; only the notice differs.
    assert page["rows"] == with_enchanter["rows"] and with_enchanter["notice"] is None
