"""Your own sell record of an item (SPEC §3.3.3, O1-O7, O11-O17).

Over the last own_record_days, across your characters: s posts sold, x expired (cancelled ones
don't count). Each expiry gets a cause from the hourly closes during the post's lifetime:
undercut (a close below your price: something cheaper was listed), lowest (you were the
cheapest in every close) or unknown (no closes; counted like lowest). streak counts lowest and
unknown expiries in a row since the last sale; an undercut expiry neither adds to it nor resets
it. After a Keep only later posts count. The item is not selling once the streak reaches
struggle_streak, or keep_retry_streak after a Keep. Pure.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

DAY_MS = 86_400_000


@dataclass(frozen=True)
class RecordSettings:
    """The config values the record needs."""

    days: int = 30                 # config own_record_days
    struggle_streak: int = 3       # config struggle_streak
    keep_retry_streak: int = 1     # config keep_retry_streak


@dataclass(frozen=True)
class ResolvedPost:
    """One of your posts with its outcome."""

    post_id: str
    price: int                     # each, copper
    at_ms: int                     # when it was posted
    resolved_at_ms: int            # when its outcome was known
    outcome: str                   # sold | expired | cancelled


@dataclass(frozen=True)
class Record:
    """An item's sell record."""

    sold: int
    expired: int
    streak: int                    # lowest or unknown expiries in a row since the last sale
    lowest_expiries: int           # expiries while you were the cheapest (or unknown), for the record line
    undercut_last: bool            # the latest expiry was undercut: cap the next post below the cheapest other
    not_selling: bool


def cause(post: ResolvedPost, closes: Sequence[tuple[int, int]]) -> str:
    """Why an expired post didn't sell (SPEC §3.3.3, O11, O14).

    Args:
        post: The expired post.
        closes: (hour ms, close copper) of the item, any order.

    Returns:
        "undercut" when a close during its lifetime was below its price, "lowest" when every
        close then was at or above it, "unknown" when there were none.
    """
    during = [price for t, price in closes if post.at_ms <= t <= post.resolved_at_ms]
    if not during:
        return "unknown"
    return "undercut" if min(during) < post.price else "lowest"


def sell_record(posts: Sequence[ResolvedPost], closes: Sequence[tuple[int, int]], now_ms: int,
                keep_at_ms: int | None, s: RecordSettings) -> Record | None:
    """An item's record from your resolved posts (module docstring).

    Args:
        posts: Your posts of the item with an outcome.
        closes: (hour ms, close copper) of the item.
        now_ms: Now.
        keep_at_ms: When you last chose Keep for the item, or None.
        s: Settings.

    Returns:
        The record; None when no sold or expired post counts (O2, O7).
    """
    since = now_ms - s.days * DAY_MS
    counted = sorted((p for p in posts if p.outcome in ("sold", "expired") and p.at_ms >= since
                      and (keep_at_ms is None or p.at_ms > keep_at_ms)),
                     key=lambda p: (p.resolved_at_ms, p.post_id))
    if not counted:
        return None
    sold = expired = streak = lowest = 0
    undercut_last = False
    for p in counted:
        if p.outcome == "sold":
            sold, streak = sold + 1, 0
            continue
        expired += 1
        undercut_last = cause(p, closes) == "undercut"
        if not undercut_last:
            streak, lowest = streak + 1, lowest + 1
    threshold = s.keep_retry_streak if keep_at_ms is not None else s.struggle_streak
    return Record(sold, expired, streak, lowest, undercut_last, streak >= threshold)


def stacks_to_post(stacks: int, streak: int) -> int:
    """How many of the stacks the plan would post it still posts (SPEC §3.3.3 quantity, O3, O4).

    Args:
        stacks: Stacks the plan would post.
        streak: The record's streak.

    Returns:
        stacks × 0.5^streak, rounded down, at least 1 (0 when there is nothing to post).
    """
    if stacks <= 0:
        return 0
    return max(1, int(stacks * 0.5 ** streak))


def repriced(price: int, streak: int, step: float, floor: int, cap: int | None) -> int:
    """The planned price after the record (SPEC §3.3.3 price, O11-O13).

    Args:
        price: The plan's price, copper.
        streak: The record's streak.
        step: reprice_step (0.05).
        floor: ceil(fallback ÷ (1 − cut)): below it vendoring or disenchanting pays more.
        cap: The cheapest other listing − 1 after an undercut expiry, or None.

    Returns:
        price × (1 − step)^streak, floored to whole copper, then capped, never below the floor.
    """
    lowered = math.floor(price * (1 - step) ** streak + 1e-9)   # floored, without float slips
    if cap is not None:
        lowered = min(lowered, cap)
    return max(lowered, floor)
