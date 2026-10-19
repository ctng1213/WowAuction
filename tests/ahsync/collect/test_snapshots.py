"""Tests for ahsync/collect/snapshots.py: raw snapshots on disk (V36, FM32)."""
from __future__ import annotations

import json

from ahsync.collect.provider import SnapshotObservation
from ahsync.collect.snapshots import SnapshotStore
from ahsync.engine.volume import Auction, IdBook

M = "forever.pvp.horde.us"
HOUR = 3_600_000


def snap(at_ms: int, *auctions: Auction) -> SnapshotObservation:
    """A blizzard snapshot."""
    return SnapshotObservation(M, "blizzard", at_ms, auctions)


def test_V36_snapshots_are_stored_gzipped_listed_and_read_back(tmp_path):
    store = SnapshotStore(tmp_path)
    a = Auction(7, 2589, 20, 50, 3)
    assert store.put(snap(2 * HOUR, a)) and store.put(snap(HOUR)) and not store.put(snap(HOUR))
    assert store.times(M, "blizzard", 0, 9 * HOUR) == [HOUR, 2 * HOUR]
    assert store.times(M, "blizzard", 2 * HOUR, 2 * HOUR) == [2 * HOUR] and store.times(M, "other", 0, 9) == []
    assert store.load(M, "blizzard", 2 * HOUR) == IdBook(2 * HOUR, (a,))
    assert sorted(p.name for p in (tmp_path / M / "blizzard").iterdir()) == [
        f"{HOUR}.json.gz", f"{2 * HOUR}.json.gz", "index.json"]       # no temp files left


def test_FM32_a_file_written_before_a_crash_but_not_listed_is_listed_by_the_next_put(tmp_path):
    store = SnapshotStore(tmp_path)
    store.put(snap(HOUR))
    (tmp_path / M / "blizzard" / "index.json").write_text(json.dumps([]), encoding="utf-8")   # crash before the index
    assert store.times(M, "blizzard", 0, 9 * HOUR) == []
    assert store.put(snap(HOUR)) and store.times(M, "blizzard", 0, 9 * HOUR) == [HOUR]


def test_prune_drops_old_snapshots_and_their_index_entries(tmp_path):
    store = SnapshotStore(tmp_path)
    for at in (HOUR, 2 * HOUR, 3 * HOUR):
        store.put(snap(at))
    assert store.prune(M, "blizzard", 2 * HOUR) == 1 and store.prune(M, "nothing", 0) == 0
    assert store.times(M, "blizzard", 0, 9 * HOUR) == [2 * HOUR, 3 * HOUR]
