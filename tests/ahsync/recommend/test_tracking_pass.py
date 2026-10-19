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
    assert tracking_pass(db) == {"matched": 1, "resolved": 0, "costed": 0, "paid": 0}     # bags not read yet: nothing held
    assert rows(db, "SELECT status, done_qty, done_at FROM actions WHERE action_id = 'buy'") == [("done", 20, T + HOUR)]
    assert rows(db, "SELECT action_id FROM purchases") == [("buy",)]
    assert tracking_pass(db)["matched"] == 0                                    # nothing twice


def test_T1_the_matched_buy_also_sets_what_you_paid(db):
    with db.transaction():
        db.conn.execute("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, confirmed) "
                        "VALUES ('p', ?, ?, 20, 40, 'ah', ?, 1)", (AMAYA, LINEN, T + HOUR))
        db.conn.execute("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, 'bags', ?, 20, 0)", (AMAYA, LINEN))
    assert tracking_pass(db) == {"matched": 1, "resolved": 0, "costed": 1, "paid": 0}
    assert db.cost_basis() == {LINEN: (20, 800)}                                 # 20 × 40c = 8s
    assert rows(db, "SELECT status FROM actions WHERE action_id = 'post'") == [("pending",)]   # its sell stays planned


def test_T10_a_crafted_rod_costs_its_bars_and_your_auctions_count_as_held(db):
    with db.transaction():
        db.conn.execute("INSERT INTO reagents(profession, spell_id, key, count) VALUES ('Blacksmithing', 2663, ?, 2)", (BAR,))
        db.conn.execute("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, confirmed) "
                        "VALUES ('p', ?, ?, 10, 1000, 'ah', ?, 1)", (AMAYA, BAR, T))
        db.conn.execute("INSERT INTO crafts(event_id, char_id, spell_id, count, at) VALUES ('k', ?, 2663, 1, ?)",
                        (AMAYA, T + HOUR))
        db.conn.executemany("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, 'bags', ?, ?, 0)",
                            [(AMAYA, BAR, 18)])                                  # 10 bought + 10 found, 2 used
        db.conn.execute("INSERT INTO own_auctions(char_id, key, price, stack, band, count) VALUES (?, ?, 4100, 1, 4, 1)",
                        (AMAYA, ROD))                                            # the rod is up on the AH
    tracking_pass(db)
    # The bars: 8 tracked left at 10s and 10 found at 0 = 80s over 18; the rod: 2 bars at 10s = 20s.
    assert db.cost_basis() == {BAR: (18, 8000), ROD: (1, 2000)}


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


def test_T21_a_stored_mail_sent_event_matches_the_mail_to_its_recipient(db):
    brugan = "Brugan-Grommash"
    with db.transaction():
        db.conn.execute("INSERT INTO actions(action_id, char_id, kind, key, qty, slot_ms, status, done_qty, to_char) "
                        "VALUES ('mail', ?, 'mail', ?, 6, ?, 'pending', 0, ?)", (brugan, BAR, T, AMAYA))
        db.conn.executemany("INSERT INTO mail_sent(event_id, char_id, to_char, key, qty, at) VALUES (?, ?, ?, ?, ?, ?)",
                            [("x", brugan, "Cara-Grommash", BAR, 6, T + HOUR), ("s", brugan, AMAYA, BAR, 4, T + 2 * HOUR)])
    assert tracking_pass(db)["matched"] == 1
    assert rows(db, "SELECT status, done_qty, done_at FROM actions WHERE action_id = 'mail'") == [("partial", 4, T + 2 * HOUR)]
    assert rows(db, "SELECT event_id, action_id FROM mail_sent ORDER BY event_id") == [("s", "mail"), ("x", None)]


def test_A75_a_sent_mail_from_the_saved_variables_matches_only_once_the_client_confirmed_it(db):
    brugan = "Brugan-Grommash"
    with db.transaction():
        db.conn.execute("INSERT INTO actions(action_id, char_id, kind, key, qty, slot_ms, status, done_qty, to_char) "
                        "VALUES ('mail', ?, 'mail', ?, 6, ?, 'pending', 0, ?)", (brugan, BAR, T, AMAYA))
    character = {"name": "Brugan", "realm": "Grommash", "faction": "Horde", "class": "MAGE", "level": 60, "gold": 1,
                 "updated": T}

    def sent(confirmed: bool) -> dict:
        """Brugan's mail_sent event of 6 Copper Bar to Amaya."""
        return {"id": f"{brugan}:{T + HOUR}:0", "type": "mail_sent", "char": brugan, "at": T + HOUR, "to": AMAYA,
                "key": BAR, "qty": 6, "confirmed": confirmed}
    apply_data(db, read_ahprofit(savedvars_text({"characters": {brugan: character}, "events": [sent(False)]})), REALMS, T)
    assert tracking_pass(db)["matched"] == 0                                     # an attempt waits (FM20)
    assert rows(db, "SELECT confirmed, action_id FROM mail_sent") == [(0, None)]
    apply_data(db, read_ahprofit(savedvars_text({"characters": {brugan: character}, "events": [sent(True)]})), REALMS, T)
    assert tracking_pass(db)["matched"] == 1
    assert rows(db, "SELECT confirmed, action_id FROM mail_sent") == [(1, "mail")]
    assert rows(db, "SELECT status FROM actions WHERE action_id = 'mail'") == [("done",)]


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


def test_T17_posted_sold_and_paid_out_are_three_events_and_profit_is_realized_once(db):
    with db.transaction():
        db.conn.execute("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, confirmed) "
                        "VALUES ('rods', ?, ?, 2, 400, 'ah', ?, 1)", (AMAYA, ROD, T))
        db.conn.execute("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, 'bags', ?, 1, 0)", (AMAYA, ROD))
        db.conn.execute("INSERT INTO posts(post_id, event_id, char_id, key, stack, price, deposit, at, confirmed) "
                        "VALUES ('post', 'post', ?, ?, 1, 1000, 20, ?, 1)", (AMAYA, ROD, T + HOUR))
        db.conn.execute("INSERT INTO auction_mail(event_id, char_id, key, qty, price, outcome, at) "
                        "VALUES ('sold', ?, ?, 1, 1000, 'sold', ?)", (AMAYA, ROD, T + DAY))
    assert tracking_pass(db)["paid"] == 0                                       # sold, not paid out yet
    with db.transaction():
        db.conn.execute("INSERT INTO payouts(event_id, char_id, key, qty, money, at) VALUES ('pay', ?, ?, 1, 970, ?)",
                        (AMAYA, ROD, T + DAY + HOUR))
    assert tracking_pass(db)["paid"] == 1
    # 9s 70c − 4s (the cost basis) − the post's 20c deposit = 5s 50c
    assert rows(db, "SELECT mail_id, realized FROM payouts") == [("sold", 550.0)]
    assert rows(db, "SELECT m.post_id FROM auction_mail m") == [("post",)]
    assert tracking_pass(db)["paid"] == 0                                       # counted once


def test_T18_a_stored_disenchant_moves_the_cost_to_its_mats(db):
    with db.transaction():
        db.conn.execute("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, confirmed) "
                        "VALUES ('bracers', ?, 4570, 1, 3000, 'ah', ?, 1)", (AMAYA, T))
        db.conn.execute("INSERT INTO disenchants(event_id, char_id, key, results, at) "
                        "VALUES ('de', ?, 4570, '10938,1;10940,3', ?)", (AMAYA, T + HOUR))
        db.conn.executemany("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, 'bags', ?, ?, 0)",
                            [(AMAYA, 10938, 1), (AMAYA, 10940, 3)])
    tracking_pass(db)
    assert db.cost_basis() == {10938: (1, 750), 10940: (3, 2250)}             # 30s over 4 units


def test_T19_a_disenchant_matches_the_planned_de_action(db):
    with db.transaction():
        db.conn.execute("INSERT INTO actions(action_id, char_id, kind, key, qty, slot_ms, status, done_qty) "
                        "VALUES ('de', ?, 'de', 4570, 1, ?, 'pending', 0)", (AMAYA, T))
        db.conn.execute("INSERT INTO disenchants(event_id, char_id, key, results, at) "
                        "VALUES ('d1', ?, 4570, '10940,3', ?)", (AMAYA, T + HOUR))
    assert tracking_pass(db)["matched"] == 1
    assert rows(db, "SELECT status, done_qty FROM actions WHERE action_id = 'de'") == [("done", 1)]
    assert rows(db, "SELECT action_id FROM disenchants") == [("de",)]


def test_K5_a_matched_post_keeps_the_chance_it_was_planned_with(db):
    with db.transaction():
        db.conn.execute("UPDATE actions SET conf = 0.62 WHERE action_id = 'post'")
        db.conn.executemany("INSERT INTO posts(post_id, event_id, char_id, key, stack, price, at, confirmed) "
                            "VALUES (?, ?, ?, ?, 20, 55, ?, 1)", [("p", "p", AMAYA, LINEN, T + DAY), ("q", "q", AMAYA, ROD, T)])
    tracking_pass(db)
    assert rows(db, "SELECT post_id, p_at_post FROM posts ORDER BY post_id") == [("p", 0.62), ("q", None)]


def test_T23_a_matched_event_keeps_its_share_of_the_plans_expected_net(db):
    with db.transaction():
        db.conn.executemany("UPDATE actions SET expected_net = ? WHERE action_id = ?",
                            [(447.0, "post"), (971.0, "craft"), (210.0, "buy")])
        db.conn.executemany("INSERT INTO posts(post_id, event_id, char_id, key, stack, price, at, confirmed) "
                            "VALUES (?, ?, ?, ?, 20, 55, ?, 1)", [("p", "p", AMAYA, LINEN, T + DAY), ("q", "q", AMAYA, ROD, T)])
        db.conn.execute("INSERT INTO crafts(event_id, char_id, spell_id, count, at) VALUES ('k', ?, 2663, 3, ?)", (AMAYA, T))
        db.conn.executemany("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, confirmed) "
                            "VALUES (?, ?, ?, 10, 40, 'ah', ?, 1)", [("b1", AMAYA, LINEN, T), ("b2", AMAYA, LINEN, T + 1)])
    tracking_pass(db)
    assert rows(db, "SELECT post_id, expected_net FROM posts ORDER BY post_id") == [("p", 447.0), ("q", None)]
    assert rows(db, "SELECT expected_net FROM crafts") == [(971.0,)]                    # the chain's, on its carrier
    assert rows(db, "SELECT event_id, expected_net FROM purchases ORDER BY event_id") == [("b1", 105.0), ("b2", 105.0)]
    with db.transaction():
        db.conn.execute("UPDATE actions SET expected_net = 999 WHERE action_id = 'post'")    # a later re-plan
    tracking_pass(db)
    assert rows(db, "SELECT expected_net FROM posts WHERE post_id = 'p'") == [(447.0,)]   # copied once, at match time


def test_FM20_a_failed_buy_attempt_matches_nothing_and_changes_no_cost_basis(db):
    with db.transaction():
        db.conn.execute("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, confirmed) "
                        "VALUES ('try', ?, ?, 20, 40, 'ah', ?, 0)", (AMAYA, LINEN, T + HOUR))     # never confirmed
        db.conn.execute("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, 'bags', ?, 20, 0)", (AMAYA, LINEN))
    assert tracking_pass(db)["matched"] == 0
    assert db.cost_basis() == {}                                                # the 20 Linen still cost "before tracking"
    assert rows(db, "SELECT status FROM actions WHERE action_id = 'buy'") == [("pending",)]


def test_FM21_items_missing_after_a_crash_are_no_sale_and_no_new_cost(db):
    with db.transaction():
        db.conn.execute("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, confirmed) "
                        "VALUES ('b', ?, ?, 40, 40, 'ah', ?, 1)", (AMAYA, LINEN, T))
        # The session that moved 20 of them was lost in a crash: the bags now show 20, later 25.
        db.conn.execute("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, 'bags', ?, 20, 0)", (AMAYA, LINEN))
    tracking_pass(db)
    assert rows(db, "SELECT qty, total_cost, untracked_qty FROM cost_basis") == [(20, 800, 0)]   # 40c each, unchanged
    assert rows(db, "SELECT COUNT(*) FROM posts WHERE outcome IS NOT NULL") == [(0,)]           # no sale invented
    with db.transaction():
        db.conn.execute("UPDATE inventory SET qty = 25")
    tracking_pass(db)
    # 25 held of the 40 bought: all of them may be bought ones, so each still costs the 40c paid (nothing invented)
    assert rows(db, "SELECT qty, total_cost, untracked_qty FROM cost_basis") == [(25, 1000, 0)]


def test_T20_the_pass_splits_a_disenchant_by_the_results_lowest_price_now(db):
    with db.transaction():
        db.conn.execute("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, confirmed) "
                        "VALUES ('bracers', ?, 4570, 1, 3000, 'ah', ?, 1)", (AMAYA, T))
        db.conn.execute("INSERT INTO disenchants(event_id, char_id, key, results, at) "
                        "VALUES ('de', ?, 4570, '10938,1;10940,3', ?)", (AMAYA, T + HOUR))
        db.conn.executemany("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, 'bags', ?, ?, 0)",
                            [(AMAYA, 10938, 1), (AMAYA, 10940, 3)])
        db.conn.executemany("INSERT INTO current(market, key, price, qty, at, src) VALUES ('m', ?, ?, 5, ?, ?)",
                            [(10940, 120, T, "ahf"), (10940, 100, T, "local"), (10938, 700, T, "ahf")])
    tracking_pass(db)
    assert db.cost_basis() == {10938: (1, 2100), 10940: (3, 900)}             # the dust's cheapest is 1s
