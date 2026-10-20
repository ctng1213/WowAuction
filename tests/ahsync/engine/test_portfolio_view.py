"""Tests for ahsync/engine/portfolio_view.py: the Portfolio page's payload (SPEC §3.5, §3.4D; D72, D73, D77-D79).

The mockup world (tests/support/mockup_export.py) is the dashboard mockup's: Monday 10:38, Amaya 21 g and Brugan
10 g 40 s in hand, 4 g 18 s committed, risk tolerance 30%, its 16 candidates (12 funded, 4 excluded) and the seven
positions of section A. Every expected text is the mockup's.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from ahsync.engine import craft
from ahsync.engine.allocation import PositionRow
from ahsync.engine.craft_plan import CraftWorld
from ahsync.engine.plan import Lot, PlanWindow, SellFacts
from ahsync.engine.plan_views import ItemLook
from ahsync.engine.portfolio import Candidate, CharacterGold, PortfolioSettings, accounting
from ahsync.engine.portfolio_view import (OppRow, PortfolioInputs, _Ctx, each_copper, materials_text,
                                          next_action_text, portfolio_view, premium_text, reason_detail,
                                          uncertain_detail, wait_text, week_row, when_text)
from ahsync.engine.selection import Eligibility
from ahsync.engine.views import COLUMN_HELP
from ahsync.engine.week import WeekInputs
from tests.support import mockup_export as mx
from tests.support import plans

HOUR, DAY = mx.HOUR, mx.DAY
NOW = mx.NOW


def joined(foot: list) -> str:
    """A footer's segments as the page reads them."""
    return " · ".join(text for text, _ in foot)


@pytest.fixture(scope="module")
def page() -> dict:
    """The Portfolio page of the mockup world."""
    return portfolio_view(mx.page_inputs())


def test_D72_the_tiles_and_the_snapshot_line_show_the_mockups_capital(page):
    tiles = [(t["label"], t["value"], t["of"], t["detail"], t["risk"]) for t in page["tiles"]]
    assert tiles == [
        ("Gold in hand", "31g 40s", "", "Amaya 21g · Brugan 10g 40s", False),
        ("Committed capital", "4g 18s", "", "12% of capital base", False),
        ("Capital base", "35g 58s", "", "gold in hand + committed", False),
        ("Risk tolerance", "30%", "", "exposure limit 10g 67s 40c · changing it re-runs the allocation", True),
        ("Remaining capacity", "6g 49s 40c", "", "10g 67s 40c limit − 4g 18s committed", False),
        ("Allocated to new opportunities", "6g 25s", "of 6g 49s 40c",
         "Today 51s 60c · next 7 days 5g 73s 40c · 24s 40c left", False)]
    assert page["fresh"] == ("portfolio snapshot Mon 10:38 · tiles show the values at 30% · gold as each character "
                             "last logged in · positions from the journal")
    assert page["risk"] == {"options": [10, 20, 30, 40, 50], "selected": 30, "values": [0.1, 0.2, 0.3, 0.4, 0.5]}
    assert list(page) == ["tiles", "fresh", "risk", "positions", "today", "week", "pool", "empty", "help"]
    assert page["empty"] is None and page["help"] is COLUMN_HELP


def test_D73_today_lists_the_funded_rows_by_net_then_the_red_line_then_the_alternatives(page):
    b = page["today"]["by_min"]["0"]
    assert [(r["item"], r["net"], r["status"]) for r in b["funded"]] == [
        ("Copper Rod", "+63s 6c", "funded"), ("Silver Rod", "+40s", "funded"),
        ("Scouting Gloves of the Monkey", "+6s 25c", "funded"), ("Embossed Leather Gloves", "+3s 80c", "funded"),
        ("Linen Cloth", "+2s 3c", "funded")]
    rod = b["funded"][0]
    assert (rod["strategy"], rod["action"], rod["qty"], rod["each"], rod["total"], rod["id"], rod["character"]) == \
        ("Sell stock", "post", 5, "41s", "2g 5s", "act:rod:1", "Brugan")
    assert b["cut"] == "capacity reached · 24s 40c left"
    assert [(r["item"], r["status"], r["detail"], r["status_class"], r["excluded"]) for r in b["excluded"]] == [
        ("Lesser Mana Potion", "Exceeds risk tolerance (needs 45s, 24s 40c left)",
         "its turn came after every funded row, so 24s 40c is also what stays unallocated", "amber", True),
        ("Grunt's Bracers of Defense", "Required profession unavailable: Enchanting 150", "Amaya has Enchanting 142",
         "neg", True),
        ("Elixir of Fortitude", "Insufficient liquidity: 3/day, 40 planned",
         "the market sells about 3 a day; 40 would take about 13 days", "amber", True)]
    assert joined(b["foot"]) == ("5 of 20 funded · expected +1g 15s 14c · gold needed 51s 60c · profit 113% on cash · "
                                 "3 alternatives excluded (in no total)")
    assert page["today"]["note"].endswith("Profit on cash = 1g 15s 14c ÷ 1g 1s 60c.")


def test_D73_the_week_lists_the_funded_rows_by_adjusted_profit_with_their_wait_and_premium(page):
    c = page["week"]
    assert [(r["item"], r["adjusted"]) for r in c["funded"]] == [
        ("Mithril Spurs", "+51s 87c"), ("Thick Leather", "+41s 75c"), ("Transmute: Iron to Gold", "+14s 8c"),
        ("Runed Copper Rod", "+11s 12c"), ("Silk Cloth", "+5s 15c"), ("Bolt of Silk Cloth", "+1s 32c"),
        ("Heavy Grinding Stone", "+95c")]
    spurs = c["funded"][0]
    assert (spurs["wait_note"], spurs["sell_when"], spurs["sale_note"], spurs["net"], spurs["gold"]) == \
        ("Wait 5.31 d · premium 41.55%", "Sat 18:00–21:00", "expected sale 18:00", "+73s 42c", "3g")
    assert c["funded"][1]["wait_note"] == "Wait 3.35 d · premium 31.75%"                  # PF18's Thick Leather
    assert [(r["item"], r["status"], r["detail"]) for r in c["excluded"]] == [
        ("Greater Magic Essence", "Forecast too uncertain (1 week of data)",
         "its Sunday peak has been seen once; a forecast needs 3 weeks")]
    assert c["cut"] == "capacity reached · 24s 40c left"
    assert joined(c["foot"]) == ("7 of 20 funded · expected +1g 73s 85c · adjusted +1g 26s 24c · gold needed 5g 73s "
                                 "40c · profit 30% on cash · 1 alternative excluded (in no total)")
    assert "Thu 19:00 3.35 d → 31.75%" in c["note"] and "Sat 18:00 5.31 d → 41.55%" in c["note"]


def test_D73_positions_count_their_cost_and_their_profit_on_committed_capital(page):
    a = page["positions"]
    assert len(a["rows"]) == 7 and a["summary"] == "gold already committed · 7 positions · they take no recommendation slots"
    assert joined(a["foot"]) == ("Capital committed 4g 18s · 12% of capital base (4g 18s ÷ 35g 58s) · estimated profit "
                                 "on positions +96s 79c · +23% on committed capital")
    assert a["foot"][-1][1] == "pos"
    coif, bar, elixir, wool, mageweave, ore, leather = a["rows"]
    assert (coif["state"], coif["state_class"], coif["paid"], coif["cost_left"], coif["net"]) == \
        ("Listed", "listed", "3g", "3g", "+45s")
    assert (bar["window"], bar["next"]) == ("Sat 19:00", "post for the Sat 19:00 peak")
    assert (wool["paid"], wool["paid_note"], wool["note"], wool["next"]) == ("0c", "found", "found · uses no capital", "hold")
    assert mageweave["qty"] == "10 of 25" and mageweave["state_class"] == "partial"
    assert (ore["state_class"], elixir["state_class"], elixir["next"]) == ("proc", "ready", "post now")
    assert (leather["net"], leather["net_class"], leather["window"], leather["next"], leather["sort"]["net"]) == \
        ("—", "", "—", "—", -1)


def test_D73_no_excluded_row_counts_in_any_total(page):
    funded_gold = 51_60 + 5_73_40
    assert page["tiles"][5]["value"] == "6g 25s" and funded_gold == 62_500
    assert page["pool"]["total"]["gold"] == "6g 25s"                    # the 4 alternatives' 1g 81s 50c is in none


def test_D78_the_funding_card_lists_every_funded_row_by_adjusted_profit_with_the_capacity_left(page):
    pool = page["pool"]
    rows = pool["rows"]
    assert len(rows) == 12
    first, second, last = rows[0], rows[1], rows[-1]
    assert (first["n"], first["item"], first["sells"], first["adjusted"], first["gold"], first["cash"], first["pct"],
            first["left"]) == (1, "Copper Rod", "today 18:00", "63s 6c", "1s", "51s", "124%", "6g 48s 40c")
    assert (second["item"], second["sells"], second["adjusted"], second["net"], second["gold"], second["cash"],
            second["pct"], second["left"]) == ("Mithril Spurs", "Sat 18:00", "51s 87c", "+73s 42c", "3g", "3g", "24%",
                                               "3g 48s 40c")
    assert (last["n"], last["item"], last["adjusted"], last["gold"], last["pct"], last["pct_note"], last["left"]) == \
        (12, "Heavy Grinding Stone", "95c", "0c", "—", "found stock", "24s 40c")
    assert pool["total"] == {"label": "Total funded", "note": "% leaves out Heavy Grinding Stone (found stock only)",
                             "rows": "12 rows", "adjusted": "2g 41s 38c", "net": "+2g 88s 99c", "gold": "6g 25s",
                             "cash": "6g 75s", "pct": "43%", "left": "24s 40c"}
    assert pool["cut"] == "capacity reached · 24s 40c left"
    x = pool["first_excluded"]
    assert (x["item"], x["sells"], x["gold"], x["left"]) == ("Lesser Mana Potion", "today 18:00", "45s",
                                                             "needs 45s: not funded")


def test_D77_with_the_risk_budget_exhausted_one_block_replaces_both_lists_and_the_tiles_stay():
    gold = [CharacterGold(mx.AMAYA, 143_000), CharacterGold(mx.BRUGAN, 104_000)]   # Amaya spent 6g 70s
    plan = mx.page_plan(gold, committed=108_800, paying_only=True)
    view = portfolio_view(mx.page_inputs(plan, gold))
    assert view["empty"] == {"title": "No new opportunities: risk budget exhausted (10g 67s 40c limit, 10g 88s committed)",
                             "text": "New suggestions come back as positions sell and free capital."}
    assert view["tiles"][4]["value"] == "0c" and len(view["positions"]["rows"]) == 7          # A and the tiles stay
    nobody = portfolio_view(replace(mx.page_inputs(), plan=None, chars=(), positions=()))
    assert nobody["empty"] == {"title": "No characters yet",
                               "text": "Log in once with the addon so AHSync knows your gold and professions."}
    assert nobody["tiles"][0]["detail"] == "no characters yet" and nobody["pool"]["rows"] == []
    assert nobody["positions"]["foot"] == [["Capital committed 0c", ""], ["estimated profit on positions 0c", ""]]


def test_D79_at_40_percent_the_lesser_mana_potion_is_funded_and_the_rest_keep_their_reasons():
    view = portfolio_view(mx.page_inputs(mx.page_plan(risk=0.40), risk=0.40))
    tiles = {t["label"]: t for t in view["tiles"]}
    assert tiles["Risk tolerance"]["detail"].startswith("exposure limit 14g 23s 20c")
    assert tiles["Remaining capacity"]["value"] == "10g 5s 20c"
    assert tiles["Allocated to new opportunities"]["detail"].endswith("3g 35s 20c left")
    b, c = view["today"]["by_min"]["0"], view["week"]
    assert "Lesser Mana Potion" in [r["item"] for r in b["funded"]]
    assert [(r["item"], r["status"]) for r in b["excluded"] + c["excluded"]] == [
        ("Grunt's Bracers of Defense", "Required profession unavailable: Enchanting 150"),
        ("Elixir of Fortitude", "Insufficient liquidity: 3/day, 40 planned"),
        ("Greater Magic Essence", "Forecast too uncertain (1 week of data)")]
    assert view["risk"]["selected"] == 40 and view["pool"]["first_excluded"] is None
    assert c["cut"] == "funded rows end here · 3g 35s 20c left"                     # no row is left out for capacity
    assert sorted(r["item"] for r in b["funded"] + b["excluded"] + c["funded"] + c["excluded"]) == \
        sorted(row[0] for row in mx._ROWS)                                                # PF1: the same candidates


def test_the_min_chance_steps_hide_rows_from_view_only_and_the_footer_still_counts_them():
    plan = mx.page_plan()
    low = mx.candidates()[1].cand_id                                                       # Silver Rod
    funded = tuple(replace(c, chance=0.4) if c.cand_id == low else c for c in plan.funded)
    view = portfolio_view(mx.page_inputs(replace(plan, funded=funded)))
    steps = view["today"]["by_min"]
    assert steps["0"]["hidden"] == 0 and steps["50"]["hidden"] == 1
    assert "Silver Rod" not in [r["item"] for r in steps["50"]["funded"]]
    assert steps["50"]["foot"] == steps["0"]["foot"] and steps["50"]["foot"][0][0].startswith("5 of 20 funded")


def test_a_real_plans_sell_stock_row_shows_its_post_price_and_skips_its_first_action():
    how = [1.0] * 168
    facts = SellFacts(2589, level=50, trend7=0.0, closes_30d=720, post_price=50, sold_per_day=60.0, listed_per_day=20.0,
                      ref_stack=20, how=tuple(how), sales_how=tuple(how), windows=(PlanWindow(6 * 24 + 17, 3, 6 * 24 + 18, 6),),
                      book=(), cur=50, cur_qty=2, vendor_sell=5, supply_now=200, conf=70)
    w = WeekInputs((Lot("Amaya-G", 2589, 20),), {2589: facts}, CraftWorld((), {}, craft.Market({}, {}, {}, {}), {}))
    monday = 345_600_000 + 3000 * 7 * DAY
    now = monday + 10 * HOUR
    plan = plans.plan(w, now)
    ids = tuple(f"a{i}" for i in range(len(plan.actions)))
    view = portfolio_view(PortfolioInputs(plan, (CharacterGold("Amaya-G", plans.RICH),), (), ids,
                                          {2589: ItemLook("Linen Cloth", 1)}, PortfolioSettings(), now))
    [row] = view["today"]["by_min"]["0"]["funded"]
    assert (row["item"], row["strategy"], row["each"], row["id"]) == ("Linen Cloth", "Sell stock", "50c", "a0")
    assert view["positions"]["rows"] == [] and view["empty"] is None and view["pool"]["total"]["rows"] == "1 row"


# ---------------------------------------------------------------- formatting and details

def test_waits_premiums_and_moments_read_as_the_page_says_them():
    assert (wait_text(335), wait_text(31), premium_text(317_500), premium_text(150_049)) == \
        ("3.35 d", "0.31 d", "31.75%", "15.00%")
    assert when_text(None, NOW, 0) == "—" and when_text(NOW, NOW, 0) == "now"
    assert when_text(mx.TONIGHT, NOW, 0) == "today 18:00" and when_text(mx.at(5, 18), NOW, 0) == "Sat 18:00"
    assert when_text(mx.TONIGHT, NOW, 7 * HOUR) == "Tue 01:00"                     # 18:00 UTC is tomorrow at +7 h


def ctx_of(capacity_left: int = 2440) -> _Ctx:
    """The mockup's accounting with a given capacity left."""
    return _Ctx(accounting(mx.chars(), [], 0.30), capacity_left, mx.page_inputs())


def excluded(reason: str, e: Eligibility | None = None, gold: int = 100, sale_ms: int | None = None) -> OppRow:
    """An excluded row with one batch."""
    c = Candidate("flip", 2589, mx.AMAYA, "-/now/now", 1, 1, gold_buys=gold, sale_ms=sale_ms)
    return OppRow("o", "Linen Cloth", "today", (c,), 1.0, reason, e)


@pytest.mark.parametrize("reason, e, detail", [
    ("Required profession unavailable: Enchanting 1", Eligibility("Enchanting", 1), "no character has Enchanting"),
    ("Required profession unavailable: Enchanting 1", None, ""),
    ("Forecast too stale (prices 30 h old)", None, "prices older than 24 h are not planned on; run AHSync to fetch newer ones"),
    ("Sales volume unknown", None, "neither the chance to sell nor the liquidity can be measured without a sales rate"),
    ("No listing left to price against", None, "nothing else is listed, and too few closes ground its price"),
    ("Insufficient liquidity: 3/day, 40 planned", None, "its windows' liquidity caps have no room left"),
    ("Missing materials: 2 Iron Bar", None, "no stock, no AH listing and no vendor sells it"),
    ("Resources already claimed: Linen Cloth (Copper Rod)", None, "a better funded row uses it first"),
    ("Insufficient gold (needs 1g, all characters hold 50s)", None, "all your characters together hold less"),
    ("Insufficient gold on Amaya (needs 1g, has 50s)", None, "your account could pay, but gold stays on its character"),
    ("Recommendation limit reached (20 of 20 today)", None, "the list is full of better rows"),
])
def test_every_reason_has_its_detail_line(reason, e, detail):
    assert reason_detail(excluded(reason, e), ctx_of()) == detail


def test_a_batch_bigger_than_the_whole_capacity_says_so():
    row = excluded("Exceeds risk tolerance (needs 10g, 24s 40c left)", gold=100_000)
    assert reason_detail(row, ctx_of()) == "it alone needs more than the whole remaining capacity (9g 42s)"


def test_forecast_details_name_closes_or_the_weeks_seen():
    assert uncertain_detail(None, None, 0) == ""
    assert uncertain_detail(Eligibility(window_closes=4), NOW, 0) == \
        "a forecast from 4 closes in 30 days is noise; a window sale needs 24"
    assert uncertain_detail(Eligibility(window_closes=1), NOW, 0).startswith("a forecast from 1 close in")
    assert uncertain_detail(Eligibility(untrusted_weeks=2), mx.at(2, 19), 0) == \
        "its Wednesday peak has been seen twice; a forecast needs 3 weeks"
    assert uncertain_detail(Eligibility(untrusted_weeks=0), None, 0) == "its peak has been seen 0 times; a forecast needs 3 weeks"
    row = excluded("Forecast too uncertain (2 weeks of data)", Eligibility(untrusted_weeks=2), sale_ms=mx.at(6, 19))
    assert reason_detail(row, ctx_of()) == "its Sunday peak has been seen twice; a forecast needs 3 weeks"


def test_each_is_the_buy_price_the_mat_gold_per_craft_or_a_known_post_price():
    flip = OppRow("o", "Linen Cloth", "today", (Candidate("flip", 1, "A-B", "-/now/now", 1, 3, gold_buys=100),), 1.0)
    craft_row = OppRow("o", "Rod", "today", (Candidate("craft", 1, "A-B", "-/-/now", 1, 2, gold_mats=5, gold_deposits=9),), 1.0)
    owned = OppRow("o", "Rod", "today", (Candidate("craft", 1, "A-B", "-/-/now", 1, 2),), 1.0)
    stock = OppRow("o", "Rod", "today", (Candidate("sell_stock", 1, "A-B", "-/-/now", 1, 2),), 1.0)
    assert (each_copper(flip), each_copper(craft_row), each_copper(owned), each_copper(stock)) == (33, 3, None, None)


def test_a_crafts_materials_name_each_source():
    c = Candidate("multistage", 6218, mx.AMAYA, "6217c+10938a/-/now", 1, 1, ((6217, 1), (10938, 1)))
    ctx = ctx_of()
    assert materials_text(c, ctx) == ["1 Copper Rod (craft)", "1 10938 (ah)"]
    assert materials_text(replace(c, strategy="flip"), ctx) == []


def test_a_positions_next_action_follows_the_plans_action_for_it():
    row = PositionRow("p", "A-B", 1, "Holding", 1, 1, 10, None, None, None)
    assert next_action_text(replace(row, next_action="de"), NOW, 0) == "disenchant"
    assert next_action_text(replace(row, next_action="vendor"), NOW, 0) == "sell to a vendor"
    assert next_action_text(replace(row, next_action="post", target_ms=NOW - HOUR), NOW, 0) == "post now"
    assert next_action_text(row, NOW, 0) == "—"


def test_a_losing_position_book_shows_a_negative_return_and_no_week_sale_has_no_premium_list():
    rows = (PositionRow("p", mx.BRUGAN, 7920, "Listed", 1, 1, 30_000, -1500.0, None, "post"),)
    inp = replace(mx.page_inputs(), positions=rows)
    view = portfolio_view(inp)
    assert view["positions"]["foot"][-1] == ["−4% on committed capital", "neg"]
    assert view["positions"]["rows"][0]["net_class"] == "neg" and "· 1 position ·" in view["positions"]["summary"]
    empty_week = portfolio_view(replace(inp, plan=mx.page_plan(gold=[CharacterGold(mx.AMAYA, 1)], risk=0.1,
                                                                    paying_only=True)))
    assert empty_week["week"]["note"].startswith("Premium = 15% + 5% × the wait in days from now to the expected sale "
                                                 "hour. Within")


def test_a_week_row_without_a_window_end_shows_its_sale_hour_and_one_selling_now_has_no_sale_note():
    later = Candidate("buy_later", 4306, mx.AMAYA, "-/now/h1", 1, 40, gold_buys=2800, buy_ms=NOW, sale_ms=mx.at(5, 19))
    row = week_row(OppRow("o", "Silk Cloth", "week", (later,), 515.0), ctx_of())
    assert (row["sell_when"], row["sale_note"], row["buy_when"], row["process"], row["materials"]) == \
        ("Sat 19:00", "expected sale 19:00", "now", "—", "—")
    now = week_row(OppRow("o", "Silk Cloth", "week", (replace(later, sale_ms=None, window_end_ms=NOW + HOUR),), 1.0),
                   ctx_of())
    assert (now["sell_when"], now["sale_note"], now["wait_note"]) == ("now", "", "Wait 0.00 d · premium 0.00%")
