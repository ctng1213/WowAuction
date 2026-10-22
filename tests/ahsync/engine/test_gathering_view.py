"""Tests for ahsync/engine/gathering_view.py: the Gathering page's cells, views, footers and texts (SPEC §3.4E)."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine import gathering_view as gv
from ahsync.engine.gathering import CatalogItem, Observed, Source
from ahsync.engine.gathering_view import PriceFacts, gathering_view
from tests.support import gathering_world as gw

HOUR = 3_600_000


def rows(payload: dict, char: str = "") -> dict[str, dict]:
    """A view's rows by item name."""
    return {r["item"]: r for r in gw.view_rows(payload, char)}


def sort_like_the_page(view_rows: list[dict], key: str, descending: bool) -> list[str]:
    """The dashboard's sortRows: by row.sort[key], a null key last in both directions, ties keep the served order."""
    keyed = [r for r in view_rows if r["sort"][key] is not None]
    nulls = [r for r in view_rows if r["sort"][key] is None]
    return [r["item"] for r in sorted(keyed, key=lambda r: r["sort"][key], reverse=descending) + nulls]


def filter_like_the_page(payload: dict, char: str = "", off: tuple[str, ...] = (), min_price: int | None = None,
                         search: str = "", only: bool | None = None, skill: str = "") -> list[str]:
    """The dashboard's gaShown over the engine's row fields: categories OR, controls AND."""
    lo_hi = {v: (lo, hi) for v, _, lo, hi in payload["filters"]["skill"]}[skill]
    only = bool(char) if only is None else only
    out = []
    for r in gw.view_rows(payload, char):
        if not any(c not in off for c in r["cats"]):
            continue
        if min_price is not None and (r["price"] is None or r["price"] < min_price):
            continue
        if search and search.lower() not in r["name"].lower():
            continue
        if only and not r["eligible"]:
            continue
        if lo_hi[0] is not None and (r["skill"] is None or not lo_hi[0] <= r["skill"] <= lo_hi[1]):
            continue
        out.append(r["item"])
    return out


def test_GA1_the_catalogs_16_rows_in_ah_price_order_and_nothing_else():
    p = gathering_view(gw.world())
    view = gw.view_rows(p, "")
    assert [(r["item"], r["ah_price"]) for r in view] == [
        ("Silver Ore", "9s"), ("Briarthorn", "3s"), ("Tin Ore", "2s 75c"), ("Mageroyal", "2s 40c"),
        ("Copper Ore", "2s 20c"), ("Medium Leather", "2s"), ("Light Leather", "95c"), ("Earthroot", "60c"),
        ("Linen Cloth", "38c"), ("Wool Cloth", "30c"), ("Silverleaf", "24c"), ("Peacebloom", "18c"),
        ("Small Egg", "12c"), ("Light Feather", "9c"), ("Raw Brilliant Smallfish", "8c"),
        ("Raw Longjaw Mud Snapper", "—")]
    r = rows(p)
    assert (r["Light Leather"]["category"], r["Light Leather"]["skill_text"], r["Light Leather"]["skill_sub"]) == \
        ("Skinning · Monster", "1", "or monster drop")
    assert r["Linen Cloth"]["skill_text"] == "—" and r["Linen Cloth"]["skill"] is None
    assert "Rough Stone" not in r and "Thorium Ore" not in r      # looted or listed, but not in the catalog
    assert p["header"] == "16 in the catalog · 7 with a rate · desktop only: the addon only records loot"


def test_GA3_skill_is_amber_when_no_character_can_gather_it():
    r = rows(gathering_view(gw.world()))
    amber = [n for n, row in r.items() if row["skill_amber"]]
    assert amber == ["Silver Ore", "Briarthorn", "Mageroyal", "Medium Leather"]
    assert r["Silver Ore"]["skill_title"] == "no character has Mining 75" and r["Silver Ore"]["skill_sub"] == ""
    assert r["Copper Ore"]["skill_title"] == "Mining 1"


def test_GA5_a_lost_profession_keeps_his_rate_in_his_own_view_only():
    skills = {**gw.SKILLS, gw.BRUGAN: {"Skinning": 40}}                    # Brugan unlearned Mining
    p = gathering_view(gw.world(skills=skills))
    copper = rows(p)["Copper Ore"]
    assert (copper["rate"], copper["character"], copper["net"], copper["skill_amber"]) == ("—", "—", "—", True)
    mine = rows(p, gw.BRUGAN)["Copper Ore"]
    assert not mine["eligible"]                                             # hidden with the toggle on
    assert (mine["rate"], mine["character"], mine["net"]) == ("38", "Brugan", "79s 38c")   # toggle off: his history
    assert rows(p, gw.AMAYA)["Copper Ore"]["rate"] == "—"                  # no fallback for anyone


def test_GA6_copper_ores_net_gold_per_hour_and_gross_only_on_the_detail():
    p = gathering_view(gw.world())
    copper = rows(p)["Copper Ore"]
    assert (copper["net"], copper["sort"]["net"], copper["net_dot"]) == ("79s 38c", 7938, False)
    assert copper["net_title"] == "38/hr × 208.89c net per unit (gross 38 × 220c = 83s 60c) · chance 85%"
    assert "83s 60c" not in {v for k, v in copper.items() if k != "net_title" and isinstance(v, str)}   # not a cell
    card = gw.view_card(p, "", str(gw.COPPER))
    assert card["lines"] == [["Gross gold/hr", "38 × 2s 20c = 83s 60c"], ["Net gold/hr", "38 × 208.89c = 79s 38c"]]
    assert p["details"][str(gw.COPPER)]["net_unit"] == "2s 20c − 5% cut 11.00c − (1 − 85%) × 0.75c deposit = 208.89c"


def test_GA7_no_sales_data_uses_the_default_chance_with_the_dot_then_day_one_copper():
    tin = rows(gathering_view(gw.world()))["Tin Ore"]
    assert (tin["net"], tin["net_dot"]) == ("10s 38c", True)
    assert "chance is the default: no sales data yet (50%, as you'd be the cheapest)" in tin["net_title"]
    assert tin["note"] == "no sales data yet: default chance · Brugan: 1 session, 30 min"
    day_one = gathering_view(gw.world(day_one=True))
    copper = rows(day_one)["Copper Ore"]
    assert (copper["net"], copper["net_dot"]) == ("79s 28c", True)          # 38 × (209 − 0.5 × 0.75) = 7,927.75
    assert day_one["default_note"] == "Every Net gold/hr here uses the default chance (50%): no sales data yet."
    assert gathering_view(gw.world())["default_note"] == ""


def test_GA8_no_rate_or_no_listing_leaves_the_gold_blank_and_nothing_is_inferred():
    manual = {**gw.MANUAL, (gw.AMAYA, gw.MUD_SNAPPER): 10}
    r = rows(gathering_view(gw.world(manual=manual)))
    wool = r["Wool Cloth"]
    assert (wool["rate"], wool["character"], wool["net"], wool["ah_price"], wool["qty"]) == ("—", "—", "—", "30c", "140")
    assert wool["rate_title"] == "No rate yet" + gv.CLICK_HINT
    snapper = r["Raw Longjaw Mud Snapper"]
    assert (snapper["ah_price"], snapper["qty"], snapper["note"]) == ("—", "0", "no listing in the newest scan · Amaya: manual rate")
    assert (snapper["rate"], snapper["rate_sub"], snapper["net"], snapper["price_title"]) == \
        ("10", "manual", "—", "No listing in the newest scan")


def test_GA9_net_gold_per_hour_sorts_both_ways_with_blank_rows_last():
    view = gw.view_rows(gathering_view(gw.world()), "")
    blanks = ["Silver Ore", "Briarthorn", "Mageroyal", "Medium Leather", "Wool Cloth", "Small Egg", "Light Feather",
              "Raw Brilliant Smallfish", "Raw Longjaw Mud Snapper"]
    assert sort_like_the_page(view, "net", True) == ["Copper Ore", "Light Leather", "Tin Ore", "Earthroot",
                                                     "Linen Cloth", "Peacebloom", "Silverleaf", *blanks]
    assert sort_like_the_page(view, "net", False) == ["Silverleaf", "Peacebloom", "Linen Cloth", "Earthroot", "Tin Ore",
                                                      "Light Leather", "Copper Ore", *blanks]
    assert [{r["item"]: r for r in view}[n]["net"] for n in ("Light Leather", "Earthroot", "Linen Cloth")] == \
        ["18s 90c", "6s 83c", "5s 3c"]
    assert all(r["sort"]["net"] is None for r in view if r["item"] in blanks)


def test_GA10_filters_combine_categories_or_and_controls_and():
    p = gathering_view(gw.world())
    others = ("Fishing", "Herbalism", "Mining", "Skinning")
    assert filter_like_the_page(p, off=others, min_price=30) == ["Light Leather", "Linen Cloth", "Wool Cloth"]
    assert filter_like_the_page(p, off=others, min_price=30, search="cloth") == ["Linen Cloth", "Wool Cloth"]
    assert filter_like_the_page(p, gw.BRUGAN, search="leather") == ["Light Leather"]
    assert filter_like_the_page(p, skill="75-150") == ["Silver Ore", "Medium Leather"]
    assert filter_like_the_page(p, min_price=100) == ["Silver Ore", "Briarthorn", "Tin Ore", "Mageroyal", "Copper Ore",
                                                     "Medium Leather"]
    assert p["filters"]["min_price"] == [["", "Any"], ["100", "1s"], ["1000", "10s"], ["5000", "50s"],
                                         ["10000", "1g"], ["custom", "Custom…"]]
    assert p["filters"]["categories"] == ["Fishing", "Herbalism", "Mining", "Skinning", "Monster"]


def test_GA11_by_char_holds_three_complete_views_with_their_footers():
    p = gathering_view(gw.world())
    assert list(p["by_char"]) == ["", gw.AMAYA, gw.BRUGAN]
    assert all(r["eligible"] for r in gw.view_rows(p, "")) and len(gw.view_rows(p, "")) == 16
    brugan = p["by_char"][gw.BRUGAN]
    assert [r["item"] for r in gw.view_rows(p, gw.BRUGAN) if r["eligible"]] == ["Tin Ore", "Copper Ore", "Light Leather",
                                                                  "Linen Cloth", "Wool Cloth", "Small Egg",
                                                                  "Light Feather"]
    assert brugan["foot"] == [["7 of 16 shown", ""], [
        "9 hidden: 5 only Amaya can gather (Earthroot, Silverleaf, Peacebloom, Raw Brilliant Smallfish, Raw Longjaw "
        "Mud Snapper), 4 no character can (Silver Ore, Briarthorn, Mageroyal, Medium Leather)", "muted"]]
    amaya = p["by_char"][gw.AMAYA]
    assert sum(r["eligible"] for r in gw.view_rows(p, gw.AMAYA)) == 10
    assert amaya["foot"] == [["10 of 16 shown", ""], [
        "6 hidden: 2 only Brugan can gather (Tin Ore, Copper Ore), 4 no character can (Silver Ore, Briarthorn, "
        "Mageroyal, Medium Leather)", "muted"]]
    assert p["characters"] == [["", "All characters"], [gw.AMAYA, "Amaya"], [gw.BRUGAN, "Brugan"]]
    assert brugan["toggle"] == {"label": "Only what Brugan can gather", "disabled": False,
                                "title": "Hides what Brugan can't gather. Monster drops always stay"}
    assert brugan["foot_all"][-1] == ["Character = whose rate: Brugan's own, else a Fallback from another character "
                                      "who can gather it", "muted"]
    assert brugan["alert"] is None
    # Linen Cloth: Brugan has no rate of his own, Amaya's 14 shows as a fallback, for display only.
    linen = rows(p, gw.BRUGAN)["Linen Cloth"]
    assert (linen["rate"], linen["fallback"], linen["character"], linen["note"]) == ("14", True, "Amaya",
                                                                                     "Brugan has no rate yet")
    assert linen["rate_title"].startswith("Amaya's observed rate; Brugan has none yet. Replaced by Brugan's own rate")


def test_GA11_each_row_is_sent_once_and_a_view_carries_only_its_keys_and_the_cells_that_differ():
    p = gathering_view(gw.world())
    assert [r["item"] for r in p["rows"]][:3] == ["Silver Ore", "Briarthorn", "Tin Ore"] and len(p["rows"]) == 16
    assert all(view["keys"] == [r["key"] for r in p["rows"]] for view in p["by_char"].values())
    assert p["by_char"][""]["cells"] == {}                                 # All characters is the shared row
    brugan = p["by_char"][gw.BRUGAN]["cells"]
    assert brugan[str(gw.SILVER)] == {"eligible": False}
    assert set(brugan[str(gw.LINEN)]) == {"note", "rate_title", "fallback"}   # Amaya's 14 as his fallback
    assert str(gw.COPPER) not in brugan                                    # his own rate, as under All characters
    assert set(p["by_char"][gw.BRUGAN]) == {"keys", "cells", "foot", "foot_all", "toggle", "alert"}
    assert [b["char"] for b in p["rate_cards"][str(gw.COPPER)]["blocks"]] == [gw.BRUGAN, gw.AMAYA]


def test_GA12_the_freshness_line_then_with_your_own_scan_as_the_newest_price():
    p = gathering_view(gw.world())
    assert p["fresh"] == [["AH prices 26 h old", "amber"], ["professions synced Mon 10:38", ""],
                          ["7 farming sessions, 3 h 40 min observed", ""]]
    facts = dict(gw.price_facts())
    facts[gw.COPPER] = replace(facts[gw.COPPER], seen_ms=gw.NOW - 12 * 60_000, source="local")
    assert gathering_view(gw.world(facts=facts))["fresh"][0] == ["AH prices 12 min old (your scan)", "pos"]
    assert gathering_view(gw.world(rates=False))["fresh"][2] == ["0 farming sessions", ""]
    assert gathering_view(gw.world(sessions=1, session_minutes=60))["fresh"][2] == ["1 farming session, 1 h observed", ""]


def test_the_day_one_page_says_no_price_history_yet_and_shows_the_compact_detail():
    p = gathering_view(gw.world(day_one=True))
    assert p["fresh"] == [["AH prices 12 min old (your scan)", "pos"], [gv.NO_HISTORY_FRESH, ""]]
    d = p["details"][str(gw.COPPER)]
    assert (d["title"], d["subtitle"]) == ("Copper Ore: day one", "selected row · compact")
    assert d["price"] == [["Current AH price", "2s 20c", "· 12 min old (your scan)", "pos", None],
                          ["Listed quantity", "64 units", "", "", None],
                          ["30-day median · deviation", "—", "needs price history", "", None],
                          ["Chance to sell", "50%", "default: no sales data yet", "", "low"]]
    assert d["history"] == gv.NO_HISTORY_LINE and d["market"]["chance_note"] == "volume unknown"
    assert p["help"]["Median 30d"].endswith('needs price history: "—" until weeks of data arrive')
    assert p["help"]["Character"].startswith("Whose observed or manual rate this is")


def test_the_full_detail_title_and_the_price_card_with_history():
    p = gathering_view(gw.world())
    d = p["details"][str(gw.LIGHT_LEATHER)]
    assert (d["title"], d["subtitle"]) == ("Light Leather: price, timing and your rate",
                                           "selected row · Skinning 1 or monster drop")
    assert p["details"][str(gw.LINEN)]["subtitle"] == "selected row · monster drop"
    copper = p["details"][str(gw.COPPER)]
    assert copper["price"][0] == ["Current AH price", "2s 20c", "· 26 h old", "amber", None]
    assert copper["price"][3] == ["Chance to sell", "85%", "", "", "high"]
    assert copper["market"] == {"chance": "85%", "confidence": 80, "dot": "high", "sales": "30 AHF", "supply": "64",
                                "days": "2.1 days", "chance_note": ""}
    assert copper["deposit"].startswith("Deposit: 15% of the 5c vendor price per unit for 24 h") and \
        "only the 15% chance it expires" in copper["deposit"]
    snapper = p["details"][str(gw.MUD_SNAPPER)]
    assert snapper["net_unit"] is None and snapper["price"][0] == ["Current AH price", "—",
                                                                  "no listing in the newest scan", "", None]
    assert p["selected"] == str(gw.COPPER)                    # the highest net gold/hr under All characters
    assert rows(p)["Copper Ore"]["price_title"] == "seen 26 h ago (AHF scan Sun 08:38) · stale: over 24 h"


def test_rate_cards_list_who_has_a_rate_first_then_why_the_others_have_none():
    p = gathering_view(gw.world())
    earth = gw.view_card(p, "", str(gw.EARTHROOT))["blocks"]
    assert [(b["kind"], b["head"], b["sub"]) for b in earth] == [
        ("manual", "Amaya · 12/hr manual", "(observed 10/hr from 8 Earthroot over 2 sessions, 48 min)"),
        ("ineligible", "Brugan — no sessions with Earthroot yet · not eligible (no Herbalism)", "")]
    assert earth[0]["foot"] == "the override replaces the observed rate in the table for Amaya until cleared"
    copper = gw.view_card(p, "", str(gw.COPPER))["blocks"]
    assert [b["head"] for b in copper] == ["Brugan · 38/hr observed", "Amaya — no sessions with Copper Ore yet · not "
                                                                       "eligible (no Mining)"]
    assert copper[0]["sub"] == "· 70 Copper Ore over 3 sessions, 1 h 50 min"
    leather = gw.view_card(p, gw.AMAYA, str(gw.LIGHT_LEATHER))
    assert [b["head"] for b in leather["blocks"]] == ["Amaya — no sessions with Light Leather yet"]
    assert leather["lines"][0] == ["Gross gold/hr", "21 × 95c = 19s 95c"]      # Brugan's 21 as her fallback
    silver = gw.view_card(p, gw.BRUGAN, str(gw.SILVER))
    assert silver == {"blocks": [{"char": gw.BRUGAN, "kind": "ineligible", "head": "Brugan — no sessions with Silver Ore yet · not "
                                  "eligible (Mining 68 of 75)", "sub": "", "foot": "", "lines": []}], "lines": []}
    alone = gathering_view(gw.world(manual={(gw.BRUGAN, gw.WOOL): 9}))
    assert gw.view_card(alone, "", str(gw.WOOL))["blocks"][0]["sub"] == "(no sessions)"
    assert rows(alone)["Wool Cloth"]["rate_title"] == "Manual override 9/hr" + gv.CLICK_HINT
    assert rows(p)["Earthroot"]["rate_title"].startswith("Manual override 12/hr (observed 10/hr: 8 units ÷ 48 min)")
    assert rows(p)["Copper Ore"]["rate_title"] == ("70 units ÷ 1 h 50 min over 3 sessions = 38.2/hr, rounded"
                                                   + gv.CLICK_HINT)


def test_D88_states_no_rates_and_a_character_with_no_gathering_profession():
    p = gathering_view(gw.world(rates=False))
    assert p["no_rates"] == ("Farm for 20+ units and the addon records your rate. Every row keeps its price and sell "
                             "window meanwhile.")
    assert all(r["rate"] == r["character"] == r["net"] == "—" for r in gw.view_rows(p, ""))
    assert p["selected"] == str(gw.SILVER)                    # no gold/hr anywhere: the first row
    assert gathering_view(gw.world())["no_rates"] is None
    plain = gathering_view(gw.world(skills={**gw.SKILLS, gw.BRUGAN: {"Blacksmithing": 112}}))
    assert plain["by_char"][gw.BRUGAN]["alert"] == {
        "title": "Brugan can gather nothing yet: Monster drops only.",
        "text": 'Turn off "Only what Brugan can gather" to see every material. 5 Monster rows remain: Light Leather, '
                "Linen Cloth, Wool Cloth, Small Egg, Light Feather."}
    assert p["no_match"].split("{q}") == ['No farmable material matches "', '". The catalog covers what it knows how to '
                                          'gather; clear the search or the filters.']


def test_three_characters_group_the_hidden_rows_by_who_else_can_gather_them():
    cara = "Cara-Grommash"
    p = gathering_view(gw.world(characters=[gw.AMAYA, gw.BRUGAN, cara], skills={**gw.SKILLS, cara: {"Herbalism": 1}}))
    hidden = p["by_char"][gw.BRUGAN]["foot"][1][0]
    assert hidden.startswith("9 hidden: 3 only Amaya can gather (Earthroot, Raw Brilliant Smallfish, Raw Longjaw Mud "
                             "Snapper), 2 only Amaya and Cara can gather (Silverleaf, Peacebloom), 4 no character can")
    everyone = gathering_view(gw.world(skills={gw.AMAYA: {"Herbalism": 300, "Fishing": 1}, gw.BRUGAN: {
        "Mining": 300, "Skinning": 300, "Herbalism": 300, "Fishing": 1}}))
    assert everyone["by_char"][gw.BRUGAN]["foot"] == [["16 of 16 shown", ""]]
    assert [s[0] for s in everyone["by_char"][""]["foot"]] == [
        '16 materials · sorted by AH Price, highest first; rows with "—" sort last',
        "Character = whose rate: the highest among the characters who can gather it"]


def test_missing_prices_professions_and_an_empty_catalog():
    item = CatalogItem(1, "Odd Herb", ("Herbalism",), (Source("node", "Herbalism", 1),))
    p = gathering_view(gv.GatheringWorld([item], {}, {}, [], gw.NOW))
    row = gw.view_rows(p, "")[0]
    assert (row["ah_price"], row["qty"], row["price_title"]) == ("—", "0", "No listing in the newest scan")
    assert p["fresh"] == [["no AH prices yet", "amber"], [gv.NO_HISTORY_FRESH, ""]]
    unknown = PriceFacts(50, gw.NOW - 4 * 24 * HOUR, "blizzard", None, None, med30=40, closes_30d=30)
    q = gathering_view(gv.GatheringWorld([item], {1: unknown}, {}, [], gw.NOW))
    assert q["fresh"][0] == ["AH prices 4 d old", "amber"] and q["fresh"][1] == ["professions not synced yet", ""]
    qrow = gw.view_rows(q, "")[0]
    assert qrow["qty"] == "—" and qrow["price_title"].startswith("seen 4 d ago (blizzard scan ")
    assert q["details"]["1"]["deposit"].startswith("Deposit: 15% of the 0c vendor price")
    assert "Prices are 4 d old (amber over 24 h)" in q["note"]
    empty = gathering_view(gv.GatheringWorld([], {}, {}, [], gw.NOW))
    assert empty["selected"] == "" and empty["header"].startswith("0 in the catalog")
    assert empty["default_note"] == ""


def test_formatting_helpers():
    assert [gv.duration(m) for m in (48, 60, 110, 220)] == ["48 min", "1 h", "1 h 50 min", "3 h 40 min"]
    assert [gv.age(ms) for ms in (12 * 60_000, 26 * HOUR, 71 * HOUR, 72 * HOUR)] == ["12 min", "26 h", "71 h", "3 d"]
    assert gv.copper2(208.8875) == "208.89c" and gv.copper2(0.75) == "0.75c" and gv.copper2(11) == "11.00c"
    assert gv.clock(gw.NOW, 2 * HOUR) == "Mon 12:38" and gv.name_of("Amaya-Grommash") == "Amaya"


def test_a_known_chance_that_is_not_the_cheapest_default_and_an_observed_rate_of_one_session():
    item = CatalogItem(1, "Odd Herb", ("Herbalism",), (Source("node", "Herbalism", 1),))
    facts = {1: PriceFacts(300, gw.NOW, "ahf", 5, 10, cur_others=200)}
    w = gv.GatheringWorld([item], facts, {"A-R": {"Herbalism": 5}}, ["A-R"], gw.NOW,
                          observed={("A-R", 1): Observed(6, 3, 30, 1)})
    row = gw.view_rows(gathering_view(w), "")[0]
    assert row["net_title"].endswith("· chance is the default: no sales data yet (25%)")
    assert row["rate_title"].startswith("3 units ÷ 30 min over 1 session = 6.0/hr")
    zero = replace(w, observed={("A-R", 1): Observed(0, 0, 0, 1)})
    assert gw.view_rows(gathering_view(zero), "")[0]["rate_title"].startswith("0 units ÷ 0 min over 1 session = 0.0")
