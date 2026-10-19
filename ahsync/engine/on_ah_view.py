"""The On the AH page: every auction you have up, what it cost you and what it makes if it sells (SPEC §3.5, D18).

Each row is one group of identical auctions (character, item, price, stack, time-left band):
- listed each and total; cost each and total from the cost basis (0 for items you found or held
  before tracking, T11, T12); the deposit, when the post that made it was captured;
- profit if it sells = listed total × (1 − cut) − cost total (the deposit comes back on a sale);
- time left as its band, and its position: "cheapest" at or below the cheapest listing, else
  "undercut: <cheapest>".
The tiles are the sums of their columns (D18). Pure.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ahsync.engine import fees
from ahsync.engine.plan_views import ItemLook
from ahsync.engine.views import market_activity, money, round_half_up, signed_money

BAND_TEXT = {1: "< 30 min", 2: "30 min – 2 h", 3: "2 – 8 h", 4: "8 – 24 h"}   # Forever's 2/8/24 h (Classic bands)


@dataclass(frozen=True)
class Listing:
    """Identical auctions of yours: one character, item, price, stack and time-left band."""

    char_id: str
    key: int
    price: int                     # each, copper
    stack: int
    band: int                      # 1-4
    count: int                     # auctions
    deposit: int | None            # copper for all of them; None when the post wasn't captured
    paid_each: float | None        # cost basis per unit; None before tracking (counts 0)
    cheapest: int | None           # the item's cheapest listing now (yours included), copper
    p: float | None                # chance it sells (posting now), or None


def _row(x: Listing, look: ItemLook) -> tuple[dict, dict]:
    """One row's cells and its money for the tiles.

    Args:
        x: The auctions.
        look: The item.

    Returns:
        (row, {"listed", "cost", "profit", "deposit"} in copper).
    """
    units = x.stack * x.count
    listed = x.price * units
    cost = (x.paid_each or 0.0) * units
    profit = listed * (1 - fees.AH_CUT) - cost
    if x.cheapest is None or x.price <= x.cheapest:
        position, position_class = "cheapest", "pos"
    else:
        position, position_class = f"undercut: {money(x.cheapest)}", "neg"
    m = market_activity(x.p, look.conf, look.sold_per_day, look.source, look.units_on_ah)
    row = {
        "key": str(x.key), "item": look.name, "quality": look.quality, "character": x.char_id.split("-")[0],
        "qty": units, "listed_each": money(x.price), "listed_total": money(listed),
        "cost_each": money(x.paid_each or 0), "cost_total": money(cost),
        "cost_note": "before tracking" if x.paid_each is None else "",
        "deposit": money(x.deposit), "profit": signed_money(profit), "profit_class": "pos" if profit > 0 else "neg",
        "time_left": BAND_TEXT.get(x.band, "—"), "position": position, "position_class": position_class,
        "chance": m.chance, "chance_note": m.chance_note, "confidence": m.confidence, "dot": m.dot,
        "sales": m.sales, "supply": m.supply, "days": m.days,
        "sort": {"qty": units, "listed": listed, "cost": round_half_up(cost), "profit": round_half_up(profit),
                 "chance": m.percent if m.percent is not None else -1, "band": x.band},
    }
    return row, {"listed": listed, "cost": cost, "profit": profit, "deposit": x.deposit or 0}


def on_ah_view(listings: Sequence[Listing], looks: dict[int, ItemLook]) -> dict:
    """The On the AH page (SPEC §3.5, D18).

    Args:
        listings: Your auctions on the market.
        looks: Item key -> name and market activity.

    Returns:
        {"rows": [...] best profit first (ties: item, character, price), "tiles": [[label, value, note]]}.
    """
    rows, sums, chars = [], {"listed": 0.0, "cost": 0.0, "profit": 0.0, "deposit": 0}, set()
    for x in listings:
        row, money_of = _row(x, looks[x.key])
        rows.append((-money_of["profit"], row["item"], x.char_id, x.price, x.key, row))
        for k in sums:
            sums[k] += money_of[k]
        chars.add(x.char_id)
    rows.sort(key=lambda r: r[:5])
    auctions = sum(x.count for x in listings)
    tiles = [["Listed value", money(sums["listed"]), f"{auctions} auctions · {len(chars)} characters"],
             ["Cost to you", money(sums["cost"]), "bought + crafted; found items 0"],
             ["Profit if all sell", signed_money(sums["profit"]), "after the 5% cut"],
             ["Deposits at stake", money(sums["deposit"]), "lost only if they expire"]]
    return {"rows": [r[5] for r in rows], "tiles": tiles}
