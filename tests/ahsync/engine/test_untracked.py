"""Tests for ahsync/engine/untracked.py: changes no event explains (SPEC §2.2.1, FM21, D51)."""
from __future__ import annotations

from ahsync.engine.untracked import unexplained

LINEN, EGG, BAR, ROD, SILK = 2589, 6888, 2840, 6217, 4306


def test_D51_what_no_event_explains_is_untracked_and_moved_items_are_left_out():
    old = {LINEN: 20, EGG: 5, BAR: 2, SILK: 3}
    new = {LINEN: 32, ROD: 1, SILK: 1}
    explained = {ROD: 1.0, BAR: -2.0}                                    # the craft: 2 bars into 1 rod
    assert unexplained(old, new, explained, {SILK}) == {LINEN: 12, EGG: -5}


def test_fractions_from_average_yields_round_half_away_from_zero():
    assert unexplained({}, {ROD: 2}, {ROD: 1.5}, set()) == {ROD: 1}          # 0.5 → 1
    assert unexplained({ROD: 2}, {}, {ROD: -1.5}, set()) == {ROD: -1}        # −0.5 → −1
    assert unexplained({ROD: 1}, {ROD: 1}, {}, set()) == {}
