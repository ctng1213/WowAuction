"""The weekly plan's world: your stock, the market and the rules, prepared once for the portfolio (SPEC §3.4C, §3.4D).

A first sell plan of everything you own gives each item's hold value: what one unit is worth
under its own best sell plan (§3.4 hold value). Crafts then price owned mats at that value, so
a craft never beats selling the mats raw by accident. The rules take items out: `ignore` from
everything, `no_buy` and "not selling" from buys and crafts, a vendor-sold item from posts and
buys (R22, R23), a suspect price from flips (FM5). The portfolio's candidates (engine/candidates)
are generated from this world, and the optimizer (engine/allocation) decides which are funded,
spending each owned unit, each auction and each unit of a window's demand once (PL4). Pure.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from ahsync.engine import craft, fees
from ahsync.engine.craft_plan import CraftWorld
from ahsync.engine.flips import auctions
from ahsync.engine.plan import Lot, PlanSettings, SellFacts, sell_plan


@dataclass(frozen=True)
class WeekInputs:
    """Everything the weekly plan reads (no gold: the portfolio's accounting holds it, SPEC §3.4D.1)."""

    lots: tuple[Lot, ...]                     # your unbound stock per character
    facts: dict[int, SellFacts]               # item key -> what the plan knows (stock, products, flip candidates)
    world: CraftWorld                         # recipes and mat sources (its hold values are filled in by prepare)
    no_buy: frozenset[int] = frozenset()      # never buy or craft (rules no_buy, ignore; not selling)
    ignore: frozenset[int] = frozenset()      # in no recommendation, not even the sell plan (rule ignore)
    suspect: frozenset[int] = frozenset()     # lowest price out of range and unconfirmed: never flipped (FM5)


@dataclass(frozen=True)
class PlanWorld:
    """The world candidates are generated from: rules applied, hold values filled in (SPEC §3.4D.3)."""

    lots: tuple[Lot, ...]                     # your stock less ignored items
    facts: dict[int, SellFacts]               # item key -> facts, ignored items left out
    post_facts: dict[int, SellFacts]          # the same, with no post price for vendor-sold items (R23)
    world: CraftWorld                         # recipes, the market at hold values (vendor first), who holds what
    vendor_sold: frozenset[int]               # anyone can buy these from a vendor: never posted or bought (R22, R23)
    no_craft: frozenset[int]                  # products never crafted: no_buy, ignore, vendor-sold, not selling (O5)
    no_buy: frozenset[int]                    # items never bought to resell: no_craft plus suspect prices (FM5)


def hold_values(lots: Sequence[Lot], facts: dict[int, SellFacts], now_ms: int, s: PlanSettings) -> dict[int, float]:
    """Each owned item's value per unit under its own best sell plan (SPEC §3.4 hold value).

    Args:
        lots: Your stock.
        facts: Item key -> facts.
        now_ms: Now.
        s: Settings.

    Returns:
        Item key -> float copper per unit; a held unit counts at the posting-now price after the cut.
    """
    totals: dict[int, list[float]] = {}
    for a in sell_plan(lots, facts, now_ms, s, {}):
        f = facts[a.key]
        value = a.expected if a.kind != "hold" else a.qty * (f.post_price or 0) * (1 - fees.AH_CUT)
        slot = totals.setdefault(a.key, [0.0, 0])
        slot[0], slot[1] = slot[0] + value, slot[1] + a.qty
    return {key: value / units for key, (value, units) in totals.items()}


def prepare(w: WeekInputs, now_ms: int, s: PlanSettings) -> PlanWorld:
    """Apply the rules and fill in hold values (SPEC §3.4 hold value, §3.4.2, R22, R23, O5, O9, FM5).

    Args:
        w: Everything the plan reads.
        now_ms: Now.
        s: Plan settings.

    Returns:
        The PlanWorld. An ignored item is no lot, no fact and no mat (O9); a vendor-sold mat is bought from the
        vendor only and an owned unit of it is worth no more than the vendor price (R22).
    """
    lots = tuple(lot for lot in w.lots if lot.key not in w.ignore)
    facts = {k: f for k, f in w.facts.items() if k not in w.ignore}
    m = w.world.market
    market = craft.vendor_first(craft.Market({k: v for k, v in m.books.items() if k not in w.ignore},
                                             {k: v for k, v in m.vendor.items() if k not in w.ignore},
                                             {k: v for k, v in m.owned.items() if k not in w.ignore},
                                             hold_values(lots, facts, now_ms, s)))
    world = replace(w.world, market=market, held={(lot.char_id, lot.key): float(lot.qty) for lot in lots})   # C22
    vendor_sold = frozenset(m.vendor)
    # SPEC §3.3.3: an item flagged not selling is bought and crafted no more; your stock is still sold (O5).
    struggling = frozenset(k for k, f in w.facts.items() if f.record is not None and f.record.not_selling)
    no_craft = w.no_buy | w.ignore | vendor_sold | struggling
    post_facts = {k: replace(f, post_price=None, level=None) if k in vendor_sold else f for k, f in facts.items()}
    return PlanWorld(lots, facts, post_facts, world, vendor_sold, no_craft, no_craft | w.suspect)


def without_bought(f: SellFacts, units: float) -> SellFacts:
    """The item with the cheapest auctions totalling `units` gone (bought by crafts).

    Args:
        f: The item.
        units: Units bought from its book.

    Returns:
        A copy whose book lacks those auctions; unchanged when nothing was bought.
    """
    if units <= 0 or f.book is None:
        return f
    left: dict[tuple[int, int, int], int] = {}
    for price, stack, band in auctions(f.book, None, None):
        if units > 0:
            units -= stack
            continue
        left[(price, stack, band)] = left.get((price, stack, band), 0) + 1
    return replace(f, book=tuple((p, s, b, n) for (p, s, b), n in sorted(left.items())))


def bought_from(before: craft.Market, after: craft.Market) -> dict[int, float]:
    """AH units the crafts bought, per item.

    Args:
        before: The market before crafting.
        after: The market after.

    Returns:
        Item key -> units.
    """
    return {key: sum(u for _, u in levels) - sum(u for _, u in after.books.get(key, ()))
            for key, levels in before.books.items()}
