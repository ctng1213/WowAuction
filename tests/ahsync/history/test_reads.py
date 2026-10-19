"""Tests for ahsync/history/reads.py: the HistoryReader reads (SQLite implementation)."""
from __future__ import annotations

import pytest

from ahsync.db.sqlite import Database
from ahsync.history.reads import HistoryWindow, ItemHistory
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
    sql(db, "INSERT INTO book_entries(sid, key, price, stack, band, count) "
            "VALUES ((SELECT sid FROM scans WHERE scan_id = ?), ?, ?, ?, ?, ?)",
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


def test_market_history_streams_the_same_values_as_the_per_item_reads(db):
    """AR7: one bulk read per market gives, item by item, exactly what the per-item reads give."""
    sql(db, "INSERT INTO candles_h1(market, key, t, close, qty, src) VALUES (?, ?, ?, ?, ?, ?)",
        (M, 1, 0, 50, 10, "ahf"), (M, 1, 0, None, 12, "local"), (M, 1, HOUR, 48, 9, "local"), (M, 1, HOUR, 51, 7, "ahf"),
        (M, 3, HOUR, 70, 1, "ahf"), (OTHER, 1, 0, 1, 1, "ahf"))
    sql(db, "INSERT INTO candles_d1(market, key, t, close, src) VALUES (?, ?, ?, ?, ?)",
        (M, 1, 0, 100, "ahf"), (M, 1, 0, 90, "local"), (M, 2, DAY, 30, "ahf"))
    sql(db, "INSERT INTO sales_h1(market, key, t, sold_per_hour) VALUES (?, ?, ?, ?)", (M, 1, 0, 2.5), (M, 3, HOUR, 1.0))
    sql(db, "INSERT INTO current(market, key, price, qty, at, src) VALUES (?, ?, ?, ?, ?, ?)",
        (M, 1, 49, 3, HOUR, "ahf"), (M, 1, 48, 4, HOUR, "local"), (M, 2, 30, 1, DAY, "ahf"))
    sql(db, "INSERT INTO volume_d1(market, key, day, listed, sold, expired, src) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (M, 1, 0, 5, 4, None, "books"), (M, 3, 0, 1, 1, 0, "ahf"))
    sql(db, "INSERT INTO scans(scan_id, market, src, at, kind, coverage) VALUES (?, ?, ?, ?, ?, ?)",
        ("b", M, "local", HOUR, "full", 1.0), ("a", M, "ahf", HOUR, "full", 1.0), ("old", M, "local", 0, "full", 1.0))
    sql(db, "INSERT INTO book_entries(sid, key, price, stack, band, count) "
            "VALUES ((SELECT sid FROM scans WHERE scan_id = ?), ?, ?, ?, ?, ?)",
        ("a", 1, 47, 1, 2, 1), ("b", 1, 46, 1, 2, 1), ("old", 1, 40, 1, 1, 1), ("old", 3, 70, 1, 4, 1))
    window = HistoryWindow(0, 2 * DAY, 0, 0)
    bulk = list(db.market_history(M, window))
    assert [h.key for h in bulk] == db.priced_keys(M, 0) == [1, 2, 3]
    for h in bulk:
        assert h == ItemHistory(h.key, db.hourly_closes(M, h.key, 0, 2 * DAY), db.hourly_supply(M, h.key, 0, 2 * DAY),
                                db.hourly_sales(M, h.key, 0, 2 * DAY), db.daily_closes(M, h.key, 0, 2 * DAY),
                                db.current_price(M, h.key), db.volume_rows(M, h.key, 0), db.newest_book(M, h.key, 0))
    one = bulk[0]
    assert (one.hourly_closes, one.hourly_supply) == ([(0, 50), (HOUR, 48)], [(0, 12), (HOUR, 9)])   # local first;
    assert one.book == (HOUR, [(47, 1, 2, 1)])     # a local hour without a close falls back to AHF's; ties: scan id



def test_IU6_scans_since_reads_your_newer_scan_prices_and_the_newest_complete_book(db):
    sql(db, "INSERT INTO scans(scan_id, market, src, at, kind, coverage) VALUES (?, ?, ?, ?, ?, ?)",
        ("old", M, "local", 1 * DAY, "full", 1.0), ("new", M, "local", 3 * DAY, "full", 1.0),
        ("part", M, "local", 4 * DAY, "partial", 0.4), ("ahf", M, "ahf", 5 * DAY, "full", 1.0))
    sql(db, "INSERT INTO book_entries(sid, key, price, stack, band, count) "
            "VALUES ((SELECT sid FROM scans WHERE scan_id = ?), ?, ?, ?, ?, ?)",
        ("old", 1, 50, 20, 3, 1), ("new", 1, 55, 20, 4, 2), ("new", 1, 45, 5, 3, 1), ("ahf", 1, 40, 1, 3, 1))
    sql(db, "INSERT INTO current(market, key, price, qty, at, src) VALUES (?, ?, ?, ?, ?, ?)",
        (M, 1, 45, 5, 4 * DAY, "local"), (M, 2, 9, 1, 1 * DAY, "local"), (M, 3, 7, 1, 5 * DAY, "ahf"))
    prices, books = db.scans_since(M, 2 * DAY)
    assert prices == {1: (45, 5, 4 * DAY)}                                   # item 2's scan is older; AHF never counts
    assert books == {1: (3 * DAY, [(45, 5, 3, 1), (55, 20, 4, 2)])}          # the newest complete local book
    assert db.scans_since(M, 5 * DAY) == ({}, {})


def test_SRC3_remote_sources_rank_as_configured_after_your_own_scans(db):
    from ahsync.analysis.market import history_window
    t = 5 * DAY
    sql(db, "INSERT INTO candles_h1(market, key, t, close, qty, src) VALUES (?, ?, ?, ?, ?, ?)",
        (M, 1, t, 100, 5, "ahf"), (M, 1, t, 90, 6, "blizzard"), (M, 1, t + HOUR, 80, 7, "ahf"),
        (M, 1, t + HOUR, 70, 8, "blizzard"), (M, 1, t + HOUR, 75, 9, "local"), (M, 1, t + 2 * HOUR, 60, 1, "zeta"),
        (M, 1, t + 2 * HOUR, 65, 1, "omega"))
    sql(db, "INSERT INTO current(market, key, price, qty, at, src) VALUES (?, ?, ?, ?, ?, ?)",
        (M, 1, 100, 5, t, "ahf"), (M, 1, 90, 6, t, "blizzard"))
    window = history_window(t + 3 * HOUR, ("local", "blizzard", "ahf"))
    [item] = db.market_history(M, window)
    assert item.hourly_closes == [(t, 90), (t + HOUR, 75), (t + 2 * HOUR, 65)]   # unranked: by name
    assert item.current.src == "blizzard"
    [default] = db.market_history(M, history_window(t + 3 * HOUR))
    assert default.hourly_closes[0] == (t, 100) and default.current.src == "ahf"
