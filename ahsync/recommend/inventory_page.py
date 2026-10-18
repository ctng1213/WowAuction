"""The Inventory page and its item details, built at refresh time (SPEC §3.5 Inventory, item detail).

This module only gathers inputs: your holdings and cost basis, the new plan, the recipes
behind its crafts and the items' intelligence. engine.inventory_view and engine.item_detail
make every number and label.
"""
from __future__ import annotations

from ahsync.engine.inventory_view import Holding, InventoryWorld, inventory_view
from ahsync.engine.item_detail import DetailFacts, item_detail
from ahsync.engine.patterns import Window
from ahsync.engine.plan_views import ItemLook, ViewAction
from ahsync.engine.profile import unpack
from ahsync.engine.views import COLUMN_HELP, round_half_up
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import IntelWindow, ItemIntel
from ahsync.recommend.plan_pages import item_looks
from ahsync.state.reads import StateReader


def windows_of(windows: tuple[IntelWindow, ...]) -> tuple[Window, ...]:
    """Intelligence windows as engine windows.

    Args:
        windows: From ItemIntel.sell_windows.

    Returns:
        patterns.Window, same order; the score sum isn't stored, so it is 0.
    """
    return tuple(Window(w.start_slot, w.hours, 0.0, w.how_avg, w.weeks, w.best_slot) for w in windows)


def detail_facts(look: ItemLook, s: ItemIntel) -> DetailFacts:
    """An item's detail facts from its intelligence.

    Args:
        look: Its name and quality.
        s: Its intelligence.

    Returns:
        The DetailFacts.
    """
    return DetailFacts(look.name, look.quality, s.med30, s.trend7, s.sold_per_day, s.volume_source,
                       min(7, sum(s.days_by_source.values())), s.units_on_ah, s.listed_per_day, s.conf, s.seasonality,
                       unpack(s.how, s.how_weeks), unpack(s.sales_how, s.sales_weeks),
                       unpack(s.supply_how, s.supply_weeks), windows_of(s.sell_windows))


def craft_uses(state: StateReader, char_ids: list[str], actions: list[ViewAction],
               names: dict[int, ItemLook]) -> dict[int, str]:
    """Which planned craft each reagent is for (D26).

    Args:
        state: Character state.
        char_ids: The market's characters.
        actions: The new plan.
        names: Item key -> look, for the products.

    Returns:
        Reagent key -> the product name (the first by name when several crafts use it).
    """
    planned = {(a.char_id, a.key) for a in actions if a.kind == "craft"}
    out: dict[int, str] = {}
    for recipe in state.known_recipes(char_ids):
        if recipe.product is None or not any((c, recipe.product) in planned for c in recipe.crafters):
            continue
        name = names[recipe.product].name if recipe.product in names else str(recipe.product)
        for key, _ in recipe.reagents:
            out[key] = min(out.get(key, name), name)
    return out


def inventory_page(state: StateReader, char_ids: list[str], actions: list[ViewAction], stats: dict[int, ItemIntel],
                   items: dict[int, ItemInfo], offset_ms: int) -> dict:
    """The Inventory payload (SPEC §3.5 dashboard data).

    Args:
        state: Character state.
        char_ids: The market's characters.
        actions: The new plan (pending actions).
        stats: Item key -> intelligence.
        items: Item metadata.
        offset_ms: Local time minus UTC.

    Returns:
        {"rows", "tiles", "details": {item key: item detail}, "help"}.
    """
    paid = state.cost_basis()
    holdings = [Holding(c, where, key, qty, bound,
                        round_half_up(paid[key][1] / paid[key][0]) if key in paid else None)
                for c, where, key, qty, bound in state.holdings(char_ids)]
    keys = {h.key for h in holdings}
    looks = item_looks(keys | {a.key for a in actions}, stats, items)
    values = {}
    for key in keys:
        s, info = stats.get(key), items.get(key)
        if s is not None and s.sell_value is not None:
            values[key] = s.sell_value
        elif info is not None and info.vendor_sell:
            values[key] = float(info.vendor_sell)
    world = InventoryWorld(actions, looks, {k: windows_of(stats[k].sell_windows) for k in keys if k in stats},
                           values, craft_uses(state, char_ids, actions, looks), state.item_rules(), offset_ms)
    view = inventory_view(holdings, world)
    details = {str(k): item_detail(detail_facts(looks[k], stats[k]), offset_ms) for k in sorted(keys) if k in stats}
    return {**view, "details": details, "help": COLUMN_HELP}
