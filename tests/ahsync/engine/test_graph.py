"""Tests for ahsync/engine/graph.py: the dependency graph the addon reprices along (SPEC §3.5 `G`)."""
from __future__ import annotations

from ahsync.engine.graph import GraphRecipe, build, graph_recipe

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
