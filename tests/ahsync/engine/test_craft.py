"""Tests for ahsync/engine/craft.py, including invariants CI1-CI3."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import pytest

from ahsync.engine.craft import Market, Pool, Recipe, craft_option, fill, gather, steps, vendor_first, walk_book
from tests.support.gen import cases

BAR, ROD, RUNED, DUST, ESSENCE = 2840, 6217, 6218, 10940, 10938
BRUGAN, AMAYA = "Brugan-Grommash", "Amaya-Grommash"
ROD_RECIPE = Recipe(2663, ROD, 1, 1, ((BAR, 2),), (BRUGAN,))
RUNED_RECIPE = Recipe(7421, RUNED, 1, 1, ((ROD, 1), (DUST, 1), (ESSENCE, 1)), (AMAYA,))
BY_PRODUCT = {ROD: [ROD_RECIPE], RUNED: [RUNED_RECIPE]}


def rod_net(n: int) -> float:
    """Copper Rod posts at 40 s with P = 1: 38 s per craft after the cut."""
    return n * 3800.0


def runed_net(n: int) -> float:
    """Runed Copper Rod sells for 59 s with P = 1: 56 s 5 c per craft."""
    return n * 5605.0


def market(books=None, vendor=None, owned=None, hold=None) -> Market:
    """A Market with empty defaults."""
    return Market(books or {}, vendor or {}, owned or {}, hold or {})


def rod(m: Market, **kw):
    """The Copper Rod craft option in a market."""
    return craft_option(ROD_RECIPE, m, BY_PRODUCT, rod_net, **kw)


CHAIN_MARKET = {BAR: [(1000, 10)], DUST: [(700, 5)], ESSENCE: [(1000, 5)]}


def test_R3_profit_is_the_product_after_the_cut_minus_the_mats():
    option = rod(market(books={BAR: [(1000, 2)]}))
    assert (option.crafts, option.profits) == (1, [1800.0])        # 40 s x 0.95 - 20 s = 18 s


def test_R4_owned_mats_cost_their_hold_value_not_nothing():
    option = rod(market(owned={BAR: 2}, hold={BAR: 950}))
    assert option.profits == [3800.0 - 1900.0]


def test_R5_a_cheaper_vendor_beats_the_ah():
    option = rod(market(books={BAR: [(1000, 10)]}, vendor={BAR: 800}), max_crafts=1)
    assert option.mat_gold == 1600.0


def test_R11_only_a_character_who_knows_the_recipe_is_suggested():
    alt_only = Recipe(2663, ROD, 1, 1, ((BAR, 2),), ("Alt-Grommash",))
    assert craft_option(alt_only, market(books={BAR: [(1000, 2)]}), {}, rod_net).crafter == "Alt-Grommash"


def test_C1_owned_bars_cover_three_crafts_with_no_gold():
    option = rod(market(owned={BAR: 6}, hold={BAR: 950}))
    assert (option.source, option.crafts, option.mat_gold) == ("Owned", 3, 0.0)
    assert option.profits == [1900.0] * 3


def test_C2_all_bars_bought_on_the_ah():
    option = rod(market(books={BAR: [(1000, 6)]}))
    assert (option.source, option.crafts, option.mat_gold) == ("AH", 3, 6000.0)
    assert option.profits == [1800.0] * 3


def test_C3_owned_bars_topped_up_from_the_ah():
    option = rod(market(books={BAR: [(1000, 2)]}, owned={BAR: 4}, hold={BAR: 950}))
    assert (option.source, option.crafts, option.mat_gold) == ("Mixed", 3, 2000.0)
    assert (option.owned_used, option.bought) == ({BAR: 4}, {BAR: 2})


def test_C4_the_ah_beats_stock_worth_more_at_its_peak():
    option = rod(market(books={BAR: [(1000, 6)]}, owned={BAR: 4}, hold={BAR: 1200}))
    assert option.profits[:3] == [1800.0] * 3                   # AH bars first: 38 s - 20 s
    assert option.profits[3:] == [3800.0 - 2400.0] * 2          # then stock, still paying 14 s
    assert (option.crafts, option.source, option.bought, option.owned_used) == (5, "Mixed", {BAR: 6}, {BAR: 4})
    dear = rod(market(books={BAR: [(1000, 6)]}, owned={BAR: 4}, hold={BAR: 2000}))
    assert (dear.crafts, dear.source, dear.owned_used) == (3, "AH", {})


def test_C5_crafts_stop_when_the_book_gets_too_dear():
    option = rod(market(books={BAR: [(1000, 2), (1500, 2), (2500, 2)]}))
    assert (option.crafts, option.profits) == (2, [1800.0, 800.0])     # the 3rd would lose 12 s


def test_C6_a_mat_with_no_source_makes_it_not_craftable():
    option = rod(market())
    assert (option.crafts, option.missing) == (0, ((BAR, 2),))


def test_C9_a_cheaper_crafted_intermediate_forms_a_chain_with_mail():
    m = market(books={ROD: [(4100, 5)], **CHAIN_MARKET})
    option = craft_option(RUNED_RECIPE, m, BY_PRODUCT, runed_net, max_crafts=1)
    assert option.source == "AH + crafted"
    assert option.crafted[ROD].cost == 2000.0 and option.crafted[ROD].crafter == BRUGAN
    assert steps(option, RUNED_RECIPE) == [("buy", ESSENCE, 1.0), ("buy", DUST, 1.0),
                                           ("craft", 2663, BRUGAN, ROD), ("mail", ROD, BRUGAN, AMAYA),
                                           ("craft", 7421, AMAYA, RUNED)]


def test_C10_buying_the_intermediate_when_it_is_cheaper():
    m = market(books={ROD: [(1500, 5)], **CHAIN_MARKET})
    option = craft_option(RUNED_RECIPE, m, BY_PRODUCT, runed_net, max_crafts=1)
    assert option.crafted == {}
    assert ("mail", ROD, BRUGAN, AMAYA) not in steps(option, RUNED_RECIPE)


def test_C11_no_mail_when_one_character_does_both_crafts():
    both = {ROD: [Recipe(2663, ROD, 1, 1, ((BAR, 2),), (AMAYA,))], RUNED: [RUNED_RECIPE]}
    option = craft_option(RUNED_RECIPE, market(books=CHAIN_MARKET), both, runed_net, max_crafts=1)
    assert [s[0] for s in steps(option, RUNED_RECIPE)] == ["buy", "buy", "craft", "craft"]


def chain(depth: int) -> dict[int, list[Recipe]]:
    """Items 1 <- 2 <- ... <- depth+1: item k is crafted from one item k+1."""
    return {k: [Recipe(100 + k, k, 1, 1, ((k + 1, 1),), (AMAYA,))] for k in range(1, depth + 1)}


def test_C12_chains_stop_at_the_maximum_depth():
    recipes = chain(4)                          # 1 <- 2 <- 3 <- 4 <- 5 (raw)
    deep = market(books={5: [(100, 10)]})       # item 4 would have to be a 4th crafted level
    assert craft_option(recipes[1][0], deep, recipes, lambda n: n * 1000.0, max_crafts=1).crafts == 0
    reachable = market(books={4: [(100, 10)]})  # item 4 bought: 1, 2 and 3 make three levels
    assert craft_option(recipes[1][0], reachable, recipes, lambda n: n * 1000.0, max_crafts=1).crafts == 1


def test_C13_a_recipe_loop_is_not_followed():
    a = Recipe(1, 1, 1, 1, ((2, 1),), (AMAYA,))
    b = Recipe(2, 2, 1, 1, ((1, 1),), (AMAYA,))
    option = craft_option(a, market(books={2: [(100, 5)]}), {1: [a], 2: [b]}, lambda n: n * 1000.0, max_crafts=1)
    assert (option.crafts, option.crafted) == (1, {})


def test_C15_a_chain_counts_its_profit_once_on_the_final_product():
    option = craft_option(RUNED_RECIPE, market(books=CHAIN_MARKET), BY_PRODUCT, runed_net, max_crafts=1)
    assert option.profits == [pytest.approx(5605.0 - (2000 + 700 + 1000))]     # 19 s 5 c


def test_C16_owned_mats_at_their_peak_hold_value():
    option = rod(market(owned={BAR: 6}, hold={BAR: 1100}))
    assert (option.source, option.profits[0]) == ("Owned", 3800.0 - 2200.0)


def test_budget_cooldown_and_cap_limit_crafts():
    m = market(books={BAR: [(1000, 100)]})
    assert rod(m, budget=4500).crafts == 2
    cooled = Recipe(2663, ROD, 1, 1, ((BAR, 2),), (BRUGAN, AMAYA), cooldown_s=86400)
    assert craft_option(cooled, m, {}, rod_net).crafts == 2                  # one per crafter per cooldown
    assert rod(market(vendor={BAR: 10}), max_crafts=7).crafts == 7


def test_walk_book_and_fill_edge_cases():
    assert walk_book([(15, 2), (10, 2)], 3) == 35
    assert walk_book([(10, 2)], 3) is None
    assert walk_book([], 0) == 0
    f = fill(3, owned=0, hold_value=0, vendor_price=None, book=[(10, 1)])
    assert (f.short, f.used["ah"], f.book_taken) == (2, 1, (1,))
    made = fill(2, 0, 0, None, [], crafted_cost=500.0, crafted_gold=400.0)
    assert (made.cost, made.gold, made.used["crafted"]) == (1000.0, 800.0, 2)


def test_enchants_make_no_item_and_yield_averages():
    enchant = Recipe(7418, None, 1, 1, ((DUST, 1),), (AMAYA,))
    option = craft_option(enchant, market(books={DUST: [(700, 1)]}), {}, lambda n: n * 1000.0)
    assert option.crafts == 1
    assert Recipe(1, 1, 1, 3, (), ()).avg_yield == 2


def test_a_recipe_nobody_knows_cannot_feed_a_chain():
    nobody = {ROD: [Recipe(2663, ROD, 1, 1, ((BAR, 2),), ())], RUNED: [RUNED_RECIPE]}
    assert craft_option(RUNED_RECIPE, market(books=CHAIN_MARKET), nobody, runed_net).missing == ((ROD, 1),)


def test_the_cheapest_of_two_recipes_for_an_intermediate_is_used():
    cheap = Recipe(2664, ROD, 1, 1, ((DUST, 1),), (AMAYA,))
    dear = Recipe(2665, ROD, 1, 1, ((ESSENCE, 1),), (AMAYA,))      # 10 s: dearer than 7 s, ignored
    two = {ROD: [ROD_RECIPE, cheap, dear], RUNED: [RUNED_RECIPE]}
    option = craft_option(RUNED_RECIPE, market(books=CHAIN_MARKET), two, runed_net, max_crafts=1)
    assert option.crafted[ROD].spell == 2664 and option.crafted[ROD].cost == 700.0


# ---------------------------------------------------------------- invariants

SEED = 5_502_026


def random_offer(rng) -> tuple:
    """need, owned, hold, vendor, book and crafted cost for one reagent."""
    book = [(rng.randint(1, 50), float(rng.randint(0, 5))) for _ in range(rng.randint(0, 4))]
    return (float(rng.randint(1, 12)), float(rng.randint(0, 6)), float(rng.randint(0, 50)),
            rng.choice([None, rng.randint(1, 50)]), book, rng.choice([None, float(rng.randint(1, 60))]))


def test_CI1_the_chosen_source_never_costs_more_than_one_left_unused():
    for i, (need, owned, hold, vendor, book, crafted) in enumerate(cases(SEED, random_offer)):
        f = fill(need, owned, hold, vendor, book, crafted, 0.0)
        used = ([hold] if f.used["owned"] else []) + ([vendor] if f.used["vendor"] else []) \
            + [price for (price, _), t in zip(book, f.book_taken) if t > 0] \
            + ([crafted] if f.used["crafted"] else [])
        unused = ([hold] if owned - f.used["owned"] > 0 else []) + ([vendor] if vendor is not None else []) \
            + [price for (price, units), t in zip(book, f.book_taken) if units - t > 0] \
            + ([crafted] if crafted is not None else [])
        if used and unused:
            assert max(used) <= min(unused) or max(used) in unused, f"seed {SEED} case {i}"
        total = f.used["owned"] + f.used["vendor"] + f.used["ah"] + f.used["crafted"] + f.short
        assert total == pytest.approx(need), f"seed {SEED} case {i}"


def random_craft(rng):
    """A random recipe of 1-2 reagents, a market, and a product value that falls per extra craft."""
    keys = rng.sample([1, 2, 3], rng.randint(1, 2))
    recipe = Recipe(9, 99, 1, 1, tuple((k, rng.randint(1, 3)) for k in keys), ("A-R",))
    m = Market({k: [(rng.randint(1, 40), float(rng.randint(0, 8))) for _ in range(rng.randint(0, 3))] for k in keys},
               {k: rng.randint(10, 60) for k in keys if rng.random() < 0.3},
               {k: float(rng.randint(0, 6)) for k in keys}, {k: float(rng.randint(0, 40)) for k in keys})
    base = rng.randint(20, 200)
    return recipe, m, (lambda n, b=base: sum(b * 0.9 ** j for j in range(n)))


def test_CI2_every_craft_pays_and_the_next_one_would_not():
    for i, (recipe, m, net) in enumerate(cases(SEED + 1, random_craft)):
        option = craft_option(recipe, m, {}, net, max_crafts=30)
        assert all(p > 0 for p in option.profits), f"seed {SEED + 1} case {i}"
        if option.crafts < 30:
            pool = Pool(m, {}, 1, frozenset(), 3)
            for _ in range(option.crafts):
                for key, count in recipe.reagents:
                    pool.take(key, count)
            fills = [pool.take(key, count) for key, count in recipe.reagents]
            following = net(option.crafts + 1) - net(option.crafts) - sum(f.cost for f in fills)
            assert any(f.short > 0 for f in fills) or following <= 0, f"seed {SEED + 1} case {i}"


def test_CI3_an_intermediate_is_valued_at_its_mats_so_profit_is_counted_once():
    for bar_price in range(500, 3000, 25):
        m = market(books={BAR: [(bar_price, 10)], DUST: [(700, 5)], ESSENCE: [(1000, 5)], ROD: [(99999, 1)]})
        option = craft_option(RUNED_RECIPE, m, BY_PRODUCT, runed_net, max_crafts=1)
        if option.crafts:
            assert option.crafted[ROD].cost == 2 * bar_price
            assert option.profit == pytest.approx(5605.0 - 2 * bar_price - 1700)


def test_LP1_committed_property_cases_match_python():
    import json
    from pathlib import Path

    import tools.gen_properties as gen
    path = Path(__file__).resolve().parents[3] / "tests" / "properties" / "craft.json"
    seed, fresh = gen.build()["craft"]
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(json.dumps({"seed": seed, "cases": fresh})),         "tests/properties/craft.json is stale: run python tools/gen_properties.py"


SHARED = json.loads((Path(__file__).resolve().parents[3] / "tests" / "scenarios" / "craft_options.json")
                    .read_text(encoding="utf-8"))


@pytest.mark.parametrize("s", SHARED, ids=[s["name"] for s in SHARED])
def test_shared_craft_options_with_the_addon(s):
    a = s["args"]
    recipe = Recipe(2663, ROD, 1, 1, tuple(tuple(r) for r in a["reagents"]), (BRUGAN,))
    m = market(books={int(k): [tuple(level) for level in v] for k, v in a["books"].items()},
               vendor={int(k): v for k, v in a.get("vendor", {}).items()},
               owned={int(k): float(v) for k, v in a["owned"].items()}, hold={int(k): float(v) for k, v in a["hold"].items()})
    option = craft_option(recipe, m, {}, lambda n: n * float(a["value_each"]))
    assert {"crafts": option.crafts, "mat_gold": option.mat_gold, "profit": option.profit, "source": option.source} == \
        s["expect"], s["why"]


def test_R22_vendor_first_drops_the_ah_for_vendor_sold_mats_and_caps_their_hold_value():
    m = vendor_first(market(books={BAR: [(500, 20.0)], ROD: [(4000, 1.0)]}, vendor={BAR: 900},
                                  owned={BAR: 6.0}, hold={BAR: 1500.0, ROD: 3000.0}))
    assert (dict(m.books), dict(m.hold_value)) == ({ROD: [(4000, 1.0)]}, {BAR: 900, ROD: 3000.0})
    option = rod(m, max_crafts=4)
    assert (option.owned_used, option.from_vendor, option.source) == ({BAR: 6.0}, {BAR: 2.0}, "Mixed")


def test_C19_the_crafter_is_who_holds_the_most_mats_and_mail_brings_the_rest():
    linen = 2589
    held = {("Amaya-G", linen): 4.0, ("Brugan-G", linen): 3.0, ("Cara-G", linen): 6.0}
    assert gather({linen: 10.0}, held, ["Amaya-G", "Brugan-G"]) == ("Amaya-G", {linen: (("Cara-G", 6),)})
    assert gather({linen: 13.0}, held, ["Amaya-G", "Brugan-G"]) == ("Amaya-G", {linen: (("Cara-G", 6), ("Brugan-G", 3))})
    assert gather({linen: 2.0}, held, ["Amaya-G", "Brugan-G"]) == ("Amaya-G", {})       # ties go to the first crafter
    assert gather({linen: 1.0}, {}, ["Brugan-G"]) == ("Brugan-G", {}) and gather({}, held, []) == ("", {})
