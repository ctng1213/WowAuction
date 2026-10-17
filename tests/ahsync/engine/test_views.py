"""Tests for ahsync/engine/views.py: display scenarios D3-D8 and the tooltip TT1-TT5."""
from __future__ import annotations

from ahsync.engine.views import (
    ChanceVolume, CraftInput, DisenchantInput, TooltipLine, age_text, chance_volume, craft_money, craft_row,
    delta_percent, disenchant_row, money, risk, round_half_up,
    signed_money, tooltip,
)

BAR = 2840
NAMES = {BAR: "Copper Bar"}
G, S = 10_000, 100


def test_D3_a_buy_shows_each_total_and_risk_against_all_your_gold():
    assert (money(25 * S), money(20 * 25 * S)) == ("25s", "5g")
    assert risk(20 * 25 * S, 50 * G) == ("10.0%", False)


def test_D4_a_post_risks_only_its_deposit():
    assert risk(15 * S, 50 * G) == ("0.3%", False)


def test_D5_risk_above_the_threshold_is_amber():
    assert risk(15 * G, 50 * G) == ("30.0%", True)


def test_D6_mats_bought_on_the_ah_show_each_and_total():
    cells = craft_money(3, 6000, {}, NAMES)
    assert (cells.each, cells.total, cells.note) == ("20s", "60s", "")


def test_D7_owning_every_mat_shows_a_dash_and_a_note():
    cells = craft_money(3, 0, {BAR: 6}, NAMES)
    assert (cells.each, cells.total, cells.note) == ("—", "—", "uses 6 owned Copper Bar")


def test_D8_a_partial_top_up_spreads_the_gold_over_the_crafts():
    cells = craft_money(3, 2000, {BAR: 4}, NAMES)
    assert (cells.each, cells.total) == ("6s 67c", "20s")          # 20 s ÷ 3 = 666.7 c → 667 c


def test_TT1_deltas_against_the_current_price():
    lines = tooltip(800, "2h ago", 1100, 1425, True, "Sat 17:00–21:00", 1230, 0.46)
    assert [line.label for line in lines] == ["Current AH price", "Historical price", "Disenchant value",
                                              "Best time to sell", "Price at best time", "Chance to sell"]
    assert [line.value for line in lines] == ["8s (2h ago)", "11s (30d, +38%)", "14s 25c (+78%)",
                                              "Sat 17:00–21:00", "12s 30c (+54%)", "46%"]
    assert lines[1].colour == "green"


def test_TT2_a_lower_historical_price_is_a_red_negative_delta():
    line = tooltip(1000, "1h ago", 900, None, False, None, None, None)[1]
    assert line == TooltipLine("Historical price", "9s (30d, −10%)", "red")


def test_TT3_no_current_price_means_values_without_deltas():
    lines = tooltip(None, "", 1100, 1425, True, None, 1230, None)
    assert [line.value for line in lines] == ["—", "11s", "14s 25c", "—", "12s 30c", "—"]


def test_TT4_without_an_enchanter_the_disenchant_line_is_left_out():
    labels = [line.label for line in tooltip(800, "2h ago", 1100, 1425, False, None, None, None)]
    assert "Disenchant value" not in labels


def test_TT5_a_half_percent_delta_rounds_up():
    assert delta_percent(1125, 1000) == 13


def test_money_formatting():
    assert money(11810) == "1g 18s 10c"
    assert money(0) == "0c" and money(None) == "—"
    assert money(-20) == "−20c" and money(213.75) == "2s 14c"
    assert signed_money(830) == "+8s 30c" and signed_money(-20) == "−20c" and signed_money(0) == "0c"
    assert round_half_up(2.5) == 3 and round_half_up(-2.5) == -2


def test_chance_volume_pair():
    assert chance_volume(0.92, 80, 48.2, "ahf", 93) == ChanceVolume("92%", 80, "high", "48/day AHF · 93 on AH")
    assert chance_volume(0.75, 30, 31, "books", None).volume == "31/day est."
    assert chance_volume(0.5, None, None, None, None) == ChanceVolume("50%", None, None, "—")
    assert chance_volume(None, 80, 10, "ahf", 5) == ChanceVolume("—", None, None, "—")
    assert chance_volume(0.5, 50, 3, None, 4).volume == "3/day · 4 on AH"


def test_risk_without_recorded_gold_and_zero_delta():
    assert risk(100, 0) == ("—", False)
    assert delta_percent(None, 100) is None and delta_percent(100, None) is None
    assert tooltip(1000, "now", 1000, None, False, None, None, None)[1].value == "10s (30d, 0%)"


def test_age_text():
    assert [age_text(v) for v in (None, 30_000, 20 * 60_000, 2 * 3_600_000, 6 * 86_400_000)] == \
        ["—", "now", "20 min", "2 h", "6 d"]


def test_disenchant_row_cells():
    pair = chance_volume(1.0, 100, 210, "ahf", 400)
    row = disenchant_row(DisenchantInput(4239, "Scouting Gloves of the Monkey", 2, 25, 800,
                                         (("Strange Dust", 2.5),), 1425, pair, 2 * 3_600_000), 50 * G)
    assert (row["buy"], row["mats"], row["ev"], row["profit"], row["profit_class"]) == \
        ("8s", "2.5 Strange Dust", "14s 25c", "+6s 25c", "pos")
    assert (row["chance"], row["volume"], row["risk"], row["price_age"]) == ("100%", "210/day AHF · 400 on AH",
                                                                            "0.2%", "2 h")
    assert row["sort"] == {"ilvl": 25, "buy": 800, "ev": 1425, "profit": 625, "price_age": 7_200_000}
    unknown_age = disenchant_row(DisenchantInput(1, "x", 2, 5, 900, (), 0.0, pair, None), 0)
    assert (unknown_age["profit_class"], unknown_age["sort"]["price_age"]) == ("neg", -1)


def test_craft_row_cells():
    pair = chance_volume(0.58, 60, 6, "books", 20)
    cells = craft_money(3, 6000, {}, NAMES)
    row = craft_row(CraftInput(2663, "Copper Rod", 1, "Brugan (Blacksmithing 112)", "AH", 3, cells, 6000,
                               3800.0, 5400.0, pair, "", "Buys 6 Copper Bar on the AH"), 50 * G)
    assert (row["mats_each"], row["mats_total"], row["value_each"], row["profit"], row["risk"]) == \
        ("20s", "60s", "38s", "+54s", "1.2%")
    assert row["note"] == "Buys 6 Copper Bar on the AH"
    assert row["sort"] == {"crafts": 3, "mats_total": 6000, "profit": 5400, "value_each": 3800}
    owned = craft_row(CraftInput(1, "x", 1, "A", "Owned", 1, craft_money(1, 0, {BAR: 2}, NAMES), 0, None,
                                 -1.0, pair, "ready", ""), 50 * G)
    assert (owned["note"], owned["value_each"], owned["sort"]["value_each"], owned["profit_class"]) == \
        ("uses 2 owned Copper Bar", "—", -1, "neg")
