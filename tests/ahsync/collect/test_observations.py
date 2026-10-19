"""Tests for ahsync/collect/observations.py: snapshots with ids into the store (V36, FM32, FM33)."""
from __future__ import annotations

import pytest

from ahsync.collect.observations import ingest_snapshot
from ahsync.collect.provider import SnapshotObservation
from ahsync.collect.snapshots import SnapshotStore
from ahsync.db.sqlite import Database
from ahsync.engine.volume import Auction
from tests.support.fake_clock import FakeClock
from tests.support.fake_transport import SimulatedCrash

M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
D = 20_000
LINEN, ORE = 2589, 2770
T = D * DAY + 18 * HOUR
FIRST = SnapshotObservation(M, "blizzard", T, (Auction(1, LINEN, 20, 50, 3), Auction(2, ORE, 200, 30, 4),
                                               Auction(3, LINEN, 20, 50, 3)))
SECOND = SnapshotObservation(M, "blizzard", T + HOUR, (Auction(2, ORE, 150, 30, 4), Auction(3, LINEN, 20, 50, 3),
                                                       Auction(4, LINEN, 10, 48, 4)))


@pytest.fixture
def db(tmp_path):
    """A fresh database."""
    database = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    yield database
    database.close()


def volume(db: Database) -> list[tuple]:
    """Stored volume rows with their covers."""
    return db.conn.execute(
        "SELECT v.src, v.key, v.sold, v.expired, v.listed, c.kind FROM volume_d1 v LEFT JOIN volume_cover c "
        "ON c.market = v.market AND c.day = v.day AND c.src = v.src ORDER BY v.src, v.key").fetchall()


def test_V36_a_snapshot_with_ids_prices_items_and_feeds_id_and_group_volume_idempotently(db, tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    assert ingest_snapshot(db, store, FIRST) == {LINEN, ORE}
    assert ingest_snapshot(db, store, SECOND) >= {LINEN, ORE}
    assert db.conn.execute("SELECT key, price, qty, src FROM current ORDER BY key").fetchall() == [
        (LINEN, 48, 10, "blizzard"), (ORE, 30, 150, "blizzard")]
    assert db.conn.execute("SELECT src, kind FROM scans ORDER BY at").fetchall() == [("blizzard", "full")] * 2
    expected = [("blizzard", LINEN, 20.0, 0.0, 10.0, "ids"), ("blizzard", ORE, 50.0, 0.0, 0.0, "ids"),
                ("books", LINEN, 20.0, 0.0, 10.0, "groups"), ("books", ORE, 200.0, 0.0, 150.0, "groups")]
    assert volume(db) == expected              # the groups see the partial buy as one gone, one new (SPEC §3.3.1)
    assert db.conn.execute("SELECT covered FROM volume_cover WHERE src = 'blizzard'").fetchone()[0] == \
        ",".join("3600" if h == 18 else "0" for h in range(24))
    ingest_snapshot(db, store, SECOND)
    assert volume(db) == expected


def test_FM32_a_crash_after_the_file_before_the_commit_is_mended_by_delivering_it_again(db, tmp_path, monkeypatch):
    store = SnapshotStore(tmp_path / "snapshots")
    ingest_snapshot(db, store, FIRST)
    real = db.store_book

    def crash(*args, **kwargs):
        """The database write fails after the file is on disk."""
        raise SimulatedCrash("store_book")
    monkeypatch.setattr(db, "store_book", crash)
    with pytest.raises(SimulatedCrash):
        ingest_snapshot(db, store, SECOND)
    assert store.times(M, "blizzard", 0, T + DAY) == [T, T + HOUR] and volume(db) == []
    monkeypatch.setattr(db, "store_book", real)
    ingest_snapshot(db, store, SECOND)
    assert [r[:3] for r in volume(db) if r[0] == "blizzard"] == [("blizzard", LINEN, 20.0), ("blizzard", ORE, 50.0)]


def test_FM33_a_malformed_snapshot_stores_nothing(db, tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    with pytest.raises(ValueError, match="refused: an auction id appears twice"):
        ingest_snapshot(db, store, SnapshotObservation(M, "blizzard", T, FIRST.auctions + FIRST.auctions[:1]))
    assert store.times(M, "blizzard", 0, T + DAY) == [] and db.conn.execute("SELECT COUNT(*) FROM scans").fetchone() == (0,)


def test_a_snapshot_delivered_late_pairs_with_the_next_one_on_another_day(db, tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    late = SnapshotObservation(M, "blizzard", D * DAY - HOUR, FIRST.auctions)
    morning = SnapshotObservation(M, "blizzard", D * DAY + HOUR, SECOND.auctions)
    ingest_snapshot(db, store, morning)
    ingest_snapshot(db, store, late)
    assert db.conn.execute("SELECT DISTINCT day FROM volume_d1 WHERE src = 'blizzard'").fetchall() == [(D,)]
