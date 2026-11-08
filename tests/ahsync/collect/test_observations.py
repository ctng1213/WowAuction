"""Tests for ahsync/collect/observations.py: snapshots with ids into the store (V36, FM32, FM33)."""
from __future__ import annotations

import pytest

from ahsync.collect.observations import id_days, ingest_snapshot, recompute_id_days
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
                ("books", LINEN, 20.0, 0.0, 10.0, "groups"), ("books", ORE, 50.0, 0.0, 0.0, "groups")]
    assert volume(db) == expected              # units per price level measure the partial buy too (SPEC §3.3.1)
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


# --- id volume from lifetimes: each day's result is independent of what else is recomputed (M33a review) ----------

def snap(at: int, *auctions: Auction) -> SnapshotObservation:
    """A snapshot of the market at a time."""
    return SnapshotObservation(M, "blizzard", at, tuple(auctions))


def day_rows(db: Database, day: int) -> tuple[list, list, list]:
    """A day's id volume, ended listings and new-listing positions."""
    return (db.conn.execute("SELECT key, sold, expired, listed FROM volume_d1 WHERE src = 'blizzard' AND day = ? "
                            "ORDER BY key", (day,)).fetchall(),
            db.conn.execute("SELECT key, ended, expired, matched FROM lifetimes_d1 WHERE day = ? ORDER BY key",
                            (day,)).fetchall(),
            db.conn.execute("SELECT key, positions FROM new_listings_d1 WHERE day = ? ORDER BY key", (day,)).fetchall())


def sparse_store(tmp_path) -> SnapshotStore:
    """Snapshots 5-6 h apart; a 24 h Linen auction posted at D−25 h is first seen at D−21 h and runs out at D−1 h."""
    store = SnapshotStore(tmp_path / "snapshots")
    start = D * DAY
    ore = Auction(9, ORE, 5, 30, 4)
    bands = {-21: 4, -16: 4, -11: 3, -6: 3}                  # Blizzard's bands for 20, 15, 10 and 5 h left
    for h in (-27, -21, -16, -11, -6, 0):
        linen = (Auction(1, LINEN, 10, 50, bands[h]),) if h in bands else ()
        store.put(snap(start + h * HOUR, ore, *linen))
    return store


def test_a_days_lifetimes_do_not_depend_on_which_other_days_are_recomputed(db, tmp_path):
    store = sparse_store(tmp_path)
    with db.transaction():
        recompute_id_days(db, store, M, "blizzard", {D})
    alone = day_rows(db, D)
    with db.transaction():
        recompute_id_days(db, store, M, "blizzard", {D - 1, D})
    assert day_rows(db, D) == alone
    assert alone[1] == [(LINEN, 1.0, 1.0, 0.0)]      # its window narrows to (D−3 h, D+1 h]; gone by D, and no listing
    #   ever left early here, so selling never competed: wholly expired (BZ42; the old overlap split said 3/4)


def test_a_lifetime_credited_hour_keeps_its_rate_when_the_next_day_starts(db, tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    t = D * DAY - 90 * 60_000                                    # D−1 22:30
    ingest_snapshot(db, store, snap(t, Auction(100, LINEN, 5, 50, 4)))
    ingest_snapshot(db, store, snap(t + HOUR, Auction(200, LINEN, 5, 50, 4)))     # sold, and an identical new one
    ingest_snapshot(db, store, snap(t + 2 * HOUR, Auction(200, LINEN, 5, 50, 4)))  # D 00:30
    hour = t // HOUR * HOUR
    assert db.conn.execute("SELECT sold_per_hour FROM sales_h1 WHERE key = ? AND t = ?", (LINEN, hour)).fetchone() == (5.0,)


def test_a_pooled_hour_comes_out_the_same_in_any_recompute_order(db, tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    t = D * DAY - 110 * 60_000                                   # D−1 22:10, then 23:50, then D 00:20
    for at, qty in ((t, 10), (t + 100 * 60_000, 6), (t + 130 * 60_000, 3)):
        store.put(snap(at, Auction(1, LINEN, qty, 50, 4)))
    hour = (D * DAY - HOUR) // HOUR * HOUR                       # 23:00, credited by both gaps
    rates = []
    for order in ((D, D - 1), (D - 1, D)):
        for day in order:
            with db.transaction():
                recompute_id_days(db, store, M, "blizzard", {day})
        rates.append(db.conn.execute("SELECT sold_per_hour FROM sales_h1 WHERE t = ?", (hour,)).fetchone())
    assert rates[0] == rates[1]


def test_a_late_snapshot_recomputes_every_day_its_lifetimes_reach(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots")
    t = D * DAY + 20 * HOUR
    for at in (t, t + 20 * HOUR, t + 60 * HOUR):                  # D 20:00, D+1 16:00, D+3 08:00
        store.put(snap(at))
    assert id_days(store, M, "blizzard", t) == {D, D + 1}        # to the last snapshot within 36 h, not D+3
    assert id_days(store, M, "blizzard", D * DAY + HOUR) == {D - 1, D}   # its book pairs reach D−1 23:00; D+1 is 39 h on
