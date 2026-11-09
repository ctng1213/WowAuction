"""Tests for ahsync/state/journal.py: the journal behind the History page (SPEC §3.5, D47)."""
from __future__ import annotations

import pytest

from ahsync.db.sqlite import Database
from ahsync.engine.tracking import ChainFacts, PostResult
from tests.support.fake_clock import FakeClock

HOUR, DAY = 3_600_000, 86_400_000
NOW = 1_760_000_000_000
A, B = "Amaya-G", "Brugan-G"
ROD, LINEN, DUST, CINCH, SILK = 6217, 2589, 10940, 4239, 4306


@pytest.fixture
def db(tmp_path):
    """Amaya's last month, every kind of event once, plus Brugan's and an old one."""
    database = Database.open(tmp_path / "j.db", FakeClock().now_ms(), None)
    with database.transaction():
        c = database.conn
        c.execute("INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) VALUES "
                  "('Blacksmithing', 2663, ?, 1, 1), ('Enchanting', 7418, NULL, 1, 1)", (ROD,))
        c.executemany("INSERT INTO actions(action_id, char_id, kind, key, qty, price, slot_ms, status, done_qty) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      [("buy", A, "buy", LINEN, 20, 45, NOW - DAY, "done", 20), ("miss", A, "buy", SILK, 40, 28, NOW - DAY, "missed", 0),
                       ("skip", A, "buy", SILK, 10, 28, NOW - 2 * DAY, "skipped", 0), ("open", A, "post", ROD, 1, 4100, NOW, "pending", 0)])
        c.execute("INSERT INTO posts(post_id, event_id, char_id, key, stack, price, duration_h, deposit, at, confirmed) "
                  "VALUES ('p', 'p', ?, ?, 1, 4100, 24, 20, ?, 1), ('try', 'try', ?, ?, 1, 4100, 24, 20, ?, 0)",
                  (A, ROD, NOW - 3 * DAY, A, ROD, NOW - 3 * DAY))
        c.execute("INSERT INTO auction_mail(event_id, char_id, key, qty, price, outcome, at, post_id) "
                  "VALUES ('m', ?, ?, 1, 4100, 'sold', ?, 'p')", (A, ROD, NOW - 2 * HOUR))
        c.executemany("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, action_id, confirmed) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      [("b1", A, LINEN, 20, 38, "ah", NOW - 3 * HOUR, "buy", 1), ("b2", A, 2320, 4, 10, "vendor", NOW - 5 * HOUR, None, 1),
                       ("b3", A, LINEN, 1, 38, "ah", NOW - 5 * HOUR, None, 0), ("b4", B, LINEN, 1, 38, "ah", NOW, None, 1),
                       ("old", A, LINEN, 1, 38, "ah", NOW - 31 * DAY, None, 1)])
        c.executemany("INSERT INTO crafts(event_id, char_id, spell_id, count, at) VALUES (?, ?, ?, 1, ?)",
                      [("c1", A, 2663, NOW - 6 * HOUR), ("c2", A, 7418, NOW - 6 * HOUR)])
        c.execute("INSERT INTO disenchants(event_id, char_id, key, results, at) VALUES ('d', ?, ?, ?, ?)",
                  (A, CINCH, f"{DUST},2", NOW - 7 * HOUR))
        c.execute("INSERT INTO payouts(event_id, char_id, key, qty, money, at, realized) VALUES ('o', ?, ?, 1, 3915, ?, 1915)",
                  (A, ROD, NOW - HOUR))
        c.execute("INSERT INTO mail_sent(event_id, char_id, to_char, key, qty, at) VALUES ('s', ?, ?, ?, 5, ?)",
                  (A, B, LINEN, NOW - 8 * HOUR))
        c.execute("INSERT INTO untracked(char_id, key, delta, at) VALUES (?, ?, -3, ?)", (A, LINEN, NOW - 9 * HOUR))
    yield database
    database.close()


def test_D47_the_journal_reads_every_kind_of_event_with_the_plan_action_it_matched(db):
    rows = db.journal([A], NOW - 30 * DAY)
    assert [(r[2], r[3]) for r in rows] == [
        ("paid_out", ROD), ("sold", ROD), ("bought", LINEN), ("bought_vendor", 2320), ("crafted", ROD),
        ("disenchanted", CINCH), ("mailed", LINEN), ("untracked", LINEN), ("missed", SILK), ("skipped", SILK),
        ("posted", ROD)]
    by_kind = {r[2]: r for r in rows}
    assert by_kind["paid_out"][7:9] == (3915, 1915.0)
    assert by_kind["sold"][7:9] == (NOW - 3 * DAY, 20)                       # the post it resolved
    assert by_kind["bought"][6] == ("done", 20, 20)
    assert by_kind["missed"][6:8] == (("missed", 0, 40), "buy")
    assert by_kind["posted"][5:9] == (4100, None, 24, 20)
    assert by_kind["mailed"][7] == B and by_kind["disenchanted"][7] == f"{DUST},2"
    assert by_kind["untracked"][4:8] == (3, None, None, -3)
    assert db.journal([], NOW) == []
    assert by_kind["posted"][9:11] == (None, PostResult(None, None, 20, None, 1, 0, 1))   # no action; a sale unpaid (T26)
    assert by_kind["posted"][11] == "p" and by_kind["untracked"][11] is None      # the event id (M14, PO9)


def test_D69_the_journal_gives_each_event_its_plan_facts(tmp_path):
    db = Database.open(tmp_path / "c.db", FakeClock().now_ms(), None)
    with db.transaction():
        c = db.conn
        c.execute("INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) "
                  "VALUES ('Blacksmithing', 2663, ?, 1, 1)", (ROD,))
        c.executemany("INSERT INTO actions(action_id, parent_id, char_id, kind, key, qty, expected_net, status) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      [("bars", None, A, "buy", 2840, 14, 0.0, "done"), ("craft", "bars", A, "craft", ROD, 7, 971.0, "done"),
                       ("p1", "bars", A, "post", ROD, 3, 300.0, "done"), ("p2", "bars", A, "post", ROD, 3, 350.0, "done"),
                       ("p3", "bars", A, "post", ROD, 1, 321.0, "pending"),
                       ("flip", None, A, "buy", LINEN, 20, 210.0, "partial"), ("fpost", "flip", A, "post", LINEN, 20, 0.0, "pending"),
                       ("debuy", None, A, "buy", CINCH, 1, 500.0, "done"), ("de", "debuy", A, "de", CINCH, 1, 0.0, "pending")])
        c.executemany("INSERT INTO posts(post_id, char_id, key, stack, price, deposit, at, outcome, action_id, expected_net, "
                      "confirmed) VALUES (?, ?, ?, ?, 4100, 2, ?, ?, ?, ?, 1)",
                      [("q1", A, ROD, 3, NOW - 5 * HOUR, "sold", "p1", 300.0), ("q2", A, ROD, 3, NOW - 4 * HOUR, "sold", "p2", 350.0),
                       ("q3", A, ROD, 1, NOW - 3 * HOUR, None, "p2", None)])
        c.executemany("INSERT INTO auction_mail(event_id, char_id, key, qty, price, outcome, at, post_id) "
                      "VALUES (?, ?, ?, 3, 4100, 'sold', ?, ?)", [("m1", A, ROD, NOW - 2 * HOUR, "q1"), ("m2", A, ROD, NOW - HOUR, "q2")])
        c.executemany("INSERT INTO payouts(event_id, char_id, key, qty, money, at, mail_id, realized) VALUES (?, ?, ?, 3, 0, ?, ?, ?)",
                      [("o1", A, ROD, NOW - HOUR, "m1", 300.0), ("o2", A, ROD, NOW, "m2", 350.0)])
        c.execute("INSERT INTO crafts(event_id, char_id, spell_id, count, at, action_id, expected_net) "
                  "VALUES ('c', ?, 2663, 7, ?, 'craft', 971.0)", (A, NOW - 6 * HOUR))
        c.executemany("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, action_id, expected_net, confirmed) "
                      "VALUES (?, ?, ?, ?, 38, 'ah', ?, ?, ?, 1)",
                      [("u1", A, LINEN, 10, NOW - 7 * HOUR, "flip", 105.0), ("u2", A, CINCH, 1, NOW - 8 * HOUR, "debuy", 500.0),
                       ("u3", A, 2840, 14, NOW - 9 * HOUR, "bars", 0.0)])
    facts = {r[2] + str(r[3]) + str(r[0]): r[9:11] for r in db.journal([A], NOW - 30 * DAY)}
    paid, up = PostResult("sold", 300.0, 2, "bars", 3), PostResult(None, None, 2, "bars")
    chain = ChainFacts("bars", (paid, PostResult("sold", 350.0, 2, "bars", 3), up), 1)         # p3 is still to come
    assert facts[f"crafted{ROD}{NOW - 6 * HOUR}"] == (971.0, chain)
    assert facts[f"posted{ROD}{NOW - 5 * HOUR}"] == (300.0, paid)
    assert facts[f"posted{ROD}{NOW - 3 * HOUR}"] == (None, up)
    assert facts[f"bought{LINEN}{NOW - 7 * HOUR}"] == (105.0, ChainFacts("flip", (), 1, False, 0.5))   # half the buy
    assert facts[f"bought{CINCH}{NOW - 8 * HOUR}"] == (500.0, ChainFacts("debuy", (), 0, True))
    assert facts[f"bought2840{NOW - 9 * HOUR}"] == (0.0, None)                                  # a mat buy carries nothing
    db.close()


def test_T26_history_sums_a_partly_sold_posts_payouts_and_counts_its_unpaid_sales(tmp_path):
    db = Database.open(tmp_path / "j.db", NOW, None)
    with db.transaction():
        c = db.conn
        c.execute("INSERT INTO posts(post_id, event_id, char_id, key, stack, price, duration_h, deposit, at, confirmed, "
                  "outcome, sold_units, returned_units) VALUES ('p', 'p', ?, 4306, 110, 139, 24, 814, ?, 1, 'expired', 40, 70)",
                  (A, NOW - 3 * DAY))
        c.executemany("INSERT INTO auction_mail(event_id, char_id, key, qty, price, outcome, at, post_id) "
                      "VALUES (?, ?, 4306, ?, 139, ?, ?, 'p')",
                      [("s30", A, 30, "sold", NOW - 2 * DAY), ("x70", A, 70, "expired", NOW - DAY), ("s10", A, 10, "sold", NOW - DAY)])
        c.execute("INSERT INTO payouts(event_id, char_id, key, qty, money, at, mail_id, realized) "
                  "VALUES ('o30', ?, 4306, 30, 0, ?, 's30', 3500.0)", (A, NOW - DAY))
    [posted] = [r for r in db.journal([A], NOW - 30 * DAY) if r[2] == "posted"]
    assert posted[10] == PostResult("expired", 3500.0, 814, None, 110, 70, 1)      # s10 not taken yet
    with db.transaction():
        db.conn.execute("INSERT INTO payouts(event_id, char_id, key, qty, money, at, mail_id, realized) "
                        "VALUES ('o10', ?, 4306, 10, 0, ?, 's10', 1170.0)", (A, NOW))
    [posted] = [r for r in db.journal([A], NOW - 30 * DAY) if r[2] == "posted"]
    assert posted[10] == PostResult("expired", 4670.0, 814, None, 110, 70, 0)      # both payouts, summed
    db.close()
