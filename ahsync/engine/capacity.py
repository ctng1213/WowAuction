"""Market capacity: what the market can absorb, shared and counted once (SPEC §3.4 market capacity).

Every quantity a recommendation proposes stays within the liquidity cap of the windows
it sells in, across all your recommendations together. Pure: data in, numbers out.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field


def slot_of(hour: int) -> int:
    """Hour of the week of a clock hour (UTC, 0 = Mon 00:00; the epoch was a Thursday).

    Args:
        hour: floor(ms / 3600000).

    Returns:
        0-167.
    """
    return (hour // 24 + 3) % 7 * 24 + hour % 24


def window_supply(current: int | None, supply_how: Sequence[float], now_hour: int, hours: Sequence[int]) -> float | None:
    """Others' listed units expected over a window (SPEC §3.4 Liquidity, R17).

    Args:
        current: Units listed now, or None when never seen.
        supply_how: 168 listed-supply ratios (SPEC §3.3.6), or empty when there is no pattern.
        now_hour: The current clock hour.
        hours: The window's clock hours.

    Returns:
        current × mean(supply_how over the hours) ÷ supply_how now; current without a
        pattern (or a zero ratio now); None when supply is unknown.
    """
    if current is None:
        return None
    base = supply_how[slot_of(now_hour)] if supply_how else 0.0
    if base <= 0 or not hours:
        return float(current)
    return current * sum(supply_how[slot_of(h)] for h in hours) / len(hours) / base


def liquidity_cap(listed_units: float | None, max_supply_share: float, stack: int) -> int:
    """Units you may plan for one sell window: a share of what others list (SPEC §3.4 Liquidity).

    Args:
        listed_units: Others' listed units in the window (window_supply), or None when unknown.
        max_supply_share: Config max_supply_share, 0-1 (0.5: at most about a third of the listings).
        stack: The fewest units a window always allows: the reference stack (gear: min_gear_units).

    Returns:
        max(stack, floor(share × listed units)); stack when supply is unknown.
    """
    if listed_units is None:
        return stack
    return max(stack, math.floor(max_supply_share * listed_units))


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


SUPPLY_ROWS, SUPPLY_STEPS = 24, 20       # 24 starting hours; factors in 5% steps (SPEC §4.3, EX13)


def supply_factor_row(supply_how: Sequence[float], first_hour: int, window_hours: int) -> str:
    """The supply factors the addon's cap applies, one per starting hour (SPEC §3.4 Liquidity, EX13).

    Args:
        supply_how: 168 listed-supply ratios; empty when there is no pattern.
        first_hour: The refresh's clock hour, floor(ms / 3600000): row 0 starts there.
        window_hours: sell_window_hours.

    Returns:
        24 characters, each char(35 + n) with n = floor(f × 20 + 0.5) clamped to 0-50, where f is
        window_supply(1, …) for a window starting that hour; "" without a pattern.
    """
    if not supply_how:
        return ""
    out = []
    for row in range(SUPPLY_ROWS):
        start = first_hour + row
        factor = window_supply(1, supply_how, start, list(range(start, start + window_hours)))
        out.append(chr(35 + max(0, min(50, math.floor(factor * SUPPLY_STEPS + 0.5)))))
    return "".join(out)

