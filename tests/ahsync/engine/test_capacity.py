"""Tests for ahsync/engine/capacity.py: market capacity is shared and counted once (SPEC §3.4)."""
from __future__ import annotations

import pytest

from ahsync.engine.capacity import Candidate, liquidity_cap, take_within_caps

DUST = 10940


def test_liquidity_cap_follows_sales_and_never_drops_below_a_stack():
    assert liquidity_cap(48, 8, 0.5, 20) == 20          # floor(48 × 8 ÷ 24 × 0.5) = 8, below one stack of 20
    assert liquidity_cap(240, 8, 0.5, 20) == 40         # floor(240 × 8 ÷ 24 × 0.5) = 40
    assert liquidity_cap(None, 8, 0.5, 5) == 5          # sales unknown: one stack


def test_R15_disenchant_buys_stop_at_the_mats_cap():
    cap = liquidity_cap(6, 8, 0.5, 1)                   # max(1, floor(6 × 8 ÷ 24 × 0.5) = 1) = 1 dust
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
