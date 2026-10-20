"""Tests for ahsync/recommend/store.py: dashboard pages, publish and skips."""
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


def test_publish_swaps_staged_rows_in_one_step_per_market(db):
    with db.transaction():
        db.stage_view("craft", M, {"rows": [1]}, 10)
    assert db.view("craft", M) is None                 # staged, not live
    db.publish_refresh([M])
    assert db.view("craft", M) == ({"rows": [1]}, 10)
    with db.transaction():
        db.stage_view("craft", OTHER, {"rows": [9]}, 11)
    db.publish_refresh([M])                            # nothing staged for M: M's page is emptied
    assert db.view("craft", M) is None and db.view("craft", OTHER) is None
    assert db.count("views_new") == 1


def test_an_interrupted_refresh_leaves_the_live_views(db):
    with db.transaction():
        db.stage_view("craft", M, {"rows": [1]}, 10)
    db.publish_refresh([M])
    with db.transaction():
        db.stage_view("craft", M, {"rows": [2]}, 20)
    db.drop_staging()                                  # what start-up does after a crash (FM7)
    assert db.view("craft", M) == ({"rows": [1]}, 10)


def test_skip_action_changes_only_open_actions(db):
    sql(db, "INSERT INTO actions(action_id, status) VALUES (?, ?)", ("a1", "pending"), ("a2", "done"))
    assert db.skip_action("a1", 99) is True
    assert db.skip_action("a2", 99) is False and db.skip_action("nope", 99) is False
    assert db.conn.execute("SELECT status, done_at FROM actions WHERE action_id = 'a1'").fetchone() == ("skipped", 99)


def test_alternatives_are_staged_and_swapped_in_with_the_plan(db):
    row = ("craft:6217:Brugan-Grommash:2840c/-/now:1", "Brugan-Grommash", "today",
           "Exceeds risk tolerance (needs 45s, 24s 40c left)", "{}")
    with db.transaction():
        db.stage_alternatives(M, [row])
        db.stage_alternatives(OTHER, [row])
    assert db.alternatives(M) == []                    # staged, not live
    db.publish_refresh([M], with_intel=False)          # a re-plan publishes them too
    assert db.alternatives(M) == [row] and db.alternatives(OTHER) == []
    with db.transaction():
        db.stage_alternatives(M, [])
    db.drop_staging()                                  # an interrupted refresh keeps the live ones (FM7)
    assert db.alternatives(M) == [row] and db.count("alternatives_new") == 0
    db.publish_refresh([M])                            # nothing staged: the market's alternatives are emptied
    assert db.alternatives(M) == []


def test_the_exported_candidates_and_the_portfolio_snapshot_are_staged_and_swapped_in_with_the_plan(db):
    rows = [("a:1", "", 120.5, "{}"), ("b:1", "Exceeds risk tolerance (needs 45s, 24s 40c left)", 80.0, "{}")]
    snap = {"snapshot_ms": 1, "reserved": {"Brugan-G": 5}}
    with db.transaction():
        db.stage_export(M, rows, snap)
        db.stage_export(OTHER, rows[:1], None)                 # a market without characters has no snapshot
    assert db.export_candidates(M) == [] and db.portfolio_snapshot(M) is None        # staged, not live
    db.publish_refresh([M], with_intel=False)
    assert db.export_candidates(M) == rows and db.portfolio_snapshot(M) == snap
    assert db.export_candidates(OTHER) == []
    with db.transaction():
        db.stage_export(M, [], None)
    db.drop_staging()                                          # an interrupted refresh keeps the live ones (FM7)
    assert db.export_candidates(M) == rows and db.count("export_candidates_new") == db.count("portfolio_snapshot_new") == 0
    db.publish_refresh([M])                                    # nothing staged: emptied, like the plan
    assert db.export_candidates(M) == [] and db.portfolio_snapshot(M) is None
