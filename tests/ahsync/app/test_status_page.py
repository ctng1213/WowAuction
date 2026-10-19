"""Tests for ahsync/app/status_page.py: the Status page built on request (SPEC §3.5 Status)."""
from __future__ import annotations

from ahsync.app import status_page
from ahsync.config import Config
from ahsync.db.sqlite import Database
from tests.support.fake_clock import FakeClock

HOUR, DAY = 3_600_000, 86_400_000
M = "forever.pvp.horde.us"


def test_the_page_reads_status_lines_sync_state_prices_and_characters(tmp_path):
    now = FakeClock().now_ms()
    db = Database.open(tmp_path / "ahsync.db", now, None)
    try:
        with db.transaction():
            db.set_status("realm:R", "warn", "unknown_realm", {"realm": "R", "faction": "Horde"}, now)
            db.conn.execute("INSERT INTO characters(id, realm, faction, market, updated) VALUES ('A-R', 'R', 'Horde', NULL, ?)",
                            (now - HOUR,))
        db.update_sync_state(M, last_hour=now // HOUR - 1, last_run=now - 10 * 60_000)
        db.upsert_current(M, "ahf", [(2589, 45, 20, now - 9 * DAY)])
        facts = status_page.status_facts(db, Config(markets=(M,)), now)
        assert facts.key_set is False and facts.db_bytes > 0 and facts.retention_days == 90
        assert (facts.markets[0].newest_price_ms, facts.markets[0].items_priced) == (now - 9 * DAY, 1)
        assert facts.characters[0].market is None
        page = status_page.status_page(db, Config(markets=(M,), api_key="k"), now, 0)
        assert [a["title"] for a in page["alerts"]] == ["AHF data is old", "Realm not mapped to a market"]
        assert page["cards"][0]["badge"] == "data 9 d old" and page["cards"][1]["lines"][0][1] == "set"
    finally:
        db.close()


def test_a_database_file_that_cant_be_read_counts_as_0_bytes(tmp_path):
    class Missing:
        """A database whose files are gone."""

        path = tmp_path / "gone.db"

    assert status_page._db_bytes(Missing()) == 0


def test_calibration_reads_resolved_posts_of_the_last_90_days_with_a_planned_chance(tmp_path):
    now = FakeClock().now_ms()
    db = Database.open(tmp_path / "ahsync.db", now, None)
    try:
        with db.transaction():
            db.conn.executemany(
                "INSERT INTO posts(post_id, char_id, key, stack, price, at, outcome, p_at_post) VALUES (?, 'A-R', 1, 1, 1, ?, ?, ?)",
                [("sold", now - DAY, "sold", 0.8), ("gone", now - DAY, "expired", 0.8), ("open", now - DAY, None, 0.8),
                 ("unplanned", now - DAY, "sold", None), ("old", now - 91 * DAY, "sold", 0.8),
                 ("pulled", now - DAY, "cancelled", 0.8)])
        c = status_page.status_facts(db, Config(markets=(M,)), now).calibration
        assert (c.posts, c.buckets[0].actual) == (2, 0.5)
    finally:
        db.close()
