"""Tests for ahsync/engine/graph.py: the dependency graph the addon reprices along (SPEC §3.5 `G`)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahsync.engine.graph import GraphRecipe, RouteCost, build, graph_recipe, input_delta, repriced, route_costs

ORE, BAR, ROD, RUNED, ESSENCE, DUST = 2770, 2840, 6217, 6218, 10938, 10940
SMELT, ROD_SPELL, RUNED_SPELL = 2657, 7818, 7421
COPPER_CHAIN = [graph_recipe(RUNED_SPELL, RUNED, 1, 1, [(ROD, 1), (DUST, 1), (ESSENCE, 1)]),
                graph_recipe(SMELT, BAR, 1, 1, [(ORE, 1)]),
                graph_recipe(ROD_SPELL, ROD, 1, 1, [(BAR, 1)])]


def test_EX18_the_copper_chain_graph_orders_ore_before_bar_before_rod_before_runed_rod():
    g = build(COPPER_CHAIN, [])
    assert [r.spell for r in g.recipes] == [SMELT, RUNED_SPELL, ROD_SPELL]         # by spell
    assert g.recipes[1] == GraphRecipe(RUNED_SPELL, RUNED, 1000, ((ROD, 1), (ESSENCE, 1), (DUST, 1)))
    assert dict(g.uses)[BAR] == (ROD_SPELL,)
    assert g.order == (ORE, BAR, ROD, ESSENCE, DUST, RUNED)
    assert g.cut == ()


def test_a_recipe_loop_is_cut_like_chains_cut_it_and_the_order_stays_acyclic():
    # C13: A (item 1) uses B (item 2), B uses A. The walk starts at item 1, follows B's recipe into 2, and does not
    # follow 2's reagent 1 back: that edge is cut, left out of the reverse index and the order's constraint.
    a, b = graph_recipe(11, 1, 1, 1, [(2, 1)]), graph_recipe(12, 2, 1, 1, [(1, 1)])
    g = build([a, b], [])
    assert g.cut == ((1, 12),)
    assert dict(g.uses) == {2: (11,)}
    assert g.order == (2, 1)
    looped = build([graph_recipe(13, 5, 1, 1, [(5, 1), (6, 2)])], [])          # a recipe using its own product
    assert (looped.cut, looped.order, dict(looped.uses)) == (((5, 13),), (6, 5), {6: (13,)})


def test_disenchantable_items_join_the_graph_and_yields_are_thousandths():
    g = build([graph_recipe(20, 7, 1, 2, [(8, 3)]), graph_recipe(20, 9, 1, 1, [(8, 1)])], [30, 7, 30])
    assert [(r.spell, r.product, r.yield1000) for r in g.recipes] == [(20, 7, 1500)]   # one per spell: the first
    assert g.disenchant == (7, 30)
    assert g.order == (8, 7, 30)
    assert build([], []) == build([], [])
    assert build([], []).order == ()


SHARED = json.loads((Path(__file__).resolve().parents[3] / "tests" / "scenarios" / "graph.json").read_text(encoding="utf-8"))


def run_reprice(args: dict) -> dict:
    """Run one shared repricing scenario (tests/scenarios/graph.json).

    Args:
        args: The scenario's args.

    Returns:
        {"costs": key -> [cost, crafted], "candidates": id -> [gold, net]}, keys as strings like the JSON.
    """
    g = build([GraphRecipe(s, p, y, tuple(tuple(m) for m in mats)) for s, p, y, mats in args["recipes"]], [])
    assert {str(k): list(v) for k, v in g.uses} == args["uses"] and list(g.order) == args["order"]
    base = route_costs(g, {int(k): v for k, v in args["base_prices"].items()})
    now = route_costs(g, {int(k): v for k, v in args["prices"].items()})
    out = {}
    for c in args["candidates"]:
        gold, net = repriced(c["gold"], c["net"], [tuple(i) for i in c["inputs"]], base, now)
        out[c["id"]] = [gold, net]
    return {"costs": {str(k): [v.cost, v.crafted] for k, v in sorted(now.items())}, "candidates": out}


@pytest.mark.parametrize("s", SHARED, ids=lambda s: f"{s['id']} {s['name']}")
def test_shared_graph_scenarios(s):
    assert run_reprice(s["args"]) == s["expect"], s["why"]


def test_DG3_the_cheaper_route_wins_and_a_tie_buys():
    g = build(COPPER_CHAIN, [])                                        # here a rod is one bar
    assert route_costs(g, {ORE: 1200, BAR: 1000, ROD: 900})[ROD] == RouteCost(900.0, False)
    assert route_costs(g, {ORE: 1200, BAR: 1000, ROD: 1000})[ROD] == RouteCost(1000.0, False)    # a tie buys
    assert route_costs(g, {BAR: 600, ROD: 1500})[ROD] == RouteCost(600.0, True)
    assert RUNED not in route_costs(g, {BAR: 600})                     # no dust or essence price: no runed rod
    assert route_costs(g, {}) == {}


def test_a_cut_reagent_and_a_second_recipe_for_the_same_product():
    looped = build([graph_recipe(11, 1, 1, 1, [(2, 1)]), graph_recipe(12, 2, 1, 1, [(1, 1)])], [])
    assert route_costs(looped, {2: 100}) == {2: RouteCost(100.0, False), 1: RouteCost(100.0, True)}
    two = build([graph_recipe(21, 5, 1, 1, [(6, 3)]), graph_recipe(22, 5, 2, 2, [(7, 1)]),
                 graph_recipe(23, 5, 1, 1, [(6, 4)])], [])
    assert route_costs(two, {6: 100, 7: 500})[5] == RouteCost(250.0, True)   # 3s, 5s / 2 = 2s 50c, 4s: the cheapest


def test_input_delta_prices_ah_and_crafted_inputs_only():
    base = {1: RouteCost(100.0, False), 2: RouteCost(50.0, False)}
    now = {1: RouteCost(80.0, False), 2: RouteCost(70.0, False)}
    inputs = [(1, 2, "own+ah"), (2, 1, "own"), (2, 1, "vendor"), (3, 1, "ah")]
    assert input_delta(inputs, base, now) == -40.0
    assert repriced(1000, 500.0, inputs, base, now) == (960, 540.0)
