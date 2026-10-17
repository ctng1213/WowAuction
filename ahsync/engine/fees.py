"""Auction house fees: the cut, the deposit and auction durations (SPEC §3.4 Fees).

Ported to addon/AHProfit/Engine/Fees.lua; keep the two identical.

The deposit rates and durations are seeded with Classic Era values and are
UNVERIFIED for Forever (SPEC §6 item 4). Forever's client has
C_AuctionHouse.CalculateItemDeposit, which can confirm them in game.
"""
from __future__ import annotations

AH_CUT = 0.05                      # faction AH cut on a sale (SPEC §3.4)
DURATIONS_H = (2, 8, 24)           # UNVERIFIED: Classic Era auction durations, shortest first
# UNVERIFIED: deposit as basis points of vendor sell price × quantity, per duration.
# Seeded as 15% per 24 h, pro rata. Integer basis points keep the floor exact in both languages.
DEPOSIT_BP = {2: 125, 8: 500, 24: 1500}


def deposit_copper(vendor_sell_copper: int, qty: int, duration_h: int) -> int:
    """Deposit for posting an auction, refunded when it sells (SPEC §3.3.2).

    Args:
        vendor_sell_copper: The item's vendor sell price per unit, in copper.
        qty: Units in the auction.
        duration_h: Auction duration in hours; one of DURATIONS_H.

    Returns:
        floor(vendor_sell × qty × rate(duration)) in copper.

    Raises:
        ValueError: If duration_h is not a known duration.
    """
    if duration_h not in DEPOSIT_BP:
        raise ValueError(f"unknown auction duration: {duration_h} h")
    return vendor_sell_copper * qty * DEPOSIT_BP[duration_h] // 10_000


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
