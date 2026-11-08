"""Tests for ahsync/engine/market_lines.py: the item detail's market lines (D105) and the tooltip's rules they share."""
from __future__ import annotations

from ahsync.engine.market_lines import (BestWindow, LineFacts, Mat, age_text, coverage_text, market_lines, percent,
                                        qualifier, strength)

TUE_0700, FRI_1800, SAT_1900 = 24 + 7, 4 * 24 + 18, 5 * 24 + 19      # UTC slots, 0 = Mon 00:00


def rows(f: LineFacts) -> list[tuple]:
    """Each row as (label, now text, 7d, 14d, 30d, word, word class)."""
    return [(r["label"], "".join(t for t, _ in r["now"]), r["d7"], r["d14"], r["d30"], r["word"], r["word_class"])
            for r in market_lines(f, 0)["rows"]]


LINEN = LineFacts(19, 0, 21, 21, 21, 24_808, 14_859, 14_859, 14_859, 0.744, True, (0.79, 0.79, 0.79), "mmm",
                  BestWindow(17, TUE_0700, 1, 0.64), BestWindow(24, FRI_1800, 1, 0.81), None, "2c", 33)


def test_D105_the_item_detail_shows_the_tooltips_lines_as_one_table():
    payload = market_lines(LINEN, 0)
    assert payload["heading"] == "Market lines (the tooltip's; the market's data covers 5% of the last 30 days)"
    assert rows(LINEN) == [
        ("Market Price", "19c now", "21c", "21c", "21c", "steady", ""),            # 19 / 21 = −9.5%: steady
        ("Market Inventory", "24,808 listed", "14,859", "14,859", "14,859", "high", "amber"),
        ("Sell Chance Now", "74% at 19c", "79%", "79%", "79%", "", ""),
        ("Best Buy Time", "17c · Tue 07:00 · 64%", "", "", "", "early", "amber"),
        ("Best Sell Time", "24c · Fri 18:00 · 81%", "", "", "", "early", "amber"),
        ("Vendor Price", "2c", "", "", "", "", "")]
    assert all(r["help"] for r in payload["rows"])


def test_D105_gear_says_est_and_shows_its_disenchant_value():
    gloves = LineFacts(100, 0, 100, 100, 100, 1_713, 2_751, 2_751, 2_751, 0.05, False, (0.0623, 0.0623, 0.0623), "eee",
                       None, BestWindow(110, SAT_1900, 1, 0.09),
                       (Mat(0.8, 1, 2, "Strange Dust", 25), Mat(0.2, 1, 2, "Lesser Magic Essence", 110)),
                       "24c · from the game", 33)
    out = rows(gloves)
    assert out[1] == ("Market Inventory", "1,713 listed", "2,751", "2,751", "2,751", "low", "amber")
    assert out[2] == ("Sell Chance Now", "5% at 1s", "6%", "6%", "6%", "est.", "amber")
    assert out[3] == ("Best Sell Time", "1s 10c · Sat 19:00 · 9% est.", "", "", "", "early", "amber")
    assert out[4:] == [("Disenchant Value", "37% below AH · 63c", "", "", "", "", ""),
                       ("80% · 1.5 Strange Dust @ 25c", "30c", "", "", "", "", ""),
                       ("20% · 1.5 Lesser Magic Essence @ 1s 10c", "33c", "", "", "", "", ""),
                       ("Vendor Price", "24c · from the game", "", "", "", "", "")]
    lines = market_lines(gloves, 0)["rows"]
    assert lines[4]["now"][0] == ["37% below AH", "neg"] and lines[5]["indent"] is True


def test_D105_missing_values_read_as_dashes_and_absent_lines_go():
    bare = LineFacts(None, None, None, None, 10, None, None, None, None, None, None)
    assert rows(bare) == [("Market Price", "none listed", "—", "—", "10c", "", ""),
                          ("Market Inventory", "0 listed", "—", "—", "—", "", ""),
                          ("Sell Chance Now", "—", "—", "—", "—", "", ""),
                          ("Vendor Price", "unknown", "", "", "", "", "")]
    assert market_lines(bare, 0)["heading"].endswith("covers — of the last 30 days)")
    thin = LineFacts(19, 2 * 3_600_000, 21, None, None, 5, None, None, None, 0.3, True, (0.41, None, None), "c--",
                     None, BestWindow(24, FRI_1800, 7, None), None, "2c", 720)
    out = rows(thin)
    assert out[0][1] == "19c (2 h)" and out[2] == ("Sell Chance Now", "30% at 19c", "41%", "—", "—", "class est.", "amber")
    assert out[3] == ("Best Sell Time", "24c · Fri 18:00 · —", "", "", "", "established", "")   # no percentage: no est.
    assert market_lines(LineFacts(**{**thin.__dict__, "buy": None}), 2 * 3_600_000)["rows"][3]["now"][0][0] == \
        "24c · Fri 20:00 · —"                                                              # local time
    same = LineFacts(100, 0, None, None, None, None, None, None, None, None, None,
                     mats=(Mat(1.0, 1, 1, "Dust", 100), Mat(0.5, 1, 1, "Shard", None)))
    de = market_lines(same, 0)["rows"][3:6]
    assert [r["now"] for r in de] == [[["same as AH", ""], [" · 1s", ""]], [["1s", ""]], [["no price", "amber"]]]
    assert market_lines(LineFacts(None, None, None, None, None, None, None, None, None, None, None,
                                  mats=(Mat(1.0, 1, 1, "Dust", 100),)), 0)["rows"][3]["now"] == [["1s", ""]]


def test_TT34_the_qualifier_word_as_the_addon_says_it():
    words = [qualifier(*v)[0] for v in ((130, 120, 110, 100), (100, 104, 108, 112), (70, 100, 105, 110),
                                         (24808, 14859, 14859, 14859), (1713, 2751, 2751, 2751), (105, 100, 98, 96))]
    assert words == ["rising", "steady", "low", "high", "low", "steady"]
    assert [qualifier(90, 100, 110, 120), qualifier(110, 100, None, None), qualifier(40, None, 50, 60)] == [
        ("falling", "neg"), ("high", "amber"), (None, "")]
    assert qualifier(130, 120, 110, 100) == ("rising", "pos")


def test_TT40_coverage_and_the_shared_rounding_of_shares():
    assert [coverage_text(h) for h in (33, 720, 719, 0, None)] == [
        "5% coverage", "100% coverage", "100% coverage", "— coverage", "— coverage"]
    assert [percent(s) for s in (0.744, 0.7445, 0.0623, 0.0, None)] == ["74%", "75%", "6%", "0%", "—"]   # as the file
    assert [age_text(a) for a in (20_000, 25 * 60_000, 2 * 3_600_000, 3 * 86_400_000, None)] == [
        "now", "25 min", "2 h", "3 d", "now"]
    assert [strength(w) for w in (2, 3, 5, 6)] == [("early", "amber"), ("pattern", ""), ("pattern", ""),
                                                    ("established", "")]
