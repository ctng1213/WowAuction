"""Crafting options: mat sourcing, crafting chains and how many crafts pay (SPEC §3.4B).

Each reagent unit comes from its cheapest source: owned stock (at its hold value),
the vendor, the AH (walking the book from the cheapest listing up), or crafted by
one of your characters (its own sourced cost; chains up to max_craft_depth, never
feeding itself). Owned stock is used first at an equal cost.

`walk_book` and `fill` are ported to addon/AHProfit/Engine/Craft.lua (the addon
re-checks craft costs live); the rest runs in AHSync only.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

KINDS = ("owned", "vendor", "ah", "crafted")      # tie order at an equal unit cost
MAX_CRAFT_DEPTH = 3
MAX_CRAFTS = 100        # backstop; product_net's falling chance to sell normally stops crafts first


@dataclass(frozen=True)
class Fill:
    """How one reagent's need was met (the result of `fill`)."""

    cost: float                       # opportunity cost: owned at hold value, the rest at its price
    gold: float                       # copper actually spent (vendor, AH, and crafted mats bought)
    used: dict[str, float]            # units per source kind
    book_taken: tuple[float, ...]     # units taken from each book level, in book order
    short: float                      # units no source could provide


def walk_book(book: Sequence[tuple[int, float]], qty: float) -> int | None:
    """Cost of buying qty units from the AH, cheapest listing first (SPEC §3.4 reagent cost).

    Args:
        book: (unit price copper, units) levels, any order.
        qty: Units to buy.

    Returns:
        Total copper, or None when the book doesn't hold enough units.
    """
    total, need = 0, qty
    for price, units in sorted(book):
        take = min(need, units)
        total += price * take
        need -= take
        if need <= 0:
            return total
    return None if need > 0 else total


def fill(need: float, owned: float, hold_value: float, vendor_price: int | None,
         book: Sequence[tuple[int, float]], crafted_cost: float | None = None,
         crafted_gold: float = 0.0) -> Fill:
    """Meet one reagent's need from its cheapest sources (SPEC §3.4B mat sourcing).

    Args:
        need: Units needed.
        owned: Units you own and may use.
        hold_value: Opportunity cost of an owned unit (SPEC §3.4 hold value), float copper.
        vendor_price: Vendor buy price per unit, or None when no vendor sells it.
        book: AH (unit price, units) levels, in book order.
        crafted_cost: Cost per unit when crafted by you, or None when it can't be.
        crafted_gold: Copper spent per crafted unit (its bought mats).

    Returns:
        The Fill. Offers are taken by unit cost, ties in KINDS order (owned first),
        then book order.
    """
    offers = [(hold_value, 0, -1, owned)]
    if vendor_price is not None:
        offers.append((float(vendor_price), 1, -1, math.inf))
    offers += [(float(price), 2, i, units) for i, (price, units) in enumerate(book)]
    if crafted_cost is not None:
        offers.append((crafted_cost, 3, -1, math.inf))
    used = {kind: 0.0 for kind in KINDS}
    taken = [0.0] * len(book)
    cost = gold = 0.0
    left = need
    for unit_cost, rank, index, units in sorted(offers):
        if left <= 0:
            break
        take = min(left, units)
        if take <= 0:
            continue
        kind = KINDS[rank]
        used[kind] += take
        cost += unit_cost * take
        gold += 0.0 if kind == "owned" else (crafted_gold if kind == "crafted" else unit_cost) * take
        if index >= 0:
            taken[index] += take
        left -= take
    return Fill(cost, gold, used, tuple(taken), max(0.0, left))


@dataclass(frozen=True)
class Recipe:
    """A recipe someone on the account knows (SPEC §2.2.1 recipes, known recipes)."""

    spell: int
    product: int | None                    # product item key; None for enchants
    yield_min: int
    yield_max: int
    reagents: tuple[tuple[int, int], ...]  # (key, count) per craft
    crafters: tuple[str, ...]              # characters who know it, sorted
    cooldown_s: int | None = None          # None = unknown (no limit applied); 0 = none

    @property
    def avg_yield(self) -> float:
        """Average units made per craft.

        Returns:
            (yield_min + yield_max) ÷ 2.
        """
        return (self.yield_min + self.yield_max) / 2


@dataclass(frozen=True)
class Market:
    """Where reagents can come from right now."""

    books: Mapping[int, Sequence[tuple[int, float]]]     # key -> AH (price, units) of other sellers
    vendor: Mapping[int, int]                            # key -> vendor buy price, vendor-sold reagents only
    owned: Mapping[int, float]                           # key -> units you own (all characters)
    hold_value: Mapping[int, float]                      # key -> opportunity cost per owned unit


@dataclass(frozen=True)
class Crafted:
    """The cheapest way you can craft one unit of an intermediate."""

    cost: float                  # per unit
    gold: float                  # copper spent per unit
    crafter: str                 # who makes it
    spell: int
    raw: frozenset[str]          # source kinds of its own raw mats ("owned", "vendor", "ah")


@dataclass
class CraftOption:
    """One recipe's crafting option for the Craft tab and the plan (SPEC §3.4B)."""

    spell: int
    crafter: str
    crafts: int = 0
    mat_gold: float = 0.0                                 # copper to buy the missing mats, all crafts
    owned_used: dict[int, float] = field(default_factory=dict)
    bought: dict[int, float] = field(default_factory=dict)
    crafted: dict[int, Crafted] = field(default_factory=dict)
    profits: list[float] = field(default_factory=list)    # marginal profit of each craft
    missing: tuple[tuple[int, float], ...] = ()           # (key, units short) when not craftable

    @property
    def profit(self) -> float:
        """Total expected profit of all crafts.

        Returns:
            Sum of the marginal profits, float copper.
        """
        return sum(self.profits)

    @property
    def source(self) -> str:
        """The row's Source label (SPEC §3.4B).

        Returns:
            "Owned", "AH" or "Mixed", plus " + crafted" when a reagent is made by you.
        """
        raw = set()
        if self.owned_used:
            raw.add("owned")
        if self.bought:
            raw.add("ah")
        for item in self.crafted.values():
            raw |= {"owned" if k == "owned" else "ah" for k in item.raw}
        base = "Mixed" if raw == {"owned", "ah"} else ("Owned" if raw == {"owned"} else "AH")
        return base + (" + crafted" if self.crafted else "")


class Pool:
    """Stateful reagent sources while crafts are added one by one."""

    def __init__(self, market: Market, by_product: Mapping[int, Sequence[Recipe]], depth: int,
                 visiting: frozenset[int], max_depth: int) -> None:
        """Create a pool over the market.

        Args:
            market: Current sources.
            by_product: product key -> recipes that make it (for crafted reagents).
            depth: Crafting depth of the recipe this pool feeds (1 = the final product).
            visiting: Product keys already on this chain (loops are not followed).
            max_depth: max_craft_depth.
        """
        self.market, self.by_product = market, by_product
        self.depth, self.visiting, self.max_depth = depth, visiting, max_depth
        self.owned_left = dict(market.owned)
        self.book_left = {k: [list(level) for level in levels] for k, levels in market.books.items()}
        self.crafted_cache: dict[int, Crafted | None] = {}

    def crafted(self, key: int) -> Crafted | None:
        """The cheapest crafted source of an intermediate, if chains allow it (SPEC §3.4B).

        Args:
            key: The reagent's item key.

        Returns:
            Its per-unit cost and crafter; None beyond max_craft_depth, on a loop,
            or when no known recipe makes it or one craft of it can't be sourced.
        """
        if key not in self.crafted_cache:
            self.crafted_cache[key] = None
            if self.depth < self.max_depth and key not in self.visiting:
                self.crafted_cache[key] = cheapest_crafted(key, self.market, self.by_product, self.depth + 1,
                                                           self.visiting | {key}, self.max_depth)
        return self.crafted_cache[key]

    def take(self, key: int, qty: float) -> Fill:
        """Source qty units of a reagent and consume them from the pool.

        Args:
            key: The reagent.
            qty: Units needed.

        Returns:
            The Fill (short > 0 when not all could be sourced; nothing is consumed then).
        """
        book = [(price, units) for price, units in self.book_left.get(key, [])]
        made = self.crafted(key)
        result = fill(qty, self.owned_left.get(key, 0.0), self.market.hold_value.get(key, 0.0),
                      self.market.vendor.get(key), book, made.cost if made else None, made.gold if made else 0.0)
        if result.short <= 0:
            self.owned_left[key] = self.owned_left.get(key, 0.0) - result.used["owned"]
            for level, units in zip(self.book_left.get(key, []), result.book_taken):
                level[1] -= units
        return result

    def snapshot(self) -> tuple[dict, dict]:
        """Copy the consumable state, to undo a craft that doesn't pay.

        Returns:
            (owned left, book left) copies.
        """
        return dict(self.owned_left), {k: [list(lv) for lv in v] for k, v in self.book_left.items()}

    def restore(self, state: tuple[dict, dict]) -> None:
        """Undo to a snapshot.

        Args:
            state: From snapshot().
        """
        self.owned_left, self.book_left = state


def cheapest_crafted(key: int, market: Market, by_product: Mapping[int, Sequence[Recipe]], depth: int,
                     visiting: frozenset[int], max_depth: int) -> Crafted | None:
    """Cost per unit of making an intermediate yourself, with its cheapest recipe.

    Args:
        key: The intermediate's item key.
        market: Current sources.
        by_product: product key -> recipes.
        depth: Depth of this craft in the chain.
        visiting: Keys on the chain so far.
        max_depth: max_craft_depth.

    Returns:
        The cheapest Crafted over the recipes that make it (ties: lower spell id); None
        when none can be sourced.
    """
    best = None
    for recipe in sorted(by_product.get(key, ()), key=lambda r: r.spell):
        pool = Pool(market, by_product, depth, visiting, max_depth)
        fills = [pool.take(reagent, count) for reagent, count in recipe.reagents]
        if any(f.short > 0 for f in fills) or not recipe.crafters:
            continue
        raw = frozenset(kind for f in fills for kind, units in f.used.items() if units > 0 and kind != "crafted")
        option = Crafted(sum(f.cost for f in fills) / recipe.avg_yield, sum(f.gold for f in fills) / recipe.avg_yield,
                         recipe.crafters[0], recipe.spell, raw)
        if best is None or option.cost < best.cost:
            best = option
    return best


def _record(option: CraftOption, pool: Pool, key: int, f: Fill) -> None:
    """Add one reagent's fill to the option's owned / bought / crafted tallies.

    Args:
        option: The option being built.
        pool: The pool (for the crafted source's details).
        key: The reagent.
        f: Its fill for this craft.
    """
    if f.used["owned"]:
        option.owned_used[key] = option.owned_used.get(key, 0.0) + f.used["owned"]
    if f.used["ah"] or f.used["vendor"]:
        option.bought[key] = option.bought.get(key, 0.0) + f.used["ah"] + f.used["vendor"]
    if f.used["crafted"]:
        option.crafted[key] = pool.crafted(key)


def craft_option(recipe: Recipe, market: Market, by_product: Mapping[int, Sequence[Recipe]],
                 product_net: Callable[[int], float], budget: float | None = None,
                 max_depth: int = MAX_CRAFT_DEPTH, max_crafts: int = MAX_CRAFTS) -> CraftOption:
    """How many crafts of a recipe pay, and what they need (SPEC §3.4B).

    Crafts are added one at a time; each must still make a profit (AH mats get
    pricier as the book is walked, and the product's value per extra craft falls).

    Args:
        recipe: The recipe.
        market: Current sources.
        by_product: product key -> recipes, for crafted reagents.
        product_net: n -> total expected net (E_post, or DE EV) of n crafts' output, float copper.
        budget: Most copper to spend on mats, or None for no limit.
        max_depth: max_craft_depth.
        max_crafts: Hard cap on crafts (unlimited vendor mats with a flat product value would never stop).

    Returns:
        The option; crafts = 0 with `missing` set when one craft can't be sourced.
    """
    option = CraftOption(recipe.spell, recipe.crafters[0] if recipe.crafters else "")
    pool = Pool(market, by_product, 1, frozenset({recipe.product}) if recipe.product else frozenset(), max_depth)
    limit = len(recipe.crafters) if recipe.cooldown_s else max_crafts    # one per cooldown per character
    while option.crafts < limit:
        state = pool.snapshot()
        fills = [(key, pool.take(key, count)) for key, count in recipe.reagents]
        short = [(key, f.short) for key, f in fills if f.short > 0]
        gold = sum(f.gold for _, f in fills)
        marginal = product_net(option.crafts + 1) - product_net(option.crafts) - sum(f.cost for _, f in fills)
        if short or marginal <= 0 or (budget is not None and option.mat_gold + gold > budget):
            pool.restore(state)
            if short and option.crafts == 0:
                option.missing = tuple(short)
            break
        for key, f in fills:
            _record(option, pool, key, f)
        option.mat_gold += gold
        option.profits.append(marginal)
        option.crafts += 1
    return option


def steps(option: CraftOption, recipe: Recipe) -> list[tuple]:
    """The chain of actions behind a craft option, in order (SPEC §3.4B crafting chains, §3.4C.3).

    Args:
        option: From craft_option.
        recipe: Its recipe.

    Returns:
        ("buy", key, units) for bought mats, ("craft", spell, crafter, key) for each
        crafted intermediate, ("mail", key, from, to) when its crafter is not the final
        crafter, then ("craft", spell, crafter, product) for the recipe itself.
    """
    out: list[tuple] = [("buy", key, units) for key, units in sorted(option.bought.items())]
    for key, made in sorted(option.crafted.items()):
        out.append(("craft", made.spell, made.crafter, key))
        if made.crafter != option.crafter:
            out.append(("mail", key, made.crafter, option.crafter))
    out.append(("craft", recipe.spell, option.crafter, recipe.product))
    return out
