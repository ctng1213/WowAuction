"""Your own sell record of an item (SPEC §3.3.3, O1-O7, O11-O17).

Over the last own_record_days, across your characters: s posts sold, x expired (cancelled ones
don't count). Each expiry gets a cause from the hourly closes during the post's lifetime:
undercut (a close below your price: something cheaper was listed), lowest (you were the
cheapest in every close) or unknown (no closes; counted like lowest). streak counts lowest and
unknown expiries in a row since the last sale; an undercut expiry neither adds to it nor resets
it. After a Keep only later posts count. The item is not selling once the streak reaches
struggle_streak, or keep_retry_streak after a Keep.

Calibration (SPEC §8.8) compares the chance each post was planned with against what happened. Pure.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

DAY_MS = 86_400_000
CALIBRATION_BUCKETS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))   # SPEC §8.8
CALIBRATION_WARN_POINTS = 15     # a bucket further off than this ...
CALIBRATION_MIN_POSTS = 20       # ... with at least this many posts raises a warning (K2, K3)


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
    stack: int = 1                 # units posted
    sold_units: int | None = None  # units it sold (a commodity sells in parts, T25); None: all if sold, else none


@dataclass(frozen=True)
class Record:
    """An item's sell record."""

    sold: float                    # posts' worth sold: each post counts its share of units sold (O19)
    expired: float                 # posts' worth returned
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
    sold, expired, streak, lowest = 0.0, 0.0, 0, 0
    undercut_last = False
    for p in counted:
        share = sold_share(p)
        sold, expired = sold + share, expired + 1.0 - share
        if share > 0:                       # any units sold end a streak, as a sale does (O19)
            streak = 0
            continue
        undercut_last = cause(p, closes) == "undercut"
        if not undercut_last:
            streak, lowest = streak + 1, lowest + 1
    threshold = s.keep_retry_streak if keep_at_ms is not None else s.struggle_streak
    return Record(sold, expired, streak, lowest, undercut_last, streak >= threshold)


def sold_share(p: ResolvedPost) -> float:
    """The share of a post's units that sold (O19).

    Args:
        p: A resolved post.

    Returns:
        sold_units ÷ stack in [0, 1]; without a unit count, 1 for a sale and 0 otherwise.
    """
    if p.sold_units is None or p.stack <= 0:
        return 1.0 if p.outcome == "sold" else 0.0
    return min(1.0, max(0.0, p.sold_units / p.stack))


@dataclass(frozen=True)
class Bucket:
    """One predicted-chance bucket of resolved posts."""

    low: float                     # includes its lower edge; the last bucket also includes 1
    high: float
    posts: int
    predicted: float               # mean planned P'
    actual: float                  # share that sold

    @property
    def off_target(self) -> bool:
        """Whether this bucket should raise a warning.

        Returns:
            True when predicted and actual differ by more than 15 points with at least 20 posts (K2, K3).
        """
        return abs(self.actual - self.predicted) * 100 > CALIBRATION_WARN_POINTS and self.posts >= CALIBRATION_MIN_POSTS


@dataclass(frozen=True)
class Calibration:
    """Predicted against actual sell rates (SPEC §8.8)."""

    posts: int
    buckets: tuple[Bucket, ...]    # the non-empty ones, lowest first
    brier: float | None            # mean (P' − outcome)²; None without posts


def calibration(results: Sequence[tuple[float, bool]]) -> Calibration:
    """Calibrate the planned chances against outcomes (SPEC §8.8, K1-K3).

    Args:
        results: (planned P' in [0, 1], sold) per resolved post; expired is not sold.

    Returns:
        The buckets with posts in them and the Brier score.
    """
    groups: dict[int, list[tuple[float, bool]]] = {}
    for p, sold in results:
        index = next(i for i, (low, high) in enumerate(CALIBRATION_BUCKETS)
                     if low <= p < high or i == len(CALIBRATION_BUCKETS) - 1)
        groups.setdefault(index, []).append((p, sold))
    buckets = tuple(Bucket(*CALIBRATION_BUCKETS[i], len(g), sum(p for p, _ in g) / len(g), sum(s for _, s in g) / len(g))
                    for i, g in sorted(groups.items()))
    brier = sum((p - (1.0 if sold else 0.0)) ** 2 for p, sold in results) / len(results) if results else None
    return Calibration(len(results), buckets, brier)


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
