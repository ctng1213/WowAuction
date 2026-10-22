"""Tests for ahsync/engine/gathering.py: the catalog, eligibility, rates and gold per hour (SPEC §3.4E)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahsync.engine import gathering as g
from ahsync.engine.gathering import CatalogItem, Observed, Rate, Source
from tests.support import gathering_world as gw

ROOT = Path(__file__).resolve().parents[3]
MONSTER = {"type": "monster", "profession": None, "skill": None}


def items() -> dict[str, CatalogItem]:
    """The mockup catalog by name."""
    valid, _ = g.catalog(gw.catalog_rows())
    return {it.name: it for it in valid}


def test_GA2_bad_catalog_rows_are_skipped_with_a_status_line_and_the_others_kept():
    rows = gw.catalog_rows() + [
        {"key": 3858, "name": "Mithril Ore", "categories": ["Mining"],
         "sources": [{"type": "node", "profession": "Minning", "skill": 175}]},
        {"key": 9999, "name": "Imaginary Ore", "categories": ["Mining"],
         "sources": [{"type": "node", "profession": "Mining", "skill": 350}]}]
    valid, skipped = g.catalog(rows)
    assert len(valid) == 16
    assert g.skipped_text(skipped) == ("gathering catalog: 2 rows skipped (Mithril Ore: unknown profession 'Minning'; "
                                       "Imaginary Ore: skill 350 outside 1–300)")
    assert g.skipped_text(skipped[:1]) == "gathering catalog: 1 row skipped (Mithril Ore: unknown profession 'Minning')"
    assert g.skipped_text([]) == ""


@pytest.mark.parametrize("row, why", [
    ("not a row", "?: no key or name"),
    ({"key": 1, "name": "A", "categories": ["Mining"], "sources": []}, "A: no source"),
    ({"key": 1, "name": "A", "categories": ["Mining"], "sources": [{"type": "chest"}]}, "A: unknown source type 'chest'"),
    ({"key": 1, "name": "A", "categories": ["Mining"], "sources": ["x"]}, "A: unknown source type 'x'"),
    ({"key": 1, "name": "A", "categories": ["Mining"], "sources": [{"type": "node", "profession": "Skinning", "skill": 1}]},
     "A: profession 'Skinning' can't gather a node source"),
    ({"key": 1, "name": "A", "categories": ["Mining"], "sources": [{"type": "node", "profession": "Mining", "skill": True}]},
     "A: skill True outside 1–300"),
    ({"key": 1, "name": "A", "categories": ["Mining"], "sources": [{"type": "node", "profession": "Mining", "skill": 0}]},
     "A: skill 0 outside 1–300"),
    ({"key": 1, "name": "A", "categories": ["Mining", "Cooking"], "sources": [MONSTER]}, "A: unknown category 'Cooking'"),
    ({"key": 1, "name": "A", "categories": ["Mining"], "sources": [MONSTER]}, "A: category 'Mining' has no source"),
    ({"key": 1, "name": "A", "categories": [], "sources": [MONSTER]}, "A: no category"),
    ({"key": 2770, "name": "Copper Twice", "categories": ["Monster"], "sources": [MONSTER]}, "Copper Twice: duplicate key 2770"),
])
def test_each_invalid_row_says_why_it_is_skipped(row, why):
    _, skipped = g.catalog(gw.catalog_rows() + [row])
    assert skipped == [why]


def test_a_row_keeps_its_url_and_lists_its_categories_in_catalog_order():
    [item], _ = g.catalog([{"key": 1, "name": "A", "categories": ["Monster", "Skinning"], "url": "https://x/1",
                            "sources": [MONSTER, {"type": "skinning", "profession": "Skinning", "skill": 5}]}])
    assert item.categories == ("Skinning", "Monster") and item.url == "https://x/1"
    assert g.category_of(item.sources[1]) == "Skinning" and g.category_of(item.sources[0]) == "Monster"


def test_GA1_the_shipped_catalog_file_validates_and_covers_the_mockups_items():
    doc = json.loads((ROOT / "ahsync" / "data" / "gathering_catalog.json").read_text(encoding="utf-8"))
    assert "Forever" in doc["_comment"] and doc["version"] == 1
    valid, skipped = g.catalog(doc["items"])
    assert skipped == [] and len(valid) == len(doc["items"])
    by_name = {it.name: it for it in valid}
    expected = {"Copper Ore": 1, "Tin Ore": 65, "Silver Ore": 75, "Peacebloom": 1, "Silverleaf": 1, "Earthroot": 15,
                "Mageroyal": 50, "Briarthorn": 70, "Light Leather": 1, "Medium Leather": 100,
                "Raw Brilliant Smallfish": 1, "Raw Longjaw Mud Snapper": 1, "Black Lotus": 300}
    assert {n: g.skill_source(by_name[n]).skill for n in expected} == expected
    assert {n: by_name[n].categories for n in ("Light Leather", "Linen Cloth", "Small Egg", "Light Feather")} == {
        "Light Leather": ("Skinning", "Monster"), "Linen Cloth": ("Monster",), "Small Egg": ("Monster",),
        "Light Feather": ("Monster",)}
    assert by_name["Copper Ore"].key == 2770 and by_name["Copper Ore"].url.endswith("item=2770")


def test_GA3_eligibility_in_the_world():
    it = items()
    brugan, amaya = gw.SKILLS[gw.BRUGAN], gw.SKILLS[gw.AMAYA]
    assert [g.eligible(it[n], brugan) for n in ("Copper Ore", "Tin Ore", "Silver Ore")] == [True, True, False]
    assert not any(g.eligible(it[n], amaya) for n in ("Copper Ore", "Tin Ore", "Silver Ore"))
    assert g.eligible(it["Light Feather"], brugan) and g.eligible(it["Light Feather"], amaya)
    assert g.eligible(it["Light Feather"], {})                         # a new character with no profession at all
    for name in ("Silver Ore", "Briarthorn", "Mageroyal", "Medium Leather"):
        assert not g.eligible(it[name], brugan) and not g.eligible(it[name], amaya)


def test_GA4_an_item_is_eligible_when_any_source_is_and_its_skill_is_the_gathering_sources():
    leather = items()["Light Leather"]
    assert g.eligible(leather, gw.SKILLS[gw.AMAYA])                     # through the monster source
    assert g.eligible(leather, gw.SKILLS[gw.BRUGAN])                    # through both
    assert g.skill_source(leather) == Source("skinning", "Skinning", 1)
    assert g.skill_source(items()["Linen Cloth"]) is None


def test_the_skill_column_takes_the_lowest_gathering_source():
    item = CatalogItem(1, "A", ("Herbalism",), (Source("node", "Herbalism", 210), Source("node", "Herbalism", 50)))
    assert g.skill_source(item).skill == 50


OBS = {("A-R", 1): Observed(10, 10, 60, 1), ("B-R", 1): Observed(20, 20, 60, 1), ("C-R", 1): Observed(20, 20, 60, 1)}


def test_GA5_the_rate_priority_is_manual_then_own_observed_then_a_fallback_among_other_eligible_characters():
    manual = {("A-R", 1): 12}
    assert g.own_rate("A-R", 1, manual, OBS) == Rate("A-R", 12, "manual", OBS[("A-R", 1)])
    assert g.own_rate("A-R", 1, {}, OBS) == Rate("A-R", 10, "observed", OBS[("A-R", 1)])
    assert g.own_rate("D-R", 1, {("D-R", 1): 5}, OBS) == Rate("D-R", 5, "manual", None)
    assert g.own_rate("D-R", 1, {}, OBS) is None
    # A character with no rate of her own gets the best among the others who are eligible now, ties by id.
    assert g.effective_rate("D-R", 1, {}, OBS, ["A-R", "B-R", "C-R", "D-R"]) == Rate("B-R", 20, "observed",
                                                                                    OBS[("B-R", 1)], True)
    # Her own rate wins even when lower, and even when she is no longer eligible (her history).
    assert g.effective_rate("A-R", 1, {}, OBS, ["B-R"]).items_per_hour == 10
    # A character who is not eligible now is no fallback for anyone.
    assert g.effective_rate("D-R", 1, {}, OBS, ["A-R", "D-R"]).char_id == "A-R"
    assert g.effective_rate("D-R", 1, {}, OBS, ["D-R"]) is None


def test_under_all_characters_the_highest_rate_among_the_eligible_ones_wins_ties_by_id():
    assert g.best_rate(1, ["C-R", "B-R", "A-R"], {}, OBS).char_id == "B-R"
    assert g.best_rate(1, ["A-R"], {("A-R", 1): 30}, OBS) == Rate("A-R", 30, "manual", OBS[("A-R", 1)])
    assert g.best_rate(1, [], {}, OBS) is None


def test_GA6_net_per_unit_and_gold_per_hour_for_copper_ore():
    dep = g.deposit_per_unit(5)
    assert dep == 0.75                                        # the 24 h deposit of 20 units, 15c, ÷ 20
    net = g.net_per_unit(220, 0.85, 5)
    assert net == pytest.approx(208.8875)                     # 220 − 11 − 0.15 × 0.75
    assert g.net_per_hour(38, net) == pytest.approx(7937.725)   # "79s 38c" half up
    assert g.gross_per_hour(38, 220) == 8360                  # "83s 60c"
    assert g.deposit_per_unit(None) == 0.0 and g.deposit_per_unit(0) == 0.0


def test_GA7_no_sales_data_takes_the_default_chance_at_the_ah_price():
    assert g.chance_to_sell(None, None, 275, 275) == (0.5, True)          # ties the cheapest: 50%
    assert g.chance_to_sell(0.85, None, 275, 275) == (0.5, True)          # S unknown: the default all the same
    assert g.chance_to_sell(None, 5.0, 275, 275) == (0.5, True)
    assert g.chance_to_sell(0.85, 30.0, 220, 220) == (0.85, False)
    assert g.chance_to_sell(None, None, 300, 275, 0.2, 0.6) == (0.2, True)   # someone cheaper: default_sell_prob
    assert g.net_per_hour(4, g.net_per_unit(275, 0.5, 25)) == 1037.5       # 4 × 259.375 → "10s 38c"
