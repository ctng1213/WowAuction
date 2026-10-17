"""Tests for ahsync/store/ingest_writes.py: the SavedVariables upserts (SPEC §2.2.1, §3.2)."""
from __future__ import annotations

import pytest

from ahsync.store.db import Database
from tests.support.fake_clock import FakeClock
from tests.support.fake_transport import DEFAULT_MARKET

MARKET = DEFAULT_MARKET


@pytest.fixture
def db(tmp_path):
    """A fresh database."""
    database = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    yield database
    database.close()


def test_savepoint_undoes_only_the_failing_rows_writes(db):
    with db.transaction():
        db.add_vendor_reagent(2320)
        with pytest.raises(ValueError):
            with db.savepoint():
                db.add_vendor_reagent(2321)
                raise ValueError("bad row")
        db.add_vendor_reagent(2324)
    assert [r[0] for r in db.conn.execute("SELECT key FROM vendor_reagents ORDER BY key")] == [2320, 2324]


def test_add_only_writes_report_whether_the_row_was_new(db):
    assert db.add_known_recipe("A-R", 2663) and not db.add_known_recipe("A-R", 2663)
    assert db.add_vendor_reagent(2320) and not db.add_vendor_reagent(2320)


def test_replace_snapshot_applies_only_newer_snapshots_and_reports_changed_keys(db):
    assert db.replace_snapshot("A-R", "bags", 10, {(2589, 0): 20, (6948, 1): 1}) == {2589, 6948}
    assert db.replace_snapshot("A-R", "bags", 10, {(2589, 0): 5}) is None          # not newer
    assert db.replace_snapshot("A-R", "bags", 11, {(2589, 0): 20, (6948, 1): 1}) == set()
    assert db.replace_snapshot("A-R", "bags", 12, {(2589, 0): 19, (6948, 1): 1}) == {2589}
    assert db.replace_snapshot("A-R", "auctions", 5, {(2857, 4100, 5, 3): 2}) == {2857}
    assert db.conn.execute("SELECT key, qty FROM inventory").fetchall() == [(2589, 19), (6948, 1)]
    assert db.conn.execute("SELECT count, seen FROM own_auctions").fetchall() == [(2, 5)]


def test_upsert_event_rejects_other_tables_and_never_changes_unconfirmable_events(db):
    with pytest.raises(ValueError):
        db.upsert_event("items", {})
    row = {"event_id": "A-R:1000:0", "char_id": "A-R", "spell_id": 2663, "count": 1, "at": 1000}
    assert db.upsert_event("crafts", row)
    assert not db.upsert_event("crafts", row | {"count": 9})
    assert db.conn.execute("SELECT count FROM crafts").fetchall() == [(1,)]


def test_item_rule_ties_are_settled_by_rule_name_whatever_the_order(db):
    db.upsert_item_rule(1, "ignore", 5, "addon")
    assert db.upsert_item_rule(1, "keep", 5, "dashboard")
    assert not db.upsert_item_rule(1, "ignore", 5, "addon")
    assert not db.upsert_item_rule(1, "no_buy", 4, "addon")
    assert db.conn.execute("SELECT rule, set_at, source FROM item_rules").fetchall() == [("keep", 5, "dashboard")]


def test_character_market_and_recipe_item_keys_lookups(db):
    assert db.character_market("A-R") is None and db.recipe_item_keys(2663) == set()
    db.upsert_character({"id": "A-R", "name": "A", "realm": "R", "faction": "Horde", "market": MARKET,
                         "class": "MAGE", "level": 1, "gold": 0, "updated": 1})
    db.upsert_recipe({"profession": "Blacksmithing", "spell_id": 2663, "product_key": 6217, "yield_min": 1,
                      "yield_max": 1, "skill_req": None, "cooldown_s": None}, {(2840,): 2})
    assert db.character_market("A-R") == MARKET and db.recipe_item_keys(2663) == {6217, 2840}
