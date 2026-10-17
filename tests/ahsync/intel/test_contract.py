"""Tests for ahsync/intel/contract.py: the versioned intelligence document (AR2)."""
from __future__ import annotations

import json

import pytest

from dataclasses import replace

from ahsync.intel.contract import (
    INTEL_SCHEMA_VERSION, IntelVersionError, IntelWindow, ItemIntel, MarketIntelligence, from_json, to_json,
)

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


def test_patterns_windows_and_curve_round_trip():
    window = IntelWindow(42, 3, 43, 1.12, 7)
    item = replace(ITEM, how=",".join(["1000"] * 168), how_weeks=",".join(["7"] * 168), sales_how="",
                   sales_weeks="", seasonality=60, sell_windows=(window,),
                   buy_windows=(IntelWindow(3, 2, 3, 0.9, 7),), curve="#" * 192, curve_hour=480_000)
    intel = MarketIntelligence("m", 1, None, 1, {2589: item})
    assert from_json(to_json(intel)) == intel


def test_a_version_1_document_reads_with_no_patterns():
    body = json.loads(to_json(MarketIntelligence("m", 1, None, 1, {2589: ITEM})))
    body["schema_version"] = 1
    for name in ("how", "how_weeks", "sales_how", "sales_weeks", "supply_how", "supply_weeks", "seasonality",
                 "sell_windows", "buy_windows", "curve", "curve_hour"):
        del body["items"]["2589"][name]
    item = from_json(json.dumps(body)).items[2589]
    assert (item.how, item.sell_windows, item.curve, item.seasonality) == ("", (), "", None)


def test_a_version_2_document_drops_its_unpacked_profiles():
    body = json.loads(to_json(MarketIntelligence("m", 1, None, 1, {2589: ITEM})))
    body["schema_version"] = 2
    body["items"]["2589"].update(how=[1.0] * 168, how_weeks=[7] * 168)
    item = from_json(json.dumps(body)).items[2589]
    assert (item.how, item.how_weeks) == ("", "")
