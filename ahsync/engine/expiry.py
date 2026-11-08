"""How an auction that vanished splits between sold and expired (SPEC §2.4 Auction lifetimes, BZ22, BZ26, BZ42).

Pure. `engine.volume.lifetimes` follows each auction to the gap it vanished in; this module says how much of it
expired: the overlap of the gap with its expiry window (`expired_fraction`, the first pass and the cancel rule's
"could not have expired"), then, with the item's early-leave rate measured in the same pass, the share where
selling and expiring compete (`expired_given_vanished`).
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence


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
RATE_WINDOW_MS = 24 * 3_600_000   # an ending's rate reads the gaps that ended in the day before it (BZ42)


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


class EarlyLeaveRates:
    """Items' early-leave rates for endings, from the gaps that ended in the RATE_WINDOW_MS before each (BZ42).

    Causal: only gaps ending in (end − RATE_WINDOW_MS, end] count, so every lifetimes pass that holds an ending (its
    lookback covers the window, `engine.volume.LIFETIME_LOOKBACK_MS`) gives it the same rate, and a later snapshot
    never changes it. A thin item is pulled toward its market's pooled rate (commodities and the rest pooled apart)
    by PRIOR_LEAVES pseudo leaves: (leaves + PRIOR_LEAVES) ÷ (exposure + PRIOR_LEAVES × pooled exposure ÷ pooled
    leaves); an item with no exposure in the window takes the pooled rate. Window sums come from integer prefix
    sums (exposure in whole ms), so they are exact whatever gaps came before the window.
    """

    def __init__(self, tallies: Sequence[tuple[int, dict[int, tuple[int, int]]]], commodities: set[int]) -> None:
        """Index a pass's per-gap tallies.

        Args:
            tallies: (gap end ms, item key -> (listing-ms exposed, early leaves)) per linked gap, ascending, from
                `tally_early_leaves`.
            commodities: Item keys seen in Blizzard's commodities list.
        """
        self.commodities = commodities
        self.times = [at for at, _ in tallies]
        self.pools = {True: [(0, 0)], False: [(0, 0)]}
        self.own: dict[int, tuple[list[int], list[tuple[int, int]]]] = {}
        for at, per_key in tallies:
            sums = {True: [0, 0], False: [0, 0]}
            for key, (exposed, left) in sorted(per_key.items()):
                pool = sums[key in commodities]
                pool[0] += exposed
                pool[1] += left
                times, cum = self.own.setdefault(key, ([], [(0, 0)]))
                times.append(at)
                cum.append((cum[-1][0] + exposed, cum[-1][1] + left))
            for flag, (exposed, left) in sums.items():
                last = self.pools[flag][-1]
                self.pools[flag].append((last[0] + exposed, last[1] + left))

    def rate(self, key: int, end_ms: int) -> float:
        """An item's rate for an ending.

        Args:
            key: The ending's item key.
            end_ms: The ending's gap end, UTC ms.

        Returns:
            Listings per listing-ms; 0 when the item's pool saw no exposure or no leave in the window.
        """
        start = end_ms - RATE_WINDOW_MS
        pool = self.pools[key in self.commodities]
        lo, hi = bisect_right(self.times, start), bisect_right(self.times, end_ms)
        pool_exposed, pool_left = pool[hi][0] - pool[lo][0], pool[hi][1] - pool[lo][1]
        if pool_exposed <= 0 or pool_left == 0:
            return 0.0
        own_exposed = own_left = 0
        if key in self.own:
            times, cum = self.own[key]
            i, j = bisect_right(times, start), bisect_right(times, end_ms)
            own_exposed, own_left = cum[j][0] - cum[i][0], cum[j][1] - cum[i][1]
        return (own_left + PRIOR_LEAVES) / (own_exposed + PRIOR_LEAVES * pool_exposed / pool_left)


def tally_early_leaves(live: dict[int, list], vanished: list[tuple], gap_ms: int,
                       at_ms: int) -> dict[int, tuple[int, int]]:
    """One gap's early-leave tally: listings that could not expire in it, and those of them that left (BZ42).

    Args:
        live: Auction id -> state as of the gap's start (its expiry window third).
        vanished: The gap's endings from `vanished_in` (a cancel is censored: exposure, no leave).
        gap_ms: The gap's length, ms.
        at_ms: The gap's end.

    Returns:
        Item key -> (listing-ms exposed, whole ms; listings that left early), by key.
    """
    gone = {auction: cancelled for auction, _, _, _, cancelled, _ in vanished}
    tally: dict[int, list] = {}
    for auction, state in sorted(live.items()):
        if state[2][0] < at_ms:                       # it could expire in this gap: not evidence of a sale rate
            continue
        row = tally.setdefault(state[0], [0, 0])
        row[0] += gap_ms
        row[1] += auction in gone and not gone[auction]
    return {key: (row[0], row[1]) for key, row in sorted(tally.items())}
