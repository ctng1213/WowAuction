"""Tests for ahsync/engine/fees.py (the same cases run against Engine/Fees.lua)."""
from __future__ import annotations

import pytest

from ahsync.engine.fees import AH_CUT, deposit_copper, sale_net, shortest_duration_covering


def test_deposit_is_floored_basis_points_of_vendor_value():
    # 20 units × 5 s vendor = 100 s; 24 h = 15% → 15 s (P9's deposit)
    assert deposit_copper(500, 20, 24) == 1500
    assert deposit_copper(500, 20, 8) == 500
    assert deposit_copper(7, 3, 2) == 0          # 21 × 1.25% = 0.2625 → 0


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
