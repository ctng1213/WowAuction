"""Tests for ahsync/recommend/positions_pass.py: positions kept current from the journal, evaluated by the plan run and
the hourly status pass (SPEC §3.4D.9; PO1, PO2, PO8, PO12)."""
from __future__ import annotations

import json

import pytest

from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine.plan import PlanAction
from ahsync.engine.portfolio import Candidate, PortfolioSettings, cash_of
from ahsync.engine.positions import committed
from ahsync.intel.contract import MarketIntelligence
from ahsync.recommend import portfolio
from ahsync.recommend.positions_pass import (clock_of, current, market_now, plan_steps, position_json, reevaluate,
                                             stored_steps, track_positions, views)
from ahsync.recommend.tracking_pass import tracking_pass
from ahsync.state.importer import apply_data
from ahsync.state.savedvars import read_ahprofit
from tests.ahsync.recommend.test_portfolio import intel
from tests.support.fake_clock import FakeClock
from tests.support.savedvars_builder import REALMS, savedvars_text

HOUR, DAY = 3_600_000, 86_400_000
T = 1_760_000_000_000
M = "forever.pvp.horde.us"
A, B = "Amaya-Grommash", "Brugan-Grommash"
BAR, ROD, THICK, ORE, IRON_BAR = 2840, 6217, 4304, 2772, 3575
LATER = f"buy_later:{BAR}:{B}:-/now/h1:1"


@pytest.fixture
def db(tmp_path):
    """Brugan's funded buy for later of 6 Copper Bar at ≤ 5s, sold Sat 19:00; Amaya's funded Thick Leather buy;
    a funded smelt of Iron Ore (Mining)."""
    database = Database.open(tmp_path / "pp.db", FakeClock().now_ms(), None)
    with database.transaction():
        c = database.conn
        c.executemany("INSERT INTO characters(id, market, gold) VALUES (?, ?, ?)", [(A, M, 210_000), (B, M, 104_000)])
        c.executemany(
            "INSERT INTO actions(action_id, char_id, kind, key, qty, price, slot_ms, window_start_ms, window_end_ms, "
            "peak_ms, expected_net, status, done_qty, parent_id, cand_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "'pending', 0, ?, ?)",
            [("buy", B, "buy", BAR, 6, 500, T, None, None, None, 3157.0, None, LATER),
             ("sell", B, "post", BAR, 6, 1200, T + 5 * DAY, T + 5 * DAY + 8 * HOUR, T + 5 * DAY + 11 * HOUR,
              T + 5 * DAY + 8 * HOUR, 3157.0, "buy", LATER),
             ("thick", A, "buy", THICK, 30, 536, T, None, None, None, 5500.0, None, f"buy_later:{THICK}:{A}:-/h1/h2:1"),
             ("ore", B, "buy", ORE, 10, 40, T, None, None, None, 0.0, None, f"craft:{IRON_BAR}:{B}:2772a/now/now:1"),
             ("smelt", B, "craft", IRON_BAR, 10, None, T + 2 * HOUR, None, None, None, 150.0, "ore",
              f"craft:{IRON_BAR}:{B}:2772a/now/now:1")])
        c.execute("INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) "
                  "VALUES ('Mining', 3307, ?, 1, 1)", (IRON_BAR,))
        c.execute("INSERT INTO reagents(profession, spell_id, key, count) VALUES ('Mining', 3307, ?, 1)", (ORE,))
        c.execute("INSERT INTO known_recipes(char_id, spell_id) VALUES (?, 3307)", (B,))
    yield database
    database.close()


def buy(db: Database, event_id: str, char: str, key: int, qty: int, price: int, at: int, confirmed: int = 1) -> None:
    """Store a purchase event as the importer would."""
    with db.transaction():
        db.conn.execute("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, confirmed) "
                        "VALUES (?, ?, ?, ?, ?, 'ah', ?, ?)", (event_id, char, key, qty, price, at, confirmed))


def test_PO1_an_unexecuted_suggestion_leaves_no_position_and_reserves_nothing_between_runs(db):
    assert tracking_pass(db, T + DAY)["opened"] == 0
    assert current(db, T + DAY) == [] and committed(current(db, T + DAY)) == []
    assert db.conn.execute("SELECT COUNT(*) FROM positions").fetchone() == (0,)


def test_PO2_a_confirmed_purchase_of_a_funded_buy_opens_a_position_an_attempt_opens_none(db):
    buy(db, f"{B}:1:0", B, BAR, 6, 500, T + HOUR)
    buy(db, f"{A}:1:0", A, THICK, 10, 536, T + HOUR, confirmed=0)
    assert tracking_pass(db, T + 2 * HOUR)["opened"] == 1
    [p] = current(db, T + 2 * HOUR)
    assert (p.position_id, p.char_id, p.key, p.units, p.cost_left, p.state, p.expected_net_open, p.target_window_ms) == \
        (f"{B}:1:0", B, BAR, 6, 3000, "Holding", 3157.0, T + 5 * DAY + 8 * HOUR)
    assert db.conn.execute("SELECT position_id, event_id, units, unit_cost, at FROM position_lots").fetchall() == \
        [(f"{B}:1:0", f"{B}:1:0", 6, 500, T + HOUR)]
    assert db.conn.execute("SELECT state, cost_left, market FROM positions").fetchall() == [("Holding", 3000, M)]


def test_PO8_the_same_purchase_read_in_two_files_is_one_lot(db):
    character = {"name": "Brugan", "realm": "Grommash", "faction": "Horde", "class": "WARRIOR", "level": 60,
                 "gold": 1, "updated": T}
    purchase = {"id": f"{B}:{T + HOUR}:0", "type": "purchase", "char": B, "at": T + HOUR, "key": BAR, "qty": 6,
                "price": 500, "source": "ah", "confirmed": True}
    text = savedvars_text({"characters": {B: character}, "events": [purchase]})
    for _ in range(2):
        apply_data(db, read_ahprofit(text), REALMS, T + 2 * HOUR)
        tracking_pass(db, T + 2 * HOUR)
    assert db.conn.execute("SELECT COUNT(*), SUM(units) FROM position_lots").fetchone() == (1, 6)
    assert track_positions(db, T + 2 * HOUR) == {"opened": 0, "lots": 0, "open": 1}


def test_PO12_rods_bought_outside_any_plan_open_no_position_and_count_in_the_cost_basis(db):
    buy(db, f"{A}:9:0", A, ROD, 5, 1000, T + HOUR)
    with db.transaction():
        db.conn.execute("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, 'bags', ?, 5, 0)", (A, ROD))
    assert tracking_pass(db, T + 2 * HOUR)["opened"] == 0 and current(db, T) == []
    assert db.cost_basis() == {ROD: (5, 5000)}                          # 10 s each
    gi = portfolio.gen_inputs(db, [(A, 210_000)], {}, {}, T, T, Config(markets=(M,)))
    rods = Candidate("sell_stock", ROD, A, "-/-/now", 1, 5, gold_deposits=100,
                     cost_basis=int(gi.cost_each[ROD] * 5 + 0.5))
    assert cash_of(rods) == 5100                                        # PF21: their 50 s count as cash


def test_the_plan_run_evaluates_positions_and_found_stock_and_processing_says_smelt(db):
    buy(db, f"{B}:1:0", B, BAR, 6, 500, T + HOUR)
    buy(db, f"{B}:2:0", B, ORE, 10, 40, T + HOUR)
    buy(db, f"{A}:3:0", A, THICK, 10, 536, T + HOUR)
    with db.transaction():
        db.conn.executemany("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, 'bags', ?, ?, 0)",
                            [(B, BAR, 6), (B, ORE, 10), (A, THICK, 10), (A, 2592, 20)])
        db.conn.execute("INSERT INTO own_auctions(char_id, key, price, stack, band, count) VALUES (?, ?, 1300, 2, 4, 1)",
                        (B, BAR))
    tracking_pass(db, T + 2 * HOUR)
    positions = current(db, T + 2 * HOUR)
    bars_post = PlanAction("post", B, BAR, 6, 1200, T + 5 * DAY, T + 5 * DAY + 8 * HOUR, T + 5 * DAY + 11 * HOUR,
                           T + 5 * DAY + 8 * HOUR, 0.9, 6157.0, 3157.0)
    wool = PlanAction("hold", A, 2592, 20, None, T + 2 * HOUR, T + 6 * DAY, None, None, None, 472.0, 472.0)
    smelt = PlanAction("craft", B, IRON_BAR, 10, None, T + 3 * HOUR, None, None, None, None, 550.0, 150.0)
    bar_sale = PlanAction("post", B, IRON_BAR, 10, 90, T + 3 * DAY, T + 3 * DAY + 8 * HOUR, None, T + 3 * DAY + 8 * HOUR,
                          0.9, 550.0, 150.0)
    steps = plan_steps([bars_post, wool, smelt, bar_sale, PlanAction("buy", B, BAR, 1, 1, T, None, None, None, None,
                                                                     0.0, 0.0)])
    stats = {BAR: intel(BAR, 650, 600.0)}
    rows = views(db, positions, [A, B], steps, stats, clock_of(T + 2 * HOUR, 0, PortfolioSettings()))
    by_key = {v.key: v for v in rows}
    assert by_key[BAR].next_action.startswith("post ") and by_key[BAR].net_now == 3157.0 * 6 / 6
    assert by_key[ORE].next_action == "smelt today · post Sun 08:53 for the 16:53 peak"   # its product's first post
    assert by_key[THICK].state == "Acquiring" and by_key[THICK].next_action.startswith("buy 20 more by ")
    assert (by_key[2592].found, by_key[2592].paid, by_key[2592].next_action) == (True, 0, "hold")
    assert position_json(by_key[2592])[:4] == [f"found:{A}:2592", A, 2592, "Holding"]
    assert market_now(None) is None and market_now(stats[BAR]).sell_value == 600.0


def test_the_hourly_status_pass_reevaluates_positions_from_the_stored_plan_and_swaps_the_snapshots_positions(db):
    buy(db, f"{B}:1:0", B, BAR, 6, 500, T + HOUR)
    with db.transaction():
        db.conn.execute("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, 'bags', ?, 6, 0)", (B, BAR))
        db.conn.execute("INSERT INTO portfolio_snapshot(market, data) VALUES (?, ?)", (M, json.dumps({"positions": []})))
    tracking_pass(db, T + 2 * HOUR)
    [sell] = [s for s in stored_steps(db, [B]) if s.kind == "post"]
    assert (sell.expected, sell.net, sell.own) == (3157.0 + 3000.0, 3157.0, True)   # its value = net + the bars' cost
    with db.transaction():
        db.conn.execute("UPDATE actions SET status = 'pending' WHERE action_id = 'buy'")
    assert [s.own for s in stored_steps(db, [B]) if s.kind == "post"] == [False]     # its bars aren't bought yet
    with db.transaction():
        db.conn.execute("UPDATE actions SET status = 'done' WHERE action_id = 'buy'")
    assert stored_steps(db, []) == []
    cfg = Config(markets=(M,))
    assert reevaluate(db, cfg, T + 2 * HOUR) == 1
    [row] = db.portfolio_snapshot(M)["positions"]
    assert row[:7] == [f"{B}:1:0", B, BAR, "Holding", 6, 6, 3000] and row[9].startswith("post ")
    with db.transaction():
        db.stage_intel(MarketIntelligence(M, T, T, 1, {BAR: intel(BAR, 650, 600.0)}))
        db.conn.execute("INSERT INTO intel SELECT * FROM intel_new")
    assert reevaluate(db, cfg, T + 2 * HOUR, 0) == 1
    assert db.conn.execute("SELECT state, latest_window_ms FROM positions").fetchone() == ("Holding", T + 5 * DAY + 8 * HOUR)
