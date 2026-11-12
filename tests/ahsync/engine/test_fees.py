"""Tests for ahsync/engine/fees.py (the same cases run against Engine/Fees.lua)."""
from __future__ import annotations

import pytest

from ahsync.engine.fees import AH_CUT, deposit_copper, sale_net, shortest_duration_covering


def test_P27_the_deposit_is_a_floored_share_of_the_vendor_value_and_at_least_1c_an_auction():
    assert deposit_copper(1173, 1, 8) == 234     # Oiled Blunderbuss, in game: 20% of 11s 73c = 234.6 → 2s 34c
    assert [deposit_copper(75, 1, h) for h in (2, 8, 24)] == [3, 15, 45]   # Healing Potion: 3.75c floored, 15c, 45c
    assert deposit_copper(500, 20, 24) == 6000   # 20 × 5s = 1g; 60% → 60s
    assert deposit_copper(500, 20, 2) == 500     # 5% → 5s
    assert deposit_copper(0, 20, 24) == 1        # vendor price unknown: the minimum, 1c since 2026-11-11
    assert deposit_copper(1, 1, 2) == 1          # 5% of 1c floors to 0: the minimum
    assert deposit_copper(500, 0, 24) == 0       # no units, no auction


def test_unknown_duration_raises():
    with pytest.raises(ValueError, match="12 h"):
        deposit_copper(100, 1, 12)


def test_shortest_duration_covering_a_window():
    assert shortest_duration_covering(8) == 8
    assert shortest_duration_covering(11) == 24      # P23: an 11 h window needs the 24 h auction
    assert shortest_duration_covering(1) == 2
    assert shortest_duration_covering(30) == 24      # nothing covers it: the longest


def test_sale_net_takes_the_cut():
    assert AH_CUT == 0.05
    assert sale_net(10000, 20) == 190000.0           # P5: 20 × 1 g × 0.95 = 19 g
