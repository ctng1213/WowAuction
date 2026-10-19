"""Tests for ahsync/recommend/tracking_pass.py: stored events matched to actions, posts resolved (SPEC §3.4.1)."""
from __future__ import annotations

import pytest

from ahsync.db.sqlite import Database
from ahsync.recommend.tracking_pass import tracking_pass
from ahsync.state.importer import apply_data
from ahsync.state.savedvars import read_ahprofit
from tests.support.fake_clock import FakeClock
from tests.support.savedvars_builder import REALMS, savedvars_text

HOUR, DAY = 3_600_000, 86_400_000
T = 1_760_000_000_000
LINEN, ROD, BAR = 2589, 6217, 2840
AMAYA = "Amaya-Grommash"


@pytest.fixture
def db(tmp_path):
    """Amaya with a pending buy of 20 Linen at ≤ 45c, a craft of 3 Copper Rods (spell 2663) and a post of 20 Linen."""
    database = Database.open(tmp_path / "t.db", FakeClock().now_ms(), None)
    with database.transaction():
        database.conn.executemany(
            "INSERT INTO actions(action_id, char_id, kind, key, qty, price, slot_ms, window_end_ms, status, done_qty) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0)",
            [("buy", AMAYA, "buy", LINEN, 20, 45, T, None), ("craft", AMAYA, "craft", ROD, 3, None, T, None),
             ("post", AMAYA, "post", LINEN, 20, 55, T + DAY, T + DAY + 11 * HOUR)])
        database.conn.execute("INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) "
                              "VALUES ('Blacksmithing', 2663, ?, 1, 1)", (ROD,))
    yield database
    database.close()


def rows(db: Database, sql: str) -> list[tuple]:
    """Fetch rows for assertions."""
    return db.conn.execute(sql).fetchall()


def test_T8_a_purchase_from_the_saved_variables_is_matched_like_any_other(db):
    character = {"name": "Amaya", "realm": "Grommash", "faction": "Horde", "class": "MAGE", "level": 60, "gold": 1,
                 "updated": T}
    purchase = {"id": f"{AMAYA}:{T + HOUR}:0", "type": "purchase", "char": AMAYA, "at": T + HOUR, "key": LINEN,
                "qty": 20, "price": 40, "source": "ah", "confirmed": True}
    apply_data(db, read_ahprofit(savedvars_text({"characters": {AMAYA: character}, "events": [purchase]})), REALMS, T)
    assert tracking_pass(db) == {"matched": 1, "resolved": 0}
    assert rows(db, "SELECT status, done_qty, done_at FROM actions WHERE action_id = 'buy'") == [("done", 20, T + HOUR)]
    assert rows(db, "SELECT action_id FROM purchases") == [("buy",)]
    assert tracking_pass(db) == {"matched": 0, "resolved": 0}           # nothing twice


def test_a_craft_matches_by_its_recipes_product_and_an_unconfirmed_post_waits(db):
    with db.transaction():
        db.conn.execute("INSERT INTO crafts(event_id, char_id, spell_id, count, at) VALUES ('k', ?, 2663, 2, ?)",
                        (AMAYA, T + HOUR))
        db.conn.execute("INSERT INTO posts(post_id, event_id, char_id, key, stack, price, at, confirmed) "
                        "VALUES ('q', 'q', ?, ?, 20, 60, ?, 0)", (AMAYA, LINEN, T + DAY + HOUR))
    assert tracking_pass(db)["matched"] == 1
    assert rows(db, "SELECT action_id, status, done_qty FROM actions WHERE action_id IN ('craft', 'post') ORDER BY 1") == \
        [("craft", "partial", 2), ("post", "pending", 0)]
    with db.transaction():
        db.conn.execute("UPDATE posts SET confirmed = 1")
    assert tracking_pass(db)["matched"] == 1
    assert rows(db, "SELECT status FROM actions WHERE action_id = 'post'") == [("done",)]


def test_T4_T5_auction_mail_resolves_posts_once(db):
    with db.transaction():
        db.conn.executemany("INSERT INTO posts(post_id, event_id, char_id, key, stack, price, at, confirmed) "
                            "VALUES (?, ?, ?, ?, 20, ?, ?, 1)",
                            [("mon", "mon", AMAYA, LINEN, 50, T), ("tue", "tue", AMAYA, LINEN, 48, T + DAY)])
        db.conn.executemany("INSERT INTO auction_mail(event_id, char_id, key, qty, price, outcome, at) VALUES (?, ?, ?, 20, ?, ?, ?)",
                            [("sold", AMAYA, LINEN, 48, "sold", T + 2 * DAY), ("gone", AMAYA, LINEN, None, "expired", T + 3 * DAY)])
    assert tracking_pass(db)["resolved"] == 2
    assert rows(db, "SELECT post_id, outcome, resolved_at FROM posts ORDER BY at") == \
        [("mon", "expired", T + 3 * DAY), ("tue", "sold", T + 2 * DAY)]
    assert rows(db, "SELECT event_id, post_id FROM auction_mail ORDER BY at") == [("sold", "tue"), ("gone", "mon")]
    assert tracking_pass(db)["resolved"] == 0
