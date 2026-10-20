"""Tests for ahsync/engine/week.py: the plan's world and its rules, through the portfolio plan (SPEC §3.4C, §3.4D)."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine import craft, week
from ahsync.engine.craft_plan import CraftWorld
from ahsync.engine.flips import FlipSettings
from ahsync.engine.plan import Lot, PlanSettings, PlanWindow, SellFacts
from ahsync.engine.week import WeekInputs
from tests.support import plans

HOUR, DAY = 3_600_000, 86_400_000
MONDAY = 345_600_000 + 3000 * 7 * DAY
SAT = 5
NOW = MONDAY + 4 * DAY + 14 * HOUR
BAR, ROD = 2840, 6217
S, FS = PlanSettings(), FlipSettings()
RICH = plans.RICH
ROD_RECIPE = craft.Recipe(2663, ROD, 1, 1, ((BAR, 2),), ("Brugan-G",))


def facts(key: int, level: int, **changes) -> SellFacts:
    """An item with a Saturday 18:00 peak (1.2×), 60 sold a day, 20 listed by others."""
    how = [1.0] * 168
    how[SAT * 24 + 18] = 1.2
    base = SellFacts(key, level=level, trend7=0.0, closes_30d=720, post_price=level, sold_per_day=60.0, listed_per_day=20.0,
                     ref_stack=1, how=tuple(how), sales_how=tuple([1.0] * 168),
                     windows=(PlanWindow(SAT * 24 + 17, 3, SAT * 24 + 18, 6),), book=(), cur=level, cur_qty=2,
                     vendor_sell=50, supply_now=20, conf=70)
    return replace(base, **changes)


def inputs(**changes) -> WeekInputs:
    """Brugan holds 6 Copper Bars (worth 10s raw) and knows Copper Rod (2 bars → a 40s rod)."""
    market = craft.Market({BAR: [], ROD: []}, {}, {BAR: 6.0}, {})
    world = CraftWorld((ROD_RECIPE,), {ROD: [ROD_RECIPE]}, market, {ROD: facts(ROD, 4000)})
    base = WeekInputs((Lot("Brugan-G", BAR, 6),), {BAR: facts(BAR, 1000), ROD: facts(ROD, 4000)}, world)
    return replace(base, **changes)


def test_owned_mats_go_to_the_craft_once_and_the_rest_is_sold():
    actions = plans.actions(inputs(), NOW)
    crafts = [a for a in actions if a.kind == "craft"]
    bars_sold = sum(a.qty for a in actions if a.key == BAR and a.kind in ("post", "hold", "vendor"))
    assert sum(a.qty for a in crafts) * 2 + bars_sold == 6          # each owned bar planned exactly once
    assert crafts and not [a for a in actions if a.kind == "buy"]   # owned bars: nothing to buy


def test_O8_no_buy_stops_buys_crafts_and_flips_but_owned_stock_is_still_sold():
    actions = plans.actions(inputs(no_buy=frozenset({ROD})), NOW)
    assert not [a for a in actions if a.kind in ("craft", "buy")]
    assert sum(a.qty for a in actions if a.key == BAR) == 6          # the bars still get a sell plan


def test_O9_ignore_leaves_an_item_out_of_every_recommendation():
    actions = plans.actions(inputs(ignore=frozenset({BAR})), NOW)
    assert not [a for a in actions if a.key == BAR]                  # no sale of the bars, and no craft uses them
    assert not [a for a in actions if a.kind == "craft"]


def test_hold_values_are_what_a_unit_fetches_under_its_own_sell_plan():
    values = week.hold_values([Lot("A", BAR, 500)], {BAR: facts(BAR, 1000)}, NOW, S)
    assert 0 < values[BAR] <= 1200 * 0.95                            # at most the Saturday peak (1.2×) after the cut


def test_crafts_buying_auctions_leave_the_rest_for_flips():
    f = facts(BAR, 1000, book=((900, 5, 4, 2), (950, 5, 4, 1)))
    assert week.without_bought(f, 7).book == ((950, 5, 4, 1),)      # 10 cheapest units bought (two auctions of 5)
    assert week.without_bought(f, 0) is f and week.without_bought(replace(f, book=None), 5).book is None
    before = craft.Market({BAR: [(900, 10.0)]}, {}, {}, {})
    assert week.bought_from(before, craft.Market({BAR: [(900, 3.0)]}, {}, {}, {})) == {BAR: 7.0}


def test_R22_a_vendor_sold_mat_is_never_bought_on_the_ah_nor_flipped():
    # Bars: a vendor sells them at 900; the AH lists 20 at 500 and would flip well (cur 500, peak 1200).
    market = craft.Market({BAR: [(500, 20.0)], ROD: []}, {BAR: 900}, {BAR: 6.0}, {})
    w = inputs(world=CraftWorld((ROD_RECIPE,), {ROD: [ROD_RECIPE]}, market, {ROD: facts(ROD, 4000)}),
               facts={BAR: facts(BAR, 1000, cur=500, cur_qty=20, book=((500, 20, 4, 1),)), ROD: facts(ROD, 4000)})
    actions = plans.actions(w, NOW)
    buys = [a for a in actions if a.kind == "buy" and a.key == BAR]
    assert buys and all(a.price == 900 and a.source == "vendor" for a in buys)   # the 500 listings are no source
    assert not [a for a in actions if a.chain and a.chain.startswith("flip:")]


def test_R23_a_vendor_sold_item_is_never_posted_held_or_crafted():
    wood, thread = 4470, 2320
    lots = (Lot("Brugan-G", wood, 20), Lot("Brugan-G", thread, 5))
    market = craft.Market({BAR: [], ROD: []}, {wood: 38, thread: 10, ROD: 5000}, {}, {})
    world = CraftWorld((ROD_RECIPE,), {ROD: [ROD_RECIPE]}, market, {ROD: facts(ROD, 9000)})
    w = inputs(lots=lots, world=world, facts={wood: facts(wood, 60, vendor_sell=9), thread: facts(thread, 40, vendor_sell=0),
                                              ROD: facts(ROD, 9000)})
    actions = plans.actions(w, NOW)
    assert [(a.kind, a.key, a.qty, a.price) for a in actions] == [("vendor", wood, 20, 9)]   # no post, hold or craft


def test_O5_an_item_flagged_not_selling_is_not_crafted_or_flipped_but_your_stock_still_sells():
    from ahsync.engine.sell_record import Record
    stuck = Record(0, 3, 3, 3, False, True)
    w = inputs(facts={BAR: facts(BAR, 1000), ROD: facts(ROD, 4000, record=stuck)})
    w = replace(w, world=replace(w.world, products={ROD: facts(ROD, 4000, record=stuck)}))
    actions = plans.actions(w, NOW)
    assert not [a for a in actions if a.kind == "craft"]                       # no new rods
    assert sum(a.qty for a in actions if a.key == BAR) == 6                    # the bars are still planned


def test_FM5_an_item_with_a_suspect_price_is_not_flipped():
    # Bars would flip well (cur 500, peak 1200), but the 500 is suspect.
    market = craft.Market({BAR: [(500, 20.0)]}, {}, {}, {})
    flippy = {BAR: facts(BAR, 1000, cur=500, cur_qty=20, book=((500, 1, 4, 20),))}
    w = inputs(world=CraftWorld((), {}, market, {}), facts=flippy)
    gold = {"Amaya-G": RICH}
    assert [a for a in plans.actions(w, NOW, gold=gold) if a.chain and a.chain.startswith("flip:")]   # flips normally
    assert not [a for a in plans.actions(replace(w, suspect=frozenset({BAR})), NOW, gold=gold)
                if a.chain and a.chain.startswith("flip:")]
