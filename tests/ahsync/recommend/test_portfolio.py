"""Tests for ahsync/recommend/portfolio.py: the portfolio plan's inputs from intelligence and state (SPEC §3.4D)."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine import disenchant
from ahsync.engine.de_table import DE_TABLE
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import IntelWindow, ItemIntel
from ahsync.recommend import portfolio
from tests.support.fake_clock import FakeClock

M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
MONDAY = 345_600_000 + 3000 * 7 * DAY
NOW = MONDAY + 4 * DAY + 14 * HOUR
BAR, ROD, DUST, ESSENCE, SCOUTING = 2840, 6217, 10940, 10938, 6585
ITEMS = {BAR: ItemInfo(BAR, "Copper Bar", 1, 10, 7, None, 10, 20), ROD: ItemInfo(ROD, "Copper Rod", 1, 15, 7, None, 50, 20),
         DUST: ItemInfo(DUST, "Strange Dust", 1, 20, 7, None, 20, 20),
         ESSENCE: ItemInfo(ESSENCE, "Lesser Magic Essence", 2, 20, 7, None, 30, 10),
         SCOUTING: ItemInfo(SCOUTING, "Scouting Gloves of the Monkey", 2, 15, 4, None, 100, 1)}


def intel(key: int, cur: int | None, sell_value: float | None = None, windows: tuple = (), age: int | None = HOUR) -> ItemIntel:
    """A minimal item intelligence record: 60 sold a day, 20 listed.

    Args:
        key: Item key.
        cur: Lowest buyout, copper.
        sell_value: Sell value per unit.
        windows: Selling windows.
        age: Price age at the build, ms.

    Returns:
        The ItemIntel.
    """
    return ItemIntel(key, cur, 20 if cur else None, age, cur, cur, cur, cur, 0.0, 60.0, 0.0, 60.0, "ahf", {"ahf": 7},
                     cur, 1, 0.9, False, sell_value, 70, 20, None, (), sell_windows=windows, closes_30d=720)


STATS = {BAR: intel(BAR, 1000, 950.0), ROD: intel(ROD, 4000, 3800.0, (IntelWindow(5 * 24 + 17, 3, 5 * 24 + 18, 1.3, 7),)),
         DUST: intel(DUST, 600, 570.0), ESSENCE: intel(ESSENCE, 900, 850.0), SCOUTING: intel(SCOUTING, 800)}


@pytest.fixture
def state(tmp_path):
    """Brugan (40g) knows Copper Rod and holds 6 bars bought at 9s each; Amaya (10g) has no Enchanting."""
    db = Database.open(tmp_path / "p.db", FakeClock().now_ms(), None)
    with db.transaction():
        db.conn.executemany("INSERT INTO characters(id, market, gold) VALUES (?, ?, ?)",
                            [("Brugan-G", M, 400_000), ("Amaya-G", M, 100_000)])
        db.conn.execute("INSERT INTO professions(char_id, profession, skill, max_skill) VALUES ('Brugan-G', "
                        "'Blacksmithing', 112, 150)")
        db.conn.execute("INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) "
                        "VALUES ('Blacksmithing', 2663, ?, 1, 1)", (ROD,))
        db.conn.execute("INSERT INTO reagents(profession, spell_id, key, count) VALUES ('Blacksmithing', 2663, ?, 2)", (BAR,))
        db.conn.execute("INSERT INTO known_recipes(char_id, spell_id) VALUES ('Brugan-G', 2663)")
        db.conn.execute("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES ('Brugan-G', 'bags', ?, 6, 0)",
                        (BAR,))
        db.conn.execute("INSERT INTO cost_basis(key, qty, total_cost) VALUES (?, 6, 5400)", (BAR,))
    yield db
    db.close()


def test_PF2_research_needs_no_enchanter_the_gloves_are_a_candidate_with_their_reason(state):
    infos = portfolio.de_infos(STATS, ITEMS)
    skill, outcomes = disenchant.outcomes_for(DE_TABLE, 2, 15, "armor")
    ev = disenchant.expected_value(outcomes, [STATS[m].sell_value if m in STATS else 0.0 for m, _, _, _ in outcomes])
    assert set(infos) == {SCOUTING} and (infos[SCOUTING].skill, infos[SCOUTING].ev_each) == (skill, ev) and skill == 1
    plan = portfolio.plan_portfolio(state, [("Amaya-G", 100_000), ("Brugan-G", 400_000)], STATS, ITEMS, NOW,
                                    Config(markets=(M,)))
    gloves = [a for a in plan.alternatives if a.candidate.key == SCOUTING]
    assert [a.reason for a in gloves] == ["Required profession unavailable: Enchanting 1"]
    assert gloves[0].candidate.expected_net == pytest.approx(20 * (ev - 800))   # one auction of 20 at 8s
    assert not [a for a in plan.actions if a.key == SCOUTING]                  # in no action
    rows = portfolio.alternative_rows(plan)
    [row] = [r for r in rows if r[0] == gloves[0].candidate.cand_id]
    assert row[1:4] == ("Amaya-G", "today", "Required profession unavailable: Enchanting 1")
    assert json.loads(row[4])["cand_id"] == row[0]


def test_PF30_a_disenchant_buy_reads_its_main_mats_sales_rate_not_the_greens():
    thin_green = {**STATS, SCOUTING: replace(STATS[SCOUTING], closes_30d=4, sold_per_day=None)}
    assert portfolio.de_infos(thin_green, ITEMS)[SCOUTING].volume_unknown is None   # never resold: irrelevant
    no_dust = {**STATS, DUST: replace(STATS[DUST], sold_per_day=None)}
    assert portfolio.de_infos(no_dust, ITEMS)[SCOUTING].volume_unknown == "Strange Dust"
    no_essence = {**STATS, ESSENCE: replace(STATS[ESSENCE], sold_per_day=None)}
    assert portfolio.de_infos(no_essence, ITEMS)[SCOUTING].volume_unknown is None   # not the main mat
    no_intel = {k: v for k, v in STATS.items() if k != DUST}
    assert portfolio.de_infos(no_intel, ITEMS)[SCOUTING].volume_unknown is None      # worth 0: the essence leads


def test_committed_capital_counts_the_bars_brugan_paid_for(state):
    plan = portfolio.plan_portfolio(state, [("Amaya-G", 100_000), ("Brugan-G", 400_000)], STATS, ITEMS, NOW,
                                    Config(markets=(M,)))
    assert plan.accounting.committed_capital == 5400                           # 6 bars at 9s each
    assert portfolio.held_units(state, ["Brugan-G"]) == [("Brugan-G", BAR, 6)]
    assert portfolio.plan_portfolio(state, [], STATS, ITEMS, NOW, Config(markets=(M,))) is None
    assert portfolio.alternative_rows(None) == []


def test_price_ages_run_on_from_the_build_and_a_later_scan_is_fresh():
    stats = {BAR: intel(BAR, 1000, age=HOUR), DUST: intel(DUST, 600, age=0), ROD: intel(ROD, None, age=None)}
    assert portfolio.price_ages(stats, NOW, NOW - 2 * HOUR) == {BAR: 3 * HOUR, DUST: 0, ROD: None}


def test_generation_inputs_read_skills_recipes_and_cost_basis(state):
    gi = portfolio.gen_inputs(state, [("Amaya-G", 100_000), ("Brugan-G", 400_000)], STATS, ITEMS, NOW, NOW,
                              Config(markets=(M,), budgets={"Amaya-G": 5_000}))
    assert gi.spendable == {"Amaya-G": 5_000, "Brugan-G": 400_000}            # config [budgets] wins
    assert gi.skills == {("Brugan-G", "Blacksmithing"): 112} and gi.recipe_needs == {2663: ("Blacksmithing", 0)}
    assert gi.cost_each == {BAR: 900.0} and gi.names[SCOUTING] == "Scouting Gloves of the Monkey"


def test_an_item_that_cannot_be_disenchanted_or_has_no_listing_gives_no_disenchant_facts():
    items = {**ITEMS, 7005: ItemInfo(7005, "Grey Shirt", 0, 15, 4, None, 1, 1)}
    stats = {**STATS, SCOUTING: replace(STATS[SCOUTING], cur_suspect=True), 7005: intel(7005, 100)}
    assert portfolio.de_infos(stats, items) == {}
    assert portfolio.de_infos({SCOUTING: intel(SCOUTING, None)}, ITEMS) == {}
