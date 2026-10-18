"""Late and missed actions, and the missed-peak re-plan (SPEC §3.4.0, §3.4.1).

The hourly status pass asks two things of every open action:
- what its status is now (late and missed mean different things per kind);
- for a post whose selling window ended unmatched: where it goes instead. The item's
  next windows in the same plan week are tried in time order; the first one within
  peak_tolerance of the ORIGINAL anchor (never of an earlier replacement), likely to
  sell and still worth more than vendor or disenchant takes it; with none, it holds
  until next week, which starts fresh.
Pure: data in, decisions out.
"""
from __future__ import annotations

from dataclasses import dataclass

from ahsync.engine import fees, sellprob
from ahsync.engine.plan import PlanSettings, SellFacts, alternative, post_chance, post_options

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
MISS_AFTER_MS = 24 * HOUR_MS      # a buy, craft, mail, DE or vendor sale unmatched 24 h after its slot (§3.4.1)


def status_now(kind: str, slot_ms: int, window_end_ms: int | None, now_ms: int) -> str:
    """An open action's status at a moment (SPEC §3.4.1 table, M7).

    Args:
        kind: The action's kind.
        slot_ms: When it was planned (a post: its post time).
        window_end_ms: A post's selling window end, or None (posting now: its 8 h window is not stored).
        now_ms: Now.

    Returns:
        "pending", "late" (a post whose post time passed while its window hasn't ended:
        "post now (late)") or "missed".
    """
    if kind == "post":
        end = window_end_ms if window_end_ms is not None else slot_ms + MISS_AFTER_MS
        if now_ms >= end:
            return "missed"
        return "late" if now_ms > slot_ms else "pending"
    if kind == "hold":
        return "pending"
    return "missed" if now_ms >= slot_ms + MISS_AFTER_MS else "pending"


@dataclass(frozen=True)
class Tried:
    """One window the re-plan looked at."""

    window: tuple[int, int, int]      # (start ms, end ms, best hour ms)
    net: float                        # expected net there, float copper
    p: float
    failed: str                       # "" when it passed; else "below the original", "unlikely to sell", "not worth it"


@dataclass(frozen=True)
class Replan:
    """Where a missed post goes: a new post, or a hold."""

    kind: str                          # "post" | "hold"
    at_ms: int | None                  # the new post time
    price: int | None
    window: tuple[int, int, int] | None
    p: float | None
    net: float                         # its expected net (0 for a hold)
    duration_h: int | None
    tried: tuple[Tried, ...]           # the windows looked at, in time order


def replan_missed(anchor_net: float, missed_end_ms: int, qty: int, f: SellFacts, now_ms: int, week_end_ms: int,
                  s: PlanSettings, min_sell_prob: float, peak_tolerance: float) -> Replan:
    """Move a missed post to a later window this plan week, or hold it (SPEC §3.4.0, M1-M5, PL3).

    Args:
        anchor_net: Expected net at the ORIGINAL peak (the action's anchor, carried by any replacement).
        missed_end_ms: End of the window that was missed; candidates start after it.
        qty: Units to post.
        f: The item.
        now_ms: Now.
        week_end_ms: End of the plan week (next Monday 00:00); candidates must start before it.
        s: Plan settings.
        min_sell_prob: Config min_sell_prob.
        peak_tolerance: Config peak_tolerance (0.10).

    Returns:
        The first window passing all three checks, in time order; else a hold.
    """
    alt = alternative(f)[1] * qty
    tried = []
    for o in post_options(f, now_ms, s):
        if o.window is None or o.window[0] < missed_end_ms or o.window[0] >= week_end_ms:
            continue
        p = post_chance(f, o.price, qty, o.hours, 0.0, s)
        e = sellprob.expected_post(p, qty, o.price, alternative(f)[1], fees.deposit_copper(f.vendor_sell, qty, o.duration_h))
        net = e - f.paid_each * qty
        failed = ("below the original" if net < (1 - peak_tolerance) * anchor_net
                  else "unlikely to sell" if p < min_sell_prob
                  else "not worth it" if e <= alt else "")
        tried.append(Tried(o.window, net, p, failed))
        if not failed:
            return Replan("post", o.at_ms, o.price, o.window, p, net, o.duration_h, tuple(tried))
    return Replan("hold", None, None, None, None, 0.0, None, tuple(tried))
