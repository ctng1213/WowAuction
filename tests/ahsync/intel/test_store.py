"""Tests for ahsync/intel/store.py: staged market intelligence."""
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
