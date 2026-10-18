"""The Inventory tab: what you own, what you paid, what it's worth and its plan (SPEC §3.5, §3.4).

Rows are per character, place and item (bags, bank, mail; auctions are on On the AH).
An item's worth is counted once across all its rows, then split by quantity:
- the expected value of its planned posts, disenchants and vendor sales;
- plus its other units (held for later, or used by a planned craft) at their hold value.
A disenchant may be planned for another character, the enchanter (SPEC §3.4A); it shows
on the owner's row with who does it. Bound and ignored items are never planned (R10).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ahsync.engine.item_detail import windows_short
from ahsync.engine.patterns import Window
from ahsync.engine.plan_views import ItemLook, ViewAction, clock
from ahsync.engine.views import market_activity, money, round_half_up

SALE_KINDS = ("post", "de", "vendor", "hold")
VALUED_KINDS = ("post", "de", "vendor")       # holds carry no expected value: their units count at hold value


@dataclass(frozen=True)
class Holding:
    """Units of one item in one place on one character (SPEC §3.2 inventory)."""

    char_id: str
    where: str                    # "bags" | "bank" | "mail"
    key: int
    qty: int
    bound: bool
    paid_each: int | None = None  # average cost per unit, copper; None = no cost basis (before tracking)


@dataclass(frozen=True)
class InventoryWorld:
    """Everything the Inventory tab needs besides the holdings."""

    actions: Sequence[ViewAction]         # the new plan (pending)
    looks: dict[int, ItemLook]            # item key -> name and market activity
    windows: dict[int, tuple[Window, ...]]   # item key -> selling windows, best first
    values: dict[int, float]              # item key -> hold value per unit, float copper
    uses: dict[int, str]                  # item key -> the product of a planned craft that uses it
    rules: dict[int, str]                 # item key -> live rule ("ignore", "no_buy", "keep")
    offset_ms: int = 0                    # local time minus UTC
    reserves: dict[tuple[str, int], int] = field(default_factory=dict)   # (char, key) -> units kept; -1 all


def _name(char_id: str) -> str:
    """A character's display name.

    Args:
        char_id: "Name-Realm".

    Returns:
        "Name".
    """
    return char_id.split("-")[0]


def _action_text(a: ViewAction, owner: str, held: int, offset_ms: int) -> str:
    """One planned action on an Inventory row (D25, D26, D28).

    Args:
        a: The action.
        owner: The row's character id.
        held: Units the character holds of the item.
        offset_ms: Local time minus UTC.

    Returns:
        E.g. "Post Sat 10:00 at 55c (18:00 peak)", "Post 6 now at 12s", "Disenchant (Brugan)".
    """
    count = f" {a.qty}" if a.qty != held and a.char_id == owner else ""
    if a.kind == "post":
        if a.peak_ms is None:
            return f"Post{count} now at {money(a.price)}"
        day, hour = clock(a.at_ms, offset_ms)
        return f"Post{count} {day} {hour} at {money(a.price)} ({clock(a.peak_ms, offset_ms)[1]} peak)"
    if a.kind == "de":
        return f"Disenchant{count}" + (f" ({_name(a.char_id)})" if a.char_id != owner else "")
    return {"vendor": "Vendor", "hold": "Hold for a later week"}[a.kind] + count


@dataclass(frozen=True)
class _Plan:
    """An Inventory row's plan cell."""

    text: str
    kind: str                     # filter value: post | de | vendor | hold | craft | none | bound | ignored | kept
    p: float | None               # chance of its first post


def _plan(h: Holding, held: int, mine: list[ViewAction], w: InventoryWorld) -> _Plan:
    """The plan cell of one character's holding of an item (D25-D28).

    Args:
        h: The holding.
        held: Units the character holds of the item, all places together.
        mine: The item's sale actions for this character's stock, in time order.
        w: The world.

    Returns:
        The plan cell.
    """
    if h.bound:
        return _Plan("Bound · not planned", "bound", None)
    if w.rules.get(h.key) == "ignore":
        return _Plan("Ignored · not planned", "ignored", None)
    kept = w.reserves.get((h.char_id, h.key), 0)
    if kept == -1:                                  # SPEC §3.4.3, D40
        return _Plan("Kept for yourself", "kept", None)
    parts = [_action_text(a, h.char_id, held, w.offset_ms) for a in mine]
    left = held - kept - sum(a.qty for a in mine)
    if left > 0 and h.key in w.uses:
        parts.append(f"{left} for crafting {w.uses[h.key]}" if mine else f"For crafting {w.uses[h.key]}")
    if not parts:
        return _Plan(f"Keeping {kept} for yourself" if kept else "Not planned", "kept" if kept else "none", None)
    posts = [a.p for a in mine if a.kind == "post"]
    note = [f"Keeping {kept} for yourself"] if kept else []
    return _Plan(" · ".join(note + parts), mine[0].kind if mine else "craft", posts[0] if posts else None)


def _worth_by_key(holdings: Sequence[Holding], w: InventoryWorld) -> dict[int, float | None]:
    """Each item's worth across all its unbound rows (module docstring).

    Args:
        holdings: All holdings.
        w: The world.

    Returns:
        Item key -> float copper, or None when nothing gives it a value.
    """
    units: dict[int, int] = {}
    for h in holdings:
        if not h.bound:
            units[h.key] = units.get(h.key, 0) + h.qty
    out: dict[int, float | None] = {}
    for key, total in units.items():
        valued = [a for a in w.actions if a.key == key and a.kind in VALUED_KINDS and not a.in_chain]
        rest = max(0, total - sum(a.qty for a in valued))
        value_each = w.values.get(key)
        if not valued and value_each is None:
            out[key] = None
        else:
            out[key] = sum(a.expected for a in valued) + rest * (value_each or 0.0)
    return out


def _sale_actions(key: int, owner: str, holders: set[str], actions: Sequence[ViewAction]) -> list[ViewAction]:
    """The sale actions that apply to one character's stock of an item.

    Args:
        key: Item key.
        owner: The character.
        holders: Everyone holding the item unbound.
        actions: The plan.

    Returns:
        Its own posts, vendor sales and holds, plus disenchants by someone who doesn't hold
        the item (an enchanter working on your stock), in time order.
    """
    out = [a for a in actions if a.key == key and a.kind in SALE_KINDS and not a.in_chain
           and (a.char_id == owner or (a.kind == "de" and a.char_id not in holders))]
    return sorted(out, key=lambda a: (a.at_ms, a.kind, a.action_id))


def _row(h: Holding, plan: _Plan, worth: float | None, w: InventoryWorld) -> dict:
    """One Inventory row's cells.

    Args:
        h: The holding.
        plan: Its plan cell.
        worth: Its share of the item's worth, float copper, or None.
        w: The world.

    Returns:
        Row dict with display cells and numeric sort keys.
    """
    look = w.looks[h.key]
    m = market_activity(plan.p, look.conf, look.sold_per_day, look.source, look.units_on_ah)
    paid_each = h.paid_each or 0
    return {
        "key": str(h.key), "item": look.name, "quality": look.quality, "character": _name(h.char_id),
        "where": h.where, "qty": h.qty, "paid_each": money(paid_each), "paid_total": money(paid_each * h.qty),
        "paid_note": "before tracking" if h.paid_each is None else "", "worth": money(worth),
        "windows": windows_short(w.windows.get(h.key, ()), w.offset_ms), "plan": plan.text, "plan_kind": plan.kind,
        "chance": m.chance, "chance_note": m.chance_note, "confidence": m.confidence, "dot": m.dot,
        "sales": m.sales, "supply": m.supply, "days": m.days,
        "sort": {"qty": h.qty, "paid": paid_each * h.qty, "worth": round_half_up(worth) if worth is not None else -1,
                 "chance": m.percent if m.percent is not None else -1, "sales": m.sales_tenths,
                 "supply": m.supply_units, "days": m.days_tenths},
    }


def inventory_view(holdings: Sequence[Holding], w: InventoryWorld) -> dict:
    """The Inventory tab (SPEC §3.5, D25-D29).

    Args:
        holdings: Every character's bags, bank and mail on the market.
        w: The plan, item facts and rules.

    Returns:
        {"rows": [...] best worth first (ties: item, character, place), "tiles": [[label, value, note]]}.
    """
    held: dict[tuple[str, int], int] = {}
    holders: dict[int, set[str]] = {}
    for h in holdings:
        if not h.bound:
            held[(h.char_id, h.key)] = held.get((h.char_id, h.key), 0) + h.qty
            holders.setdefault(h.key, set()).add(h.char_id)
    worth_by_key = _worth_by_key(holdings, w)
    units = {}
    for (_, key), qty in held.items():
        units[key] = units.get(key, 0) + qty
    rows, worth_total, paid_total, unplanned = [], 0.0, 0, set()
    for h in holdings:
        mine = [] if h.bound else _sale_actions(h.key, h.char_id, holders[h.key], w.actions)
        plan = _plan(h, held.get((h.char_id, h.key), 0), mine, w)
        key_worth = None if plan.kind in ("bound", "ignored") else worth_by_key.get(h.key)
        worth = None if key_worth is None else key_worth * h.qty / units[h.key]
        if plan.kind in ("bound", "ignored"):
            unplanned.add(h.key)
        worth_total += worth or 0.0
        paid_total += (h.paid_each or 0) * h.qty
        rows.append((-(worth if worth is not None else -1.0), w.looks[h.key].name, h.char_id, h.where, h.key,
                     _row(h, plan, worth, w)))
    rows.sort(key=lambda r: r[:5])
    tiles = [["Worth", money(worth_total), "under each item's plan"],
             ["Paid", money(paid_total), "found items and items from before tracking count as 0"],
             ["Not planned", f"{len(unplanned)} items", "bound or ignored"]]
    return {"rows": [r[5] for r in rows], "tiles": tiles}
