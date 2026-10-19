"""The plan's dashboard pages: Today, Week and Flip payloads, built at refresh time (SPEC §3.5, §3.4).

The views come from engine.plan_views; this module only gathers their inputs: the new
plan, the week's kept history (done, missed, ...), item names and market activity. The
Today page's chance filter has a few fixed steps, each staged with its own rows, hidden
count and total, so the page never computes (SPEC §8.5).
"""
from __future__ import annotations

from ahsync.engine.plan import PlanAction
from ahsync.engine.plan_views import ItemLook, ViewAction, flip_view, today_view, week_view
from ahsync.engine.views import COLUMN_HELP, item_name, item_quality, round_half_up
from ahsync.history.reads import ItemInfo, suffix_names_of
from ahsync.intel.contract import ItemIntel, price_level

MIN_CHANCE_STEPS = (0, 25, 50, 75)        # the Today page's "Min chance" dropdown, percent


def view_actions(actions: list[PlanAction], rows: list[tuple], history: list[tuple]) -> list[ViewAction]:
    """The new plan and the kept history as view actions.

    Args:
        actions: The new plan, in plan_rows order.
        rows: plan_rows(actions, ...): their ids and parents.
        history: Kept actions (action_id, parent_id, kind, char_id, key, qty, price, slot_ms, expected_net, status).

    Returns:
        History first, then the new plan; history has no chance, peak or expected value.
    """
    parents = {r[13] for r in rows if r[13]} | {h[1] for h in history if h[1]}
    out = [ViewAction(h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], h[8] or 0.0, None, h[9],
                      in_chain=bool(h[1]) or h[0] in parents) for h in history]
    for a, r in zip(actions, rows):
        out.append(ViewAction(r[0], r[13], a.kind, a.char_id, a.key, a.qty, a.price, a.at_ms, a.net, a.p, "pending",
                              a.peak_ms, a.chain is not None, a.expected, a.source == "vendor"))
    return out


def item_looks(keys: set[int], stats: dict[int, ItemIntel], items: dict[int, ItemInfo],
               paid: dict[int, tuple[int, int]] | None = None, records: dict | None = None) -> dict[int, ItemLook]:
    """Names and market activity for the items the plan names.

    Args:
        keys: Item keys.
        stats: Item key -> intelligence.
        items: Item metadata.
        paid: Item key -> (units, total cost copper) from the cost basis (T7); None for none.
        records: Item key -> your sell record (SPEC §3.3.3, D42); None for none.

    Returns:
        Item key -> ItemLook; a variant missing from items is named from its base item and suffix (D60); an
        unknown item shows its key.
    """
    out = {}
    for key in sorted(keys):
        s = stats.get(key)
        name, quality = item_name(key, items, suffix_names_of(items)), item_quality(key, items)   # SPEC §2.2.1, D60
        basis = (paid or {}).get(key)
        paid_each = round_half_up(basis[1] / basis[0]) if basis and basis[0] else None
        record = (records or {}).get(key)
        out[key] = ItemLook(name, quality, s.conf if s else None, s.sold_per_day if s else None,
                            s.volume_source if s else None, s.units_on_ah if s else None, price_level(s) if s else None,
                            s.seasonality if s else None, paid_each, record, s.volume_kind, s.volume_unknown) if s else \
            ItemLook(name, quality, paid_each=paid_each, record=record)
    return out


def plan_pages(actions: list[ViewAction], stats: dict[int, ItemIntel], items: dict[int, ItemInfo], total_gold: int,
               now_ms: int, offset_ms: int, paid: dict[int, tuple[int, int]] | None = None,
               records: dict | None = None) -> dict[str, dict]:
    """The Today, Week and Flip payloads (SPEC §3.5 dashboard data).

    Args:
        actions: From view_actions.
        stats: Item key -> intelligence.
        items: Item metadata.
        total_gold: All your gold.
        now_ms: Now.
        offset_ms: Local time minus UTC.
        paid: Item key -> (units, total cost copper) from the cost basis, for "below what you paid" (T7).
        records: Item key -> your sell record, for the record line and Needs a decision (D42, D43).

    Returns:
        {"today": {"by_min": {"0": view, ...}, "help"}, "week": view, "flip": view}.
    """
    looks = item_looks({a.key for a in actions}, stats, items, paid, records)
    today = {str(step): today_view(actions, now_ms, offset_ms, step / 100, looks, total_gold)
             for step in MIN_CHANCE_STEPS}
    week = week_view(actions, now_ms, offset_ms, looks, total_gold)
    flips = flip_view([a for a in actions if a.status == "pending"], offset_ms, looks, total_gold)
    return {"today": {"by_min": today, "help": COLUMN_HELP}, "week": {**week, "help": COLUMN_HELP},
            "flip": {**flips, "help": COLUMN_HELP}}
