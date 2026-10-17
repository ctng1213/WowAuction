"""Market capacity: what the market can absorb, shared and counted once (SPEC §3.4 market capacity).

Every quantity a recommendation proposes stays within the liquidity cap of the windows
it sells in, across all your recommendations together. Pure: data in, numbers out.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def liquidity_cap(sold_per_day: float | None, window_hours: float, max_market_share: float, stack: int) -> int:
    """Units you may plan for one sell window (SPEC §3.4 Liquidity).

    Args:
        sold_per_day: Estimated market sales in units per day, or None when unknown.
        window_hours: The sell window, in hours (sell_window_hours for posting now).
        max_market_share: Config max_market_share, 0-1.
        stack: The item's reference stack, in units.

    Returns:
        max(1 stack, floor(S × W ÷ 24 × share)) units; one stack when sales are unknown.
    """
    if sold_per_day is None:
        return stack
    return max(stack, math.floor(sold_per_day * window_hours / 24 * max_market_share))


@dataclass(frozen=True)
class Candidate:
    """A proposed buy and the units it would put on the market."""

    id: int                                              # unique: the final tie-break
    profit: float                                        # expected profit, float copper
    units: dict[int, float] = field(default_factory=dict)   # item key -> expected units to sell


def take_within_caps(candidates: list[Candidate], caps: dict[int, float],
                     used: dict[int, float] | None = None) -> tuple[list[int], list[int]]:
    """Take buys best profit first while every item they add is still under its cap (SPEC §3.4, R15).

    A buy is taken while each of its items' running total is below that item's cap, so
    the last buy taken may pass it; later buys of that item are dropped.

    Args:
        candidates: The proposed buys, in any order.
        caps: Item key -> liquidity cap in units, for every item a candidate adds.
        used: Item key -> units already committed (held or planned); not changed.

    Returns:
        (ids taken, ids dropped), each in decision order: profit highest first, then id.

    Raises:
        KeyError: A candidate adds an item that has no cap.
    """
    running = dict(used or {})
    taken, dropped = [], []
    for c in sorted(candidates, key=lambda c: (-c.profit, c.id)):
        if all(running.get(key, 0.0) < caps[key] for key in sorted(c.units)):
            taken.append(c.id)
            for key, units in sorted(c.units.items()):
                running[key] = running.get(key, 0.0) + units
        else:
            dropped.append(c.id)
    return taken, dropped
