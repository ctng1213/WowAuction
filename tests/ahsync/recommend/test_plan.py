"""Tests for ahsync/recommend/plan.py: the weekly plan's inputs and rows from intelligence and state (SPEC §3.4C)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine.plan import PlanAction
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import IntelWindow, ItemIntel
from ahsync.recommend import plan
from tests.support.fake_clock import FakeClock

M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
MONDAY = 345_600_000 + 3000 * 7 * DAY
NOW = MONDAY + 4 * DAY + 14 * HOUR
BAR, ROD, DUST, GLOVES = 2840, 6217, 10940, 4239
ITEMS = {BAR: ItemInfo(BAR, "Copper Bar", 1, 10, 7, None, 10, 20), ROD: ItemInfo(ROD, "Copper Rod", 1, 15, 7, None, 50, 20),
         DUST: ItemInfo(DUST, "Strange Dust", 1, 20, 7, None, 20, 20),
         GLOVES: ItemInfo(GLOVES, "Embossed Leather Gloves", 2, 25, 4, None, 100, 1)}


def intel(key: int, cur: int, sell_value: float | None = None, windows: tuple = ()) -> ItemIntel:
    """A minimal item intelligence record: 60 sold a day, 20 listed."""
    return ItemIntel(key, cur, 20, HOUR, cur, cur, cur, cur, 0.0, 60.0, 0.0, 60.0, "ahf", {"ahf": 7}, cur, 1, 0.9,
                     False, sell_value, 70, 20, None, (), sell_windows=windows)


SAT_PEAK = (IntelWindow(5 * 24 + 17, 3, 5 * 24 + 18, 1.3, 7),)
STATS = {BAR: intel(BAR, 1000, 950.0), ROD: intel(ROD, 4000, 3800.0, SAT_PEAK), DUST: intel(DUST, 600, 570.0),
         GLOVES: intel(GLOVES, 800)}


@pytest.fixture
def state(tmp_path):
    """Brugan (40g) knows Copper Rod and holds 6 bars; Amaya (10g) enchants at 142; Linen is ignored."""
    db = Database.open(tmp_path / "p.db", FakeClock().now_ms(), None)
    with db.transaction():
        db.conn.executemany("INSERT INTO characters(id, market, gold) VALUES (?, ?, ?)",
                            [("Brugan-G", M, 400_000), ("Amaya-G", M, 100_000)])
        db.conn.executemany("INSERT INTO professions(char_id, profession, skill, max_skill) VALUES (?, ?, ?, ?)",
                            [("Brugan-G", "Blacksmithing", 112, 150), ("Amaya-G", "Enchanting", 142, 150)])
        db.conn.execute("INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) "
                        "VALUES ('Blacksmithing', 2663, ?, 1, 1)", (ROD,))
        db.conn.execute("INSERT INTO reagents(profession, spell_id, key, count) VALUES ('Blacksmithing', 2663, ?, 2)", (BAR,))
        db.conn.execute("INSERT INTO known_recipes(char_id, spell_id) VALUES ('Brugan-G', 2663)")
        db.conn.execute("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES ('Brugan-G', 'bags', ?, 6, 0)",
                        (BAR,))
        db.conn.execute("INSERT INTO item_rules(key, rule, set_at, source) VALUES (2589, 'ignore', 1, 'dashboard')")
    yield db
    db.close()


CHARS = [("Amaya-G", 100_000), ("Brugan-G", 400_000)]


def test_inputs_come_from_your_stock_recipes_budgets_and_rules(state):
    w = plan.week_inputs(state, CHARS, STATS, ITEMS, Config(markets=(M,), budgets={"Amaya-G": 5_000}))
    assert [(lot.char_id, lot.key, lot.qty) for lot in w.lots] == [("Brugan-G", BAR, 6)]
    assert w.budgets == {"Amaya-G": 5_000, "Brugan-G": 400_000} and w.total_gold == 500_000   # config [budgets] wins
    assert {BAR, ROD} <= set(w.facts) and w.world.products[ROD].windows[0].best_slot == 5 * 24 + 18
    assert (w.no_buy, w.ignore) == (frozenset({2589}), frozenset({2589}))


def test_a_green_is_disenchanted_by_the_enchanter_who_can():
    offer = plan.de_offer(ITEMS[GLOVES], STATS, [("Amaya-G", 142)])
    assert offer is not None and offer[1] == "Amaya-G" and offer[0] > 0
    assert plan.de_offer(ITEMS[GLOVES], STATS, []) is None and plan.de_offer(ITEMS[BAR], STATS, [("A", 300)]) is None
    assert plan.de_offer(None, STATS, [("A", 300)]) is None
    assert plan.sell_facts(STATS[GLOVES], ITEMS[GLOVES], offer).gear


def test_only_items_whose_peak_could_beat_their_cheapest_auction_are_flip_candidates():
    assert plan.flip_candidate(replace(STATS[ROD], cur=2500), 0.2)            # 4000 × 0.95 ≥ 1.2 × 2500
    assert not plan.flip_candidate(STATS[ROD], 0.2) and not plan.flip_candidate(STATS[BAR], 0.2)   # no window


def test_rows_link_a_chain_to_its_first_step_and_carry_the_plan_week():
    actions = [PlanAction("buy", "B", BAR, 2, 1000, NOW, None, None, None, None, 0.0, 0.0, chain="craft:2663"),
               PlanAction("craft", "B", ROD, 1, None, NOW + 60_000, None, None, None, None, 4000.0, 1800.0,
                          chain="craft:2663"),
               PlanAction("post", "B", ROD, 1, 4000, NOW + 2 * DAY, NOW + 3 * DAY, NOW + 3 * DAY + 3 * HOUR,
                          NOW + 3 * DAY + HOUR, 0.9, 4000.0, 1800.0, 24, "craft:2663")]
    rows = plan.plan_rows(actions, M, NOW)
    assert [r[0] for r in rows] == [f"{M}:{NOW}:0", f"{M}:{NOW}:1", f"{M}:{NOW}:2"]
    assert [r[13] for r in rows] == [None, f"{M}:{NOW}:0", f"{M}:{NOW}:0"]      # parent: the chain's first step
    assert rows[2][16:18] == (1800.0, NOW + 3 * DAY) and rows[0][19] == MONDAY  # the post's anchor; Monday 00:00
    assert plan.week_start_ms(NOW) == MONDAY


def test_the_plan_for_a_market_with_and_without_characters(state):
    actions = plan.plan_market(state, CHARS, STATS, ITEMS, NOW, Config(markets=(M,)))
    assert actions and all(a.at_ms >= NOW for a in actions)
    assert plan.plan_market(state, [], STATS, ITEMS, NOW, Config(markets=(M,))) == []
