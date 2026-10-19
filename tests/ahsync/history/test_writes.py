"""Tests for ahsync/history/writes.py: local current and book volume writes."""
from __future__ import annotations

import pytest

from ahsync.db.sqlite import Database
from tests.support.fake_clock import FakeClock

M, OTHER = "forever.pvp.horde.us", "forever.pvp.alliance.us"
DAY = 86_400_000


@pytest.fixture
def db(tmp_path):
    """A fresh database."""
    database = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    yield database
    database.close()


def sql(db: Database, statement: str, *rows: tuple) -> None:
    """Insert rows with one statement."""
    with db.transaction():
        db.conn.executemany(statement, rows)




def test_local_current_moves_only_forward_in_time(db):
    with db.transaction():
        db.upsert_newer_current(M, [(1, 50, 4, 200)])
        db.upsert_newer_current(M, [(1, 40, 9, 100)])          # an older scan read late
    assert db.conn.execute("SELECT price, qty, at FROM current WHERE key = 1").fetchone() == (50, 4, 200)


def test_a_days_volume_is_replaced_whole(db):
    with db.transaction():
        db.replace_volume_day(M, 10, "books", {1: (4.0, 1.0, 5.0), 2: (1.0, 0.0, 0.0)})
        db.replace_volume_day(M, 10, "books", {1: (6.0, 0.0, 2.0)})
    assert db.conn.execute("SELECT key, sold FROM volume_d1 WHERE day = 10").fetchall() == [(1, 6.0)]


def test_an_hour_range_of_sales_is_replaced_whole(db):
    with db.transaction():
        db.replace_sales_hours(M, 0, 10, {1: {0: 4.0, 10: 2.0}, 2: {0: 1.0}})
        db.replace_sales_hours(M, 0, 5, {1: {0: 6.0, 20: 9.0}})          # 20 is outside the range
    assert db.conn.execute("SELECT key, t, sold_per_hour FROM sales_h1 ORDER BY key, t").fetchall() == [
        (1, 0, 6.0), (1, 10, 2.0)]
