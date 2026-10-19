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
from ahsync.engine.views import age_text, market_activity, money, round_half_up, signed_money

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
    p: float | None                # chance it sells in the time it has left (D53), or None
    end_ms: int | None = None      # its soonest end, ms; None when the addon didn't capture it (S16)
    below: tuple[int, int, int] | None = None   # (units, lowest, highest price) listed below yours now; None: unknown


def _position(x: Listing) -> tuple[str, str]:
    """Where your price stands now (D53).

    Args:
        x: The auctions.

    Returns:
        ("cheapest" | "undercut by 12 at 38c–40c" | "undercut: 39s", "pos" | "neg").
    """
    if x.below is not None:
        units, lo, hi = x.below
        if not units:
            return "cheapest", "pos"
        prices = money(lo) if lo == hi else f"{money(lo)}–{money(hi)}"
        return f"undercut by {units} at {prices}", "neg"
    if x.cheapest is None or x.price <= x.cheapest:
        return "cheapest", "pos"
    return f"undercut: {money(x.cheapest)}", "neg"


def _time_left(x: Listing, now_ms: int) -> str:
    """The Time left cell: from its end when known, else its band (D53).

    Args:
        x: The auctions.
        now_ms: Now.

    Returns:
        "ends in 5 h", "ending", or a band text.
    """
    if x.end_ms is None:
        return BAND_TEXT.get(x.band, "—")
    return "ending" if x.end_ms <= now_ms else f"ends in {age_text(x.end_ms - now_ms)}"


def _row(x: Listing, look: ItemLook, now_ms: int) -> tuple[dict, dict]:
    """One row's cells and its money for the tiles.

    Args:
        x: The auctions.
        look: The item.
        now_ms: Now, for the time left.

    Returns:
        (row, {"listed", "cost", "profit", "deposit"} in copper).
    """
    units = x.stack * x.count
    listed = x.price * units
    cost = (x.paid_each or 0.0) * units
    profit = listed * (1 - fees.AH_CUT) - cost
    position, position_class = _position(x)
    m = market_activity(x.p, look.conf, look.sold_per_day, look.source, look.units_on_ah, look.kind)
    row = {
        "key": str(x.key), "item": look.name, "quality": look.quality, "character": x.char_id.split("-")[0],
        "qty": units, "listed_each": money(x.price), "listed_total": money(listed),
        "cost_each": money(x.paid_each or 0), "cost_total": money(cost),
        "cost_note": "before tracking" if x.paid_each is None else "",
        "deposit": money(x.deposit), "profit": signed_money(profit), "profit_class": "pos" if profit > 0 else "neg",
        "time_left": _time_left(x, now_ms), "position": position, "position_class": position_class,
        "chance": m.chance, "chance_note": m.chance_note, "confidence": m.confidence, "dot": m.dot,
        "sales": m.sales, "supply": m.supply, "days": m.days,
        "sort": {"qty": units, "listed": listed, "cost": round_half_up(cost), "profit": round_half_up(profit),
                 "chance": m.percent if m.percent is not None else -1,
                 "band": x.end_ms if x.end_ms is not None else x.band * 10**13},
    }
    return row, {"listed": listed, "cost": cost, "profit": profit, "deposit": x.deposit or 0}


def on_ah_view(listings: Sequence[Listing], looks: dict[int, ItemLook], now_ms: int = 0) -> dict:
    """The On the AH page (SPEC §3.5, D18, D53).

    Args:
        listings: Your auctions on the market.
        looks: Item key -> name and market activity.
        now_ms: Now, for the time left.

    Returns:
        {"rows": [...] best profit first (ties: item, character, price), "tiles": [[label, value, note]]}.
    """
    rows, sums, chars = [], {"listed": 0.0, "cost": 0.0, "profit": 0.0, "deposit": 0}, set()
    for x in listings:
        row, money_of = _row(x, looks[x.key], now_ms)
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
