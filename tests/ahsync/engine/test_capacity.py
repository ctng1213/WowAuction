"""Tests for ahsync/engine/capacity.py: market capacity is shared and counted once (SPEC §3.4)."""
from __future__ import annotations

import pytest

from ahsync.engine.capacity import Candidate, liquidity_cap, slot_of, take_within_caps, window_supply

DUST = 10940


def test_liquidity_cap_is_a_share_of_others_listings_and_never_below_a_stack():
    assert liquidity_cap(93, 0.5, 20) == 46             # floor(0.5 × 93)
    assert liquidity_cap(30, 0.5, 20) == 20             # 15 is below one stack of 20
    assert liquidity_cap(None, 0.5, 5) == 5             # supply unknown: one stack


def test_R17_a_window_where_the_ah_is_usually_fuller_allows_more():
    now_hour = 20_000 * 24                              # a Friday 00:00 (slot 96)
    pattern = [1.0] * 168
    for h in (now_hour + 10, now_hour + 11):
        pattern[slot_of(h)] = 1.5
    assert slot_of(now_hour) == 96
    listed = window_supply(93, pattern, now_hour, [now_hour + 10, now_hour + 11])
    assert (listed, liquidity_cap(listed, 0.5, 20)) == (139.5, 69)   # 93 × 1.5; floor(0.5 × 139.5)
    assert window_supply(93, (), now_hour, [now_hour]) == 93.0         # no pattern: today's supply
    assert window_supply(None, pattern, now_hour, [now_hour]) is None
    assert window_supply(93, [0.0] * 168, now_hour, [now_hour]) == 93.0   # no ratio now to scale from


def test_R15_disenchant_buys_stop_at_the_mats_cap():
    cap = liquidity_cap(2, 0.5, 1)                      # others list 2 dust: max(1, floor(0.5 × 2) = 1) = 1
    greens = [Candidate(4239, 625.0, {DUST: 2.5}), Candidate(4567, 612.0, {DUST: 2.5}), Candidate(7001, 325.0, {DUST: 2.5})]
    taken, dropped = take_within_caps(greens, {DUST: cap})
    assert (cap, taken, dropped) == (1, [4239], [4567, 7001]), \
        "the best green's 2.5 dust already passes the cap; the others would only add unsellable dust"


def test_buys_are_taken_best_profit_first_with_a_full_tie_break_and_units_already_held_count():
    a, b = Candidate(2, 100.0, {DUST: 1.0}), Candidate(1, 100.0, {DUST: 1.0})
    assert take_within_caps([a, b], {DUST: 2}) == ([1, 2], [])          # equal profit: lower id first
    assert take_within_caps([a, b], {DUST: 2}, {DUST: 1.5}) == ([1], [2])   # 1.5 held + 1 reaches the cap
    assert take_within_caps([], {DUST: 2}) == ([], [])


def test_a_mat_without_a_cap_is_an_error():
    with pytest.raises(KeyError):
        take_within_caps([Candidate(1, 1.0, {DUST: 1.0})], {})
