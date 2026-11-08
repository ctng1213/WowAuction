"""Auction house fees: the cut, the deposit and auction durations (SPEC §3.4 Fees).

Ported to addon/AHProfit/Engine/Fees.lua; keep the two identical.

The durations are Forever's (2, 8 and 24 h, verified 2026-10-18). The deposit rule was checked in game by
the user on 2026-11-08 (SPEC §6 item 4): 5%, 20% and 60% of the vendor value, floored, at least 1s an auction.
"""
from __future__ import annotations

AH_CUT = 0.05                      # faction AH cut on a sale (SPEC §3.4)
DURATIONS_H = (2, 8, 24)           # Forever's auction durations, shortest first (verified 2026-10-18)
# Deposit as basis points of vendor sell price × quantity, per duration (checked in game 2026-11-08: a 11s 73c
# Oiled Blunderbuss took 2s 34c for 8 h). Integer basis points keep the floor exact in both languages.
DEPOSIT_BP = {2: 500, 8: 2000, 24: 6000}
MIN_DEPOSIT_COPPER = 100           # the least an auction's deposit can be, whatever its value (checked 2026-11-08)


def deposit_copper(vendor_sell_copper: int, qty: int, duration_h: int) -> int:
    """Deposit for posting an auction, refunded when it sells, lost when it expires or is cancelled (SPEC §3.4 Fees).

    Args:
        vendor_sell_copper: The item's vendor sell price per unit, in copper; 0 when unknown (the minimum then).
        qty: Units in the auction; 0 or fewer is no auction.
        duration_h: Auction duration in hours; one of DURATIONS_H.

    Returns:
        max(MIN_DEPOSIT_COPPER, floor(vendor_sell × qty × rate(duration))) in copper; 0 when qty ≤ 0.

    Raises:
        ValueError: If duration_h is not a known duration.
    """
    if duration_h not in DEPOSIT_BP:
        raise ValueError(f"unknown auction duration: {duration_h} h")
    if qty <= 0:
        return 0
    return max(MIN_DEPOSIT_COPPER, vendor_sell_copper * qty * DEPOSIT_BP[duration_h] // 10_000)


def shortest_duration_covering(window_hours: int) -> int:
    """The shortest auction duration that stays up for a whole sell window (SPEC §3.3.2).

    Args:
        window_hours: Length of the sell window in whole hours.

    Returns:
        The duration in hours; the longest one when none covers the window.
    """
    for duration in DURATIONS_H:
        if duration >= window_hours:
            return duration
    return DURATIONS_H[-1]


def sale_net(price_copper: int, qty: int) -> float:
    """What a sale pays after the AH cut.

    Args:
        price_copper: Unit price in copper.
        qty: Units sold.

    Returns:
        price × qty × (1 − cut), float copper (SPEC §3.3 numeric rules).
    """
    return price_copper * qty * (1 - AH_CUT)
