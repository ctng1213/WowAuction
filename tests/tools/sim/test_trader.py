"""Tests for tools/sim/trader.py: our characters in the simulated market, through AHSync (SPEC §8.10; SU10-SU12)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

from ahsync.state.savedvars import read_ahprofit  # noqa: E402
from sim.emulator import capture, id_book  # noqa: E402
from sim.market import DAY_MS, HOUR_MS, MINUTE_MS, Item, Market, World  # noqa: E402
from sim.runner import simulate  # noqa: E402
from sim.trader import (OUR_PREFIX, SALE_MAIL_DELAY_MS, CharacterSpec, NaiveTrader, Trader,  # noqa: E402
                        client_band)
from tests.support.savedvars_builder import savedvars_text  # noqa: E402

START = 1_793_923_200_000                    # Fri 2026-11-06 00:00 UTC
M = "forever.normal.horde.us"
LINEN = Item(2589, "Linen Cloth", True, 50, 0, 0, vendor_sell=13)
AMAYA, BRUGAN = CharacterSpec("Amaya", 10_000, bags=((2589, 20),)), CharacterSpec("Brugan", 0)


def by_hand(*specs: CharacterSpec) -> tuple[Market, Trader]:
    """A quiet Linen market and a trader driven by hand."""
    world = World(1, START, 1, (LINEN,))
    return Market(world), Trader(world, specs)


def test_SU10_our_post_appears_in_the_next_snapshot_with_its_band_and_price():
    market, trader = by_hand(AMAYA)
    [auction] = trader.post(market, AMAYA.char_id, 2589, 20, 55, 24)
    market.run_until(START + HOUR_MS)
    [seen] = id_book(capture(market.book(), START + HOUR_MS), M).auctions
    assert (seen.id, seen.qty, seen.price, seen.band) == (auction.id, 20, 55, 4)      # VERY_LONG
    assert trader.events[-1] | {"id": ""} == {"id": "", "type": "post", "char": AMAYA.char_id, "at": START, "key": 2589,
                                              "stack": 20, "price": 55, "durationH": 24, "deposit": 156,
                                              "confirmed": True}            # 60% × 13 c × 20 units


def test_SU11_we_buy_20_of_a_50_unit_floor_and_pay_the_book_walk():
    market, trader = by_hand(AMAYA)
    floor = market.post(2589, 50, 10, 24, "s1")
    market.post(2589, 50, 12, 24, "s2")
    assert trader.buy(market, AMAYA.char_id, 2589, 20, 12) == [(20, 10)]
    assert floor.units == 30 and min(a.price for a in market.book()) == 10
    assert trader.ledger.cash[AMAYA.char_id] == 10_000 - 200 and trader.held(AMAYA.char_id, 2589) == 40
    assert trader.buy(market, AMAYA.char_id, 2589, 40, 11) == [(30, 10)]          # nothing at 12 c for 11 c


def test_SU12_the_ledger_balances_after_every_event_and_each_moves_only_its_buckets():
    market, trader = by_hand(AMAYA, BRUGAN)
    ledger, amaya = trader.ledger, AMAYA.char_id
    moved = []

    def step(action, *buckets):
        before = ledger.totals()
        action()
        after = ledger.totals()
        assert ledger.balanced(), (buckets, after)
        moved.append(({b for b in after if after[b] != before[b]}, set(buckets)))

    market.post(2589, 30, 40, 24, "s1")
    step(lambda: trader.buy(market, amaya, 2589, 30, 40), "cash", "bags")
    a, = trader.post(market, amaya, 2589, 10, 60, 2)                     # 50 units cost 1,200: 240 each post
    trader.post(market, amaya, 2589, 10, 70, 24, cancel_at_ms=START + 3 * HOUR_MS)     # deposits: the 1 s minimum
    step(lambda: market.take(a.id, 4, "b1") or trader.observe(market), "listed", "deposits", "mail_gold")
    step(lambda: market.run_until(START + 2 * HOUR_MS) or trader.observe(market), "listed", "deposits", "mail_items")
    step(lambda: market.run_until(START + 3 * HOUR_MS) or trader.observe(market), "listed", "deposits", "mail_items")
    step(lambda: trader.collect(amaya, START + 3 * HOUR_MS), "mail_gold", "mail_items", "cash", "bags")
    step(lambda: trader.ledger.mail(amaya, BRUGAN.char_id, 2589, 5), "bags", "mail_items")
    step(lambda: trader.vendor_sell(amaya, 2589, 5), "bags", "cash")
    assert all(changed <= named for changed, named in moved), moved
    # Sold 4 of a (cost 96 of its 240); vendored 5 of 41 units costing 984 (120): 20 found units cost 0.
    assert ledger.gains == (4 * 60 - 96) + (5 * 13 - 120)
    assert ledger.cuts == 12 and ledger.deposits_lost == 2 * 100 - 100 * 4 // 10      # a's share returned with its sale
    mails = [(e["type"], e.get("outcome")) for e in trader.events if e["type"] in ("auction_mail", "payout")]
    assert mails == [("auction_mail", "sold"), ("payout", None), ("auction_mail", "expired"),
                     ("auction_mail", "cancelled")]                       # by arrival: the sale's letter came at 01:00


def test_mail_arrives_an_hour_after_a_sale_and_is_recorded_as_the_addon_does():
    market, trader = by_hand(AMAYA)
    a, = trader.post(market, AMAYA.char_id, 2589, 20, 60, 24)
    market.now_ms = START + 10 * MINUTE_MS
    market.take(a.id, 20, "b1")
    trader.observe(market)
    trader.collect(AMAYA.char_id, START + 10 * MINUTE_MS + SALE_MAIL_DELAY_MS - 1)
    assert trader.ledger.mail_gold[AMAYA.char_id] == 20 * 60 - 60 + 156            # not there yet
    trader.collect(AMAYA.char_id, START + 10 * MINUTE_MS + SALE_MAIL_DELAY_MS)
    sold, payout = trader.events[-2:]
    assert (sold["outcome"], sold["qty"], sold["price"]) == ("sold", 20, 60)
    assert (payout["type"], payout["money"], payout["deposit"]) == ("payout", 1296, 156)
    assert trader.ledger.cash[AMAYA.char_id] == 10_000 - 156 + 1296 and trader.ledger.balanced()
    assert [client_band(ms) for ms in (29 * MINUTE_MS, HOUR_MS, 7 * HOUR_MS, 9 * HOUR_MS)] == [1, 2, 3, 4]


def test_the_naive_trader_buys_a_listing_that_pays_at_med7_and_posts_its_bags_there():
    world = World(1, START, 1, (LINEN,))
    market, naive = Market(world), NaiveTrader(world, (CharacterSpec("Cira", 10_000),))
    market.post(2589, 10, 30, 24, "s1")
    document = SimpleNamespace(items={2589: SimpleNamespace(med7=50)})
    naive.login(market, [document], START)                       # 30 c < 50 × 0.95 − 10 c deposit a unit
    [ours] = [a for a in market.book() if a.seller.startswith(OUR_PREFIX)]
    assert (ours.units, ours.price) == (10, 50) and naive.ledger.balanced()
    market.post(2589, 10, 45, 24, "s2")
    naive.login(market, [document], START + DAY_MS)              # 45 c does not pay
    assert naive.counts["purchase"] == 1


def test_a_trader_follows_the_plan_through_the_real_import(tmp_path):
    item = Item(2589, "Linen Cloth", True, 50, 300, 60, vendor_sell=13, max_units=10)
    world = World(3, START, 3, (item,))
    trader = Trader(world, (CharacterSpec("Amaya", 10_000, bags=((2589, 100),)),), tmp_path / "ahsync")
    simulate(world, tmp_path / "run", agent=trader)
    db = trader.session.db
    assert trader.session.replans == 3 and trader.ledger.balanced()      # Fri, Sat, Sun at 10:00
    done = db.conn.execute("SELECT count(*), sum(qty) FROM actions WHERE kind = 'post' AND status = 'done'").fetchone()
    assert done[0] >= 1 and trader.counts["post"] >= 1 and trader.counts.get("payout", 0) >= 1
    assert db.conn.execute("SELECT count(*) FROM posts").fetchone()[0] == trader.counts["post"]
    assert db.conn.execute("SELECT count(*) FROM payouts").fetchone()[0] == trader.counts["payout"]
    data = read_ahprofit(savedvars_text(trader.session.savedvars(Market(world), START)))
    assert not data.problems and [c.char_id for c in data.characters] == ["Amaya-Simulated"]
    trader.close()                                   # Windows can't delete an open SQLite file (CI, PR #74)
    with pytest.raises(sqlite3.ProgrammingError):
        db.conn.execute("SELECT 1")
    Trader(world, (AMAYA,)).close()                  # a trader without AHSync has nothing to close
