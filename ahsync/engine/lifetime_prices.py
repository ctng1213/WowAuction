"""Prices the auction lifetimes observe: where new listings were placed, and what sold auctions sold at (SPEC §2.4).

Pure. A listing's position against the book at first sight feeds the undercut share u(p) (BZ29); the price each sold
auction left at feeds the sold-price median (BZ52, BZ53), published beside the ask medians: an hourly close is the
cheapest ask each hour, so a high ask that sits unsold fills most hours, while a sold price counts only what was
bought (LESSONS 2026-11-07).
"""
from __future__ import annotations

from collections.abc import Iterable
from itertools import accumulate

from ahsync.engine.lifetimes import Lifetimes

UNITS_MILLI = 1000          # sold units are stored and compared in thousandths (SPEC §3.3 units-weighted median)


def new_listing_positions(lt: Lifetimes) -> dict[tuple[int, int], dict[int, dict[int, float]]]:
    """New listings' units by their position against the book at first sight (SPEC §2.4, §3.3.2, BZ29).

    Args:
        lt: From `lifetimes`.

    Returns:
        gap -> item key -> position -> units, the position being price ÷ the lowest other price, in thousandths
        rounded half up (floor(x × 1000 + 0.5), §3.3); a listing with no other auction of its key is left out.
    """
    out: dict[tuple[int, int], dict[int, dict[int, float]]] = {}
    for _, key, units, gap, price, lowest in lt.listed:
        if lowest is None:
            continue
        position = position_milli(price, lowest)
        by_position = out.setdefault(gap, {}).setdefault(key, {})
        by_position[position] = by_position.get(position, 0.0) + units
    return out


def position_milli(price: int, lowest: int) -> int:
    """A price's position against a lowest price, in thousandths (SPEC §3.3 rounding).

    Args:
        price: Copper per unit.
        lowest: The lowest other price, copper per unit (> 0).

    Returns:
        floor(price × 1000 ÷ lowest + 0.5), in integers only.
    """
    return (2 * price * 1000 + lowest) // (2 * lowest)


def sold_prices(lt: Lifetimes) -> dict[tuple[int, int], dict[int, dict[int, float]]]:
    """The units each gap sold, by the unit price they sold at (SPEC §2.4 Sold prices, BZ52).

    An ending's units counted sold, qty × (1 − expired), sold at its last sighting's price; a commodity cancel sold
    nothing (BZ40), nor does an ending with no price. A partial fill sold its units at its auction's price. Sales estimated before first sight (BZ45)
    have no price and are left out.

    Args:
        lt: From `lifetimes.lifetimes`.

    Returns:
        gap -> item key -> unit price copper -> units (float); items and prices with no units are left out.
    """
    sales = [(e.gap, e.key, e.price, e.qty * (1.0 - e.expired)) for e in lt.endings
             if not e.cancelled and e.price is not None]
    sales += [(gap, key, price, float(units)) for _, key, units, gap, price in lt.fills]
    out: dict[tuple[int, int], dict[int, dict[int, float]]] = {}
    for gap, key, price, units in sales:
        if units > 0.0:
            by_price = out.setdefault(gap, {}).setdefault(key, {})
            by_price[price] = by_price.get(price, 0.0) + units
    return out


def units_milli(units: float) -> int:
    """Units in thousandths, as stored (SPEC §3.3 rounding).

    Args:
        units: Units, float, ≥ 0.

    Returns:
        floor(units × 1000 + 0.5).
    """
    return int(units * UNITS_MILLI + 0.5)


def weighted_median(sold: Iterable[tuple[int, float]]) -> tuple[int | None, float]:
    """The units-weighted median of sold prices (SPEC §3.3, BZ53).

    Sorted by price, it is the lowest price whose cumulative units reach half the total; when they equal half
    exactly at a price, the floor of the mean of that price and the next higher one (the even-count rule). Units
    are compared in thousandths (units_milli), so the halfway test is exact.

    Args:
        sold: (unit price copper, units) pairs; a price may repeat.

    Returns:
        (median copper or None, total units); None and 0.0 when there are no units.
    """
    by_price: dict[int, int] = {}
    for price, units in sold:
        by_price[price] = by_price.get(price, 0) + units_milli(units)
    prices = sorted(p for p, u in by_price.items() if u > 0)
    cumulative = list(accumulate(by_price[p] for p in prices))
    total = cumulative[-1] if cumulative else 0
    if total == 0:
        return None, 0.0
    i = next(i for i, c in enumerate(cumulative) if 2 * c >= total)
    if 2 * cumulative[i] == total and i + 1 < len(prices):
        return (prices[i] + prices[i + 1]) // 2, total / UNITS_MILLI
    return prices[i], total / UNITS_MILLI
