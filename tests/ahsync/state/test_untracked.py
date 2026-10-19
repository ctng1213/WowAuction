"""Tests for ahsync/state/untracked.py: untracked changes recorded while a file is stored (SPEC §2.2.1, D51)."""
from __future__ import annotations

import pytest

from ahsync.db.sqlite import Database
from ahsync.state import untracked
from tests.support.fake_clock import FakeClock

A = "Amaya-G"
T = 1_760_000_000_000
HOUR = 3_600_000
LINEN, EGG, BAR, ROD, SILK, THREAD = 2589, 6888, 2840, 6217, 4306, 2320


@pytest.fixture
def db(tmp_path):
    """Amaya's bags at T: 20 Linen, 5 eggs, 2 bars, 3 Silk; she knows the Copper Rod (2 bars)."""
    database = Database.open(tmp_path / "u.db", FakeClock().now_ms(), None)
    with database.transaction():
        c = database.conn
        c.execute("INSERT INTO snapshots(char_id, location, at) VALUES (?, 'bags', ?)", (A, T))
        c.executemany("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, 'bags', ?, ?, 0)",
                      [(A, LINEN, 20), (A, EGG, 5), (A, BAR, 2), (A, SILK, 3)])
        c.execute("INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) "
                  "VALUES ('Blacksmithing', 2663, ?, 1, 1)", (ROD,))
        c.execute("INSERT INTO reagents(profession, spell_id, key, count) VALUES ('Blacksmithing', 2663, ?, 2)", (BAR,))
    yield database
    database.close()


def later_bags(db: Database, rows: list[tuple[int, int]], at: int) -> None:
    """Replace Amaya's bags with a later snapshot."""
    db.conn.execute("UPDATE snapshots SET at = ? WHERE char_id = ? AND location = 'bags'", (at, A))
    db.conn.execute("DELETE FROM inventory WHERE char_id = ?", (A,))
    db.conn.executemany("INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, 'bags', ?, ?, 0)",
                        [(A, k, q) for k, q in rows])


def test_D51_a_newer_bag_snapshot_records_what_no_event_explains(db):
    with db.transaction():
        held = untracked.before(db.conn, {A: T + 2 * HOUR, "Nobody-G": T})
        assert list(held) == [A]                                              # no earlier snapshot: nothing to compare
        later_bags(db, [(LINEN, 32), (ROD, 1), (SILK, 1), (THREAD, 4)], T + 2 * HOUR)
        db.conn.execute("INSERT INTO crafts(event_id, char_id, spell_id, count, at) VALUES ('c', ?, 2663, 1, ?)", (A, T + HOUR))
        db.conn.execute("INSERT INTO posts(post_id, char_id, key, stack, price, at, confirmed) VALUES ('p', ?, ?, 2, 90, ?, 1)",
                        (A, SILK, T + HOUR))
        db.conn.execute("INSERT INTO purchases(event_id, char_id, key, qty, price, source, at, confirmed) "
                        "VALUES ('v', ?, ?, 4, 10, 'vendor', ?, 1)", (A, THREAD, T + HOUR))
        assert untracked.record(db.conn, held) == {LINEN, EGG}
    assert db.conn.execute("SELECT key, delta, at FROM untracked ORDER BY key").fetchall() == [
        (LINEN, 12, T + 2 * HOUR), (EGG, -5, T + 2 * HOUR)]


def test_an_older_or_same_bag_snapshot_records_nothing(db):
    with db.transaction():
        assert untracked.before(db.conn, {A: T}) == {}                        # not newer than the stored one
        held = untracked.before(db.conn, {A: T + HOUR})
        assert untracked.record(db.conn, held) == set()                       # the file's bags were not applied
    assert db.conn.execute("SELECT COUNT(*) FROM untracked").fetchone() == (0,)
