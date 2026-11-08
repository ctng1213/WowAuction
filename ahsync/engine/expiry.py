"""How an auction that vanished splits between sold and expired (SPEC §2.4 Auction lifetimes, BZ22, BZ26, BZ42).

Pure. `engine.volume.lifetimes` follows each auction to the gap it vanished in; this module says how much of it
expired: the overlap of the gap with its expiry window (`expired_fraction`, the first pass and the cancel rule's
"could not have expired"), then, with the item's early-leave rate measured in the same pass, the share where
selling and expiring compete (`expired_given_vanished`).
"""
from __future__ import annotations

import math


def expired_fraction(window: tuple[int, int], absent_ms: int) -> float:
    """The share of an auction that expired, given it was gone by a time (SPEC §2.4 item 4, kind 1's band rule).

    Args:
        window: Its expiry window, (start, end] ms, the expiry equally likely anywhere in it.
        absent_ms: The first snapshot it was missing from.

    Returns:
        clamp((absent − start) ÷ (end − start), 0, 1): 0 when the gap is wholly before the window (sold), 1 when
        the window ends inside or before the gap (expired).
    """
    start, end = window
    return min(1.0, max(0.0, (absent_ms - start) / (end - start)))


PRIOR_LEAVES = 5.0   # pseudo early leaves pulling a thin item's early-leave rate toward its market's (BZ42)


def expired_given_vanished(window: tuple[int, int], gap: tuple[int, int], rate_per_ms: float) -> float:
    """The share of an auction that expired, given it vanished in a gap that overlaps its expiry window (BZ42).

    Selling and expiring compete: the expiry is equally likely anywhere in the window after the auction was last
    seen, and until then a buyer takes the whole listing at the item's early-leave rate (constant). The share is
    P(it expired in the gap before a buyer came) ÷ P(it was gone by the gap's end), so a slow seller that vanishes
    in its expiry hour mostly expired, where `expired_fraction` (the overlap alone) split it by time.

    Args:
        window: Its expiry window, (start, end] ms.
        gap: (last seen ms, first absent ms).
        rate_per_ms: The item's early-leave rate, listings per listing-ms; 0 when none was seen.

    Returns:
        A share in [0, 1]: 0 when the gap is wholly before the window; `expired_fraction` when the window ends
        before the auction was last seen (a band clash: nothing better is known).
    """
    start, end = window
    last, absent = gap
    low, high = max(start, last), min(end, absent)
    if end <= last:
        return expired_fraction(window, absent)
    if high <= low:
        return 0.0
    if rate_per_ms <= 0.0:
        return 1.0
    width, r = end - low, rate_per_ms
    x0, x1, c = low - last, high - last, end - last
    s0 = math.exp(-r * x0)
    s1 = math.exp(-r * x1)
    gone_by_expiry = -s0 * math.expm1(-r * (x1 - x0))                 # e^{-r x0} - e^{-r x1}, without cancellation
    p_expired = gone_by_expiry / (r * width)
    p_sold = -math.expm1(-r * x0) + (s0 * (c - x0) - s1 * (c - x1) - gone_by_expiry / r) / width
    return p_expired / (p_expired + max(0.0, p_sold))


def early_leave_rates(exposure: dict[int, float], leaves: dict[int, int], commodities: set[int]) -> dict[int, float]:
    """Each item's early-leave rate: listings that vanished while they could not yet expire, per listing-ms (BZ42).

    A thin item is pulled toward its market's pooled rate (commodities and the rest pooled apart) by PRIOR_LEAVES
    pseudo leaves: rate = (leaves + PRIOR_LEAVES) ÷ (exposure + PRIOR_LEAVES ÷ pooled rate).

    Args:
        exposure: Item key -> listing-ms observed in gaps wholly before the listing's expiry window.
        leaves: Item key -> listings that vanished in such a gap (cancels left out).
        commodities: Item keys seen in Blizzard's commodities list.

    Returns:
        Item key -> rate, listings per listing-ms; 0 for every item of a pool that saw no exposure or no leave.
    """
    pools: dict[bool, list[float]] = {True: [0.0, 0.0], False: [0.0, 0.0]}
    for key in sorted(exposure):
        pool = pools[key in commodities]
        pool[0] += leaves.get(key, 0)
        pool[1] += exposure[key]
    rates = {}
    for key in sorted(exposure):
        n, e = pools[key in commodities]
        pooled = n / e if e > 0 else 0.0
        rates[key] = (leaves.get(key, 0) + PRIOR_LEAVES) / (exposure[key] + PRIOR_LEAVES / pooled) if pooled > 0 else 0.0
    return rates


def tally_early_leaves(live: dict[int, list], vanished: list[tuple], gap_ms: int, at_ms: int,
                        exposure: dict[int, float], leaves: dict[int, int]) -> None:
    """Add one gap to the early-leave tallies: listings that could not expire in it, and those of them that left.

    Args:
        live: Auction id -> state as of the gap's start (its expiry window third).
        vanished: The gap's endings from `vanished_in`.
        gap_ms: The gap's length, ms.
        at_ms: The gap's end.
        exposure: Key -> listing-ms; updated.
        leaves: Key -> listings; updated.
    """
    gone = {auction: cancelled for auction, _, _, _, cancelled, _ in vanished}
    for auction, state in sorted(live.items()):
        if state[2][0] < at_ms:                       # it could expire in this gap: not evidence of a sale rate
            continue
        exposure[state[0]] = exposure.get(state[0], 0.0) + gap_ms
        if auction in gone and not gone[auction]:
            leaves[state[0]] = leaves.get(state[0], 0) + 1
