"""Tests for ahsync/recommend/portfolio_page.py: the Portfolio page's inputs from the run and state (SPEC §3.5)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine.portfolio import PortfolioSettings
from ahsync.engine.positions import committed
from ahsync.recommend import portfolio
from ahsync.recommend.portfolio_page import portfolio_page, prices_stale
from ahsync.recommend.positions_pass import clock_of, current, plan_steps, views
from ahsync.recommend.tracking_pass import tracking_pass
from tests.ahsync.recommend.test_portfolio import ITEMS, STATS, intel
from tests.support.fake_clock import FakeClock

M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
NOW = 345_600_000 + 3000 * 7 * DAY + 4 * DAY + 14 * HOUR
BAR, ROD = 2840, 6217
CHARS = [("Amaya-G", 100_000), ("Brugan-G", 400_000)]


@pytest.fixture
def state(tmp_path):
    """Brugan (40 g) holds 6 Copper Bars bought for 54 s and knows Copper Rod; Amaya has 10 g."""
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


def test_the_page_shows_the_runs_capital_its_rows_and_brugans_bars_as_a_position(state):
    with state.transaction():                       # the 6 bars were a funded buy for later: a position (PO2)
        state.conn.execute("INSERT INTO actions(action_id, run_at, slot_ms, char_id, kind, key, qty, price, expected_net, "
                           "status, done_qty, cand_id) VALUES ('buy', ?, ?, 'Brugan-G', 'buy', ?, 6, 900, 300.0, "
                           "'pending', 0, ?)", (NOW - DAY, NOW - 2 * HOUR, BAR, f"buy_later:{BAR}:Brugan-G:-/now/h1:1"))
        state.conn.execute("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, confirmed) "
                           "VALUES ('Brugan-G:1:0', 'Brugan-G', ?, 6, 900, 'ah', ?, 1)", (BAR, NOW - HOUR))
    tracking_pass(state, NOW)
    cfg = Config(markets=(M,))
    positions = current(state, NOW)
    plan = portfolio.plan_portfolio(state, CHARS, STATS, ITEMS, NOW, cfg, committed=committed(positions))
    ids = [f"a{i}" for i in range(len(plan.actions))]
    held = views(state, positions, [c for c, _ in CHARS], plan_steps(plan.actions), STATS,
                 clock_of(NOW, 0, PortfolioSettings()))
    page = portfolio_page(CHARS, plan, ids, STATS, ITEMS, NOW, 0, cfg, NOW, held)
    tiles = {t["label"]: t for t in page["tiles"]}
    assert tiles["Gold in hand"]["value"] == "50g" and tiles["Gold in hand"]["detail"] == "Amaya 10g · Brugan 40g"
    assert tiles["Committed capital"]["value"] == "54s"                              # the bars at what Brugan paid
    [bars] = page["positions"]["rows"]
    assert (bars["item"], bars["character"], bars["cost_left"]) == ("Copper Bar", "Brugan", "54s")
    [gloves] = page["today"]["by_min"]["0"]["excluded"]                               # the run's one candidate
    assert (gloves["item"], gloves["status"], gloves["detail"], gloves["strategy"]) == (
        "Scouting Gloves of the Monkey", "Required profession unavailable: Enchanting 1", "no character has Enchanting",
        "Disenchant")
    assert page["empty"]["title"] == "No new opportunities: every candidate is excluded"
    assert page["risk"]["selected"] == 30


def test_without_characters_the_page_says_so_and_has_no_rows(state):
    page = portfolio_page([], None, [], STATS, ITEMS, NOW, 0, Config(markets=(M,)), NOW)
    assert page["empty"]["title"] == "No characters yet" and page["positions"]["rows"] == []


def test_market_data_is_too_old_when_the_freshest_price_is_more_than_a_day_old():
    assert prices_stale(STATS, NOW, NOW) is False
    old = {k: replace(i, cur_age_ms=25 * HOUR) for k, i in STATS.items()}
    assert prices_stale(old, NOW, NOW) is True
    assert prices_stale({BAR: intel(BAR, 1000, age=HOUR)}, NOW + 24 * HOUR, NOW) is True     # an hour old, a day ago
    assert prices_stale({}, NOW, NOW) is True and prices_stale({9: replace(STATS[BAR], cur_age_ms=None)}, NOW, NOW)
