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
    assert f'I[{LINEN}]="45,20,60,49,49,40,60,4,480,480,93,86,72,45,20,42,0,900,,0,,,7;138:4:139:1120:7;"' in text
    assert f'I[{SHORT}]=",,,,,,,0,,,,6,,,1,,1,250,,0,,,;;"' in text and f"I[{OTHER}]" not in text
    linen_curve = market_lua.trim_curve(CURVE, False)
    assert f'C[{LINEN}]="{linen_curve}"' in text and f"C[{SHORT}]" not in text
    strings = len("45,20,60,49,49,40,60,4,480,480,93,86,72,45,20,42,0,900,,0,,,7;138:4:139:1120:7;") + 3 + len(",,,,,,,0,,,,6,,,1,,1,250,,0,,,;;")
    assert f'checksum = "3:{strings}"' in text and "schemaVersion = 1," in text and f"generatedAt = {BUILT}," in text
    assert f'charMarkets = {{ ["Amaya-Grommash"] = "{M}" }}' in text
    assert f'["{M}"] = {{ observationsUntil = {BUILT - HOUR}, curveHour = {BUILT // HOUR}, I = I1, C = C1, D = D1, F = {{  }}, S = {{  }}, K = K1, G = G1 }},' in text


def test_AR6_the_shortlist_keeps_the_full_curve_and_others_get_the_0_5_20_columns_unchanged():
    full = market_lua.exported_items(intel(LINEN_INTEL, replace(LINEN_INTEL, key=OTHER)), {LINEN})
    # Since EX19 the tooltip-only item keeps one row: the refresh hour's 0, 5 and 20 ahead points.
    assert full[0][2] == CURVE and full[1][2] == CURVE[0] + CURVE[3] + CURVE[5]
    assert LINEN_INTEL.curve == CURVE                                       # the intelligence itself is unchanged


def test_EX19_tooltip_only_items_carry_one_window_of_each_kind_and_a_three_point_curve():
    windows = tuple(IntelWindow(s, 2, s, 1.1, 6) for s in (10, 40, 70))
    item = replace(LINEN_INTEL, sell_windows=windows, buy_windows=tuple(replace(w, how_avg=0.9) for w in windows))
    [(_, linen, linen_curve), (_, wool, wool_curve)] = market_lua.exported_items(
        intel(item, replace(item, key=OTHER)), {LINEN})
    assert linen.endswith(";10:2:10:1100:6/40:2:40:1100:6/70:2:70:1100:6;10:2:10:900:6/40:2:40:900:6/70:2:70:900:6")
    assert len(linen_curve) == 24 * 8
    assert wool.endswith(";10:2:10:1100:6;10:2:10:900:6") and wool_curve == CURVE[0] + CURVE[3] + CURVE[5]
    assert market_lua.item_text(item).count("/") == 4                     # the default: shortlisted, every window


def test_IU12_no_hour_of_week_profile_is_exported_but_the_windows_are():
    item = replace(LINEN_INTEL, buy_windows=(IntelWindow(3, 3, 4, 0.85, 7),))
    [(_, text, _)] = market_lua.exported_items(intel(item), set())
    assert text.endswith(";138:4:139:1120:7;3:3:4:850:7") and "1000,1000" not in text


def test_lua_strings_are_escaped_and_items_are_chunked_into_functions():
    assert market_lua.lua_string('a"b\\c\nd\re') == '"a\\"b\\\\c\\nd\\re"'
    many = intel(*(replace(EMPTY, key=k, cur=10) for k in range(1, market_lua.CHUNK + 2)))
    text = market_lua.market_lua([many], {}, {}, BUILT)
    assert text.count("do local fill = function(I, C, D)") == 2 and "observationsUntil" in text
    assert market_lua.trim_curve("", False) == ""
    none_until = replace(many, observations_until_ms=None, items={})
    assert "observationsUntil = nil" in market_lua.market_lua([none_until], {}, {}, BUILT)


def test_A23_disenchantable_exported_items_carry_their_skill_and_kind_and_count_in_the_checksum():
    text = market_lua.market_lua([intel(LINEN_INTEL, replace(EMPTY, key=OTHER))], {}, {}, BUILT,
                                 {LINEN: "1,2,18,a", OTHER: "1,2,18,w"})
    assert f'D[{LINEN}]="1,2,18,a"' in text and f"D[{OTHER}]" not in text               # OTHER isn't exported
    strings = len("45,20,60,49,49,40,60,4,480,480,93,86,72,45,20,42,0,900,,0,,,7;138:4:139:1120:7;") + 3 + len("1,2,18,a")
    assert f'checksum = "3:{strings}"' in text


def test_EX5_prices_older_than_7_days_are_left_out_unless_shortlisted():
    stale = replace(LINEN_INTEL, key=OTHER, cur_age_ms=8 * 24 * HOUR)
    keys = [k for k, _, _ in market_lua.exported_items(intel(LINEN_INTEL, stale), set())]
    assert keys == [LINEN]
    assert [k for k, _, _ in market_lua.exported_items(intel(LINEN_INTEL, stale), {OTHER})] == [LINEN, OTHER]


def test_EX6_vendor_prices_are_exported_sorted_by_key_outside_the_checksum():
    plain = market_lua.market_lua([intel(LINEN_INTEL)], {}, {}, BUILT)
    text = market_lua.market_lua([intel(LINEN_INTEL)], {}, {}, BUILT, vendor={4470: 38, 2320: 10})
    assert "  vendor = { [2320] = 10, [4470] = 38 }," in text and "  vendor = {  }," in plain
    assert plain.split("checksum = ")[1].split(",")[0] == text.split("checksum = ")[1].split(",")[0]



def test_EX10_each_item_ends_in_its_typical_daily_supply():
    from dataclasses import replace

    from ahsync.export.market_lua import item_text
    linen = ItemIntel(LINEN, 45, 20, 3_600_000, 49, 49, 40, 60, 0.0, 48.0, 0.0, 48.0, "ahf", {"ahf": 7}, 45, 20, 0.9,
                      False, 42.0, 86, 93, None, ())
    assert item_text(replace(linen, median_daily_supply=70)).split(";")[0].endswith(",93,86,,45,20,42,0,900,70,0,,,7")
    unseen = item_text(replace(linen, units_on_ah=None, median_daily_supply=40)).split(";")[0].split(",")
    assert (unseen[10], unseen[18], len(unseen)) == ("", "40", 23)
    assert item_text(linen).split(";")[0].endswith(",,0,,,7")


def test_EX20_each_item_ends_in_its_14_day_median():
    from ahsync.export.market_lua import item_text
    numbers = item_text(replace(LINEN_INTEL, med14=52)).split(";")[0].split(",")
    assert (numbers[-2], numbers[-3], len(numbers)) == ("52", "", 23)              # after closes30, before salesDays
    assert item_text(LINEN_INTEL).split(";")[0].split(",")[-2] == ""                 # unknown: empty, never 0


def test_EX21_each_item_ends_in_its_days_of_sales_data():
    from ahsync.export.market_lua import item_text
    last = lambda intel: item_text(intel).split(";")[0].split(",")[-1]            # noqa: E731
    assert last(replace(LINEN_INTEL, days_by_source={"blizzard": 2})) == "2"       # thin: 2 of the last 7 days
    assert last(replace(LINEN_INTEL, days_by_source={"ahf": 4, "blizzard": 3})) == "7"
    assert last(replace(LINEN_INTEL, days_by_source={})) == ""                     # no sales data: empty, never 0


def test_A58_each_item_ends_in_whether_its_price_is_unconfirmed():
    from ahsync.export.market_lua import item_text
    linen = ItemIntel(LINEN, 1, 20, 3_600_000, 49, 49, 40, 60, 0.0, 48.0, 0.0, 48.0, "ahf", {"ahf": 7}, 45, 20, 0.9,
                      False, 42.0, 86, 93, None, (), cur_suspect=True)
    assert item_text(linen).split(";")[0].endswith(",1,,,7")                       # suspect, closes30, med14, salesDays


def test_EX11_flip_offers_go_in_each_market_entry_for_exported_items_outside_the_checksum():
    plain = market_lua.market_lua([intel(LINEN_INTEL)], {M: set()}, {}, BUILT)
    text = market_lua.market_lua([intel(LINEN_INTEL)], {M: set()}, {}, BUILT, flips={M: {LINEN: "53,61,138", 999: "1,1,1"}})
    assert f'F = {{ [{LINEN}] = "53,61,138" }}' in text and "[999]" not in text
    assert plain.split("checksum = ")[1].split(",")[0] == text.split("checksum = ")[1].split(",")[0]


def test_EX13_shortlisted_items_with_a_supply_pattern_carry_their_factor_row():
    flat = ",".join(["1000"] * 168)
    weeks = ",".join(["7"] * 168)
    patterned = replace(LINEN_INTEL, supply_how=flat, supply_weeks=weeks)
    text = market_lua.market_lua([intel(patterned)], {M: {LINEN}}, {}, BUILT, window_hours=8)
    assert f'S = {{ [{LINEN}] = "{"7" * 24}" }}' in text                 # flat: 1.0 every hour
    assert 'S = {  }' in market_lua.market_lua([intel(patterned)], {M: set()}, {}, BUILT)      # not shortlisted
    assert 'S = {  }' in market_lua.market_lua([intel(LINEN_INTEL)], {M: {LINEN}}, {}, BUILT)  # no pattern


def test_TT23_each_item_ends_in_its_hourly_closes_over_30_days():
    from ahsync.export.market_lua import item_text
    from ahsync.intel.contract import ItemIntel
    linen = ItemIntel(2589, 45, 20, 3_600_000, 49, 49, 40, 60, 0.0, 48.0, 0.0, 48.0, "ahf", {"ahf": 7}, 45, 20, 0.9,
                      False, 42.4, 86, 93, None, (), closes_30d=31)
    assert item_text(linen).split(";")[0].endswith(",0,31,,7")



def test_ages_count_back_from_the_export_for_a_cloud_document_too():
    from dataclasses import replace
    from ahsync.export.market_lua import aged_at
    from ahsync.intel.contract import ItemIntel, MarketIntelligence
    item = ItemIntel(2589, 45, 40, 600_000, 50, 52, 40, 60, 1.5, 38.0, 4.0, 40.0, "blizzard", {}, 44, 20, 0.8,
                     False, 41.8, 61, 93, 0, ())
    cloud = MarketIntelligence("forever.normal.horde.us", 1_000_000, 4_600_000, 1, {2589: item}, ages_at_ms=4_600_000)
    assert aged_at(cloud, 4_600_000) is cloud                                   # exported at its own time: as is
    assert aged_at(cloud, 5_200_000).items[2589].cur_age_ms == 1_200_000        # 10 min old + 10 min since
    desktop = replace(cloud, ages_at_ms=None)
    assert aged_at(desktop, 1_600_000).items[2589].cur_age_ms == 1_200_000      # a desktop document counts from built
