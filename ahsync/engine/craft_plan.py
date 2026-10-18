"""The weekly plan, part 3: craft to sell later (SPEC §3.4C.3).

Each recipe's output is valued at the product's best selling window in the next 7
days (or posting now), with the room left in that window's capacity. The craft count
comes from craft.craft_option (each extra craft must still pay). A planned craft
becomes a linked chain: buy the missing mats → craft any intermediates (mailing them
when another character makes the next step) → craft → post the product. The chain's
profit sits on the craft; mat buys and mail carry none (SPEC §3.4 expected net, D22).
Pure: data in, numbers out.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from ahsync.engine import craft, fees, sellprob
from ahsync.engine.plan import PlanAction, PlanSettings, PostOption, SellFacts, alternative, post_chance, \
    post_options, window_id

STEP_MS = 60_000      # chain steps a minute apart, so their order shows in time


@dataclass(frozen=True)
class CraftWorld:
    """What craft planning reads: your recipes, where mats come from, and the products' facts."""

    recipes: tuple[craft.Recipe, ...]                         # known recipes that make an item, by spell
    by_product: Mapping[int, Sequence[craft.Recipe]]          # product key -> recipes, for crafted reagents
    market: craft.Market                                      # books, vendors, your stock at hold value
    products: Mapping[int, SellFacts]                         # product key -> what the plan knows


def units_made(crafts: int, recipe: craft.Recipe) -> int:
    """Units n crafts make on average, rounded half up, at least one when crafting.

    Args:
        crafts: Number of crafts.
        recipe: The recipe.

    Returns:
        Units; 0 for no crafts.
    """
    return 0 if crafts <= 0 else max(1, math.floor(crafts * recipe.avg_yield + 0.5))


def _best_sale(f: SellFacts, opts: list[PostOption], ledger: dict, units: int,
               s: PlanSettings) -> tuple[int, float, float] | None:
    """Where the product's units sell best, within the capacity left.

    Args:
        f: The product.
        opts: Its post options (posting now included).
        ledger: (item key, window id) -> units already planned.
        units: Units to post together.
        s: Settings.

    Returns:
        (option index, expected value, P); None when no window has room.
    """
    best = None
    for i, o in enumerate(opts):
        used = ledger.get((f.key, window_id(o)), 0.0)
        if used + units > o.cap:
            continue
        p = post_chance(f, o.price, units, o.hours, used, s)
        deposit = fees.deposit_copper(f.vendor_sell, units, o.duration_h)
        e = sellprob.expected_post(p, units, o.price, alternative(f)[1], deposit)
        if best is None or e > best[1]:
            best = (i, e, p)
    return best


def product_value(f: SellFacts, recipe: craft.Recipe, opts: list[PostOption], ledger: dict,
                  s: PlanSettings) -> Callable[[int], float]:
    """n -> expected value of n crafts' output at its best window (SPEC §3.4C.3).

    Args:
        f: The product.
        recipe: The recipe.
        opts: The product's post options.
        ledger: The capacity ledger; read only.
        s: Settings.

    Returns:
        A function of the craft count; output no window has room for is worth nothing,
        so the craft count stops where capacity ends.
    """
    cache: dict[int, float] = {0: 0.0}

    def value(n: int) -> float:
        """Expected value of n crafts' output.

        Args:
            n: Crafts.

        Returns:
            Float copper.
        """
        if n not in cache:
            best = _best_sale(f, opts, ledger, units_made(n, recipe), s)
            cache[n] = best[1] if best else -math.inf
        return cache[n]
    return value


def intermediate_buys(option: craft.CraftOption, recipe: craft.Recipe, world: CraftWorld) -> list[tuple[str, int, float]]:
    """The mats bought for the intermediates a chain crafts itself (SPEC §3.4B crafting chains).

    craft_option prices an intermediate's mats into its cost; the chain also has to buy them.
    An intermediate made from your own stock buys nothing.

    Args:
        option: The planned craft option.
        recipe: The final recipe.
        world: Recipes and market.

    Returns:
        (its crafter, mat key, units) per bought mat, by intermediate then mat.
    """
    needs = {key: count * option.crafts for key, count in recipe.reagents}
    out = []
    for key, made in sorted(option.crafted.items()):
        if "owned" in made.raw:
            continue
        inner = next(r for r in world.by_product[key] if r.spell == made.spell)
        crafts = math.ceil(needs.get(key, 0) / inner.avg_yield)
        out.extend((made.crafter, mat, float(count * crafts)) for mat, count in inner.reagents)
    return out


def consume(market: craft.Market, option: craft.CraftOption, extra: Sequence[tuple[str, int, float]] = ()) -> craft.Market:
    """The market after a chain: the owned mats it used and the AH units it bought are gone.

    Args:
        market: Before.
        option: The planned craft option.
        extra: Further (crafter, key, units) bought for intermediates.

    Returns:
        A new Market; vendor stock is unlimited and unchanged.
    """
    owned = {k: v - option.owned_used.get(k, 0.0) for k, v in market.owned.items()}
    bought = dict(option.bought)
    for _, key, units in extra:
        bought[key] = bought.get(key, 0.0) + units
    books = {}
    for key, levels in market.books.items():
        need = bought.get(key, 0.0) if key not in market.vendor else 0.0
        left = []
        for price, units in sorted(levels):
            take = min(need, units)
            need -= take
            if units - take > 0:
                left.append((price, units - take))
        books[key] = left
    return replace(market, owned=owned, books=books)


def buy_terms(market: craft.Market, key: int, units: float) -> tuple[int, int]:
    """Price and cost of buying a mat: the vendor when it sells it, else the AH from the cheapest listing.

    Args:
        market: The market before the buy.
        key: The mat.
        units: Units to buy.

    Returns:
        (the highest unit price paid, total copper).
    """
    if key in market.vendor:
        return market.vendor[key], math.ceil(market.vendor[key] * units)
    top, need, cost = 0, units, 0.0
    for price, have in sorted(market.books.get(key, ())):
        if need <= 0:
            break
        take = min(need, have)
        top, need, cost = price, need - take, cost + price * take
    return top, math.ceil(cost)


def buy_source(market: craft.Market, key: int) -> str:
    """Where a chain buys a mat, as buy_terms prices it: a vendor that sells it, else the AH (D41).

    Args:
        market: The market before the buy.
        key: The mat.

    Returns:
        "vendor" or "ah".
    """
    return "vendor" if key in market.vendor else "ah"


def chain_actions(recipe: craft.Recipe, option: craft.CraftOption, world: CraftWorld, ledger: dict, now_ms: int,
                  s: PlanSettings) -> list[PlanAction]:
    """One planned craft as its linked steps, all under chain id "craft:<spell>" (SPEC §3.4C.3, C7, D22).

    Mat buys, intermediate crafts and mail come first, a minute apart from now; the craft
    carries the chain's expected profit; the post follows at the product's chosen window
    and carries its own net against the mats' cost. Mats are bought by the final crafter.

    Args:
        recipe: The recipe.
        option: Its planned craft option (crafts > 0).
        world: Recipes, market and products.
        ledger: The capacity ledger; read only here.
        now_ms: Now.
        s: Settings.

    Returns:
        The steps in time order; the ledger is not changed.
    """
    chain = f"craft:{recipe.spell}"
    f = world.products[recipe.product]
    opts = post_options(f, now_ms, s)
    units = units_made(option.crafts, recipe)
    i, expected, p = _best_sale(f, opts, ledger, units, s)
    profit = sum(option.profits)
    cost = expected - profit
    out, at = [], now_ms
    needs = {key: count * option.crafts for key, count in recipe.reagents}     # intermediate units the craft uses
    for crafter, key, units in intermediate_buys(option, recipe, world):
        price, _ = buy_terms(world.market, key, units)
        out.append(PlanAction("buy", crafter, key, math.ceil(units - 1e-9), price, at, None, None, None, None, 0.0,
                              0.0, chain=chain, source=buy_source(world.market, key)))
        at += STEP_MS
    for step in craft.steps(option, recipe):
        if step[0] == "buy":
            price, _ = buy_terms(world.market, step[1], step[2])
            out.append(PlanAction("buy", option.crafter, step[1], math.ceil(step[2] - 1e-9), price, at, None, None,
                                  None, None, 0.0, 0.0, chain=chain, source=buy_source(world.market, step[1])))
        elif step[0] == "mail":
            out.append(PlanAction("mail", step[2], step[1], needs.get(step[1], 1), None, at, None, None, None, None, 0.0, 0.0,
                                  chain=chain))
        elif step[3] != recipe.product:
            out.append(PlanAction("craft", step[2], step[3], needs.get(step[3], 1), None, at, None, None, None, None, 0.0, 0.0,
                                  chain=chain))
        else:
            out.append(PlanAction("craft", option.crafter, recipe.product, option.crafts, None, at, None, None, None,
                                  None, expected, profit, chain=chain))
        at += STEP_MS
    o = opts[i]
    start, end, best = o.window if o.window else (None, None, None)
    out.append(PlanAction("post", option.crafter, recipe.product, units, o.price, max(o.at_ms, at), start, end, best,
                          p, expected, expected - cost, o.duration_h, chain))
    return out


def craft_plan(world: CraftWorld, budgets: dict[str, int], total_gold: int, now_ms: int, s: PlanSettings,
               max_risk_pct: float, ledger: dict, max_depth: int = craft.MAX_CRAFT_DEPTH,
               exclude: set[int] = frozenset()) -> tuple[list[PlanAction], craft.Market]:
    """Every craft-to-sell chain worth making, best expected profit × confidence first (SPEC §3.4C.3).

    Each chain's mat gold comes from its crafter's budget and stays within max_risk_pct of
    all your gold (§3.4 Risk); its owned mats and AH units are used up for later chains, and
    its product's post takes room in the shared capacity ledger (PL4).

    Args:
        world: Recipes, market and products.
        budgets: Character id -> copper it may spend; changed in place.
        total_gold: All your characters' gold, copper.
        now_ms: Now.
        s: Plan settings.
        max_risk_pct: Config max_risk_pct.
        ledger: The shared capacity ledger; updated with the chains' posts.
        max_depth: Config max_craft_depth.
        exclude: Products never to craft (rules no_buy and ignore).

    Returns:
        (the chains' steps in the order taken, the market after them: what is left for flips
        and for the sell plan of your remaining stock).
    """
    risk_limit = total_gold * max_risk_pct / 100
    market = world.market
    ranked = []
    for recipe in world.recipes:
        f = world.products.get(recipe.product)
        if f is None or recipe.product in exclude or not recipe.crafters:
            continue
        option = _option(recipe, world, market, ledger, now_ms, s, budgets, risk_limit, max_depth)
        if option.crafts:
            ranked.append((-sum(option.profits) * f.conf / 100, recipe.spell, recipe))
    actions = []
    for _, _, recipe in sorted(ranked, key=lambda r: (r[0], r[1])):
        option = _option(recipe, replace(world, market=market), market, ledger, now_ms, s, budgets, risk_limit,
                         max_depth)
        if not option.crafts:
            continue
        steps = chain_actions(recipe, option, replace(world, market=market), ledger, now_ms, s)
        post = steps[-1]
        key = (post.key, post.window_start_ms if post.window_start_ms is not None else None)
        ledger[key] = ledger.get(key, 0.0) + post.qty
        budgets[option.crafter] = budgets.get(option.crafter, 0) - math.ceil(option.mat_gold)
        market = consume(market, option, intermediate_buys(option, recipe, world))
        actions.extend(steps)
    return actions, market


def _option(recipe: craft.Recipe, world: CraftWorld, market: craft.Market, ledger: dict, now_ms: int,
            s: PlanSettings, budgets: dict[str, int], risk_limit: float, max_depth: int) -> craft.CraftOption:
    """The craft option for a recipe now: its count, sources and profit, within budget and risk.

    Args:
        recipe: The recipe.
        world: Recipes and products.
        market: The market as it stands (after earlier chains).
        ledger: The capacity ledger; read only.
        now_ms: Now.
        s: Settings.
        budgets: Character id -> copper left.
        risk_limit: The most one chain may spend on mats, copper.
        max_depth: Config max_craft_depth.

    Returns:
        The option; crafts = 0 when nothing pays.
    """
    f = world.products[recipe.product]
    value = product_value(f, recipe, post_options(f, now_ms, s), ledger, s)
    budget = min(risk_limit, max(0, budgets.get(recipe.crafters[0], 0)))
    return craft.craft_option(recipe, market, world.by_product, value, budget=budget, max_depth=max_depth)
