"""Tests for ahsync/intel/contract.py: the versioned intelligence document (AR2)."""
from __future__ import annotations

import json

import pytest

from ahsync.intel.contract import INTEL_SCHEMA_VERSION, IntelVersionError, ItemIntel, MarketIntelligence, from_json, to_json

ITEM = ItemIntel(2589, 45, 40, 3_600_000, 50, 52, 40, 60, 1.5, 38.0, 4.0, 40.0, "books", {"books": 7}, 44, 20, 0.8,
                 False, 41.8, 61, 93, 7_200_000, ((45, 20, 3, 2), (50, 20, 4, 1)))


def test_AR2_intelligence_round_trips_and_a_newer_schema_is_refused():
    intel = MarketIntelligence("forever.pvp.horde.us", 1_000, 900, 1, {2589: ITEM})
    text = to_json(intel)
    assert from_json(text) == intel and to_json(from_json(text)) == text
    newer = json.loads(text)
    newer["schema_version"] = INTEL_SCHEMA_VERSION + 1
    with pytest.raises(IntelVersionError, match="newer AHSync"):
        from_json(json.dumps(newer))


def test_serialization_is_deterministic_with_sorted_item_keys():
    a = MarketIntelligence("m", 1, None, 1, {2: ITEM, 1: ITEM})
    b = MarketIntelligence("m", 1, None, 1, {1: ITEM, 2: ITEM})
    assert to_json(a) == to_json(b)
    assert list(json.loads(to_json(a))["items"]) == ["1", "2"]
