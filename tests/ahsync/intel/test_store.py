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
    with db.transaction():
        db.stage_item_stats(M, 1, {"med7": 5}, 10)
    assert db.count("item_stats") == 0 and db.count("item_stats_new") == 1
    db.publish_refresh([M])
    assert db.count("item_stats") == 1 and db.count("item_stats_new") == 0
