"""Tests for ahsync/engine/disenchant.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahsync.engine import disenchant as de
from ahsync.engine.de_table import ARMOR, DE_TABLE, STRANGE_DUST, UNCOMMON, WEAPON

ROOT = Path(__file__).resolve().parents[3]
SHARED = json.loads((ROOT / "tests" / "scenarios" / "disenchant.json").read_text(encoding="utf-8"))
DUST_SELL_VALUE = 570.0       # 6 s × 0.95 with P = 1
TABLE_25 = ((UNCOMMON, ARMOR, 21, 25, 25, ((STRANGE_DUST, 1.0, 2, 3),)),)   # R1's "2.5 dust on average"
GLOVES = 4239


@pytest.mark.parametrize("scenario", SHARED, ids=[s["id"] for s in SHARED])
def test_shared_scenarios(scenario):
    assert getattr(de, scenario["call"])(**scenario["args"]) == pytest.approx(scenario["expect"]), scenario["why"]


def test_R1_a_profitable_green_is_listed_with_its_profit():
    found = de.targets([(GLOVES, 800, UNCOMMON, 25, ARMOR)], TABLE_25, {STRANGE_DUST: DUST_SELL_VALUE}, best_skill=100)
    assert [(t.key, t.ev, t.profit) for t in found] == [(GLOVES, pytest.approx(1425), pytest.approx(625))]


def test_R2_without_enchanting_nothing_is_listed():
    assert de.targets([(GLOVES, 800, UNCOMMON, 25, ARMOR)], TABLE_25, {STRANGE_DUST: DUST_SELL_VALUE}, None) == []


def test_targets_skip_items_too_high_or_not_disenchantable_and_sort_by_profit():
    listings = [(1, 800, UNCOMMON, 25, ARMOR), (2, 500, UNCOMMON, 25, ARMOR), (3, 100, 1, 25, ARMOR),
                (4, 100, UNCOMMON, 25, ARMOR)]
    found = de.targets(listings, TABLE_25, {STRANGE_DUST: DUST_SELL_VALUE}, best_skill=25)
    assert [t.key for t in found] == [4, 2, 1]          # profits 13s 25c, 9s 25c, 6s 25c; key 3 is grey
    assert de.targets(listings, TABLE_25, {STRANGE_DUST: DUST_SELL_VALUE}, best_skill=24) == []


def test_a_material_without_a_sell_value_counts_as_zero():
    found = de.targets([(GLOVES, 0, UNCOMMON, 25, ARMOR)], TABLE_25, {}, best_skill=25)
    assert found[0].ev == 0.0


def test_outcomes_from_the_real_table():
    skill, outcomes = de.outcomes_for(DE_TABLE, UNCOMMON, 25, WEAPON)
    assert skill == 25
    assert outcomes[0][1] == 0.75           # weapons are mostly essence
    assert de.outcomes_for(DE_TABLE, 1, 25, ARMOR) is None
