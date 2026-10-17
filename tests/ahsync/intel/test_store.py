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
