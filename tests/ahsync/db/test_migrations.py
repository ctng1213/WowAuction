"""Tests for ahsync/db/migrations.py: the schema version and its upgrade steps (SPEC §3.2 "Format version").

The steps themselves run in tests/ahsync/db/test_sqlite.py, against databases of each older version.
"""
from __future__ import annotations

from ahsync.db.migrations import MIGRATIONS, SCHEMA_VERSION


def test_every_version_after_the_first_has_exactly_one_upgrade_step():
    assert sorted(MIGRATIONS) == list(range(2, SCHEMA_VERSION + 1))
    assert all(step.strip() for step in MIGRATIONS.values())


def test_T25_schema_29_counts_a_posts_units_sold_and_returned():
    assert "sold_units" in MIGRATIONS[29] and "returned_units" in MIGRATIONS[29]
