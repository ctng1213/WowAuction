"""The timing premium: profit later is worth less than profit now (SPEC §3.4D.2, PF17-PF19, PF26).

Every candidate is scored by its adjusted profit = expected net ÷ (1 + premium). The wait is
kept in whole hundredths of a day and the premium in whole millionths, so the arithmetic is
integer up to the one division, and addon/AHProfit/Engine/Timing.lua (its twin, shared
scenarios in tests/scenarios/timing.json) gives the same doubles. Pure: times are UTC ms.
"""
from __future__ import annotations

import math

MINUTE_MS = 60_000
MINUTES_PER_DAY = 1440
MICRO = 1_000_000                  # the premium is kept in millionths


def wait_hundredths(now_ms: int, sale_ms: int | None) -> int:
    """The wait from now to the expected sale hour, in hundredths of a day, half up (SPEC §3.4D.2).

    Args:
        now_ms: Now, UTC ms.
        sale_ms: The expected sale hour (the start of its selling window, or now for a post now), UTC ms;
            None for a candidate that sells now.

    Returns:
        floor(minutes × 100 ÷ 1440 + 0.5) in integer arithmetic, minutes being the whole minutes from now
        to the sale, never below 0. Mon 10:38 to Thu 19:00 is 4,822 min → 335.
    """
    if sale_ms is None:
        return 0
    minutes = max(0, (sale_ms - now_ms) // MINUTE_MS)
    return (minutes * 200 + MINUTES_PER_DAY) // (2 * MINUTES_PER_DAY)


def wait_days(now_ms: int, sale_ms: int | None) -> float:
    """The wait in days, to 0.01 d (SPEC §3.4D.2).

    Args:
        now_ms: Now, UTC ms.
        sale_ms: The expected sale hour, UTC ms; None for now.

    Returns:
        wait_hundredths ÷ 100, e.g. 3.35.
    """
    return wait_hundredths(now_ms, sale_ms) / 100


def immediate(hundredths: int, immediate_hours: int) -> bool:
    """Whether a wait is inside the immediate window: a Today row with no premium (SPEC §3.4D.2, §3.4D.4, PF19).

    Args:
        hundredths: The wait, hundredths of a day.
        immediate_hours: Config immediate_hours (8 or 24).

    Returns:
        True when wait × 24 ≤ immediate_hours, compared in integers (hundredths × 24 ≤ hours × 100).
    """
    return hundredths * 24 <= immediate_hours * 100


def premium_micro(hundredths: int, premium_base: float, premium_per_day: float, immediate_hours: int) -> int:
    """The timing premium in millionths (SPEC §3.4D.2): 0 inside the immediate window, else base + per day × wait.

    Args:
        hundredths: The wait, hundredths of a day.
        premium_base: Config premium_base, a fraction (0.15).
        premium_per_day: Config premium_per_day, a fraction per day (0.05).
        immediate_hours: Config immediate_hours.

    Returns:
        0 when immediate; else floor(base × 1e6 + 0.5) + floor(per_day × 1e4 + 0.5) × hundredths. At 3.35 d with
        the defaults: 150,000 + 500 × 335 = 317,500 (31.75%).
    """
    if immediate(hundredths, immediate_hours):
        return 0
    return math.floor(premium_base * MICRO + 0.5) + math.floor(premium_per_day * 10_000 + 0.5) * hundredths


def adjusted(expected_net: float, premium: int) -> float:
    """Adjusted profit = expected net ÷ (1 + premium) (SPEC §3.4D.2); a float, rounded only when shown.

    Args:
        expected_net: Expected net, float copper.
        premium: The premium in millionths (premium_micro).

    Returns:
        expected_net × 1e6 ÷ (1e6 + premium), float copper: 5500 at 317,500 → 4174.57; exactly expected_net
        when the premium is 0 (no float round trip inside the immediate window, PF26).
    """
    if premium == 0:
        return expected_net
    return expected_net * MICRO / (MICRO + premium)


def adjusted_profit(expected_net: float, now_ms: int, sale_ms: int | None, premium_base: float,
                    premium_per_day: float, immediate_hours: int) -> float:
    """A candidate's adjusted profit from its sale time (SPEC §3.4D.2).

    Args:
        expected_net: Expected net, float copper.
        now_ms: Now, UTC ms.
        sale_ms: Its expected sale hour, UTC ms; None for now.
        premium_base: Config premium_base.
        premium_per_day: Config premium_per_day.
        immediate_hours: Config immediate_hours.

    Returns:
        adjusted(expected_net, premium_micro(wait_hundredths(now_ms, sale_ms), ...)), float copper.
    """
    return adjusted(expected_net, premium_micro(wait_hundredths(now_ms, sale_ms), premium_base, premium_per_day,
                                                immediate_hours))
