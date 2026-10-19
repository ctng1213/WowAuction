"""Tests for ahsync/collect/local_scans.py: own full scans as local prices, closes, book volume (V19-V21) and hourly sales (V27)."""
from __future__ import annotations

import pytest

from ahsync.collect.local_scans import derive_from_scans, prices_from_book, recompute_book_volume, recompute_sales_hours
from ahsync.db.sqlite import BookScan, Database
from tests.support.fake_clock import FakeClock

M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
D = 20_000                                  # a day number
LINEN, BAR = 2589, 2840
LONG = 3


@pytest.fixture
def db(tmp_path):
    """A fresh database."""
    database = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    yield database
    database.close()


def scan(db: Database, scan_id: str, at_ms: int, rows: list[tuple], coverage: float = 1.0) -> tuple:
    """Store one local scan and return its derive_from_scans tuple."""
    kind = "full" if coverage >= 1 else "partial"
    db.store_book(BookScan(scan_id, M, "local", "A-R", at_ms, kind, coverage), rows)
    return (M, scan_id, at_ms, coverage >= 1)


def volume(db: Database, day: int) -> list[tuple]:
    """Stored books volume for a day."""
    return db.conn.execute("SELECT key, sold, expired, listed FROM volume_d1 WHERE market = ? AND day = ? "
                           "AND src = 'books' ORDER BY key", (M, day)).fetchall()


def test_V19_two_complete_scans_two_hours_apart_feed_the_days_volume_once(db):
    first = scan(db, "s1", D * DAY + 10 * HOUR, [(LINEN, 50, 20, LONG, 5)])
    second = scan(db, "s2", D * DAY + 12 * HOUR, [(LINEN, 50, 20, LONG, 3)])
    with db.transaction():
        derive_from_scans(db, [first, second])
    assert volume(db, D) == [(LINEN, 40.0, 0.0, 0.0)]
    with db.transaction():
        derive_from_scans(db, [first, second])          # the same files read again
    assert volume(db, D) == [(LINEN, 40.0, 0.0, 0.0)]


def test_V20_a_partial_scan_never_pairs(db):
    full = scan(db, "s1", D * DAY + 10 * HOUR, [(LINEN, 50, 20, LONG, 5)])
    partial = scan(db, "s2", D * DAY + 12 * HOUR, [(LINEN, 50, 20, LONG, 3)], coverage=0.6)
    with db.transaction():
        derive_from_scans(db, [full, partial])
    assert volume(db, D) == []


def test_V21_a_scan_becomes_local_current_and_an_hourly_close(db):
    at = D * DAY + 14 * HOUR + 20 * 60_000
    entry = scan(db, "s1", at, [(LINEN, 45, 20, LONG, 2), (LINEN, 50, 20, LONG, 1)])
    with db.transaction():
        assert derive_from_scans(db, [entry]) == {LINEN}
    assert db.conn.execute("SELECT price, qty, at FROM current WHERE key = ? AND src = 'local'",
                           (LINEN,)).fetchone() == (45, 40, at)
    assert db.conn.execute("SELECT t, close, qty FROM candles_h1 WHERE key = ? AND src = 'local'",
                           (LINEN,)).fetchone() == (D * DAY + 14 * HOUR, 45, 60)


def test_an_older_scan_read_late_neither_rolls_back_current_nor_misses_its_pair(db):
    newer = scan(db, "s2", D * DAY + 12 * HOUR, [(LINEN, 50, 20, LONG, 3)])
    with db.transaction():
        derive_from_scans(db, [newer])
    older = scan(db, "s1", D * DAY + 10 * HOUR, [(LINEN, 48, 20, LONG, 5)])
    with db.transaction():
        derive_from_scans(db, [older])
    assert db.conn.execute("SELECT price FROM current WHERE key = ? AND src = 'local'", (LINEN,)).fetchone() == (50,)
    assert volume(db, D)[0][1] == 100.0          # 48c group: 5 gone; 50c group: 3 new


def test_a_pair_across_midnight_counts_for_the_later_day_and_far_scans_dont_pair(db):
    late = scan(db, "s1", D * DAY + 23 * HOUR, [(BAR, 100, 5, LONG, 4)])
    early = scan(db, "s2", (D + 1) * DAY + 1 * HOUR, [(BAR, 100, 5, LONG, 1)])
    far = scan(db, "s3", (D + 1) * DAY + 9 * HOUR, [])
    with db.transaction():
        derive_from_scans(db, [late, early, far])
    assert volume(db, D) == [] and volume(db, D + 1) == [(BAR, 15.0, 0.0, 0.0)]


def test_prices_from_book_and_an_empty_day(db):
    assert prices_from_book([(1, 50, 20, 3, 1), (1, 45, 5, 3, 2), (2, 10, 1, 1, 3)]) == {1: (45, 10, 30), 2: (10, 3, 3)}
    with db.transaction():
        assert recompute_book_volume(db, M, D) == {}


def test_ingest_of_a_file_with_scans_writes_local_prices(tmp_path):
    from pathlib import Path

    from ahsync.state import savedvars as sv
    from ahsync.state.importer import apply_data
    database = Database.open(tmp_path / "x.db", FakeClock().now_ms(), None)
    golden = Path(__file__).resolve().parents[2] / "fixtures" / "savedvars" / "a1.lua"
    data = sv.read_ahprofit_file(golden)
    result = apply_data(database, data, {"Grommash": "pvp"}, FakeClock().now_ms())
    stored = database.conn.execute("SELECT COUNT(*) FROM scans WHERE src = 'local'").fetchone()[0]
    priced = database.conn.execute("SELECT COUNT(*) FROM current WHERE src = 'local'").fetchone()[0]
    assert (stored > 0) == (priced > 0)
    assert result.problems == () or all("scan" not in p for p in result.problems)
    database.close()


def sales(db: Database) -> list[tuple]:
    """Stored hourly sales rates."""
    return db.conn.execute("SELECT key, t, sold_per_hour FROM sales_h1 WHERE market = ? ORDER BY key, t", (M,)).fetchall()


def test_V27_ahf_and_local_books_pair_partial_ones_never_and_rereading_changes_nothing(db):
    ten = D * DAY + 10 * HOUR
    db.store_book(BookScan("ahf:1", M, "ahf", None, ten, "full", 1.0), [(LINEN, 50, 20, LONG, 5)])
    with db.transaction():
        recompute_sales_hours(db, M, [ten])
    new = [scan(db, "p", ten + HOUR // 3, [(LINEN, 50, 20, LONG, 1)], coverage=0.6),
           scan(db, "s", ten + HOUR, [(LINEN, 50, 20, LONG, 3)])]
    with db.transaction():
        derive_from_scans(db, new)
    assert sales(db) == [(LINEN, ten, 40.0)]
    with db.transaction():
        derive_from_scans(db, new)
        recompute_sales_hours(db, M, [ten])
    assert sales(db) == [(LINEN, ten, 40.0)]


def test_sales_hours_of_books_far_apart_are_recomputed_separately(db):
    ten = D * DAY + 10 * HOUR
    for i, at in enumerate((ten, ten + HOUR, ten + 9 * HOUR, ten + 10 * HOUR)):
        db.store_book(BookScan(f"a{i}", M, "ahf", None, at, "full", 1.0), [(LINEN, 50, 20, LONG, 5 - i)])
    with db.transaction():
        recompute_sales_hours(db, M, [ten + 10 * HOUR, ten, ten + HOUR, ten + 9 * HOUR])
    assert sales(db) == [(LINEN, ten, 20.0), (LINEN, ten + 9 * HOUR, 20.0)]


def test_V24_a_new_method_recomputes_estimates_with_stored_books_and_counts_the_rest(db, monkeypatch):
    from ahsync.collect import local_scans
    monkeypatch.setattr(local_scans, "VOLUME_METHOD_VERSION", 1)
    first = scan(db, "s1", D * DAY + 10 * HOUR, [(LINEN, 50, 20, LONG, 5)])
    second = scan(db, "s2", D * DAY + 12 * HOUR, [(LINEN, 50, 20, LONG, 3)])
    with db.transaction():
        derive_from_scans(db, [first, second])                       # version 1 rows for day D
        db.replace_volume_day(M, D - 40, "books", {BAR: (9.0, 0.0, 0.0)}, 1)    # scans long pruned
        db.replace_volume_day(M, D, "ahf", {LINEN: (33.0, 0.0, 0.0)})            # a confirmed source
    stamps = lambda: db.conn.execute("SELECT day, src, method FROM volume_d1 ORDER BY day, src").fetchall()  # noqa: E731
    assert stamps() == [(D - 40, "books", 1), (D, "ahf", None), (D, "books", 1)]
    monkeypatch.setattr(local_scans, "VOLUME_METHOD_VERSION", 2)
    with db.transaction():
        assert local_scans.recompute_stale_volume(db, M) == 1             # day D - 40 can't be recomputed
    assert stamps() == [(D - 40, "books", 1), (D, "ahf", None), (D, "books", 2)]
    assert volume(db, D) == [(LINEN, 40.0, 0.0, 0.0)]
    assert db.conn.execute("SELECT sold FROM volume_d1 WHERE src = 'ahf'").fetchall() == [(33.0,)]    # untouched
    monkeypatch.setattr(local_scans, "VOLUME_METHOD_VERSION", 1)
    with db.transaction():
        assert local_scans.recompute_stale_volume(db, M) == 0             # nothing older than the current method


def cover(db: Database, day: int) -> list[tuple]:
    """Stored books cover for a day."""
    return db.conn.execute("SELECT kind, covered FROM volume_cover WHERE market = ? AND day = ? AND src = 'books'",
                           (M, day)).fetchall()


def test_V29_books_of_every_source_pair_for_daily_volume_and_record_the_hours_observed(db):
    from ahsync.collect.local_scans import book_days
    eighteen = D * DAY + 18 * HOUR
    db.store_book(BookScan("ahf1", M, "ahf", None, eighteen, "full", 1.0), [(LINEN, 50, 20, LONG, 5)])
    mine = scan(db, "s1", eighteen + 40 * 60_000, [(LINEN, 50, 20, LONG, 4)])
    with db.transaction():
        derive_from_scans(db, [mine])
    assert volume(db, D) == [(LINEN, 20.0, 0.0, 0.0)]
    db.store_book(BookScan("ahf2", M, "ahf", None, eighteen + HOUR, "full", 1.0), [(LINEN, 50, 20, LONG, 3)])
    with db.transaction():
        for day in sorted(book_days(db, M, [eighteen + HOUR])):          # as catch-up does for AHF books
            recompute_book_volume(db, M, day)
    assert volume(db, D) == [(LINEN, 40.0, 0.0, 0.0)]
    expected = ",".join("3600" if h == 18 else "0" for h in range(24))
    assert cover(db, D) == [("groups", expected)]
    assert book_days(db, M, [eighteen]) == {D}


def test_a_day_without_pairs_has_no_cover(db):
    lone = scan(db, "s1", D * DAY + 10 * HOUR, [(LINEN, 50, 20, LONG, 5)])
    with db.transaction():
        derive_from_scans(db, [lone])
    assert (volume(db, D), cover(db, D)) == ([], [])
