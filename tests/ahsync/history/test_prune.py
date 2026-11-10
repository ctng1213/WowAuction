"""Retention (SPEC §3.2): scenario S6 and the other windows."""
from __future__ import annotations

from ahsync.clock import DAY_MS
from ahsync.db.sqlite import BookScan, Database
from ahsync.history.prune import prune
from tests.support.builders import duration_ms, item_key
from tests.support.fake_clock import FakeClock
from tests.support.fake_transport import DEFAULT_MARKET

MARKET = DEFAULT_MARKET
NOW = FakeClock().now_ms()


def open_db(tmp_path) -> Database:
    """A fresh database."""
    return Database.open(tmp_path / "ahsync.db", NOW, None)


def test_S6_rows_89d23h_and_90d01h_old_keep_the_first_and_delete_the_second(tmp_path):
    db = open_db(tmp_path)
    linen = item_key("Linen Cloth")
    kept, dropped = NOW - duration_ms("89d23h"), NOW - duration_ms("90d1h")
    db.upsert_candles("candles_h1", MARKET, [(linen, t, 50, 50, 50, 50, 1) for t in (kept, dropped)], "ahf")

    report = prune(db, NOW)

    assert db.conn.execute("SELECT t FROM candles_h1").fetchall() == [(kept,)]
    assert report.deleted["candles_h1"] == 1
    days = [(NOW - duration_ms(age)) // 86_400_000 for age in ("80d", "100d")]   # sold prices: by whole days (BZ52)
    with db.transaction():
        for day in days:
            db.replace_sold_prices_day(MARKET, day, "blizzard", {linen: {50: 1.0}})
    assert prune(db, NOW).deleted["sold_prices_d1"] == 1
    assert db.conn.execute("SELECT day FROM sold_prices_d1").fetchall() == [(days[0],)]
    db.close()


def test_books_keep_30_days_and_rule_tombstones_90(tmp_path):
    db = open_db(tmp_path)
    db.store_book(BookScan("old", MARKET, "ahf", None, NOW - 31 * DAY_MS), [(1, 10, 1, 4, 1)])
    db.store_book(BookScan("new", MARKET, "ahf", None, NOW - 29 * DAY_MS), [(1, 10, 1, 4, 1)])
    db.conn.executemany("INSERT INTO item_rules(key, rule, set_at, source) VALUES(?, ?, ?, 'addon')",
                        [(1, "none", NOW - 91 * DAY_MS), (2, "no_buy", NOW - 91 * DAY_MS), (3, "none", NOW - DAY_MS)])
    db.conn.execute("INSERT INTO volume_d1(market, key, day, src) VALUES(?, 1, ?, 'books')",
                    (MARKET, NOW // DAY_MS - 91))
    db.record_fetch_failure(MARKET, NOW // 3_600_000 - 91 * 24, "bad", NOW)

    report = prune(db, NOW)

    assert [r[0] for r in db.conn.execute("SELECT scan_id FROM scans")] == ["new"]
    assert db.count("book_entries") == 1
    assert sorted(r[0] for r in db.conn.execute("SELECT key FROM item_rules")) == [2, 3]
    assert db.count("volume_d1") == 0 and db.count("fetch_failures") == 0
    assert report.total == 4
    db.close()


def test_skips_follow_the_90_day_retention(tmp_path):
    db = open_db(tmp_path)
    db.record_skip("A-R:1:0", "A-R", "old", NOW - duration_ms("90d1h"))
    db.record_skip("A-R:2:0", "A-R", "kept", NOW - duration_ms("89d23h"))

    report = prune(db, NOW)

    assert [r[0] for r in db.conn.execute("SELECT action_id FROM skips")] == ["kept"]
    assert report.deleted["skips"] == 1
    db.close()


def test_closed_positions_go_90_days_after_they_close_and_open_ones_stay(tmp_path):
    db = open_db(tmp_path)
    with db.transaction():
        db.conn.executemany("INSERT INTO positions(position_id, char_id, key, state, opened_at, units, units_left, "
                            "cost_total, cost_left, closed_at) VALUES (?, 'A-R', 1, ?, 0, 1, 0, 10, 0, ?)",
                            [("old", "Completed", NOW - duration_ms("90d1h")), ("recent", "Completed", NOW - DAY_MS),
                             ("open", "Holding", None)])

    report = prune(db, NOW)

    assert [r[0] for r in db.conn.execute("SELECT position_id FROM positions ORDER BY position_id")] == ["open", "recent"]
    assert report.deleted["positions"] == 1
    db.close()
