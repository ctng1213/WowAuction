"""Tests for ahsync/engine/craft_plan.py: craft to sell later (SPEC §3.4C.3), invariant PL1."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine import craft, craft_plan
from ahsync.engine.craft_plan import CraftWorld
from ahsync.engine.plan import PlanSettings, PlanWindow, SellFacts
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
    base = SellFacts(key, level=level, trend7=0.0, post_price=level, sold_per_day=60.0, listed_per_day=20.0,
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


def test_C7_an_ah_sourced_craft_buys_its_mats_first_then_crafts_then_posts():
    actions, _ = craft_plan.craft_plan(world(), {"Brugan-G": RICH}, RICH, NOW, S, 25, {})
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
    early, _ = craft_plan.craft_plan(world(products={ROD: rod(1)}), {"Brugan-G": RICH}, RICH, NOW, S, 25, {})
    assert {a.window_start_ms for a in early if a.kind == "post"} == {None}             # +10% < +12.5%: post now
    trusted, _ = craft_plan.craft_plan(world(products={ROD: rod(6)}), {"Brugan-G": RICH}, RICH, NOW, S, 25, {})
    assert {a.peak_ms for a in trusted if a.kind == "post"} == {MONDAY + SAT * DAY + 18 * HOUR}


def test_C20_a_chain_posts_the_units_it_crafted_not_the_units_of_its_last_mat_buy():
    ore = 2770
    smelt = craft.Recipe(2657, BAR, 1, 1, ((ore, 1),), ("Brugan-G",))           # 1 Copper Ore -> 1 Copper Bar
    w = world(recipes=(ROD_RECIPE,), books={BAR: [], ore: [(100, 6.0)]},     # no bars listed: 6 ore at 1c
              products={ROD: product(ROD, 4000, supply_now=6)})                 # others list 6 rods: cap 3
    w = replace(w, by_product={**w.by_product, BAR: [smelt]})
    ledger: dict = {}
    actions, _ = craft_plan.craft_plan(w, {"Brugan-G": RICH}, RICH, NOW, S, 25, ledger)
    buy = next(a for a in actions if a.kind == "buy")
    post = next(a for a in actions if a.kind == "post")
    assert (buy.key, buy.qty) == (ore, 6)                                       # 3 rods × 2 bars, smelted from ore
    assert post.qty == 3 and isinstance(post.qty, int)                          # the 3 rods crafted, not 6.0
    assert ledger == {(ROD, post.window_start_ms): 3.0}


def test_D10_a_craft_planned_now_has_a_linked_post_within_7_days():
    actions, _ = craft_plan.craft_plan(world(), {"Brugan-G": RICH}, RICH, NOW, S, 25, {})
    made = next(a for a in actions if a.kind == "craft")
    [post] = [a for a in actions if a.kind == "post"]
    assert made.at_ms == NOW + 60_000 and post.chain == made.chain and NOW <= post.at_ms < NOW + 7 * DAY


def test_D9_a_product_whose_only_window_is_past_the_plan_week_is_not_crafted_for_it():
    # The plan runs Fri 14:00 to next Fri 14:00. A Friday 15:00-18:00 window occurs today (inside) and next
    # Friday at 15:00 (after the plan ends): the post goes to today's, never to next Friday's.
    late = {ROD: product(ROD, 4000, 4, 16, post_price=None)}               # Fri 15-18 peak 16:00, nothing now
    actions, _ = craft_plan.craft_plan(world(products=late), {"Brugan-G": RICH}, RICH, NOW, S, 25, {})
    assert [a.window_start_ms for a in actions if a.kind == "post"] == [MONDAY + 4 * DAY + 15 * HOUR]  # today's
    actions, _ = craft_plan.craft_plan(world(products={ROD: product(ROD, 4000, 4, 13, post_price=None)}),
                                       {"Brugan-G": RICH}, RICH, NOW, S, 25, {})
    assert all(a.window_start_ms < NOW + 7 * DAY for a in actions if a.kind == "post")


def test_D14_a_craft_for_a_thursday_peak_is_made_before_its_thursday_11_00_post():
    products = {ROD: product(ROD, 4000, THU, 19)}
    actions, _ = craft_plan.craft_plan(world(products=products), {"Brugan-G": RICH}, RICH, NOW, S, 25, {})
    made = next(a for a in actions if a.kind == "craft")
    post = next(a for a in actions if a.kind == "post")
    assert post.at_ms == MONDAY + 7 * DAY + THU * DAY + 11 * HOUR and made.at_ms < post.at_ms   # next Thu 11:00


def test_D22_mat_buys_and_mail_carry_no_net_the_craft_carries_the_chains_profit():
    recipes = (ROD_RECIPE, RUNED_RECIPE)
    books = {BAR: [(1000, 40.0)], ROD: [], DUST: [(300, 10.0)]}
    products = {ROD: product(ROD, 4000), RUNED: product(RUNED, 9000)}
    actions, _ = craft_plan.craft_plan(world(recipes, books, products), {"Brugan-G": RICH, "Amaya-G": RICH}, RICH,
                                       NOW, S, 25, {})
    runed = [a for a in actions if a.chain == "craft:7421"]
    assert [a.kind for a in runed] == ["buy", "buy", "craft", "mail", "craft", "post"]
    assert all(a.net == 0 for a in runed if a.kind in ("buy", "mail") or (a.kind == "craft" and a.key == ROD))
    final = next(a for a in runed if a.kind == "craft" and a.key == RUNED)
    assert final.char_id == "Amaya-G" and final.net > 0
    mail = next(a for a in runed if a.kind == "mail")
    assert (mail.char_id, mail.key) == ("Brugan-G", ROD)                     # Brugan makes the rod, mails it to Amaya


def test_chains_spend_from_their_crafters_budget_and_stop_at_the_risk_limit():
    budgets = {"Brugan-G": 4000}                                             # two crafts' bars (2 × 2 × 10s)
    actions, market = craft_plan.craft_plan(world(), budgets, RICH, NOW, S, 25, {})
    crafts = [a for a in actions if a.kind == "craft"]
    assert sum(a.qty for a in crafts) == 2 and budgets["Brugan-G"] == 0
    assert market.books[BAR] == [(1000, 36.0)]                               # the bought bars are gone for flips
    poor, _ = craft_plan.craft_plan(world(), {"Brugan-G": RICH}, 8_000, NOW, S, 25, {})   # 25% of 80s = 20s
    assert sum(a.qty for a in poor if a.kind == "craft") == 1


def test_nothing_is_planned_without_a_paying_product_or_a_known_crafter():
    cheap = {ROD: product(ROD, 1500)}                                        # worth less than its 20s of bars
    assert craft_plan.craft_plan(world(products=cheap), {"Brugan-G": RICH}, RICH, NOW, S, 25, {})[0] == []
    nobody = craft.Recipe(2663, ROD, 1, 1, ((BAR, 2),), ())
    assert craft_plan.craft_plan(world((nobody,)), {"Brugan-G": RICH}, RICH, NOW, S, 25, {})[0] == []
    assert craft_plan.craft_plan(world(), {"Brugan-G": RICH}, RICH, NOW, S, 25, {}, exclude={ROD})[0] == []
    full = {(ROD, w): 999.0 for w in [None, MONDAY + SAT * DAY + 17 * HOUR]}
    assert craft_plan.craft_plan(world(), {"Brugan-G": RICH}, RICH, NOW, S, 25, full)[0] == []    # no room left
    assert craft_plan.units_made(0, ROD_RECIPE) == 0 and craft_plan.units_made(3, ROD_RECIPE) == 3


def test_D41_a_chain_says_where_it_buys_each_mat():
    flux = 2880
    recipe = craft.Recipe(2663, ROD, 1, 1, ((BAR, 2), (flux, 1)), ("Brugan-G",))
    w = world(recipes=(recipe,))
    w = replace(w, market=craft.Market(w.market.books, {flux: 100}, {}, {}))
    actions, _ = craft_plan.craft_plan(w, {"Brugan-G": RICH}, RICH, NOW, S, 25, {})
    buys = {a.key: a.source for a in actions if a.kind == "buy"}
    assert buys == {BAR: "ah", flux: "vendor"}
    assert craft_plan.buy_source(w.market, flux) == "vendor" and craft_plan.buy_source(w.market, BAR) == "ah"


def test_vendor_mats_are_bought_at_the_vendor_price():
    market = craft.Market({}, {BAR: 900}, {}, {})
    assert craft_plan.buy_terms(market, BAR, 2) == (900, 1800)
    assert craft_plan.buy_terms(craft.Market({BAR: [(1000, 1.0), (1100, 5.0)]}, {}, {}, {}), BAR, 2) == (1100, 2100)


def random_world(rng):
    """A random Copper Rod market, budgets and gold."""
    books = {BAR: [(rng.randint(500, 2500), float(rng.randint(0, 20))) for _ in range(rng.randint(0, 3))]}
    products = {ROD: product(ROD, rng.randint(1000, 8000), rng.randint(0, 6), rng.randint(1, 22),
                             supply_now=rng.choice([None, 0, 20, 200]))}
    budgets = {"Brugan-G": rng.choice([0, 3000, RICH])}
    return world(books=books, products=products), budgets, rng.choice([10_000, RICH])


def test_PL1_crafts_respect_budgets_never_start_before_now_and_always_have_a_post_within_7_days():
    for i, (w, budgets, gold) in enumerate(cases(SEED_PL1, random_world, 300)):
        start = dict(budgets)
        actions, _ = craft_plan.craft_plan(w, budgets, gold, NOW, S, 25, {})
        assert budgets["Brugan-G"] >= 0 or start["Brugan-G"] == 0, (SEED_PL1, i)
        assert all(a.at_ms >= NOW for a in actions), (SEED_PL1, i)
        for made in [a for a in actions if a.kind == "craft" and a.key == ROD]:
            posts = [a for a in actions if a.kind == "post" and a.chain == made.chain]
            assert len(posts) == 1 and posts[0].at_ms < NOW + 7 * DAY, (SEED_PL1, i)
            peak, post = posts[0].peak_ms, posts[0].at_ms
            assert peak is None or post == max(peak - S.post_lead_hours * HOUR, post) or post >= NOW, (SEED_PL1, i)


def test_an_intermediate_from_your_own_stock_buys_nothing_and_a_spent_budget_ends_later_chains():
    recipes = (ROD_RECIPE, RUNED_RECIPE)
    owned_bars = craft.Market({BAR: [], ROD: [], DUST: [(300, 10.0)]}, {}, {BAR: 2.0}, {BAR: 100.0})
    w = CraftWorld(recipes, {ROD: [ROD_RECIPE], RUNED: [RUNED_RECIPE]}, owned_bars,
                   {ROD: product(ROD, 4000), RUNED: product(RUNED, 9000)})
    actions, _ = craft_plan.craft_plan(w, {"Brugan-G": RICH, "Amaya-G": RICH}, RICH, NOW, S, 25, {})
    runed = [a for a in actions if a.chain == "craft:7421"]
    assert [(a.kind, a.key) for a in runed if a.kind == "buy"] == [("buy", DUST)]   # the rod's bars were yours
    two = (ROD_RECIPE, craft.Recipe(2664, RUNED, 1, 1, ((BAR, 2),), ("Brugan-G",)))
    budgets = {"Brugan-G": 2000}                                                    # one craft's bars
    actions, _ = craft_plan.craft_plan(world(two, products={ROD: product(ROD, 4000), RUNED: product(RUNED, 9000)}),
                                       budgets, RICH, NOW, S, 25, {})
    assert [a.key for a in actions if a.kind == "craft"] == [RUNED] and budgets == {"Brugan-G": 0}


def test_C8_a_mat_buy_that_got_half_its_units_re_plans_the_craft_for_what_you_have():
    first, _ = craft_plan.craft_plan(world(books={BAR: [(1000, 4.0)]}), {"Brugan-G": RICH}, RICH, NOW, S, 25, {})
    assert [(a.kind, a.qty) for a in first if a.kind in ("buy", "craft")] == [("buy", 4), ("craft", 2)]
    # Only 2 of the 4 bars could be bought: you own 2 and the AH has none left.
    half = replace(world(books={BAR: []}), market=craft.Market({BAR: []}, {}, {BAR: 2.0}, {BAR: 0.0}))
    again, _ = craft_plan.craft_plan(half, {"Brugan-G": RICH}, RICH, NOW + HOUR, S, 25, {})
    assert [(a.kind, a.qty) for a in again if a.kind in ("buy", "craft")] == [("craft", 1)]


def test_C14_a_rod_crafted_but_not_mailed_is_owned_stock_and_only_the_final_craft_is_re_planned():
    # Brugan crafted the rod; the mail to Amaya never happened. The rod is owned stock (account-wide).
    market = craft.Market({BAR: [(1000, 40.0)], DUST: [(700, 1.0)]}, {}, {ROD: 1.0}, {ROD: 0.0})   # 1 dust: 1 craft
    products = {RUNED: product(RUNED, 9000), ROD: product(ROD, 4000)}
    w = replace(world(recipes=(ROD_RECIPE, RUNED_RECIPE), products=products), market=market)
    actions, _ = craft_plan.craft_plan(w, {"Brugan-G": RICH, "Amaya-G": RICH}, RICH, NOW, S, 25, {})
    runed = [a for a in actions if a.chain == "craft:7421"]
    assert [(a.kind, a.key, a.char_id) for a in runed if a.kind != "post"][:2] == [("buy", DUST, "Amaya-G"),
                                                                                   ("craft", RUNED, "Amaya-G")]
    assert not [a for a in runed if a.kind == "craft" and a.key == ROD]           # the rod is not crafted again
