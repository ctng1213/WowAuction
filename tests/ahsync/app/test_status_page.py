"""Tests for ahsync/app/status_page.py: the Status page built on request (SPEC §3.5 Status)."""
from __future__ import annotations

from ahsync.app import status_page
from ahsync.config import Config, MarketSources
from ahsync.db.sqlite import Database
from tests.support.fake_clock import FakeClock

HOUR, DAY = 3_600_000, 86_400_000
M = "forever.pvp.horde.us"
AHF_PRIMARY = {M: MarketSources(primary="ahf")}   # these tests predate M19: AHF is their primary


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
        facts = status_page.status_facts(db, Config(markets=(M,), sources=AHF_PRIMARY), now)
        assert facts.key_set is False and facts.db_bytes > 0 and facts.retention_days == 90
        assert (facts.markets[0].newest_price_ms, facts.markets[0].items_priced) == (now - 9 * DAY, 1)
        assert facts.characters[0].market is None
        page = status_page.status_page(db, Config(markets=(M,), api_key="k", sources=AHF_PRIMARY), now, 0)
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


def test_K7_the_profit_line_sums_the_last_90_days_of_every_characters_plan_events(tmp_path):
    now = FakeClock().now_ms()
    db = Database.open(tmp_path / "ahsync.db", now, None)
    try:
        with db.transaction():
            db.conn.executemany("INSERT INTO characters(id, realm, faction, market) VALUES (?, 'R', 'Horde', ?)",
                                [("A-R", M), ("B-R", None)])
            db.conn.executemany(
                "INSERT INTO posts(post_id, char_id, key, stack, price, deposit, at, outcome, expected_net, confirmed) "
                "VALUES (?, ?, 1, 1, 1, 3, ?, ?, ?, 1)",
                [("paid", "A-R", now - DAY, "sold", 447.0), ("gone", "B-R", now - DAY, "expired", 120.0),
                 ("old", "A-R", now - 91 * DAY, "expired", 500.0), ("up", "A-R", now - DAY, None, 250.0)])
            db.conn.execute("INSERT INTO auction_mail(event_id, char_id, key, qty, price, outcome, at, post_id) "
                            "VALUES ('m', 'A-R', 1, 1, 1, 'sold', ?, 'paid')", (now - DAY,))
            db.conn.execute("INSERT INTO payouts(event_id, char_id, key, qty, money, at, mail_id, realized) "
                            "VALUES ('o', 'A-R', 1, 1, 0, ?, 'm', 410.0)", (now - DAY,))
        sums = status_page.status_facts(db, Config(markets=(M,)), now).profit
        assert (sums.expected, sums.realized, sums.resolved, sums.open_posts) == (567.0, 407.0, 2, 1)
    finally:
        db.close()


def test_K4_backtests_pair_your_scan_estimates_with_confirmed_rows_of_the_same_item_and_day(tmp_path):
    now = FakeClock().now_ms()
    db = Database.open(tmp_path / "ahsync.db", now, None)
    try:
        today = now // DAY
        with db.transaction():
            db.conn.executemany("INSERT INTO volume_d1(market, key, day, listed, sold, expired, src) VALUES (?, ?, ?, 0, ?, 0, ?)",
                                [(M, 1, today, 8.0, "books"), (M, 1, today, 10.0, "blizzard"),   # 0.8
                                 (M, 2, today, 5.0, "books"), (M, 2, today, 0.0, "blizzard"),    # confirmed 0: no ratio
                                 (M, 3, today - 40, 9.0, "books"), (M, 3, today - 40, 9.0, "blizzard"),   # too old
                                 (M, 4, today, 4.0, "books"), (M, 4, today, 9.0, "mail"),        # mail: no comparison
                                 (M, 5, today - 1, 1.0, "books"), (M, 5, today - 1, 12.0, "blizzard")])   # 2 h of 24
            db.conn.executemany("INSERT INTO volume_cover(market, day, src, kind, covered) VALUES (?, ?, ?, ?, ?)",
                                [(M, d, "blizzard", "ids", None) for d in (today - 40, today - 1, today)]
                                + [(M, today, "mail", "own", None),
                                   (M, today - 1, "books", "groups", ",".join(["3600"] * 2 + ["0"] * 22))])
        [(src, b)] = status_page.status_facts(db, Config(markets=(M,)), now).markets[0].backtests
        assert (src, b.days, b.median_ratio, b.within) == ("blizzard", 2, 0.9, 2)   # 0.8 and 1 × 12 ÷ 12 = 1.0, both within ±25%
    finally:
        db.close()


def test_the_status_facts_read_the_ahledger_tables_updated_time_and_item_count_come_from_the_database(tmp_path):
    now = FakeClock().now_ms()
    updated_s = now // 1000 - 26 * 3600
    db = Database.open(tmp_path / "ahsync.db", now, None)
    try:
        facts = status_page.status_facts(db, Config(markets=(M,)), now).markets[0]
        assert (facts.ahledger_on, facts.ahledger_updated_ms, facts.ahledger_items) == (True, None, 0)
        with db.transaction():
            db.upsert_ahledger_rows(M, updated_s, [(k, 5, 5, 5, 5, 5, 5) for k in (1, 2, 3)])
            db.upsert_ahledger_rows(M, updated_s - 3600, [(9, 5, 5, 5, 5, 5, 5)])   # an older row of another table
            db.set_source_state(M, "ahledger", updated_ms=updated_s * 1000)
        facts = status_page.status_facts(db, Config(markets=(M,)), now).markets[0]
        assert (facts.ahledger_updated_ms, facts.ahledger_items) == (updated_s * 1000, 3)
        line = [l for l in status_page.status_page(db, Config(markets=(M,)), now, 0)["cards"][0]["lines"]
                if l[0] == "AHledger table"]
        assert line == [["AHledger table", "26 h old · 3 items", "amber", "https://ahledger.com"]]
        off = Config(markets=(M,), sources={M: MarketSources(addresses={"ahledger": ""})})
        assert status_page.status_facts(db, off, now).markets[0].ahledger_on is False
    finally:
        db.close()


def test_D99_a_cloud_markets_coverage_card_shows_the_summarys_hours(tmp_path):
    from ahsync.config import CloudConfig
    from ahsync.intel.contract import MarketIntelligence
    now = FakeClock().now_ms()
    end = now // HOUR
    db = Database.open(tmp_path / "ahsync.db", now, None)
    try:
        with db.transaction():
            db.stage_intel(MarketIntelligence(M, now - 6 * HOUR, now - 18 * 60_000, 5, {},
                                              coverage=((end - 48, end - 38), (end - 34, end - 1))))
        db.publish_refresh([M], with_intel=[M])
        cfg = Config(markets=(M,), cloud=CloudConfig("https://x.cloudfront.net"),
                     sources={M: MarketSources("blizzard", ("blizzard",), {})})
        card = status_page.status_page(db, cfg, now, 0)["coverage"][0]
        rows = {r["src"]: r for r in card["sources"]}
        assert rows["blizzard"]["label"] == "Blizzard (cloud)" and rows["blizzard"]["hours"] == 11 + 34
        assert rows["local"]["hours"] == 0
    finally:
        db.close()


def test_D104_the_status_payload_carries_a_cloud_markets_note_and_no_note_for_a_local_one(tmp_path):
    from ahsync.config import CloudConfig
    from ahsync.intel.contract import MarketIntelligence
    now = FakeClock().now_ms()
    end = now // HOUR
    db = Database.open(tmp_path / "ahsync.db", now, None)
    try:
        with db.transaction():
            db.stage_intel(MarketIntelligence(M, now - 6 * HOUR, now - 18 * 60_000, 5, {},
                                              coverage=((end - 48, end - 38), (end - 34, end - 7))))
        db.publish_refresh([M], with_intel=[M])
        cloud = Config(markets=(M,), cloud=CloudConfig("https://x.cloudfront.net"),
                       sources={M: MarketSources("blizzard", ("blizzard",), {})})
        note = status_page.status_page(db, cloud, now, 0)["coverage"][0]["cloud_note"]
        assert note.startswith("Blizzard data collected to ") and " · analysed to " in note
        assert status_page.status_facts(db, cloud, now).markets[0].cloud_analysed_hour == end - 6   # last hour + 1
        assert status_page.status_page(db, Config(markets=(M,)), now, 0)["coverage"][0]["cloud_note"] is None
    finally:
        db.close()
