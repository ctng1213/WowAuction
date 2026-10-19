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
    assert not plan.flip_candidate(replace(STATS[ROD], cur=2500, cur_suspect=True), 0.2)  # FM5


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


def test_R21_forecasts_start_from_the_7_day_median_not_a_month_old_level():
    flat = (IntelWindow(2, 116, 2, 1.0, 0),)
    venom = replace(intel(1475, 90, 85.0, flat), cur_qty=7, med7=103, med30=2094)
    assert plan.sell_facts(venom, None, None).level == 103
    assert not plan.flip_candidate(venom, 0.2)                    # 103 × 0.95 = 97.85 < 1.2 × 90
    month_only = replace(venom, med7=None)
    assert plan.sell_facts(month_only, None, None).level == 2094 and plan.flip_candidate(month_only, 0.2)


def test_sell_records_come_from_your_resolved_posts_the_closes_during_them_and_keep(state):
    t0 = NOW - 10 * DAY
    with state.transaction():
        state.conn.executemany("INSERT INTO posts(post_id, event_id, char_id, key, stack, price, at, outcome, resolved_at, "
                               "confirmed) VALUES (?, ?, 'Brugan-G', ?, 1, 4000, ?, ?, ?, 1)",
                               [(f"p{n}", f"p{n}", ROD, t0 + n * 2 * DAY, outcome, t0 + n * 2 * DAY + DAY)
                                for n, outcome in enumerate(("expired", "expired", "expired"))])
        state.conn.executemany("INSERT INTO candles_h1(market, key, t, close, src) VALUES (?, ?, ?, 4000, 'ahf')",
                               [(M, ROD, t0 + n * 2 * DAY + HOUR) for n in range(3)])
    records = plan.sell_records(state, ["Brugan-G", "Amaya-G"], M, NOW, Config(markets=(M,)))
    assert (records[ROD].expired, records[ROD].streak, records[ROD].not_selling) == (3, 3, True)
    with state.transaction():
        state.conn.execute("INSERT OR REPLACE INTO item_rules(key, rule, set_at, source) VALUES (?, 'keep', ?, 'dashboard')",
                           (ROD, NOW - DAY))
    assert plan.sell_records(state, ["Brugan-G"], M, NOW, Config(markets=(M,))) == {}      # Keep: a fresh start
    assert plan.sell_records(state, [], M, NOW, Config(markets=(M,))) == {}


def test_EX11_flip_offers_cover_candidates_only_and_never_vendor_sold_or_suspect_items():
    cfg = Config(markets=(M,))
    cheap = {**STATS, ROD: replace(STATS[ROD], cur=2500)}
    offers = plan.flip_offers(cheap, ITEMS, set(), NOW, cfg)
    assert list(offers) == [ROD]                                    # bars and dust have no window that beats them
    expected, price, slot = offers[ROD].split(",")
    assert int(price) >= 4000 and int(expected) > 2500 * 1.2 and 0 <= int(slot) < 168
    assert plan.flip_offers(cheap, ITEMS, {ROD}, NOW, cfg) == {}                                         # vendor-sold
    assert plan.flip_offers({ROD: replace(cheap[ROD], cur_suspect=True)}, ITEMS, set(), NOW, cfg) == {}  # FM5


def test_R24_a_stack_dumped_below_the_next_price_level_is_a_flip_candidate_without_a_window():
    dumped = replace(intel(2589, 10), book=((10, 20, 4, 1), (50, 20, 4, 5)), chance=0.9)
    assert plan.now_candidate(dumped, 0.2) and plan.flip_candidate(dumped, 0.2)     # 49 × 0.95 × 0.9 = 41.9 ≥ 12
    assert not plan.window_candidate(dumped, 0.2)                                   # no selling window
    assert plan.sell_facts(dumped, None, None).p90 == 10 and plan.sell_facts(dumped, None, None).med7 == 10


def test_R25_the_second_level_too_close_is_no_candidate():
    close = replace(intel(2589, 45), book=((45, 20, 4, 1), (50, 20, 4, 5)), chance=0.9)
    assert not plan.now_candidate(close, 0.2)                                       # 41.9 < 1.2 × 45 = 54
    assert not plan.now_candidate(replace(close, book=((10, 20, 4, 1),), cur=10), 0.2)   # one level only
    assert not plan.now_candidate(replace(close, cur=10, cur_suspect=True), 0.2)    # FM5
