"""Tests for ahsync/intel/store.py: staged market intelligence."""
from __future__ import annotations

import gzip

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




def test_staged_intelligence_is_not_live_until_published(db):
    from ahsync.intel.contract import MarketIntelligence
    intel = MarketIntelligence(M, 10, 5, 1, {})
    with db.transaction():
        db.stage_intel(intel)
    assert db.read_intel(M) is None and db.count("intel_new") == 1
    db.publish_refresh([M])
    assert db.read_intel(M) == intel and db.count("intel_new") == 0


def test_SRC2_newer_prices_follow_the_primary_source(db):
    sql(db, "INSERT INTO intel(market, schema_version, body, built) VALUES (?, ?, ?, ?)", (M, 1, "{}", 5 * DAY))
    sql(db, "INSERT INTO current(market, key, price, qty, at, src) VALUES (?, ?, ?, ?, ?, ?)",
        (M, 1, 100, 1, 6 * DAY, "blizzard"), (M, 1, 100, 1, 4 * DAY, "ahf"))
    assert (db.prices_newer_than_intel(M, "blizzard"), db.prices_newer_than_intel(M)) == (True, False)


def test_the_published_intelligence_is_parsed_once_until_a_refresh_publishes_another(db):
    from ahsync.intel.contract import MarketIntelligence
    with db.transaction():
        db.stage_intel(MarketIntelligence(M, 10, 5, 1, {}))
    db.publish_refresh([M])
    first = db.read_intel(M)
    assert db.read_intel(M) is first                                      # a re-plan reuses the parsed document
    with db.transaction():
        db.stage_intel(MarketIntelligence(M, 20, 15, 1, {}))
    db.publish_refresh([M])
    assert db.read_intel(M).built_ms == 20 and db.read_intel(M) is not first


def test_BZ42_the_dashboard_reads_the_hours_summary_not_the_first_of_the_day(db):
    from ahsync.intel.contract import MarketIntelligence
    from ahsync.web.server import open_server_db
    dashboard = open_server_db(db.path, db.intel_cache)
    try:
        nine = MarketIntelligence(M, 9 * 3_600_000, 9 * 3_600_000, 1, {})
        with db.transaction():
            db.stage_intel(nine)
        db.publish_refresh([M])
        assert dashboard.read_intel(M).observations_until_ms == 9 * 3_600_000
        with db.transaction():
            db.stage_intel(MarketIntelligence(M, 9 * 3_600_000, 10 * 3_600_000, 1, {}))   # same baselines
        db.publish_refresh([M])
        assert dashboard.read_intel(M).observations_until_ms == 10 * 3_600_000
        assert dashboard.read_intel(M) is db.read_intel(M)                 # one parsed summary per process
    finally:
        dashboard.close()


def test_BZ43_a_held_summary_is_published_without_parsing_it_again(db, monkeypatch):
    from ahsync.intel import store
    from ahsync.intel.contract import MarketIntelligence, to_json
    intel = MarketIntelligence(M, 10, 5, 1, {})
    text = gzip.compress(to_json(intel).encode("utf-8"))
    with db.transaction():
        db.stage_intel(intel, text)
    db.publish_refresh([M])
    db.hold_intel(intel)
    monkeypatch.setattr(store, "from_json", lambda _text: pytest.fail("parsed again"))
    assert db.read_intel(M) is intel
    assert db.conn.execute("SELECT body FROM intel WHERE market = ?", (M,)).fetchone()[0] == text


def test_BZ43_forgetting_a_market_drops_its_record_before_the_next_parse(db):
    from ahsync.intel.store import IntelCache
    cache, parsed = IntelCache(), []
    cache.get(M, (1, 1), lambda: parsed.append("a") or "first")
    seen = []
    cache.get(M, (2, 1), lambda: seen.append(dict(cache._records)) or "second")
    assert seen == [{}] and cache.get(M, (2, 1), lambda: pytest.fail("parsed again")) == "second"
    cache.forget(M)
    assert cache._records == {}
