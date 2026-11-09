"""Tests for ahsync/mcp/stats.py: the in-memory market table (SPEC §3.8.4; MCP14)."""
from __future__ import annotations

import sqlite3

import pytest

from ahsync.mcp.server import open_read_only
from ahsync.mcp.stats import COLUMNS, TABLE, MarketStats
from tests.ahsync.mcp.conftest import MARKET


@pytest.fixture()
def db(world):
    """A read-only database on the refreshed world."""
    database = open_read_only(world.db_path)
    yield database
    database.conn.close()


def test_MCP14_market_stats_is_the_published_intelligence_one_row_per_item(db):
    stats = MarketStats()
    stats.build(db, (MARKET,))
    intel = db.read_intel(MARKET)
    conn = stats.connect()
    rows = {r[1]: dict(zip(COLUMNS, r)) for r in conn.execute(f"SELECT * FROM {TABLE}")}
    assert set(rows) == set(intel.items) and stats.count() == len(intel.items)
    meta = {r[0]: r[1:] for r in db.conn.execute("SELECT key, item_id, name, class, subclass, vendor_sell FROM items")}
    for key, item in intel.items.items():
        row = rows[key]
        assert row["market"] == MARKET
        for field in ("cur", "cur_age_ms", "med7", "med30", "p10_30", "p90_30", "trend7", "sold_per_day",
                      "expired_per_day", "listed_per_day", "volume_source", "units_on_ah", "chance", "conf"):
            assert row[field] == getattr(item, field), (key, field)   # as published: None stays NULL, never 0
        if key in meta:
            assert (row["item_id"], row["name"], row["class"], row["subclass"], row["vendor_sell"]) == meta[key]
    assert any(r["cur"] is None for r in rows.values()) or all(isinstance(r["cur"], int) for r in rows.values())
    conn.close()
    assert stats.size_bytes() > 0
    stats.close()


def test_MCP14_a_newer_refresh_swaps_the_whole_table_and_close_frees_it(db, monkeypatch):
    stats = MarketStats()
    assert stats.stale(db, (MARKET,)) and stats.size_bytes() == 0 and stats.count() == 0
    stats.refresh(db, (MARKET,))
    first_uri, before = stats.uri, stats.count()
    held = stats.connect()                                                # a query already running on the old table
    stats.refresh(db, (MARKET,))
    assert stats.uri == first_uri                                         # nothing newer: no rebuild
    facts = db.intel_facts(MARKET)
    newer = type(facts)(facts.built_ms + 1, facts.observations_until_ms, facts.coverage)
    monkeypatch.setattr(db, "intel_facts", lambda market: newer)
    assert stats.stale(db, (MARKET,))
    stats.refresh(db, (MARKET,))
    assert stats.uri != first_uri and stats.count() == before
    assert held.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0] == before   # the old table, whole, to the end
    held.close()
    stats.close()
    assert stats.uri is None and stats.size_bytes() == 0
    with pytest.raises(RuntimeError):
        stats.connect()
    with pytest.raises(sqlite3.OperationalError):                         # the freed table is gone
        sqlite3.connect(first_uri, uri=True).execute(f"SELECT 1 FROM {TABLE}")
