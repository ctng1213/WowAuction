"""Tests for ahsync/engine/de_table.py and its generated Lua twin."""
from __future__ import annotations

from pathlib import Path

import pytest

import tools.gen_disenchant_data as gen
from ahsync.engine.de_table import DE_TABLE, RARE, UNCOMMON

ROOT = Path(__file__).resolve().parents[3]


def test_every_row_has_chances_summing_to_one_and_sane_quantities():
    for quality, kind, lo, hi, skill, outcomes in DE_TABLE:
        assert quality in (UNCOMMON, RARE) and kind in ("armor", "weapon") and lo <= hi and skill >= 1
        assert sum(chance for _, chance, _, _ in outcomes) == pytest.approx(1.0)
        assert all(1 <= qmin <= qmax for _, _, qmin, qmax in outcomes)


def test_bands_do_not_overlap():
    seen: dict[tuple, set[int]] = {}
    for quality, kind, lo, hi, _, _ in DE_TABLE:
        levels = seen.setdefault((quality, kind), set())
        assert not levels & set(range(lo, hi + 1))
        levels |= set(range(lo, hi + 1))


def test_the_addon_data_file_is_generated_from_this_table():
    committed = (ROOT / "addon" / "AHProfit" / "Data" / "Disenchant.lua").read_text(encoding="utf-8")
    assert committed.replace("\r\n", "\n") == gen.render(DE_TABLE), "run python tools/gen_disenchant_data.py"
