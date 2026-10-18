"""Tests for ahsync/export/market_lua.py: MarketIntelligence.lua (SPEC §3.5), AR6, IU12, EX2."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine.sellprob import encode_curve
from ahsync.export import market_lua
from ahsync.intel.contract import IntelWindow, ItemIntel, MarketIntelligence

HOUR = 3_600_000
M = "forever.pvp.horde.us"
BUILT = 497_861 * HOUR
LINEN, SHORT, OTHER = 2589, 6948, 3300
# A 24 × 8 curve whose point in column c of row r is (r + c) % 50 steps, so every column is distinguishable.
GRID = [[((r + c) % 50) / 50 for c in range(8)] for r in range(24)]
CURVE = encode_curve(GRID)
LINEN_INTEL = ItemIntel(LINEN, 45, 20, HOUR, 49, 49, 40, 60, 0.4, 48.0, 0.0, 48.0, "ahf", {"ahf": 7}, 45, 20, 0.9,
                        False, 42.4, 86, 93, None, (), how="1000," * 167 + "1000", how_weeks="7," * 167 + "7",
                        seasonality=72, sell_windows=(IntelWindow(5 * 24 + 18, 4, 5 * 24 + 19, 1.12, 7),),
                        curve=CURVE, curve_hour=BUILT // HOUR)
EMPTY = ItemIntel(SHORT, None, None, None, None, None, None, None, 0.0, None, None, None, None, {}, None, 1, 0.25,
                  True, None, 6, None, None, ())


def intel(*items: ItemIntel) -> MarketIntelligence:
    """One market's intelligence built at BUILT."""
    return MarketIntelligence(M, BUILT, BUILT - HOUR, 1, {i.key: i for i in items})


def test_EX2_the_file_holds_compact_item_strings_curves_and_a_checked_header():
    text = market_lua.market_lua([intel(LINEN_INTEL, EMPTY, replace(EMPTY, key=OTHER))], {M: {SHORT}},
                                 {"Amaya-Grommash": M}, BUILT)
    assert f'I[{LINEN}]="45,20,60,49,49,40,60,4,480,480,93,86,72,45,20,42,0;138:4:139:1120:7;"' in text
    assert f'I[{SHORT}]=",,,,,,,0,,,,6,,,1,,1;;"' in text and f"I[{OTHER}]" not in text
    linen_curve = market_lua.trim_curve(CURVE, False)
    assert f'C[{LINEN}]="{linen_curve}"' in text and f"C[{SHORT}]" not in text
    strings = len("45,20,60,49,49,40,60,4,480,480,93,86,72,45,20,42,0;138:4:139:1120:7;") + 72 + len(",,,,,,,0,,,,6,,,1,,1;;")
    assert f'checksum = "3:{strings}"' in text and "schemaVersion = 1," in text and f"generatedAt = {BUILT}," in text
    assert f'charMarkets = {{ ["Amaya-Grommash"] = "{M}" }}' in text
    assert f'["{M}"] = {{ observationsUntil = {BUILT - HOUR}, curveHour = {BUILT // HOUR}, I = I1, C = C1 }},' in text


def test_AR6_the_shortlist_keeps_the_full_curve_and_others_get_the_0_5_20_columns_unchanged():
    full = market_lua.exported_items(intel(LINEN_INTEL, replace(LINEN_INTEL, key=OTHER)), {LINEN})
    assert full[0][2] == CURVE and len(full[1][2]) == 24 * 3
    rows = [full[1][2][i:i + 3] for i in range(0, 72, 3)]
    assert all(row == CURVE[r * 8] + CURVE[r * 8 + 3] + CURVE[r * 8 + 5] for r, row in enumerate(rows))
    assert LINEN_INTEL.curve == CURVE                                       # the intelligence itself is unchanged


def test_IU12_no_hour_of_week_profile_is_exported_but_the_windows_are():
    item = replace(LINEN_INTEL, buy_windows=(IntelWindow(3, 3, 4, 0.85, 7),))
    [(_, text, _)] = market_lua.exported_items(intel(item), set())
    assert text.endswith(";138:4:139:1120:7;3:3:4:850:7") and "1000,1000" not in text


def test_lua_strings_are_escaped_and_items_are_chunked_into_functions():
    assert market_lua.lua_string('a"b\\c\nd\re') == '"a\\"b\\\\c\\nd\\re"'
    many = intel(*(replace(EMPTY, key=k, cur=10) for k in range(1, market_lua.CHUNK + 2)))
    text = market_lua.market_lua([many], {}, {}, BUILT)
    assert text.count("do local fill = function(I, C)") == 2 and "observationsUntil" in text
    assert market_lua.trim_curve("", False) == ""
    none_until = replace(many, observations_until_ms=None, items={})
    assert "observationsUntil = nil" in market_lua.market_lua([none_until], {}, {}, BUILT)
