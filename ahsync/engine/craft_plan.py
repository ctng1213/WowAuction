"""The weekly plan, part 3: craft to sell later (SPEC §3.4C.3).

Each recipe's output is valued at the product's best selling window in the next 7
days (or posting now), with the room left in that window's capacity. The craft count
comes from craft.craft_option (each extra craft must still pay); its crafts are the
portfolio's Craft and Multistage batches (SPEC §3.4D.3), and the optimizer decides how many
are funded. A funded craft becomes a linked chain: buy the missing mats → craft any
intermediates (mailing them when another character makes the next step) → mail the owned
mats other characters hold to the crafter (craft.gather picks the crafter, C19, C22) →
craft → post the product. The chain's profit sits on the craft; mat buys and mail carry
none (SPEC §3.4 expected net, D22). Pure: data in, numbers out.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from ahsync.engine import craft, fees, sellprob
from ahsync.engine.plan import OptionValue, PlanAction, PlanSettings, PostOption, SellFacts, alternative, \
    choose_option, post_chance, post_options, window_id

STEP_MS = 60_000      # chain steps a minute apart, so their order shows in time


@dataclass(frozen=True)
class CraftWorld:
    """What craft planning reads: your recipes, where mats come from, and the products' facts."""

    recipes: tuple[craft.Recipe, ...]                         # known recipes that make an item, by spell
    by_product: Mapping[int, Sequence[craft.Recipe]]          # product key -> recipes, for crafted reagents
    market: craft.Market                                      # books, vendors, your stock at hold value
    products: Mapping[int, SellFacts]                         # product key -> what the plan knows
    held: Mapping[tuple[str, int], float] = field(default_factory=dict)   # (character, key) -> owned units (C22)


def units_made(crafts: int, recipe: craft.Recipe) -> int:
    """Units n crafts make on average, rounded half up, at least one when crafting.

    Args:
        crafts: Number of crafts.
        recipe: The recipe.

    Returns:
        Units; 0 for no crafts.
    """
    return 0 if crafts <= 0 else max(1, math.floor(crafts * recipe.avg_yield + 0.5))


def best_sale(f: SellFacts, opts: list[PostOption], ledger: dict, units: int,
              s: PlanSettings, value: OptionValue | None = None) -> tuple[int, float, float] | None:
    """Where the product's units sell best, within the capacity left.

    Args:
        f: The product.
        opts: Its post options (posting now included).
        ledger: (item key, window id) -> units already planned.
        units: Units to post together.
        s: Settings.
        value: choose_option's value (the timing premium); None picks by expected value.

    Returns:
        (option index, expected value, P) as plan.choose_option picks it: a window must beat posting now by its
        required uplift (R26); None when no window has room.
    """
    scored = []
    for i, o in enumerate(opts):
        used = ledger.get((f.key, window_id(o)), 0.0)
        if used + units > o.cap:
            continue
        p = post_chance(f, o.price, units, o.hours, used, s)
        deposit = fees.deposit_copper(f.vendor_sell, units, o.duration_h)
        scored.append((i, sellprob.expected_post(p, units, o.price, alternative(f)[1], deposit), p))
    return choose_option(opts, scored, s, value)


def product_value(f: SellFacts, recipe: craft.Recipe, opts: list[PostOption], ledger: dict,
                  s: PlanSettings, value: OptionValue | None = None) -> Callable[[int], float]:
    """n -> expected value of n crafts' output at its best window (SPEC §3.4C.3).

    Args:
        f: The product.
        recipe: The recipe.
        opts: The product's post options.
        ledger: The capacity ledger; read only.
        s: Settings.
        value: best_sale's value.

    Returns:
        A function of the craft count; output no window has room for is worth nothing,
        so the craft count stops where capacity ends.
    """
    cache: dict[int, float] = {0: 0.0}

    def worth(n: int) -> float:
        """Expected value of n crafts' output.

        Args:
            n: Crafts.

        Returns:
            Float copper.
        """
        if n not in cache:
            best = best_sale(f, opts, ledger, units_made(n, recipe), s, value)
            cache[n] = best[1] if best else -math.inf
        return cache[n]
    return worth


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


def use_held(held: Mapping[tuple[str, int], float], crafter: str, used: Mapping[int, float],
             mail: Mapping[int, Sequence[tuple[str, int]]]) -> dict[tuple[str, int], float]:
    """Who still holds which owned mats after a chain: the crafter's own are used first, then the mailed ones (C22).

    Args:
        held: (character, key) -> units before the chain.
        crafter: The chain's crafter.
        used: Item key -> owned units the chain uses.
        mail: From craft.gather: key -> ((sender, units), ...).

    Returns:
        A new mapping; nothing goes below 0.
    """
    left = dict(held)
    for key, units in used.items():
        left[(crafter, key)] = left.get((crafter, key), 0.0) - min(units, left.get((crafter, key), 0.0))
        for sender, sent in mail.get(key, ()):
            left[(sender, key)] = max(0.0, left.get((sender, key), 0.0) - sent)
    return left


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
                  s: PlanSettings, value: OptionValue | None = None, chain: str | None = None) -> list[PlanAction]:
    """One planned craft as its linked steps, all under one chain id (SPEC §3.4C.3, C7, C22, D22).

    Mat buys, intermediate crafts and mail come first, a minute apart from now; the craft
    carries the chain's expected profit; the post follows at the product's chosen window
    and carries its own net against the mats' cost. The crafter is option.crafter, the one craft.gather picked in
    _option (the holder of most of the owned mats it uses, C19, C24); mats are bought by the final crafter,
    and each owned mat another character holds is a mail action from that character to the
    crafter, a minute before the craft (C22).

    Args:
        recipe: The recipe.
        option: Its planned craft option (crafts > 0).
        world: Recipes, market, products and who holds which owned mats.
        ledger: The capacity ledger; read only here.
        now_ms: Now.
        s: Settings.
        value: best_sale's value, as the option was valued with.
        chain: The chain id; "craft:<spell>" by default.

    Returns:
        The steps in time order; the ledger is not changed.
    """
    chain = chain or f"craft:{recipe.spell}"
    _, mail = craft.gather(option.owned_used, world.held, (option.crafter,))
    f = world.products[recipe.product]
    opts = post_options(f, now_ms, s)
    units = units_made(option.crafts, recipe)
    i, expected, p = best_sale(f, opts, ledger, units, s, value)
    profit = sum(option.profits)
    out, at = [], now_ms
    needs = {key: count * option.crafts for key, count in recipe.reagents}     # intermediate units the craft uses
    for buyer, key, mat_units in intermediate_buys(option, recipe, world):
        out.append(_buy(world.market, buyer, key, mat_units, at, chain))
        at += STEP_MS
    for step in craft.steps(option, recipe):
        if step[0] == "buy":
            out.append(_buy(world.market, option.crafter, step[1], step[2], at, chain))
        elif step[0] == "mail":
            out.append(_step("mail", step[2], step[1], needs.get(step[1], 1), at, chain, to_char=step[3]))
        elif step[3] != recipe.product:
            out.append(_step("craft", step[2], step[3], needs.get(step[3], 1), at, chain))
        else:
            for key, sends in sorted(mail.items()):                                     # C22
                out += [_step("mail", sender, key, sent, at, chain, to_char=option.crafter) for sender, sent in sends]
            at += STEP_MS if mail else 0
            out.append(PlanAction("craft", option.crafter, recipe.product, option.crafts, None, at, None, None, None,
                                  None, expected, profit, chain=chain, paid=expected - profit))
        at += STEP_MS
    o = opts[i]
    start, end, best = o.window if o.window else (None, None, None)
    out.append(PlanAction("post", option.crafter, recipe.product, units, o.price, max(o.at_ms, at), start, end, best,
                          p, expected, profit, o.duration_h, chain, paid=expected - profit))
    return out


def _buy(market: craft.Market, buyer: str, key: int, units: float, at_ms: int, chain: str) -> PlanAction:
    """A chain's mat buy, priced and sourced as buy_terms and buy_source say (D41).

    Args:
        market: The market before the buy.
        buyer: Who buys.
        key: The mat.
        units: Units to buy (rounded up to whole units).
        at_ms: When, UTC ms.
        chain: The chain id.

    Returns:
        The buy; it carries no net (D22).
    """
    price, _ = buy_terms(market, key, units)
    return PlanAction("buy", buyer, key, math.ceil(units - 1e-9), price, at_ms, None, None, None, None, 0.0, 0.0,
                      chain=chain, source=buy_source(market, key))


def _step(kind: str, char_id: str, key: int, qty: int, at_ms: int, chain: str, to_char: str | None = None) -> PlanAction:
    """A chain step with no money: an intermediate craft or a mail (D22).

    Args:
        kind: "craft" or "mail".
        char_id: Who acts (a mail: the sender).
        key: The item made or mailed.
        qty: Units.
        at_ms: When, UTC ms.
        chain: The chain id.
        to_char: A mail's recipient; None otherwise.

    Returns:
        The step; it carries no net.
    """
    return PlanAction(kind, char_id, key, qty, None, at_ms, None, None, None, None, 0.0, 0.0, chain=chain, to_char=to_char)


def craft_option_for(recipe: craft.Recipe, world: CraftWorld, now_ms: int, s: PlanSettings,
                     max_depth: int = craft.MAX_CRAFT_DEPTH, value: OptionValue | None = None,
                     max_crafts: int = craft.MAX_CRAFTS, crafter: str | None = None) -> craft.CraftOption:
    """A recipe's craft option: its count, sources and profit, crafted by the character gather picks (C19, C22).

    The crafter is the one craft.gather picks for the owned mats the crafts use (the holder of most of them,
    ties to the first who knows the recipe). Gold is not limited here: the portfolio funds the crafts it can
    afford one batch at a time (SPEC §3.4D.3-D.4, C24).

    Args:
        recipe: The recipe (it has crafters).
        world: Recipes, market (owned mats at hold value), products and who holds which owned mats.
        now_ms: Now.
        s: Settings.
        max_depth: Config max_craft_depth; 1 leaves crafted intermediates out (the bought route, PF4).
        value: best_sale's value (the timing premium).
        max_crafts: The most crafts (the funded batches).
        crafter: The crafter to use; None lets gather pick.

    Returns:
        The option, its crafter set; crafts = 0 when nothing pays.
    """
    f = world.products[recipe.product]
    worth = product_value(f, recipe, post_options(f, now_ms, s), {}, s, value)
    made = craft.craft_option(recipe, world.market, world.by_product, worth, max_depth=max_depth,
                              max_crafts=max_crafts)
    chosen = crafter or craft.gather(made.owned_used, world.held, recipe.crafters)[0]
    return replace(made, crafter=chosen)
