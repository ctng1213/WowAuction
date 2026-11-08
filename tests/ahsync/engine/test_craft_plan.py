"""Tests for ahsync/engine/craft_plan.py: craft to sell later (SPEC §3.4C.3), invariant PL1 (through the plan)."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine import craft, craft_plan, plan
from ahsync.engine.craft_plan import CraftWorld
from ahsync.engine.portfolio import PortfolioSettings
from ahsync.engine.week import WeekInputs
from tests.support import plans
from ahsync.engine.plan import Lot, PlanSettings, PlanWindow, SellFacts
from tests.support.gen import cases

HOUR, DAY = 3_600_000, 86_400_000
MONDAY = 345_600_000 + 3000 * 7 * DAY
SAT, THU = 5, 3
NOW = MONDAY + 4 * DAY + 14 * HOUR            # Fri 14:00
BAR, ROD, RUNED, DUST = 2840, 6217, 6218, 10940
S = PlanSettings()
RICH = 100_000_000
SEED_PL1 = 4011


def product(key: int, level: int, peak_day: int = SAT, peak_hour: int = 18, **changes) -> SellFacts:
    """A product that peaks once a week (1.2×), sells 60/day, and others list 20."""
    how = [1.0] * 168
    how[peak_day * 24 + peak_hour] = 1.2
    base = SellFacts(key, level=level, trend7=0.0, closes_30d=720, post_price=level, sold_per_day=60.0, listed_per_day=20.0,
                     ref_stack=1, how=tuple(how), sales_how=tuple([1.0] * 168),
                     windows=(PlanWindow(peak_day * 24 + peak_hour - 1, 3, peak_day * 24 + peak_hour, 6),), book=(),
                     cur=level, cur_qty=2, vendor_sell=50, supply_now=20, conf=70)
    return replace(base, **changes)


ROD_RECIPE = craft.Recipe(2663, ROD, 1, 1, ((BAR, 2),), ("Brugan-G",))
RUNED_RECIPE = craft.Recipe(7421, RUNED, 1, 1, ((ROD, 1), (DUST, 1)), ("Amaya-G",))


def world(recipes=(ROD_RECIPE,), books=None, products=None) -> CraftWorld:
    """Copper Bars at 10s on the AH (40 of them), nothing owned."""
    by_product: dict = {}
    for r in recipes:
        by_product.setdefault(r.product, []).append(r)
    market = craft.Market(books if books is not None else {BAR: [(1000, 40.0)]}, {}, {}, {})
    return CraftWorld(tuple(recipes), by_product, market, products or {ROD: product(ROD, 4000)})



def chains(w: CraftWorld, now: int = NOW) -> list:
    """Every known recipe's chain with all the crafts that pay, as the plan realizes a fully funded craft candidate.

    Args:
        w: The craft world (its hold values and holders as given).
        now: Now.

    Returns:
        craft_plan.chain_actions of craft_option_for, per recipe by spell.
    """
    out = []
    for recipe in sorted(w.recipes, key=lambda r: r.spell):
        if recipe.product in w.products and recipe.crafters:
            option = craft_plan.craft_option_for(recipe, w, now, S)
            if option.crafts:
                out += craft_plan.chain_actions(recipe, option, w, {}, now, S)
    return out


def plan_world(w: CraftWorld, gold: dict, ps: PortfolioSettings | None = None, no_buy=frozenset()) -> list:
    """The portfolio plan of a craft world with no stock: what gets funded.

    Args:
        w: The craft world.
        gold: Character -> gold.
        ps: Portfolio settings; 100% risk tolerance by default.
        no_buy: Items never bought or crafted.

    Returns:
        The plan's actions.
    """
    return plans.actions(WeekInputs((), dict(w.products), w, no_buy), NOW, gold=gold, ps=ps)

def test_C7_an_ah_sourced_craft_buys_its_mats_first_then_crafts_then_posts():
    actions = chains(world())
    kinds = [(a.kind, a.key) for a in actions]
    assert kinds[:3] == [("buy", BAR), ("craft", ROD), ("post", ROD)]
    buy, made, post = actions[:3]
    assert buy.at_ms <= made.at_ms < post.at_ms and buy.chain == made.chain == post.chain == "craft:2663"
    assert buy.qty == 2 * made.qty and buy.price == 1000


def test_R26_a_crafted_rod_posts_now_while_its_window_is_early_and_only_slightly_better():
    def rod(weeks: int) -> SellFacts:
        """A rod whose Saturday peak is only 1.1× and backed by `weeks` weeks of data."""
        how = [1.0] * 168
        how[SAT * 24 + 18] = 1.1
        return product(ROD, 4000, how=tuple(how), windows=(PlanWindow(SAT * 24 + 17, 3, SAT * 24 + 18, weeks),))
    early = chains(world(products={ROD: rod(1)}))
    assert {a.window_start_ms for a in early if a.kind == "post"} == {None}             # +10% < +12.5%: post now
    trusted = chains(world(products={ROD: rod(6)}))
    assert {a.peak_ms for a in trusted if a.kind == "post"} == {MONDAY + SAT * DAY + 18 * HOUR}


def test_C20_a_chain_posts_the_units_it_crafted_not_the_units_of_its_last_mat_buy():
    ore = 2770
    smelt = craft.Recipe(2657, BAR, 1, 1, ((ore, 1),), ("Brugan-G",))           # 1 Copper Ore -> 1 Copper Bar
    w = world(recipes=(ROD_RECIPE,), books={BAR: [], ore: [(100, 6.0)]},     # no bars listed: 6 ore at 1c
              products={ROD: product(ROD, 4000, supply_now=6)})                 # others list 6 rods: cap 3
    w = replace(w, by_product={**w.by_product, BAR: [smelt]})
    actions = chains(w)
    buy = next(a for a in actions if a.kind == "buy")
    post = next(a for a in actions if a.kind == "post")
    assert (buy.key, buy.qty) == (ore, 6)                                       # 3 rods × 2 bars, smelted from ore
    assert post.qty == 3 and isinstance(post.qty, int)                          # the 3 rods crafted, not 6.0


def test_D10_a_craft_planned_now_has_a_linked_post_within_7_days():
    actions = chains(world())
    made = next(a for a in actions if a.kind == "craft")
    [post] = [a for a in actions if a.kind == "post"]
    assert made.at_ms == NOW + 60_000 and post.chain == made.chain and NOW <= post.at_ms < NOW + 7 * DAY


def test_D9_a_product_whose_only_window_is_past_the_plan_week_is_not_crafted_for_it():
    # The plan runs Fri 14:00 to next Fri 14:00. A Friday 15:00-18:00 window occurs today (inside) and next
    # Friday at 15:00 (after the plan ends): the post goes to today's, never to next Friday's.
    late = {ROD: product(ROD, 4000, 4, 16, post_price=None)}               # Fri 15-18 peak 16:00, nothing now
    actions = chains(world(products=late))
    assert [a.window_start_ms for a in actions if a.kind == "post"] == [MONDAY + 4 * DAY + 15 * HOUR]  # today's
    actions = chains(world(products={ROD: product(ROD, 4000, 4, 13, post_price=None)}))
    assert all(a.window_start_ms < NOW + 7 * DAY for a in actions if a.kind == "post")


def test_D14_a_craft_for_a_thursday_peak_is_made_before_its_thursday_11_00_post():
    products = {ROD: product(ROD, 4000, THU, 19)}
    actions = chains(world(products=products))
    made = next(a for a in actions if a.kind == "craft")
    post = next(a for a in actions if a.kind == "post")
    assert post.at_ms == MONDAY + 7 * DAY + THU * DAY + 11 * HOUR and made.at_ms < post.at_ms   # next Thu 11:00


def test_D22_mat_buys_and_mail_carry_no_net_the_craft_carries_the_chains_profit():
    recipes = (ROD_RECIPE, RUNED_RECIPE)
    books = {BAR: [(1000, 40.0)], ROD: [], DUST: [(300, 10.0)]}
    products = {ROD: product(ROD, 4000), RUNED: product(RUNED, 9000)}
    actions = chains(world(recipes, books, products))
    runed = [a for a in actions if a.chain == "craft:7421"]
    assert [a.kind for a in runed] == ["buy", "buy", "craft", "mail", "craft", "post"]
    assert all(a.net == 0 for a in runed if a.kind in ("buy", "mail") or (a.kind == "craft" and a.key == ROD))
    final = next(a for a in runed if a.kind == "craft" and a.key == RUNED)
    assert final.char_id == "Amaya-G" and final.net > 0
    mail = next(a for a in runed if a.kind == "mail")
    assert (mail.char_id, mail.key) == ("Brugan-G", ROD)                     # Brugan makes the rod, mails it to Amaya


def test_chains_spend_from_their_crafters_gold_and_stop_at_the_exposure_budget():
    crafts = plan_world(world(products={ROD: product(ROD, 4000, vendor_sell=0)}), {"Brugan-G": 4200})
    assert sum(a.qty for a in crafts if a.kind == "craft") == 2                # 2 × (2 × 10s of bars + the 1s deposit)
    poor = plan_world(world(products={ROD: product(ROD, 4000, vendor_sell=0)}), {"Brugan-G": 8000},
                      PortfolioSettings())                                    # 30% of 80s = 24s: one craft's 20s
    assert sum(a.qty for a in poor if a.kind == "craft") == 1
    market = craft_plan.consume(world().market, craft_plan.craft_option_for(ROD_RECIPE, world(), NOW, S, 3, None, 2))
    assert market.books[BAR] == [(1000, 36.0)]                               # the bought bars are gone for flips


def test_nothing_is_planned_without_a_paying_product_or_a_known_crafter():
    cheap = {ROD: product(ROD, 1500)}                                        # worth less than its 20s of bars
    assert chains(world(products=cheap)) == []
    nobody = craft.Recipe(2663, ROD, 1, 1, ((BAR, 2),), ())
    assert chains(world((nobody,))) == [] and plan_world(world((nobody,)), {"Brugan-G": RICH}) == []
    assert plan_world(world(), {"Brugan-G": RICH}, no_buy=frozenset({ROD})) == []
    full = {(ROD, w): 999.0 for w in [None, MONDAY + SAT * DAY + 17 * HOUR]}
    f = product(ROD, 4000)
    worth = craft_plan.product_value(f, ROD_RECIPE, plan.post_options(f, NOW, S), full, S)
    assert worth(1) == float("-inf")                                          # no room left: output worth nothing
    assert craft_plan.units_made(0, ROD_RECIPE) == 0 and craft_plan.units_made(3, ROD_RECIPE) == 3


def test_D41_a_chain_says_where_it_buys_each_mat():
    flux = 2880
    recipe = craft.Recipe(2663, ROD, 1, 1, ((BAR, 2), (flux, 1)), ("Brugan-G",))
    w = world(recipes=(recipe,))
    w = replace(w, market=craft.Market(w.market.books, {flux: 100}, {}, {}))
    actions = chains(w)
    buys = {a.key: a.source for a in actions if a.kind == "buy"}
    assert buys == {BAR: "ah", flux: "vendor"}
    assert craft_plan.buy_source(w.market, flux) == "vendor" and craft_plan.buy_source(w.market, BAR) == "ah"


def test_vendor_mats_are_bought_at_the_vendor_price():
    market = craft.Market({}, {BAR: 900}, {}, {})
    assert craft_plan.buy_terms(market, BAR, 2) == (900, 1800)
    assert craft_plan.buy_terms(craft.Market({BAR: [(1000, 1.0), (1100, 5.0)]}, {}, {}, {}), BAR, 2) == (1100, 2100)


def random_world(rng):
    """A random Copper Rod market and Brugan's gold.

    Args:
        rng: The seeded Random.

    Returns:
        (world, gold by character).
    """
    books = {BAR: [(rng.randint(500, 2500), float(rng.randint(0, 20))) for _ in range(rng.randint(0, 3))]}
    products = {ROD: product(ROD, rng.randint(1000, 8000), rng.randint(0, 6), rng.randint(1, 22),
                             supply_now=rng.choice([None, 0, 20, 200]))}
    return world(books=books, products=products), {"Brugan-G": rng.choice([0, 3000, RICH])}


def test_PL1_crafts_respect_budgets_never_start_before_now_and_always_have_a_post_within_7_days():
    for i, (w, gold) in enumerate(cases(SEED_PL1, random_world, 300)):
        actions = plan_world(w, gold, PortfolioSettings())
        assert sum(a.qty * a.price for a in actions if a.kind == "buy") <= gold["Brugan-G"], (SEED_PL1, i)
        assert all(a.at_ms >= NOW for a in actions), (SEED_PL1, i)
        for made in [a for a in actions if a.kind == "craft" and a.key == ROD]:
            posts = [a for a in actions if a.kind == "post" and a.chain == made.chain]
            assert len(posts) == 1 and posts[0].at_ms < NOW + 7 * DAY, (SEED_PL1, i)
            peak, post = posts[0].peak_ms, posts[0].at_ms
            assert peak is None or post == max(peak - S.post_lead_hours * HOUR, post) or post >= NOW, (SEED_PL1, i)


def test_an_intermediate_from_your_own_stock_buys_nothing_and_spent_gold_ends_later_chains():
    recipes = (ROD_RECIPE, RUNED_RECIPE)
    owned_bars = craft.Market({BAR: [], ROD: [], DUST: [(300, 10.0)]}, {}, {BAR: 2.0}, {BAR: 100.0})
    w = CraftWorld(recipes, {ROD: [ROD_RECIPE], RUNED: [RUNED_RECIPE]}, owned_bars,
                   {ROD: product(ROD, 4000), RUNED: product(RUNED, 9000)})
    runed = [a for a in chains(w) if a.chain == "craft:7421"]
    assert [(a.kind, a.key) for a in runed if a.kind == "buy"] == [("buy", DUST)]   # the rod's bars were yours
    two = (ROD_RECIPE, craft.Recipe(2664, RUNED, 1, 1, ((BAR, 2),), ("Brugan-G",)))
    products = {ROD: product(ROD, 4000, vendor_sell=0), RUNED: product(RUNED, 9000, vendor_sell=0)}
    actions = plan_world(world(two, products=products), {"Brugan-G": 2100})          # one craft's bars and 1s deposit
    assert [a.key for a in actions if a.kind == "craft"] == [RUNED]                  # the better one per copper


def test_C8_a_mat_buy_that_got_half_its_units_re_plans_the_craft_for_what_you_have():
    first = chains(world(books={BAR: [(1000, 4.0)]}))
    assert [(a.kind, a.qty) for a in first if a.kind in ("buy", "craft")] == [("buy", 4), ("craft", 2)]
    # Only 2 of the 4 bars could be bought: you own 2 and the AH has none left.
    half = replace(world(books={BAR: []}), market=craft.Market({BAR: []}, {}, {BAR: 2.0}, {BAR: 0.0}))
    again = chains(half, now=NOW + HOUR)
    assert [(a.kind, a.qty) for a in again if a.kind in ("buy", "craft")] == [("craft", 1)]


def test_C14_a_rod_crafted_but_not_mailed_is_owned_stock_and_only_the_final_craft_is_re_planned():
    # Brugan crafted the rod; the mail to Amaya never happened. The rod is owned stock (account-wide).
    market = craft.Market({BAR: [(1000, 40.0)], DUST: [(700, 1.0)]}, {}, {ROD: 1.0}, {ROD: 0.0})   # 1 dust: 1 craft
    products = {RUNED: product(RUNED, 9000), ROD: product(ROD, 4000)}
    w = replace(world(recipes=(ROD_RECIPE, RUNED_RECIPE), products=products), market=market)
    actions = chains(w)
    runed = [a for a in actions if a.chain == "craft:7421"]
    assert [(a.kind, a.key, a.char_id) for a in runed if a.kind != "post"][:2] == [("buy", DUST, "Amaya-G"),
                                                                                   ("craft", RUNED, "Amaya-G")]
    assert not [a for a in runed if a.kind == "craft" and a.key == ROD]           # the rod is not crafted again


def test_C22_owned_mats_another_character_holds_are_mailed_to_the_crafter_gather_picks():
    from ahsync.engine import plan_views
    from ahsync.engine.plan_views import ItemLook
    from ahsync.recommend.plan import plan_rows
    from ahsync.recommend.plan_pages import view_actions
    both = craft.Recipe(2663, ROD, 1, 1, ((BAR, 2),), ("Amaya-G", "Brugan-G"))

    def plan(amaya: int, brugan: int) -> list:
        """The rod chain when Amaya and Brugan hold these Copper Bars and the AH lists none."""
        market = craft.Market({BAR: []}, {}, {BAR: float(amaya + brugan)}, {BAR: 100.0})
        w = replace(world((both,)), market=market, held={("Amaya-G", BAR): float(amaya), ("Brugan-G", BAR): float(brugan)})
        return chains(w)
    actions = plan(8, 6)
    [mail] = [a for a in actions if a.kind == "mail"]
    made = next(a for a in actions if a.kind == "craft")
    assert (made.char_id, made.qty) == ("Amaya-G", 7)                          # she holds the most of the 14 bars
    assert (mail.char_id, mail.to_char, mail.key, mail.qty) == ("Brugan-G", "Amaya-G", BAR, 6)
    assert mail.at_ms == made.at_ms - 60_000 and mail.chain == made.chain and mail.net == 0
    looks = {BAR: ItemLook("Copper Bar", 1), ROD: ItemLook("Copper Rod", 1, 70, 60.0, "ahf", 20)}
    shown = view_actions(actions, plan_rows(actions, "m", NOW), [])
    rows = {r["action"]: r for r in plan_views.today_view(shown, NOW, 0, 0.0, looks, RICH)["rows"]}
    assert rows["craft"]["lines"][0] == "6 Copper Bar from Brugan · not mailed yet"
    assert (rows["mail"]["character"], rows["mail"]["note"]) == ("Brugan", "to Amaya · for crafting Copper Rod")
    flipped = plan(4, 10)                                                       # now Brugan holds the most
    [mail] = [a for a in flipped if a.kind == "mail"]
    assert (mail.char_id, mail.to_char, mail.qty) == ("Amaya-G", "Brugan-G", 4)
    assert {a.char_id for a in flipped if a.kind in ("craft", "post")} == {"Brugan-G"}
    assert not [a for a in plan(14, 0) if a.kind == "mail"]                     # she holds them all: no mail


def test_use_held_takes_the_crafters_own_units_first_then_the_mailed_ones():
    held = {("Amaya-G", BAR): 8.0, ("Brugan-G", BAR): 6.0, ("Cara-G", BAR): 1.0}
    left = craft_plan.use_held(held, "Amaya-G", {BAR: 12.0}, {BAR: (("Brugan-G", 4),)})
    assert left == {("Amaya-G", BAR): 0.0, ("Brugan-G", BAR): 2.0, ("Cara-G", BAR): 1.0}


def test_C24_the_crafts_are_funded_from_the_gold_of_the_crafter_gather_picks():
    both = craft.Recipe(2663, ROD, 1, 1, ((BAR, 2),), ("Amaya-G", "Brugan-G"))
    market = craft.Market({BAR: [(1000, 40.0)]}, {}, {}, {})                         # bars at 10s on the AH
    w = world((both,), products={ROD: product(ROD, 9000, supply_now=200, vendor_sell=0)})
    lots = (Lot("Brugan-G", BAR, 2),)                                                # Brugan holds the 2 owned bars
    result = plans.plan(WeekInputs(lots, {}, replace(w, market=replace(market, owned={BAR: 2.0}))), NOW,
                        gold={"Amaya-G": 100_000, "Brugan-G": 10_100})                # 10g and 1g 1s
    made = next(a for a in result.actions if a.kind == "craft")
    # Brugan's 1g 1s buys 10 bars and the rods' one post's 1s minimum deposit: with his 2 owned bars that is 6 crafts,
    # though Amaya's 10g could have bought more.
    assert (made.char_id, made.qty) == ("Brugan-G", 6)
    assert dict(result.selection.reserved) == {"Brugan-G": 10_100} and not [a for a in result.actions if a.kind == "mail"]
    assert {a.reason for a in result.alternatives} == {"Insufficient gold on Brugan (needs 20s, has 0c)"}


def test_C23_a_re_plan_while_the_bars_are_on_their_way_mails_and_buys_nothing_and_says_on_its_way():
    from ahsync.engine import plan_views
    from ahsync.engine.plan_chains import Arriving
    from ahsync.engine.plan_views import ItemLook
    from ahsync.engine.transit import Transit
    from ahsync.recommend.plan import plan_rows
    from ahsync.recommend.plan_pages import view_actions
    rod = craft.Recipe(2663, ROD, 1, 1, ((BAR, 2),), ("Amaya-G", "Brugan-G"))
    # Brugan mailed his 6 bars at 10:40; his bags no longer have them and Amaya's mailbox wasn't read since:
    # the 6 count as hers (state.owned_lots with transit, T22), next to the 8 in her bags. The AH lists bars too.
    on_way = Transit("Amaya-G", "Brugan-G", BAR, 6, NOW - 20 * 60_000)
    market = craft.Market({BAR: [(1000, 40.0)]}, {}, {BAR: 14.0}, {BAR: 100.0})
    w = replace(world((rod,), products={ROD: product(ROD, 4000, supply_now=14)}), market=market,   # others list 14: cap 7
                held={("Amaya-G", BAR): 14.0})
    actions = chains(w)
    assert [(a.kind, a.char_id, a.qty) for a in actions if a.kind != "post"] == [("craft", "Amaya-G", 7)]
    lost = replace(w, market=replace(market, owned={BAR: 8.0}), held={("Amaya-G", BAR): 8.0})   # without transit
    again = chains(lost)
    assert [(a.kind, a.qty) for a in again if a.kind == "buy"] == [("buy", 6)]               # it would buy them again
    looks = {BAR: ItemLook("Copper Bar", 1), ROD: ItemLook("Copper Rod", 1, 70, 60.0, "ahf", 20)}
    shown = view_actions(actions, plan_rows(actions, "m", NOW), [])
    arriving = Arriving((on_way,), {ROD: frozenset({BAR})})
    [row] = plan_views.today_view(shown, NOW, 0, 0.0, looks, RICH, arriving)["rows"]
    assert row["lines"][0] == "6 Copper Bar from Brugan · mailed Fri 13:40 · on its way"
    other = Arriving((replace(on_way, recipient="Cara-G"),), {ROD: frozenset({BAR})})     # not hers: no line
    [row] = plan_views.today_view(shown, NOW, 0, 0.0, looks, RICH, other)["rows"]
    assert not [line for line in row["lines"] if "on its way" in line]
