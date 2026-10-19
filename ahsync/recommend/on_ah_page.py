"""The On the AH page, built at refresh time (SPEC §3.5 On the AH, D18).

This module only gathers inputs: your stored auctions, the deposit of the post behind each
(when it was captured), the cost basis and the item's intelligence. engine.on_ah_view makes
every number and label.
"""
from __future__ import annotations

import math
from dataclasses import replace

from ahsync.config import Config, market_settings
from ahsync.engine import sellprob
from ahsync.engine.on_ah_view import Listing, on_ah_view
from ahsync.engine.sellprob import MarketSettings, Supply
from ahsync.engine.views import COLUMN_HELP
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel
from ahsync.recommend.plan_pages import item_looks
from ahsync.state.reads import StateReader

HOUR_MS = 3_600_000
BAND_LEFT_MS = {1: 0, 2: HOUR_MS // 2, 3: 2 * HOUR_MS, 4: 8 * HOUR_MS}   # no end known: the band's shortest time


def _chance_left(s: ItemIntel, price: int, units: int, left_ms: int, now_ms: int, settings: MarketSettings) -> float:
    """The chance the units still up sell in the time they have left (D53).

    Args:
        s: The item's intelligence.
        price: Their price each, copper.
        units: Units up.
        left_ms: Time left, ms.
        now_ms: Now.
        settings: Market defaults; the window is cut to the time left.

    Returns:
        P in [0, 1]; 0 once the time is up.
    """
    if left_ms <= 0:
        return 0.0
    hours = max(1, math.ceil(left_ms / HOUR_MS))
    supply = Supply(s.sold_per_day, s.listed_per_day, s.book or None, s.cur, s.cur_qty)
    return sellprob.posting_chance(supply, price, units, now_ms, replace(settings, sell_window_hours=hours))[0]


def _below(s: ItemIntel | None, price: int) -> tuple[int, int, int] | None:
    """What is listed below a price in the item's newest book (D53).

    Args:
        s: The item's intelligence, or None.
        price: Your price each, copper.

    Returns:
        (units, lowest, highest price); None without a book.
    """
    if s is None or not s.book:
        return None
    cheaper = [(p, stack * count) for p, stack, _, count in s.book if p < price]
    if not cheaper:
        return (0, 0, 0)
    return (sum(u for _, u in cheaper), min(p for p, _ in cheaper), max(p for p, _ in cheaper))


def on_ah_page(state: StateReader, char_ids: list[str], stats: dict[int, ItemIntel], items: dict[int, ItemInfo],
               now_ms: int = 0, cfg: Config | None = None) -> dict:
    """The On the AH payload (SPEC §3.5 dashboard data, D53).

    Args:
        state: Database reads.
        char_ids: The market's characters.
        stats: Item key -> intelligence.
        items: Item metadata.
        now_ms: Now, for the time left.
        cfg: Config (market defaults for the chance); None uses the defaults.

    Returns:
        {"rows", "tiles", "help"}.
    """
    paid = state.cost_basis()
    settings = market_settings(cfg) if cfg is not None else MarketSettings()
    listings = []
    for char_id, key, price, stack, band, count, deposit, end_ms in state.own_auctions(char_ids):
        s, basis = stats.get(key), paid.get(key)
        p = None
        if s is not None and not s.volume_unknown:
            left = end_ms - now_ms if end_ms is not None else BAND_LEFT_MS.get(band, HOUR_MS)
            p = _chance_left(s, price, stack * count, left, now_ms, settings)
        listings.append(Listing(char_id, key, price, stack, band, count,
                                deposit * count if deposit is not None else None,
                                basis[1] / basis[0] if basis and basis[0] else None,
                                s.cur if s else None, p, end_ms, _below(s, price)))
    looks = item_looks({x.key for x in listings}, stats, items)
    return {**on_ah_view(listings, looks, now_ms), "help": COLUMN_HELP}
