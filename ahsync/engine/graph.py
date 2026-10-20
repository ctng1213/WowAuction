"""The dependency graph the addon reprices along (SPEC §3.5 `G`, §3.4D.10, EX18).

Items are the nodes. Each recipe your characters know adds an edge from each of its
reagents to its product, so a new price for Copper Bar reaches Copper Rod and then
Runed Copper Rod. `order` lists every graph item with inputs before what they make.
A recipe loop (A uses B, B uses A) is cut the way crafting chains cut it (C13): a
reagent already on the path being followed is not followed again, and that edge is
left out of the reverse index and the order. The cut edges are returned so the
caller can log them. Items one could disenchant are graph items too (their results
come from the addon's static disenchant table, not from here). Pure: data in,
graph out; every list is sorted, so the same recipes always give the same graph.

Repricing along it (`route_costs`, `repriced`, SPEC §4.0, DG1-DG3) is the twin of
addon/AHProfit/Engine/Graph.lua, which reprices live; AHSync itself only runs it
in the shared scenarios (tests/scenarios/graph.json).
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class GraphRecipe:
    """One known recipe as the graph holds it (SPEC §3.5 `R`)."""

    spell: int
    product: int                               # the item it makes
    yield1000: int                             # average units made per craft, in thousandths
    reagents: tuple[tuple[int, int], ...]      # (item key, count per craft), by key


@dataclass(frozen=True)
class Graph:
    """The dependency graph of one market (SPEC §3.5 `G`)."""

    recipes: tuple[GraphRecipe, ...]                    # by spell
    uses: tuple[tuple[int, tuple[int, ...]], ...]       # reagent key -> the spells that use it (cut edges left out)
    disenchant: tuple[int, ...]                         # items one could disenchant, by key
    order: tuple[int, ...]                              # every graph item, inputs before what they make
    cut: tuple[tuple[int, int], ...]                    # (reagent key, spell) edges cut to break a loop (C13)


def graph_recipe(spell: int, product: int, yield_min: int, yield_max: int,
                 reagents: Iterable[tuple[int, int]]) -> GraphRecipe:
    """A recipe for the graph.

    Args:
        spell: The recipe's spell id.
        product: The item it makes.
        yield_min: Fewest units a craft makes.
        yield_max: Most units a craft makes.
        reagents: (item key, count per craft).

    Returns:
        The GraphRecipe, its yield the average in thousandths, (min + max) × 500, its reagents by key.
    """
    return GraphRecipe(spell, product, (yield_min + yield_max) * 500, tuple(sorted(reagents)))


def _topological(nodes: Sequence[int], inputs: dict[int, list[tuple[int, int]]]) -> tuple[list[int], list[tuple[int, int]]]:
    """Order the nodes inputs first by a depth-first walk, cutting each edge back onto the path (C13).

    Args:
        nodes: Every node, ascending: the walk starts from each unvisited one in this order.
        inputs: Node -> (spell, reagent key) edges into it, by spell then key.

    Returns:
        (the nodes in topological order, the (reagent key, spell) edges cut, in the order met).
    """
    state: dict[int, int] = {}                         # 1 on the current path, 2 done
    order: list[int] = []
    cut: list[tuple[int, int]] = []
    for root in nodes:
        if root in state:
            continue
        state[root] = 1
        stack = [(root, iter(inputs.get(root, ())))]
        while stack:
            node, edges = stack[-1]
            edge = next(edges, None)
            if edge is None:
                stack.pop()
                state[node] = 2
                order.append(node)
                continue
            spell, reagent = edge
            if state.get(reagent) == 1:                # a loop: this reagent is already being followed
                cut.append((reagent, spell))
            elif reagent not in state:
                state[reagent] = 1
                stack.append((reagent, iter(inputs.get(reagent, ()))))
    return order, cut


def build(recipes: Iterable[GraphRecipe], disenchant: Iterable[int]) -> Graph:
    """The dependency graph of your known recipes and the items one could disenchant (SPEC §3.5 `G`, EX18).

    Args:
        recipes: The recipes your characters know (one per spell is kept, the first given).
        disenchant: Item keys one could disenchant.

    Returns:
        The Graph: recipes by spell; the reverse index of the edges kept; the disenchantable items; every item in
        topological order (a depth-first walk from each item in ascending key order, inputs before products);
        and the edges cut to break loops.
    """
    by_spell: dict[int, GraphRecipe] = {}
    for r in recipes:
        by_spell.setdefault(r.spell, r)
    kept = [by_spell[s] for s in sorted(by_spell)]
    inputs: dict[int, list[tuple[int, int]]] = {}
    nodes = set(disenchant)
    for r in kept:
        nodes.add(r.product)
        for key, _ in r.reagents:
            nodes.add(key)
            inputs.setdefault(r.product, []).append((r.spell, key))
    order, cut = _topological(sorted(nodes), {k: sorted(v) for k, v in inputs.items()})
    dropped = set(cut)
    uses: dict[int, list[int]] = {}
    for r in kept:
        for key, _ in r.reagents:
            if (key, r.spell) not in dropped:
                uses.setdefault(key, []).append(r.spell)
    return Graph(tuple(kept), tuple((k, tuple(sorted(set(v)))) for k, v in sorted(uses.items())),
                 tuple(sorted(set(disenchant))), tuple(order), tuple(cut))


@dataclass(frozen=True)
class RouteCost:
    """One graph item's cheapest route at a set of prices (SPEC §4.0 repricing, §3.4B, DG3)."""

    cost: float          # copper a unit: its buy price, or its recipe's reagent costs ÷ the yield
    crafted: bool        # True when crafting it is strictly cheaper than buying it


def route_cost(graph: Graph, key: int, done: Mapping[int, RouteCost], prices: Mapping[int, int]) -> RouteCost | None:
    """The cheaper of buying an item and crafting it from reagents already costed (SPEC §4.0, DG3).

    Its twin is `Graph.RouteCost` in addon/AHProfit/Engine/Graph.lua: the same sums in the same order.

    Args:
        graph: The dependency graph.
        key: The item.
        done: Route costs of the items before it in `graph.order`; a reagent not in it (an edge cut to break a loop,
            or an item with no price and no recipe) makes that recipe unusable.
        prices: Item key -> buy price, copper a unit (the cheapest listing, or the market file's price).

    Returns:
        The RouteCost; a tie goes to buying. None when it has no price and no usable recipe.
    """
    buy = prices.get(key)
    craft: float | None = None
    for r in graph.recipes:                                    # by spell, as the addon's index lists them
        if r.product != key:
            continue
        total, usable = 0.0, True
        for reagent, count in r.reagents:
            got = done.get(reagent)
            if got is None:
                usable = False
                break
            total += count * got.cost
        if usable:
            each = total * 1000 / r.yield1000
            craft = each if craft is None or each < craft else craft
    if craft is not None and (buy is None or craft < buy):
        return RouteCost(craft, True)
    if buy is None:
        return None
    return RouteCost(float(buy), False)


def route_costs(graph: Graph, prices: Mapping[int, int]) -> dict[int, RouteCost]:
    """Every graph item's cheapest route, intermediates first in the exported order (SPEC §4.0, DG1-DG3).

    Args:
        graph: The dependency graph.
        prices: Item key -> buy price, copper a unit.

    Returns:
        Item key -> RouteCost, for the items with a price or a usable recipe.
    """
    done: dict[int, RouteCost] = {}
    for key in graph.order:
        got = route_cost(graph, key, done, prices)
        if got is not None:
            done[key] = got
    return done


def input_delta(inputs: Sequence[tuple[int, int, str]], base: Mapping[int, RouteCost],
                now: Mapping[int, RouteCost]) -> float:
    """How much a candidate's bought and crafted inputs cost more now than when it was exported (SPEC §4.0, DG1).

    Args:
        inputs: (item key, count, source) per input, as `K` lists them; a source naming `ah` or `craft` is priced,
            `own` and `vendor` alone are not (owned units are valued at their hold value, vendors don't move).
        base: Route costs at the exported prices.
        now: Route costs at the live prices.

    Returns:
        Σ count × (cost now − cost then), float copper, over the priced inputs known at both; 0 when none moved.
    """
    delta = 0.0
    for key, count, source in inputs:
        parts = source.split("+")
        if "ah" not in parts and "craft" not in parts:
            continue
        then, later = base.get(key), now.get(key)
        if then is not None and later is not None:
            delta += count * (later.cost - then.cost)
    return delta


def repriced(gold: int, net: float, inputs: Sequence[tuple[int, int, str]], base: Mapping[int, RouteCost],
             now: Mapping[int, RouteCost]) -> tuple[int, float]:
    """A candidate's gold needed and expected net after its inputs' prices moved (SPEC §4.0, DG1).

    Args:
        gold: The exported gold needed, copper.
        net: The exported expected net, copper.
        inputs: As input_delta.
        base: Route costs at the exported prices.
        now: Route costs at the live prices.

    Returns:
        (gold + floor(delta + 0.5), net − delta): gold stays integer copper, net is float copper (SPEC §3.3).
    """
    delta = input_delta(inputs, base, now)
    return gold + math.floor(delta + 0.5), net - delta
