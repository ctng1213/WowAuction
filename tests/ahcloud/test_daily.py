"""Tests for ahcloud/daily.py: the history into the baselines, read only (SPEC §2.4; BZ12, BZ59-BZ61, FM70, FM72)."""
from __future__ import annotations

import pytest

from ahcloud.daily import carry_state, run_daily
from ahcloud.hourly import run_hourly
from ahcloud.ingest import DB_KEY, run_ingest
from ahcloud.publish import read_latest
from ahcloud.store import LocalStore
from ahsync.collect.blizzard import raw_key
from ahsync.db.sqlite import DatabaseError
from tests.support.cloud_world import A_MS, B_MS, C_MS, HOUR, LINEN, M, SNAP_A, SNAP_B, SNAP_C, FakeCatalog
from tests.support.fake_clock import FakeClock


def stores(tmp_path, *snaps):
    """A data store with raw snapshots, and an empty intel store."""
    data, intel = LocalStore(tmp_path / "data"), LocalStore(tmp_path / "intel")
    for at_ms, envelope in snaps:
        data.put(raw_key(M, at_ms), envelope)
    return data, intel


def ingested(tmp_path, *snaps):
    """Stores whose history Ingest has brought up to the newest of the snapshots."""
    data, intel = stores(tmp_path, *snaps)
    run_ingest(data, FakeCatalog(), M, tmp_path / "ingest", FakeClock(C_MS), cap=None)
    return data, intel


def analyze(data, intel, tmp_path):
    """One daily analysis."""
    return run_daily(data, intel, M, tmp_path / "run")


def test_BZ59_the_analysis_reads_the_history_and_never_writes_it_back(tmp_path):
    data, intel = ingested(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    before = data.get(DB_KEY)
    writes = []
    original = data.put
    data.put = lambda key, value, if_match=None: (writes.append(key), original(key, value, if_match))[1]
    outcome = analyze(data, intel, tmp_path)
    assert (outcome.state, outcome.built_ms, outcome.items) == ("published", B_MS, 2)
    assert writes == [] and data.get(DB_KEY) == before                          # the data store untouched
    doc = read_latest(intel, M)[0]
    assert (doc.built_ms, doc.observations_until_ms, doc.ages_at_ms) == (B_MS, B_MS, B_MS)


def test_BZ60_a_history_behind_the_raw_still_publishes_and_the_hourly_runs_layer_over_it(tmp_path):
    data, intel = ingested(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    data.put(raw_key(M, C_MS), SNAP_C)                                       # Ingest has not taken C
    assert analyze(data, intel, tmp_path).built_ms == B_MS
    doc = read_latest(intel, M)[0]
    assert (doc.built_ms, doc.observations_until_ms) == (B_MS, C_MS)          # the analysis applied C on top
    assert doc.items[LINEN].cur == 118


def test_BZ61_the_analysis_publishes_nothing_while_the_history_is_rebuilding(tmp_path, monkeypatch):
    data, intel = ingested(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    analyze(data, intel, tmp_path)
    before = read_latest(intel, M)[1]
    monkeypatch.setattr("ahcloud.ingest.REBUILD_SNAPSHOTS_PER_RUN", 1)          # the rebuild takes A, then B
    run_ingest(data, FakeCatalog(), M, tmp_path / "ingest", FakeClock(C_MS), cap=1, fresh=True)
    assert analyze(data, intel, tmp_path).state == "rebuilding"
    assert read_latest(intel, M)[1] == before


def test_an_empty_store_publishes_nothing(tmp_path):
    data, intel = stores(tmp_path)
    assert analyze(data, intel, tmp_path).state == "empty" and read_latest(intel, M) is None


def test_BZ12_a_crash_before_the_pointer_is_repaired_by_the_next_analysis(tmp_path):
    data, intel = ingested(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))

    class Crash(Exception):
        """The run dies before the pointer."""

    def dying(key, value, if_match=None):
        raise Crash()

    original = intel.put
    intel.put = dying
    with pytest.raises(Crash):
        analyze(data, intel, tmp_path)
    assert read_latest(intel, M) is None
    intel.put = original
    assert analyze(data, intel, tmp_path).state == "published" and read_latest(intel, M)[0].built_ms == B_MS


def test_FM70_a_failed_analysis_leaves_the_history_and_the_last_document(tmp_path, monkeypatch):
    data, intel = ingested(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    analyze(data, intel, tmp_path)
    history, document = data.get(DB_KEY), read_latest(intel, M)[1]

    def out_of_memory(*args):
        raise MemoryError()

    monkeypatch.setattr("ahcloud.daily.analyze_market", out_of_memory)
    with pytest.raises(MemoryError):
        analyze(data, intel, tmp_path)
    assert data.get(DB_KEY) == history and read_latest(intel, M)[1] == document


def test_FM72_a_damaged_database_fails_the_analysis(tmp_path):
    data, intel = stores(tmp_path, (A_MS, SNAP_A))
    data.put(DB_KEY, b"this is not a database" * 200)
    with pytest.raises(DatabaseError):
        analyze(data, intel, tmp_path)
    assert read_latest(intel, M) is None


def test_the_daily_run_keeps_the_hourly_rings_it_did_not_build(tmp_path):
    data, intel = ingested(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    analyze(data, intel, tmp_path)
    data.put(raw_key(M, C_MS), SNAP_C)
    run_hourly(data, intel, M)
    hourly = read_latest(intel, M)[0].items[LINEN].recent_closes
    run_ingest(data, FakeCatalog(), M, tmp_path / "ingest", FakeClock(C_MS + HOUR))   # C is now in history too
    analyze(data, intel, tmp_path)
    daily = read_latest(intel, M)[0]
    assert daily.items[LINEN].recent_closes == hourly and daily.observations_until_ms == C_MS == daily.built_ms
    assert carry_state(daily, None).observations_until_ms is None
