"""Tests for ahcloud/profile.py: a run's stage times and memory high-water marks as one log line (BZ64, BZ65)."""
from __future__ import annotations

import json

import pytest

from ahcloud.profile import Profile, peak_rss_mb


class Ticks:
    """A fake monotonic clock that moves only when told to, in seconds."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_stages_add_up_in_whole_ms_with_the_memory_after_each():
    ticks, memory = Ticks(), iter([300, 450, 450])
    profile = Profile("ingest", ticks, lambda: next(memory))
    with profile.stage("download"):
        ticks.now += 1.2345
    with profile.stage("ingest"):
        ticks.now += 0.5
    with profile.stage("download"):                                   # entered again: adds up
        ticks.now += 0.0019
    ticks.now += 0.01
    line = json.loads(profile.line(outcome="ingested", snapshots=2))
    assert line == {"profile": "ingest", "outcome": "ingested", "snapshots": 2, "total_ms": 1746,
                    "stages_ms": {"download": 1235, "ingest": 500}, "rss_mb": {"download": 450, "ingest": 450}}
    assert sum(line["stages_ms"].values()) <= line["total_ms"]


def test_a_stage_that_raises_is_still_timed():
    ticks = Ticks()
    profile = Profile("daily", ticks, lambda: None)                   # memory unknown (Windows)
    with pytest.raises(RuntimeError):
        with profile.stage("analyze"):
            ticks.now += 2.0
            raise RuntimeError("out of memory")
    line = json.loads(profile.line(outcome="failed"))
    assert line["stages_ms"] == {"analyze": 2000} and line["rss_mb"] == {}


def test_the_memory_high_water_mark_is_whole_mb_or_unknown():
    peak = peak_rss_mb()
    assert peak is None or (isinstance(peak, int) and 1 <= peak < 1_000_000)
