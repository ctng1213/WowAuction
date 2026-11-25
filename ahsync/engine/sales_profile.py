"""An item's sales profile: commodity, busy, thin or rare gear (SPEC §3.3 Sales profiles, P32, P33, PI22).

The profile is one classification every rule that depends on an item's kind reads (plan `sales-profiles-proposal.md`,
the user, 2026-11-25). Only the busy line is held: an item becomes or stops being busy only once the new status held
on 3 consecutive days, so an item whose sold curve flickers doesn't flip the rules that read it (the demand spread,
P31). Pure: data in, a word out.
"""
from __future__ import annotations

from collections.abc import Sequence

from ahsync.engine.lifetime_prices import (CURVE_MIN_UNITS, HOUR_MS, LEVEL_HALF_LIFE_HOURS, LEVEL_HOURS, SoldHour,
                                          units_milli)

COMMODITY, BUSY, THIN, RARE = "commodity", "busy", "thin", "rare"
PROFILES = (COMMODITY, BUSY, THIN, RARE)
DAY_MS = 86_400_000
HOLD_DAYS = 3                   # a new busy status counts once it held this many consecutive days (the user, 2026-11-25)
HOLD_LOOKBACK_DAYS = 9          # days looked back for such a run; none in them reads the status now
THIN_MIN_SOLD_PER_DAY = 1.0     # thin gear sells at least this many a day (proposal §2)


def classify(commodity: bool, busy: bool, level_source: str | None, sold_per_day: float | None) -> str:
    """The sales profile from an item's facts (SPEC §3.3 Sales profiles, P32).

    Args:
        commodity: Seen in Blizzard's commodities list.
        busy: The held busy status (`held_busy`), or for an older document, whether it has a sold curve.
        level_source: The level's source, "sold" or "asks", or None.
        sold_per_day: Units sold a day, or None when unknown.

    Returns:
        COMMODITY for a commodity; else BUSY when busy; THIN with a sold level and THIN_MIN_SOLD_PER_DAY or more a
        day; RARE otherwise.
    """
    if commodity:
        return COMMODITY
    if busy:
        return BUSY
    if level_source == "sold" and sold_per_day is not None and sold_per_day >= THIN_MIN_SOLD_PER_DAY:
        return THIN
    return RARE


def has_curve(sold: Sequence[SoldHour], at_ms: int) -> bool:
    """Whether the item had a sold curve at a time: CURVE_MIN_UNITS weighted clean units (SPEC §3.3 Bounds).

    The same test `lifetime_prices.sold_curve` applies to `weighted_sold`'s clean weights (summed in the same order,
    then rounded once to thousandths), over the rows up to `at_ms`, so at now it agrees with `price_level`'s curve
    exactly; the price pattern only moves prices, never units, so it is left out. A plain loop: the full refresh
    runs it up to 3 times an item.

    Args:
        sold: The item's stored prices by hour.
        at_ms: The time, UTC ms; rows after its hour are not yet sold then.

    Returns:
        True when the weighted clean units reach CURVE_MIN_UNITS.
    """
    at_hour = at_ms // HOUR_MS
    total = 0
    for row in sold:
        age = at_hour - row.hour_ms // HOUR_MS
        if 0 <= age < LEVEL_HOURS and row.clean > 0:
            total += row.clean * 0.5 ** (age / LEVEL_HALF_LIFE_HOURS)
    return units_milli(total) >= units_milli(CURVE_MIN_UNITS)


def held_busy(sold: Sequence[SoldHour], now_ms: int, now_status: bool | None = None) -> bool:
    """The busy status with the 3-day hold (SPEC §3.3 Sales profiles, P33, PI22).

    The raw status of each day is `has_curve` at now − d days. The held status is the raw status of the most recent
    run of HOLD_DAYS equal days ending at or before now: what a status that changes only after holding 3 days in a
    row reads today, computed from history alone. With no such run in the HOLD_LOOKBACK_DAYS looked back, the status
    now.

    Args:
        sold: The item's stored prices by hour.
        now_ms: Now, UTC ms.
        now_status: Whether it has a sold curve now, when the caller already knows (`price_level`'s curve is not
            None); None computes it.

    Returns:
        The held busy status.
    """
    raw: list[bool] = []
    for d in range(HOLD_LOOKBACK_DAYS + HOLD_DAYS - 1):
        raw.append(now_status if d == 0 and now_status is not None else has_curve(sold, now_ms - d * DAY_MS))
        if d >= HOLD_DAYS - 1 and len(set(raw[d - HOLD_DAYS + 1:d + 1])) == 1:
            return raw[d]
    return raw[0]
