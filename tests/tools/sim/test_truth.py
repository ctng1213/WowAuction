"""Tests for tools/sim/truth.py: the simulator's event log and its sums (SPEC §8.10)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

from sim.truth import Event, Truth  # noqa: E402


def test_the_sums_count_units_bought_and_listings_ended_by_how_they_ended():
    t = Truth()
    for e in [Event(10, "post", 1, 7, 20, 50, 20, "s1"), Event(20, "buy", 1, 7, 5, 50, 15, "b1"),
              Event(30, "buy", 1, 7, 15, 50, 0, "b2"), Event(40, "post", 2, 7, 10, 60, 10, "s2"),
              Event(50, "expire", 2, 7, 10, 60, 0, ""), Event(60, "post", 3, 7, 5, 55, 5, "s3"),
              Event(70, "cancel", 3, 7, 5, 55, 0, "s3"), Event(80, "buy", 9, 8, 1, 900, 0, "b3")]:
        t.add(e)
    assert t.sold_units(7, 0, 100) == 20 and t.sold_units(7, 20, 100) == 15     # the span's start is exclusive
    assert t.endings(7, 0, 100) == (3, 1, 1)                                     # bought out, expired, cancelled
    assert [json.loads(line)["kind"] for line in t.to_jsonl().decode().splitlines()][:2] == ["post", "buy"]
    with pytest.raises(ValueError, match="unknown truth event kind"):
        t.add(Event(90, "relist", 4, 7, 1, 1, 1, "s"))


def test_what_snapshots_saw_positions_early_leaves_and_relists():
    t = Truth()
    for e in [Event(10, "post", 1, 7, 20, 50, 20, "s1"), Event(15, "buy", 1, 7, 4, 50, 16, "b1"),
              Event(30, "post", 2, 7, 5, 45, 5, "s2"), Event(35, "buy", 2, 7, 5, 45, 0, "b2"),   # gone before 40
              Event(42, "buy", 1, 7, 6, 50, 10, "b3"), Event(50, "cancel", 1, 7, 10, 50, 0, "s1"),
              Event(50, "post", 3, 7, 10, 55, 10, "s1"), Event(60, "post", 4, 7, 2, 60, 2, "s4"),
              Event(70, "expire", 4, 7, 2, 60, 0, "")]:
        t.add(e)
    snaps = [20, 40, 52, 65]
    assert t.seen(7, snaps) == frozenset({1, 3, 4})                 # 2 was bought out between 20 and 40
    assert t.seen_sold_units(7, 0, 100, snaps) == 6                  # 4 bought before 1's first snapshot; 2 unseen
    assert t.post_ratios(7, 0, 100) == {2: (0.9, 5), 4: (60 / 55, 2)}    # 3 was posted alone: left out
    assert t.positions(7, 0, 100, frozenset({4})) == [(60 / 55, 2)]
    assert t.early_leaves(7, 0, 100) == (2, 1)                       # 2 bought out, 1 cancelled; 4 expired
    assert t.relists(7, 0, 100, 5) == (2, 1) and t.relists(7, 0, 100, 0) == (2, 1)
    assert t.relists(7, 0, 49, 5) == (1, 0)
