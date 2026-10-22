"""Tests for ahsync/engine/gathering_history.py: Gathering's historical cells and detail blocks (SPEC §3.4E.7)."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine import gathering_history as gh
from ahsync.engine.gathering_history import History
from ahsync.engine.profile import Profile
from tests.support import gathering_world as gw

HOUR, MINUTE = 3_600_000, 60_000
COPPER = History(220, 200, 30, 61, gw.COPPER_HOW, gw.WINDOWS[gw.COPPER])
FLAT = Profile(tuple([1.0] * 168), tuple([0] * 168), tuple([False] * 168))


def text(segments: list[list[str]]) -> str:
    """The reading line as the page joins it."""
    return " ".join(t for t, _ in segments)


def test_GA28_the_median_needs_24_closes_and_an_unknown_count_keeps_it():
    assert gh.has_median(COPPER) and gh.has_median(replace(COPPER, closes_30d=24))
    assert not gh.has_median(replace(COPPER, closes_30d=23)) and not gh.has_median(replace(COPPER, med30=None))
    assert gh.has_median(replace(COPPER, closes_30d=None))           # older documents: no count, the median stays
    thin = gh.columns(replace(COPPER, closes_30d=23), gw.NOW, 0)
    assert (thin["median"], thin["median_title"], thin["deviation"], thin["window"], thin["window_title"]) == (
        "—", "thin data: 23 hourly closes in 30 days; the median needs 24", "—", "—",
        "thin data: 23 hourly closes in 30 days; the median needs 24")
    assert gh.columns(replace(COPPER, med30=None), gw.NOW, 0)["median_title"] == "no price history yet"
    assert gh.median_lines(replace(COPPER, closes_30d=23)) == [
        ["30-day median", "—", "thin data: 23 hourly closes in 30 days; the median needs 24", "", None],
        ["Deviation", "—", "", "", None]]
    assert gh.median_lines(replace(COPPER, price=None))[1] == ["Deviation", "—", "", "", None]
    assert gh.median_lines(COPPER) == [["30-day median", "2s", "", "", None], ["Deviation", "", "+10%", "pos", None]]


def test_signs_and_their_colours():
    assert [gh.signed(p) for p in (10, -22, 0)] == ["+10%", "−22%", "0%"]
    assert [gh.sign_class(p) for p in (10, -22, 0, None)] == ["pos", "neg", "", ""]


def test_GA29_copper_ores_window_starts_sat_17_and_its_price_is_the_median_times_its_ratio():
    c = gh.columns(COPPER, gw.NOW, 0)
    assert (c["median"], c["deviation"], c["deviation_class"]) == ("2s", "+10%", "pos")
    assert (c["window"], c["window_muted"], c["window_title"]) == (
        "Sat 17:00", False, "Start of the top selling window Sat 17:00–21:00 (best hour Sat 19:00) · seasonality "
                            "strength 61")
    # 200 × 1.12 = 224 (2s 24c); (224 − 220) ÷ 220 = 1.8% → +2%
    assert (c["window_price"], c["window_delta"], c["window_delta_class"], c["window_price_title"]) == (
        "2s 24c", "+2% vs now", "pos", "2s median × 1.12 (Sat 17:00–21:00)")
    # Mon 10:38 → Sat 17:00 = 5 d 6 h 22 min = 7,582 min: the Sell window sorts by the wait to it
    assert c["sort"] == {"median": 200, "deviation": 10, "window": 7582, "window_price": 224}
    unlisted = gh.columns(replace(COPPER, price=None), gw.NOW, 0)
    assert (unlisted["deviation"], unlisted["window_price"], unlisted["window_delta"]) == ("—", "2s 24c", "")
    # local time: 2 h ahead, the same window starts Sat 19:00 local
    assert gh.columns(COPPER, gw.NOW, 2 * HOUR)["window"] == "Sat 19:00"


def test_GA30_under_the_strength_no_timing_edge_and_at_it_the_window():
    weak = gh.columns(replace(COPPER, seasonality=39), gw.NOW, 0)
    assert (weak["window"], weak["window_muted"], weak["window_price"], weak["sort"]["window"]) == (
        "no timing edge", True, "—", None)
    assert weak["window_title"] == "Seasonality strength 39, under 40: weak pattern, no strong timing advantage"
    assert gh.columns(replace(COPPER, seasonality=40), gw.NOW, 0)["window"] == "Sat 17:00"     # inclusive
    none = gh.columns(replace(COPPER, seasonality=None), gw.NOW, 0)
    assert (none["window"], none["window_sub"], none["window_title"]) == ("—", "too little data", gh.EVIDENCE_TITLE)
    no_window = gh.columns(replace(COPPER, windows=()), gw.NOW, 0)
    assert (no_window["window"], no_window["window_title"]) == ("—", gh.NO_WINDOW_TITLE)
    assert gh.state(replace(COPPER, min_strength=70)) == gh.WEAK
    blank = gh.blank_columns("needs history")
    assert (blank["median"], blank["window"], blank["median_title"]) == ("—", "—", "needs history")
    assert set(blank["sort"].values()) == {None}


def test_GA31_the_wait_runs_to_the_windows_start_and_is_zero_inside_it():
    top = gw.WINDOWS[gw.COPPER][0]
    assert gh.wait_minutes(top, gw.NOW, 0) == 7582 and gh.wait_text(7582) == "5 d 6 h 22 min"
    assert gh.wait_minutes(top, gw.at(gw.SAT, "18:10") + 7 * 24 * HOUR, 0) == 0          # inside: no wait
    assert gh.wait_minutes(top, gw.at(gw.SAT, "21:00") + 7 * 24 * HOUR, 0) == 7 * 1440 - 4 * 60
    assert gh.wait_text(120) == "0 d 2 h"


def test_GA31_the_heatmap_marks_now_the_top_window_and_windows_2_and_3():
    heat = gh.heat(COPPER, COPPER.windows, gw.NOW, 0)
    rows = [{"day": r["day"], "cells": [[lv, heat["texts"][i], mark] for lv, i, mark in r["cells"]]}
            for r in heat["rows"]]                                     # the hovers picked as the page picks them
    mon, wed, sat, sun = rows[0]["cells"], rows[2]["cells"], rows[5]["cells"], rows[6]["cells"]
    assert mon[10] == [0, "×0.97 · 7 weeks · now", "now"]       # the page's hover: "Mon" "10:00" and this text
    assert [sat[h][2] for h in range(16, 22)] == ["", "top", "top", "top", "top", ""]
    assert [sun[h][2] for h in (19, 20, 21, 22)] == ["next", "next", "next", ""]
    assert [wed[h][2] for h in (20, 21, 22)] == ["next", "next", ""]
    assert wed[20] == [3, "×1.08 · 4 weeks · window 3", "next"] and sat[17][0] == 4
    assert sum(c[2] != "" for r in rows for c in r["cells"]) == 1 + 4 + 3 + 2
    assert gh.HEAT_HOURS[10] == "10:00" and len(gh.HEAT_HOURS) == 24
    assert heat["texts"][0] == "×1.02 · 7 weeks" and len(heat["texts"]) == len(set(heat["texts"])) < 40
    # now inside a window: the now outline wins, the hover names both
    inside = gh.heat(COPPER, COPPER.windows, gw.at(gw.SAT, "18:10") + 7 * 24 * HOUR, 0)
    cell = inside["rows"][5]["cells"][18]
    assert (inside["texts"][cell[1]], cell[2]) == ("×1.12 · 7 weeks · now · window 1", "now")
    sparse = Profile(COPPER.how.values, tuple(0 if s == 0 else 7 for s in range(168)), COPPER.how.confident)
    empty = gh.heat(replace(COPPER, how=sparse), (), gw.NOW, 0)
    assert empty["rows"][0]["cells"][0] == [None, 0, ""] and empty["texts"][0] == "no data"
    assert gh.heat(replace(COPPER, how=None), (), gw.NOW, 0) is None
    assert gh.heat(replace(COPPER, how=FLAT), (), gw.NOW, 0) is None             # no slot has data


def test_GA31_the_reading_line_for_copper_ore_at_mon_10_38():
    line = gh.reading(COPPER, gw.NOW, 0)
    assert text(line) == ("now 2s 20c (Mon 10:00 slot ratio 0.97) · best Sat 17:00–21:00 at 2s 24c · +2% for a "
                          "5 d 6 h 22 min wait")
    assert [c for _, c in line] == ["", "muted", "", "pos", ""]
    assert text(gh.reading(replace(COPPER, seasonality=22), gw.NOW, 0)) == (
        "now 2s 20c (Mon 10:00 slot ratio 0.97) · no timing edge: seasonality strength 22, under 40")
    assert text(gh.reading(replace(COPPER, seasonality=None), gw.NOW, 0)).endswith(
        "· too little data for a selling window")
    assert text(gh.reading(replace(COPPER, price=None), gw.at(gw.SAT, "18:10") + 7 * 24 * HOUR, 0)) == (
        "now no listing (Sat 18:00 slot ratio 1.12) · best Sat 17:00–21:00 at 2s 24c · in the window now")
    assert gh.reading(replace(COPPER, how=None), gw.NOW, 0) == []


def test_GA31_a_now_slot_without_weeks_of_data_says_no_data_as_its_cell_does():
    # found rendering W1: the cell read "Wed 12:00 no data · now" while the line quoted the slot's fallback ratio
    weeks = tuple(0 if s == 10 else 7 for s in range(168))                       # Mon 10:00 has no week of data
    sparse = replace(COPPER, how=Profile(gw.COPPER_HOW.values, weeks, gw.COPPER_HOW.confident))
    assert gh.reading(sparse, gw.NOW, 0)[1] == ["(Mon 10:00 slot: no data)", "muted"]
    heat = gh.heat(sparse, sparse.windows, gw.NOW, 0)
    assert heat["texts"][heat["rows"][0]["cells"][10][1]] == "no data · now"


def test_GA31_the_legend_names_now_and_the_windows_it_marks():
    w = COPPER.windows
    assert gh.legend(w, gw.NOW, 0) == {"now": "now (Mon 10:00)", "top": "the top selling window (Sat 17:00–21:00)",
                                       "next": "windows 2 and 3", "text": gh.HEAT_TEXT}
    assert gh.legend(w[:2], gw.NOW, 0)["next"] == "window 2" and gh.legend(w[:1], gw.NOW, 0)["next"] == ""
    assert gh.legend((), gw.NOW, 0)["top"] == ""


def test_GA32_the_top_windows_table_and_the_seasonality_sentence():
    assert [[r["rank"], r["window"], r["price"], r["weeks"]] for r in gh.window_rows(COPPER, COPPER.windows, 0)] == [
        ["1", "Sat 17:00–21:00", "2s 24c", "7"], ["2", "Sun 19:00–22:00", "2s 18c", "7"],
        ["3", "Wed 20:00–22:00", "2s 16c", "4"]]           # 200 × 1.12, 1.09, 1.08; weeks of the thinnest slot
    assert gh.window_rows(COPPER, COPPER.windows, 0)[1]["title"] == "2s median × 1.09"
    assert gh.timing_lines(COPPER, 0) == [
        ["Seasonality strength", "61", "· the weekly profile beat a flat price on the held-out week", "", None],
        ["Sell window in the table", "Sat 17:00", "· start of Sat 17:00–21:00 (best hour Sat 19:00)", "", None]]
    assert gh.timing_lines(replace(COPPER, seasonality=22, windows=()), 0) == [
        ["Seasonality strength", "22", "· weak pattern: no strong timing advantage", "", None],
        ["Sell window in the table", "no timing edge", "· seasonality strength 22, under 40", "", None]]
    assert gh.timing_lines(replace(COPPER, seasonality=None), 0) == [
        ["Seasonality strength", "—", "· insufficient evidence", "", None],
        ["Sell window in the table", "—", "· too little data", "", None]]


def test_GA32_the_detail_shows_windows_only_with_a_timing_edge():
    full = gh.detail(COPPER, gw.NOW, 0)
    assert len(full["windows"]) == 3 and full["windows_note"] == gh.WINDOWS_NOTE and full["heat"] is not None
    assert full["timing_note"] == ('Under 40 the sentence reads "weak pattern: no strong timing advantage" and the '
                                   'table says "no timing edge".')
    weak = gh.detail(replace(COPPER, seasonality=22), gw.NOW, 0)
    assert (weak["windows"], weak["windows_note"], weak["legend"]["top"]) == ([], "", "")
    assert sum(c[2] != "" for r in weak["heat"]["rows"] for c in r["cells"]) == 1      # now only


    assert gh.window_price(190, gw.WINDOWS[gw.MEDIUM_LEATHER][0]) == 200                 # 199.5 → 200, half up
