"""The On the AH page, built at refresh time (SPEC §3.5 On the AH, D18).

This module only gathers inputs: your stored auctions, the deposit of the post behind each
(when it was captured), the cost basis and the item's intelligence. engine.on_ah_view makes
every number and label.
"""
from __future__ import annotations

from ahsync.engine.on_ah_view import Listing, on_ah_view
from ahsync.engine.views import COLUMN_HELP
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel
from ahsync.recommend.plan_pages import item_looks
from ahsync.state.reads import StateReader


def on_ah_page(state: StateReader, char_ids: list[str], stats: dict[int, ItemIntel], items: dict[int, ItemInfo]) -> dict:
    """The On the AH payload (SPEC §3.5 dashboard data).

    Args:
        state: Database reads.
        char_ids: The market's characters.
        stats: Item key -> intelligence.
        items: Item metadata.

    Returns:
        {"rows", "tiles", "help"}.
    """
    paid = state.cost_basis()
    listings = []
    for char_id, key, price, stack, band, count, deposit in state.own_auctions(char_ids):
        s, basis = stats.get(key), paid.get(key)
        listings.append(Listing(char_id, key, price, stack, band, count,
                                deposit * count if deposit is not None else None,
                                basis[1] / basis[0] if basis and basis[0] else None,
                                s.cur if s else None, s.chance if s and not s.volume_unknown else None))
    looks = item_looks({x.key for x in listings}, stats, items)
    return {**on_ah_view(listings, looks), "help": COLUMN_HELP}
