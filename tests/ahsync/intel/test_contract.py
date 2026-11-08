"""Tests for ahsync/intel/contract.py: the versioned intelligence document (AR2)."""
from __future__ import annotations

import json

import pytest

from dataclasses import replace

from ahsync.engine import profile
from ahsync.intel.contract import (
    INTEL_SCHEMA_VERSION, IntelVersionError, IntelWindow, ItemIntel, MarketIntelligence, from_json, to_json,
)

ITEM = ItemIntel(2589, 45, 40, 3_600_000, 50, 52, 40, 60, 1.5, 38.0, 4.0, 40.0, "books", {"books": 7}, 44, 20, 0.8,
                 False, 41.8, 61, 93, 7_200_000, ((45, 20, 3, 2), (50, 20, 4, 1)))


def item_of(text: str, key: str = "2589") -> dict:
    """One item of a document as an object, from schema 14's rows or an older schema's objects."""
    body = json.loads(text)
    row = body["items"][key]
    return dict(zip(body["item_fields"], row)) if "item_fields" in body else row


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
    body = json.loads(to_json(MarketIntelligence("m", 1, None, 1, {2589: ITEM}), schema=13))   # items as objects
    body["schema_version"] = 1
    for name in ("how", "how_weeks", "sales_how", "sales_weeks", "supply_how", "supply_weeks", "seasonality",
                 "sell_windows", "buy_windows", "curve", "curve_hour"):
        del body["items"]["2589"][name]
    item = from_json(json.dumps(body)).items[2589]
    assert (item.how, item.sell_windows, item.curve, item.seasonality) == ("", (), "", None)


def test_a_version_2_document_drops_its_unpacked_profiles():
    body = json.loads(to_json(MarketIntelligence("m", 1, None, 1, {2589: ITEM}), schema=13))
    body["schema_version"] = 2
    body["items"]["2589"].update(how=[1.0] * 168, how_weeks=[7] * 168)
    item = from_json(json.dumps(body)).items[2589]
    assert (item.how, item.how_weeks) == ("", "")


def test_W15_windows_carry_their_forecast_and_a_version_8_document_reads_it_as_none():
    window = IntelWindow(42, 3, 43, 1.12, 7, 112, 46, 70)
    intel = MarketIntelligence("m", 1, None, 1, {2589: replace(ITEM, sell_windows=(window,), buy_windows=(window,))})
    assert from_json(to_json(intel)) == intel
    body = json.loads(to_json(intel, schema=13))
    body["schema_version"] = 8
    for name in ("sell_windows", "buy_windows"):
        for w in body["items"]["2589"][name]:
            for field in ("expected_price", "exec_qty", "conf"):
                del w[field]
    old = from_json(json.dumps(body)).items[2589].sell_windows[0]
    assert (old.start_slot, old.expected_price, old.exec_qty, old.conf) == (42, None, None, None)


def test_V23_supply_level_is_listed_now_else_the_median_daily_supply():
    from dataclasses import replace

    from ahsync.intel.contract import supply_level
    linen = ItemIntel(2589, 45, 20, 3_600_000, 49, 49, 40, 60, 0.0, 48.0, 0.0, 61.0, "ahf", {"ahf": 7}, 45, 20, 0.9,
                      False, 42.0, 86, 93, None, ())
    assert supply_level(linen) == 93
    assert supply_level(replace(linen, units_on_ah=None, median_daily_supply=70)) == 70
    assert supply_level(replace(linen, units_on_ah=None)) is None


def test_BZ15_schema_10_round_trips_its_cloud_fields_and_writes_schema_9_without_them():
    item = replace(ITEM, last_hour=(4.0, 1.0, 0.0), recent_closes="125,,120", recent_sold="4,,2",
                   recent_listed="0,,10", recent_units="10,,18")
    intel = MarketIntelligence("forever.normal.horde.us", 1_000, 2_000, 1, {2589: item},
                               catalog={2589: ("Linen Cloth", 1, 7, 13, None)}, coverage=((497_900, 497_950),),
                               settings={"default_sell_prob": 0.25})
    text = to_json(intel)
    assert intel.schema_version == INTEL_SCHEMA_VERSION
    assert from_json(text) == intel and to_json(from_json(text)) == text
    old = json.loads(to_json(intel, schema=9))
    assert old["schema_version"] == 9 and not {"catalog", "coverage", "settings"} & set(old)
    assert not {"last_hour", "recent_closes", "recent_sold", "recent_listed", "recent_units"} & set(old["items"]["2589"])
    assert from_json(json.dumps(old)) == replace(intel, schema_version=9, items={2589: ITEM}, catalog={}, coverage=(),
                                                 settings={})
    with pytest.raises(ValueError, match=f"cannot write schema {INTEL_SCHEMA_VERSION + 1}"):
        to_json(intel, schema=INTEL_SCHEMA_VERSION + 1)
    with pytest.raises(ValueError, match="cannot write schema 8"):
        to_json(intel, schema=8)


def test_BZ37_schema_11_carries_med14_and_schema_10_is_written_without_it():
    intel = MarketIntelligence("forever.normal.horde.us", 1_000, 2_000, 1, {2589: replace(ITEM, med14=52)})
    assert INTEL_SCHEMA_VERSION >= 11 and item_of(to_json(intel))["med14"] == 52
    assert from_json(to_json(intel)) == intel
    old = json.loads(to_json(intel, schema=10))
    assert old["schema_version"] == 10 and "med14" not in old["items"]["2589"]
    assert from_json(json.dumps(old)).items[2589].med14 is None                  # an older document: "—" in game


def test_BZ23_schema_12_carries_expired_share_and_schema_11_is_written_without_it():
    intel = MarketIntelligence("forever.normal.horde.us", 1_000, 2_000, 1,
                               {2589: replace(ITEM, expired_share=0.25, undercut_share=0.3)})
    assert INTEL_SCHEMA_VERSION >= 12 and item_of(to_json(intel))["expired_share"] == 0.25
    assert from_json(to_json(intel)) == intel
    old = json.loads(to_json(intel, schema=11))
    assert old["schema_version"] == 11 and "expired_share" not in old["items"]["2589"]
    assert "undercut_share" not in old["items"]["2589"]                          # BZ29's field is schema 12's too
    assert from_json(json.dumps(old)).items[2589].expired_share is None


def test_a_schema_9_document_reads_with_empty_cloud_fields():
    body = json.loads(to_json(MarketIntelligence("m", 1, None, 1, {2589: ITEM}), schema=9))
    intel = from_json(json.dumps(body))
    assert (intel.catalog, intel.coverage, intel.settings, intel.items[2589].last_hour) == ({}, (), {}, None)


def test_BZ41_schema_13_carries_the_market_lines_and_schema_12_is_written_without_them():
    sell = IntelWindow(114, 4, 115, 1.1, 1, 24, 20, 40, chance24=0.81)
    buy = IntelWindow(31, 3, 31, 0.9, 1, 17, 20, 40, found_share=0.64)
    item = replace(ITEM, commodity=True, sell_through=(0.79, 0.79, None), sell_through_how="mm-", supply7=14_859,
                   supply14=14_859, sell_chance=0.744, sell_windows=(sell,), buy_windows=(buy,))
    intel = replace(MarketIntelligence("forever.normal.horde.us", 1_000, 2_000, 1, {2589: item}), cancel_share=0.108)
    assert from_json(to_json(intel)) == intel
    old = json.loads(to_json(intel, schema=12))
    assert old["schema_version"] == 12 and "cancel_share" not in old
    assert not {"commodity", "sell_through", "sell_through_how", "supply7", "supply14", "sell_chance"} & set(old["items"]["2589"])
    assert "chance24" not in old["items"]["2589"]["sell_windows"][0] and "found_share" not in old["items"]["2589"]["buy_windows"][0]
    back = from_json(json.dumps(old)).items[2589]
    assert back.sell_through == (None, None, None) and back.sell_windows[0].chance24 is None


def test_BZ46_schema_14_writes_items_as_rows_and_schema_13_as_objects_with_plain_profiles():
    how, weeks = profile.pack(profile.Profile((0.76, 0.88) + (1.0,) * 166, (7,) * 168, (True,) * 168))
    intel = MarketIntelligence("m", 1, None, 1, {2589: replace(ITEM, how=how, how_weeks=weeks)})
    body = json.loads(to_json(intel))
    assert body["item_fields"] == sorted(body["item_fields"]) and len(body["items"]["2589"]) == len(body["item_fields"])
    assert (item_of(to_json(intel))["how"], item_of(to_json(intel))["how_weeks"]) == ("760,880,1000*166", "7*168")
    assert from_json(to_json(intel)) == intel
    old = json.loads(to_json(intel, schema=13))
    assert "item_fields" not in old and old["items"]["2589"]["how"] == ",".join(["760", "880"] + ["1000"] * 166)
    assert old["items"]["2589"]["how_weeks"] == ",".join(["7"] * 168)
    assert profile.unpack(from_json(json.dumps(old)).items[2589].how, weeks) == profile.unpack(how, weeks)
    body["items"]["2589"].pop()
    with pytest.raises(ValueError):
        from_json(json.dumps(body))                                  # a row of the wrong length is refused

