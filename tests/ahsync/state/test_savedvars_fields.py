"""Tests for ahsync/state/savedvars_fields.py: the SavedVariables field converters (SPEC §2.2.1)."""
from __future__ import annotations

import pytest

from ahsync.state.savedvars_fields import _bool, _compact_rows, _int, _results, _RowError, _str


def test_numbers_and_strings_are_checked_not_coerced():
    assert (_int(5, "n"), _str("a", "s"), _bool(True, "b")) == (5, "a", True)
    for convert, value in ((_int, 1.5), (_int, "5"), (_str, 5), (_bool, "yes")):
        with pytest.raises(_RowError):
            convert(value, "f")


def test_compact_rows_need_their_width():
    assert _compact_rows("2589,20;2840,-1", 2, "bags") == [(2589, 20), (2840, -1)]
    with pytest.raises(_RowError):
        _compact_rows("2589", 2, "bags")


def test_disenchant_results_are_summed_per_key_and_ordered_by_key():
    assert _results("10940,2;10938,1;10940,1", "results") == ((10938, 1), (10940, 3))
    for bad in ("10940", "x,1", "10940,0", ""):
        with pytest.raises(_RowError):
            _results(bad, "results")
