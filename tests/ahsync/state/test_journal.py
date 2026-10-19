"""Tests for ahsync/state/journal.py: the journal behind the History page (SPEC §3.5, D47)."""
from __future__ import annotations

import pytest

from ahsync.db.sqlite import Database
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
    yield database
    database.close()


def test_D47_the_journal_reads_every_kind_of_event_with_the_plan_action_it_matched(db):
    rows = db.journal([A], NOW - 30 * DAY)
    assert [(r[2], r[3]) for r in rows] == [
        ("paid_out", ROD), ("sold", ROD), ("bought", LINEN), ("bought_vendor", 2320), ("crafted", ROD),
        ("disenchanted", CINCH), ("mailed", LINEN), ("missed", SILK), ("skipped", SILK), ("posted", ROD)]
    by_kind = {r[2]: r for r in rows}
    assert by_kind["paid_out"][7:] == (3915, 1915.0)
    assert by_kind["sold"][7:] == (NOW - 3 * DAY, 20)                       # the post it resolved
    assert by_kind["bought"][6] == ("done", 20, 20)
    assert by_kind["missed"][6:8] == (("missed", 0, 40), "buy")
    assert by_kind["posted"][5:] == (4100, None, 24, 20)
    assert by_kind["mailed"][7] == B and by_kind["disenchanted"][7] == f"{DUST},2"
    assert db.journal([], NOW) == []
