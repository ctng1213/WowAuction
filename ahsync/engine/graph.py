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
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
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
