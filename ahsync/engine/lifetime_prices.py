"""Prices the auction lifetimes observe: where new listings were placed, and what sold auctions sold at (SPEC §2.4).

Pure. A listing's position against the book at first sight feeds the undercut share u(p) (BZ29); the price each sold
auction left at feeds the sold-price median (BZ52, BZ53), published beside the ask medians: an hourly close is the
cheapest ask each hour, so a high ask that sits unsold fills most hours, while a sold price counts only what was
bought (LESSONS 2026-11-07).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import accumulate

from ahsync.engine.capacity import slot_of
from ahsync.engine.lifetimes import Lifetimes

UNITS_MILLI = 1000          # sold units are stored and compared in thousandths (SPEC §3.3 units-weighted median)
HOUR_MS = 3_600_000
LEVEL_MIN_UNITS = 3.0       # sold units in 30 days a sold level needs (SPEC §3.3 The price level)
CURVE_MIN_UNITS = 20.0      # weighted clean units a sold curve needs (the user, 2026-11-22)
LEVEL_HALF_LIFE_HOURS = 24  # a sold unit's weight halves every day
LEVEL_HOURS = 30 * 24       # the hours of sold prices a level reads
ASK_WEIGHTS = (4, 2, 1)     # the ask level's weights on med7, med14 and med30
CURVE_SHARES = (0, 10, 25, 50, 75, 90, 100)   # the sold curve's percentiles


@dataclass(frozen=True)
class SoldHour:
    """One stored price of one hour (`sold_prices_h1`, SPEC §2.4)."""

    hour_ms: int            # the clock hour of the snapshot that found the auctions gone, UTC ms
    price: int              # unit price, copper
    units: float            # units counted sold at it
    clean: float            # of them, from endings that cannot have expired (expired 0) and fills


@dataclass(frozen=True)
class Level:
    """An item's price level and its bounds (SPEC §3.3 The price level)."""

    level: int | None                     # copper; None without sales or medians
    source: str | None                    # "sold" or "asks"; None with no level
    units: float                          # sold units in the 30 days, unweighted
    curve: tuple[int, ...] | None         # copper at CURVE_SHARES, with CURVE_MIN_UNITS weighted clean units
    low: int | None                       # the low bound, copper
    high: int | None                      # the high bound, copper


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
    return _by_gap([(e.gap, e.key, e.price, e.qty * (1.0 - e.expired)) for e in lt.endings
                    if not e.cancelled and e.price is not None]
                   + [(gap, key, price, float(units)) for _, key, units, gap, price in lt.fills])


def clean_sold_prices(lt: Lifetimes) -> dict[tuple[int, int], dict[int, dict[int, float]]]:
    """Of `sold_prices`, the units that cannot have expired (SPEC §2.4, BZ68; the user, 2026-11-22).

    An ending with any chance of having expired in its gap (`expired` > 0) may be a listing nobody bought, mostly at
    the dearest asks, so the sold curve leaves it out; a partial fill is a certain sale.

    Args:
        lt: From `lifetimes.lifetimes`.

    Returns:
        gap -> item key -> unit price copper -> units (float), as `sold_prices` with only expired-0 endings and fills.
    """
    return _by_gap([(e.gap, e.key, e.price, float(e.qty)) for e in lt.endings
                    if not e.cancelled and e.price is not None and e.expired == 0.0]
                   + [(gap, key, price, float(units)) for _, key, units, gap, price in lt.fills])


def _by_gap(sales: Sequence[tuple[tuple[int, int], int, int, float]]) -> dict[tuple[int, int], dict[int, dict[int, float]]]:
    """Sold units summed per gap, item and price.

    Args:
        sales: (gap, item key, unit price copper, units) per sale.

    Returns:
        gap -> item key -> unit price -> units; items and prices with no units are left out.
    """
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


def ask_level(med7: int | None, med14: int | None, med30: int | None) -> int | None:
    """The ask level: the 7, 14 and 30-day medians weighted 4:2:1 over the ones known (SPEC §3.3, BZ69).

    Args:
        med7: 7-day median close, copper, or None.
        med14: 14-day median close, copper, or None.
        med30: 30-day median close, copper, or None.

    Returns:
        Copper, floored; None when none is known.
    """
    parts = [(w, v) for w, v in zip(ASK_WEIGHTS, (med7, med14, med30)) if v is not None]
    return sum(w * v for w, v in parts) // sum(w for w, _ in parts) if parts else None


def weighted_sold(sold: Sequence[SoldHour], now_ms: int, how: Sequence[float] | None, clean: bool
                  ) -> list[tuple[int, float]]:
    """The last 30 days' sold prices as the level weighs them (SPEC §3.3 The price level).

    Args:
        sold: The item's stored prices by hour.
        now_ms: The document's time, UTC ms; its hour is age 0.
        how: 168 slot ratios to divide each price by (`level_divides_by_pattern` on), or None to keep it as sold.
        clean: Weigh only the clean units (the curve) instead of all of them (the level).

    Returns:
        (price copper, its units × 0.5 ^ (age in hours ÷ 24)), the price divided by its hour's `how` and rounded
        half up when `how` is given; rows outside the 30 days and rows with no units are left out.
    """
    now_hour = now_ms // HOUR_MS
    out = []
    for row in sold:
        age = now_hour - row.hour_ms // HOUR_MS
        units = row.clean if clean else row.units
        if not 0 <= age < LEVEL_HOURS or units <= 0:
            continue
        price = row.price
        if how is not None:
            price = int(price / how[slot_of(row.hour_ms // HOUR_MS)] + 0.5)
        out.append((price, units * 0.5 ** (age / LEVEL_HALF_LIFE_HOURS)))
    return out


def sold_curve(weighted: Sequence[tuple[int, float]]) -> tuple[int, ...] | None:
    """The sold curve: the weighted prices' CURVE_SHARES percentiles (SPEC §3.3 Bounds, BZ70).

    Args:
        weighted: (price copper, weighted units), from `weighted_sold` over the clean units.

    Returns:
        Per share, the lowest price whose cumulative weighted units reach that share of the total (compared in
        thousandths of a unit, so a share is reached exactly); None when the weights' sum, rounded once to
        thousandths, is under CURVE_MIN_UNITS.
    """
    if units_milli(sum(units for _, units in weighted)) < units_milli(CURVE_MIN_UNITS):
        return None
    by_price: dict[int, int] = {}
    for price, units in weighted:
        by_price[price] = by_price.get(price, 0) + units_milli(units)
    prices = sorted(p for p, u in by_price.items() if u > 0)
    cumulative = list(accumulate(by_price[p] for p in prices))
    total = cumulative[-1]
    return tuple(prices[next(i for i, c in enumerate(cumulative) if 100 * c >= share * total)] if share else prices[0]
                 for share in CURVE_SHARES)


def price_level(sold: Sequence[SoldHour], now_ms: int, how: Sequence[float] | None,
                medians: tuple[int | None, int | None, int | None], p10_30: int | None) -> Level:
    """An item's price level and bounds (SPEC §3.3 The price level; BZ69, BZ70).

    A sold level at LEVEL_MIN_UNITS sold units in the 30 days: the units-weighted median (§3.3's rule) of the
    weighted prices, else the ask level. With a sold curve the bounds are its 10th and the larger of its 90th and
    the level; otherwise `p10_30` (the level when unknown or above it) and the level.

    Args:
        sold: The item's stored prices by hour (`sold_prices_h1`).
        now_ms: The document's time, UTC ms.
        how: 168 ratios to divide the prices by (`level_divides_by_pattern` on), or None (off, the default).
        medians: (med7, med14, med30), copper or None each.
        p10_30: The 30-day 10th percentile of closes, copper, or None.

    Returns:
        The Level; all None (units 0 or the sold units) when there is neither a sold level nor a median.
    """
    milli = sum(units_milli(row.units) for row in sold if 0 <= now_ms // HOUR_MS - row.hour_ms // HOUR_MS < LEVEL_HOURS)
    units = milli / UNITS_MILLI                    # summed in stored thousandths, so exactly 3 units is 3 (BZ69)
    level, source = None, None
    if milli >= units_milli(LEVEL_MIN_UNITS):
        level, _ = weighted_median(weighted_sold(sold, now_ms, how, clean=False))
        source = "sold" if level is not None else None
    if level is None:
        level = ask_level(*medians)
        source = "asks" if level is not None else None
    curve = sold_curve(weighted_sold(sold, now_ms, how, clean=True)) if source == "sold" else None
    return level_with_bounds(level, source, units, curve, p10_30)


def level_with_bounds(level: int | None, source: str | None, units: float, curve: tuple[int, ...] | None,
                      p10_30: int | None) -> Level:
    """A level with the bounds §3.3 gives it (BZ70).

    Args:
        level: Copper, or None.
        source: "sold" or "asks", or None.
        units: The sold units behind it.
        curve: The sold curve at CURVE_SHARES, or None.
        p10_30: The 30-day 10th percentile of closes, copper, or None.

    Returns:
        The Level: with a curve, its 10th and the larger of its 90th and the level; otherwise p10_30 (the level when
        unknown or above it) and the level; all None but the units without a level.
    """
    if level is None:
        return Level(None, None, units, None, None, None)
    if curve is not None:
        return Level(level, source, units, curve, curve[1], max(curve[5], level))
    low = p10_30 if p10_30 is not None and p10_30 <= level else level
    return Level(level, source, units, None, low, level)
