"""Tests for ahsync/state/positions.py: the positions store (SPEC §3.2 positions and position_lots, §3.4D.9)."""
from __future__ import annotations

import json

import pytest

from ahsync.db.sqlite import Database
from ahsync.engine.positions import Lot, Move, Opening, Position, World
from ahsync.engine.position_views import PositionView
from tests.support.fake_clock import FakeClock

HOUR, DAY = 3_600_000, 86_400_000
T = 1_760_000_000_000
A, B = "Amaya-G", "Brugan-G"
M = "forever.pvp.horde.us"
LINEN, ROD, BAR, ORE = 2589, 6217, 2840, 2770
CAND = f"flip:{LINEN}:{A}:-/now/now:1"


@pytest.fixture
def db(tmp_path):
    """A funded flip of Linen (buy 20 at ≤ 45c, then a post in a window), a funded copper-rod craft whose chain buys
    its bars first, and a sell-stock post nobody funded as a position."""
    database = Database.open(tmp_path / "p.db", FakeClock().now_ms(), None)
    with database.transaction():
        c = database.conn
        c.executemany(
            "INSERT INTO actions(action_id, char_id, kind, key, qty, price, slot_ms, window_start_ms, expected_net, "
            "status, done_qty, parent_id, cand_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [("buy", A, "buy", LINEN, 20, 45, T, None, 210.0, "partial", 10, None, CAND),
             ("sell", A, "post", LINEN, 20, 60, T + DAY, T + DAY + 8 * HOUR, 0.0, "pending", 0, "buy", CAND),
             ("bars", B, "buy", BAR, 2, 500, T, None, 0.0, "done", 2, None, f"craft:{ROD}:{B}:2840a/now/now:1"),
             ("craft", B, "craft", ROD, 1, None, T, None, 300.0, "done", 1, "bars", f"craft:{ROD}:{B}:2840a/now/now:1"),
             ("stock", A, "post", LINEN, 5, 60, T, None, 50.0, "done", 5, None, None)])
        c.executemany("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, action_id, confirmed, "
                      "expected_net) VALUES (?, ?, ?, ?, ?, 'ah', ?, ?, ?, ?)",
                      [(f"{A}:1:0", A, LINEN, 10, 38, T + HOUR, "buy", 1, 105.0),
                       (f"{A}:2:0", A, LINEN, 10, 40, T + 2 * HOUR, "buy", 0, 105.0),      # an attempt: opens nothing
                       (f"{B}:1:0", B, BAR, 2, 500, T + HOUR, "bars", 1, 0.0)])
        c.execute("INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) "
                  "VALUES ('Blacksmithing', 2663, ?, 1, 1)", (ROD,))
        c.execute("INSERT INTO reagents(profession, spell_id, key, count) VALUES ('Blacksmithing', 2663, ?, 2)", (BAR,))
        c.execute("INSERT INTO crafts(event_id, char_id, spell_id, count, at, action_id, expected_net) "
                  "VALUES (?, ?, 2663, 1, ?, 'craft', 300.0)", (f"{B}:2:0", B, T + 2 * HOUR))
        c.execute("INSERT INTO cost_basis(key, qty, total_cost) VALUES (?, 2, 1000)", (BAR,))
        c.executemany("INSERT INTO characters(id, market, gold) VALUES (?, ?, ?)", [(A, M, 1000), (B, M, 2000)])
    yield database
    database.close()


def test_matched_confirmed_purchases_and_crafts_are_openings_with_their_share_and_window(db):
    linen, bars, rod = db.position_openings()
    assert linen == Opening(f"{A}:1:0", A, LINEN, 10, 38, T + HOUR, "buy", CAND, 105.0, T + DAY + 8 * HOUR)
    assert (bars.event_id, bars.chain_id, bars.expected_net, bars.target_window_ms) == (f"{B}:1:0", "bars", 300.0, None)
    assert (rod.key, rod.units, rod.unit_cost, rod.craft, rod.chain_id) == (ROD, 1, 1000, True, "bars")   # 2 bars at 5s
    db.add_position(linen, M)
    assert db.add_lot(f"{A}:1:0", linen) and not db.add_lot(f"{A}:1:0", linen)        # PO8: one lot, not two
    assert [o.event_id for o in db.position_openings()] == [f"{B}:1:0", f"{B}:2:0"]     # a lot is no opening again
    assert db.conn.execute("SELECT expected_net_open, strategy, market, state FROM positions").fetchall() == \
        [(105.0, "flip", M, "Holding")]


def test_seeds_carry_their_lots_and_an_unfinished_buy_is_acquiring_until_24_h_after_its_slot(db):
    linen, bars, rod = db.position_openings()
    for o in (linen, bars):
        db.add_position(o, M)
        db.add_lot(o.event_id, o)
    db.add_position(rod, None)
    db.add_lot(rod.event_id, rod)
    db.add_position(Opening("orphan", A, 1, 1, 1, T, "x", "flip:1:A:-/now/now:1"), M)    # no lot: no seed
    first, second, crafted = db.position_seeds()
    assert (first.position_id, first.lots, first.acquiring_until, first.to_buy, first.key) == \
        (f"{A}:1:0", (Lot(f"{A}:1:0", 10, 38, T + HOUR),), T + DAY, 10, LINEN)
    assert (second.acquiring_until, second.to_buy, second.strategy, second.processing) == (None, 0, "craft", True)
    assert (crafted.key, crafted.char_id, crafted.market) == (ROD, B, None)
    existing = db.existing_positions()
    assert [(e.position_id, e.chain_id) for e in existing] == \
        [("orphan", ""), (f"{A}:1:0", "buy"), (f"{B}:1:0", "bars"), (f"{B}:2:0", "bars")]


def test_moves_read_every_event_that_moves_units_and_crafts_use_their_recipe(db):
    with db.transaction():
        c = db.conn
        c.execute("INSERT INTO posts(post_id, char_id, market, key, stack, price, deposit, at, confirmed) "
                  "VALUES ('p1', ?, ?, ?, 10, 60, 5, ?, 1)", (A, M, LINEN, T + 3 * HOUR))
        c.execute("INSERT INTO auction_mail(event_id, char_id, key, qty, price, outcome, at, post_id) "
                  "VALUES ('m1', ?, ?, 10, 60, 'sold', ?, 'p1')", (A, LINEN, T + 4 * HOUR))
        c.execute("INSERT INTO payouts(event_id, char_id, key, qty, money, at, mail_id, deposit) "
                  "VALUES ('o1', ?, ?, 10, 575, ?, 'm1', 5)", (A, LINEN, T + 5 * HOUR))
        c.execute("INSERT INTO disenchants(event_id, char_id, key, results, at) VALUES ('d1', ?, 4239, '10940,2', ?)",
                  (A, T + 5 * HOUR))
        c.execute("INSERT INTO mail_sent(event_id, char_id, to_char, key, qty, at, confirmed) "
                  "VALUES ('s1', ?, ?, ?, 2, ?, 1)", (A, B, LINEN, T + 6 * HOUR))
        c.execute("INSERT INTO crafts(event_id, char_id, spell_id, count, at) VALUES ('c9', ?, 9999, 1, ?)", (B, T))
    moves = sorted(db.position_moves(T + HOUR), key=lambda m: (m.at_ms, m.event_id))
    assert [m.kind for m in moves] == ["craft", "post", "sold", "de", "payout", "mail"]
    assert moves[0] == Move(T + 2 * HOUR, f"{B}:2:0", "craft", B, ROD, 1, reagents=((BAR, 2),))
    assert moves[4] == Move(T + 5 * HOUR, "o1", "payout", A, LINEN, 10, None, "m1", 575, 5)
    assert moves[5].to_char == B and db.position_moves(T + 7 * HOUR) == []


def test_the_world_writes_and_the_snapshots_positions(db):
    with db.transaction():
        db.conn.executemany("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, ?, ?, ?, ?)",
                            [(A, "bags", LINEN, 6, 0), (A, "bank", LINEN, 4, 0), (A, "bags", ROD, 1, 1)])
        db.conn.execute("INSERT INTO snapshots(char_id, location, at) VALUES (?, 'bags', ?)", (A, T))
        db.conn.execute("INSERT INTO portfolio_snapshot(market, data) VALUES (?, ?)", (M, json.dumps({"positions": []})))
    assert db.position_world() == World({(A, LINEN): 10}, {(A, "bags"): T})
    linen = db.position_openings()[0]
    db.add_position(linen, M)
    p = Position(linen.event_id, B, M, CAND, LINEN, "Partially sold", T, 10, 4, 380, 228, 105.0, 12.5, None,
                 "collect mail: 6 sold, 3s 42c waiting")
    db.write_positions([p])
    db.write_position_views([PositionView(linen.event_id, B, LINEN, "Ready to sell", 10, 4, 380, 228, 77.0, T, "sell now")])
    assert db.conn.execute("SELECT char_id, state, units_left, cost_left, realized, expected_net_now, latest_window_ms, "
                           "next_action FROM positions").fetchall() == [(B, "Ready to sell", 4, 228, 12.5, 77.0, T, "sell now")]
    assert db.replace_snapshot_positions(M, [["x"]]) and not db.replace_snapshot_positions("other", [])
    assert db.portfolio_snapshot(M)["positions"] == [["x"]]


def test_closed_positions_go_after_90_days_with_their_lots(db):
    linen, bars, _ = db.position_openings()
    for o in (linen, bars):
        db.add_position(o, M)
        db.add_lot(o.event_id, o)
    with db.transaction():
        db.conn.execute("UPDATE positions SET closed_at = ? WHERE position_id = ?", (T, linen.event_id))
    assert db.delete_closed_positions_before(T + 1) == 1
    assert db.conn.execute("SELECT position_id FROM position_lots").fetchall() == [(bars.event_id,)]
