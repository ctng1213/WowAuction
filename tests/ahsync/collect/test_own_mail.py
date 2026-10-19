"""Tests for ahsync/collect/own_mail.py: your auction mail as a daily volume source (V9)."""
from __future__ import annotations

import pytest

from ahsync.collect.own_mail import recompute_mail_volume
from ahsync.db.sqlite import Database
from tests.support.fake_clock import FakeClock

M = "forever.pvp.horde.us"
DAY = 86_400_000
D = 20_000
LINEN = 2589


@pytest.fixture
def db(tmp_path):
    """A fresh database."""
    database = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    yield database
    database.close()


def mail(db: Database, event_id: str, char_id: str, outcome: str, qty: int, at_ms: int) -> None:
    """Store one auction mail."""
    db.conn.execute("INSERT INTO auction_mail(event_id, char_id, key, qty, price, outcome, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (event_id, char_id, LINEN, qty, 100, outcome, at_ms))


def rows(db: Database) -> list[tuple]:
    """Stored mail volume and covers."""
    return db.conn.execute(
        "SELECT v.day, v.key, v.sold, v.expired, c.kind, c.covered FROM volume_d1 v JOIN volume_cover c "
        "ON c.market = v.market AND c.day = v.day AND c.src = v.src WHERE v.src = 'mail' ORDER BY v.day").fetchall()


def test_V9_your_sold_and_expired_mail_is_stored_as_a_days_own_volume(db):
    with db.transaction():
        mail(db, "e1", "Amaya-G", "sold", 2, D * DAY + 1000)
        mail(db, "e2", "Amaya-G", "sold", 1, D * DAY + 2000)
        mail(db, "e3", "Brugan-G", "expired", 1, D * DAY + 3000)
        mail(db, "e4", "Amaya-G", "cancelled", 5, D * DAY + 4000)
        mail(db, "e5", "Other-G", "sold", 9, D * DAY + 5000)              # not on this market
        assert recompute_mail_volume(db, M, ["Amaya-G", "Brugan-G"], D - 1, D + 1) == 1
    assert rows(db) == [(D, LINEN, 3.0, 1.0, "own", None)]
    with db.transaction():
        recompute_mail_volume(db, M, ["Amaya-G", "Brugan-G"], D - 1, D + 1)
    assert rows(db) == [(D, LINEN, 3.0, 1.0, "own", None)]
    with db.transaction():
        assert recompute_mail_volume(db, M, [], D - 1, D + 1) == 0
    assert rows(db) == []
