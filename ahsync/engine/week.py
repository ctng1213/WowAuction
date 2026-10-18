"""The whole weekly plan: your stock, crafts and flips allocated together (SPEC §3.4C).

Order (§3.4 hold value, §3.4C.4):
1. A first sell plan of everything you own gives each item's hold value: what one
   unit is worth under its own best sell plan.
2. Crafts use owned mats at that hold value, so a craft never beats selling the mats
   raw by accident; the units they use leave the stock.
3. The sell plan of the stock that is left.
4. Flips, from the auctions the crafts didn't buy.
One capacity ledger and one set of budgets run through all of it, so each owned unit,
each auction and each unit of a window's demand is spent once (PL4). Pure.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from ahsync.engine import craft, fees
from ahsync.engine.craft_plan import CraftWorld, craft_plan
from ahsync.engine.flips import FlipSettings, auctions, flip_plan
from ahsync.engine.plan import Lot, PlanAction, PlanSettings, SellFacts, sell_plan


@dataclass(frozen=True)
class WeekInputs:
    """Everything the weekly plan reads."""

    lots: tuple[Lot, ...]                     # your unbound stock per character
    facts: dict[int, SellFacts]               # item key -> what the plan knows (stock, products, flip candidates)
    world: CraftWorld                         # recipes and mat sources (its hold values are filled in here)
    budgets: dict[str, int]                   # character id -> copper it may spend
    total_gold: int                           # all your characters' gold
    no_buy: frozenset[int] = frozenset()      # never buy or craft (rules no_buy, ignore; not selling)
    ignore: frozenset[int] = frozenset()      # in no recommendation, not even the sell plan (rule ignore)


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


def without_used(lots: Sequence[Lot], used: dict[int, float]) -> list[Lot]:
    """Your stock after crafts took owned mats, taken from characters in id order.

    Args:
        lots: Your stock.
        used: Item key -> units the crafts used (may be fractional; rounded up).

    Returns:
        The lots that are left, empty ones dropped.
    """
    need = {key: -int(-units // 1) for key, units in used.items()}
    out = []
    for lot in sorted(lots, key=lambda lot: (lot.key, lot.char_id)):
        take = min(lot.qty, need.get(lot.key, 0))
        need[lot.key] = need.get(lot.key, 0) - take
        if lot.qty - take > 0:
            out.append(replace(lot, qty=lot.qty - take))
    return out


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


def _bought(before: craft.Market, after: craft.Market) -> dict[int, float]:
    """AH units the crafts bought, per item.

    Args:
        before: The market before crafting.
        after: The market after.

    Returns:
        Item key -> units.
    """
    return {key: sum(u for _, u in levels) - sum(u for _, u in after.books.get(key, ()))
            for key, levels in before.books.items()}


def week_plan(w: WeekInputs, now_ms: int, s: PlanSettings, fs: FlipSettings, max_depth: int) -> list[PlanAction]:
    """The weekly plan (SPEC §3.4C): crafts, the sell plan of the rest of your stock, then flips.

    Args:
        w: Everything the plan reads; its budgets are copied, not changed.
        now_ms: Now.
        s: Plan settings.
        fs: Flip settings (min_flip_margin, max_risk_pct).
        max_depth: Config max_craft_depth.

    Returns:
        Every action, crafts first, then the sell plan, then flips; each part in its own order.
    """
    lots = [lot for lot in w.lots if lot.key not in w.ignore]
    facts = {k: f for k, f in w.facts.items() if k not in w.ignore}
    m = w.world.market
    market = craft.vendor_first(craft.Market({k: v for k, v in m.books.items() if k not in w.ignore},
                                             {k: v for k, v in m.vendor.items() if k not in w.ignore},
                                             {k: v for k, v in m.owned.items() if k not in w.ignore},
                                             hold_values(lots, facts, now_ms, s)))   # an ignored item is no mat (O9)
    world = replace(w.world, market=market)
    budgets, ledger = dict(w.budgets), {}
    vendor_sold = set(m.vendor)               # anyone can buy these from a vendor: never posted (R22, R23)
    crafts, after = craft_plan(world, budgets, w.total_gold, now_ms, s, fs.max_risk_pct, ledger, max_depth,
                               exclude=set(w.no_buy | w.ignore) | vendor_sold)
    used = {k: world.market.owned.get(k, 0.0) - after.owned.get(k, 0.0) for k in world.market.owned}
    no_posts = {k: replace(f, post_price=None, level=None) if k in vendor_sold else f for k, f in facts.items()}
    sold = [a for a in sell_plan(without_used(lots, used), no_posts, now_ms, s, ledger)
            if not (a.key in vendor_sold and a.kind == "hold")]
    bought = _bought(world.market, after)
    flip_facts = {k: without_bought(f, bought.get(k, 0.0)) for k, f in facts.items()}
    flips = flip_plan(flip_facts, budgets, w.total_gold, now_ms, s, fs, ledger,
                      exclude=set(w.no_buy | w.ignore) | vendor_sold)      # a vendor sells it: no flip (R22)
    return crafts + sold + flips
