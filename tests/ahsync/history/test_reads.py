"""Tests for ahsync/history/reads.py: the HistoryReader reads (SQLite implementation)."""
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


def test_items_and_priced_keys(db):
    sql(db, "INSERT INTO items(key, item_id, name, quality, ilvl, class, vendor_buy, vendor_sell, stack) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (2840, 2840, "Copper Bar", 1, 10, 7, 40, 10, 20),
        (9, 9, None, None, None, None, None, None, None))
    assert db.items()[2840].name == "Copper Bar" and db.items()[9].name == "9"
    sql(db, "INSERT INTO candles_h1(market, key, t, close, src) VALUES (?, ?, ?, ?, ?)", (M, 2840, 5 * DAY, 100, "ahf"))
    sql(db, "INSERT INTO current(market, key, price, qty, at, src) VALUES (?, ?, ?, ?, ?, ?)", (M, 7, 5, 1, 6 * DAY, "ahf"))
    assert db.priced_keys(M, 0) == [7, 2840] and db.priced_keys(M, 7 * DAY) == []


def test_daily_closes_prefer_local(db):
    sql(db, "INSERT INTO candles_d1(market, key, t, close, src) VALUES (?, ?, ?, ?, ?)",
        (M, 1, DAY, 100, "ahf"), (M, 1, DAY, 90, "local"), (M, 1, 2 * DAY, 120, "ahf"), (M, 1, 3 * DAY, None, "ahf"))
    assert db.daily_closes(M, 1, 0, 9 * DAY) == [(DAY, 90), (2 * DAY, 120)]


def test_volume_rows_by_day_and_source(db):
    sql(db, "INSERT INTO volume_d1(market, key, day, listed, sold, expired, src) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (M, 1, 10, 5, 4, 1, "books"), (M, 1, 10, 6, 5, None, "ahf"), (M, 1, 3, 1, 1, 1, "books"))
    assert db.volume_rows(M, 1, 5) == {10: {"ahf": (5, 0.0, 6), "books": (4, 1, 5)}}


def test_newest_complete_book_listing_the_item(db):
    sql(db, "INSERT INTO scans(scan_id, market, src, at, kind, coverage) VALUES (?, ?, ?, ?, ?, ?)",
        ("old", M, "local", 1 * DAY, "full", 1.0), ("new", M, "local", 2 * DAY, "full", 1.0),
        ("part", M, "local", 3 * DAY, "partial", 0.4))
    sql(db, "INSERT INTO book_entries(scan_id, key, price, stack, band, count) VALUES (?, ?, ?, ?, ?, ?)",
        ("old", 1, 50, 20, 3, 1), ("new", 1, 55, 20, 4, 2), ("new", 1, 45, 5, 3, 1), ("part", 1, 40, 1, 3, 1))
    assert db.newest_book(M, 1, 0) == (2 * DAY, [(45, 5, 3, 1), (55, 20, 4, 2)])
    assert db.newest_book(M, 2, 0) is None


HOUR = 3_600_000


def test_hourly_supply_prefers_local_and_counts_nothing_listed_as_zero(db):
    sql(db, "INSERT INTO candles_h1(market, key, t, close, qty, src) VALUES (?, ?, ?, ?, ?, ?)",
        (M, 1, 0, 50, 10, "ahf"), (M, 1, 0, 48, 12, "local"), (M, 1, HOUR, None, None, "ahf"),
        (M, 1, 2 * HOUR, 50, 9, "ahf"), (OTHER, 1, HOUR, 50, 99, "ahf"))
    assert db.hourly_supply(M, 1, 0, HOUR) == [(0, 12), (HOUR, 0)]


def test_hourly_sales_and_complete_scans_of_either_source(db):
    sql(db, "INSERT INTO sales_h1(market, key, t, sold_per_hour) VALUES (?, ?, ?, ?)",
        (M, 1, HOUR, 4.0), (M, 1, 0, 2.5), (M, 2, 0, 9.0))
    assert db.hourly_sales(M, 1, 0, HOUR) == [(0, 2.5), (HOUR, 4.0)]
    sql(db, "INSERT INTO scans(scan_id, market, src, at, kind, coverage) VALUES (?, ?, ?, ?, ?, ?)",
        ("a", M, "ahf", 5, "full", 1.0), ("l", M, "local", 3, "full", 1.0), ("p", M, "local", 4, "partial", 0.6))
    assert db.complete_scans(M, 0, 10) == [("l", 3), ("a", 5)]
