"""How an auction that vanished splits between sold and expired (SPEC §2.4 Auction lifetimes, BZ22, BZ26, BZ44).

Pure. `engine.lifetimes.step` follows each auction to the gap it vanished in; this module says how much of it
expired: the overlap of the gap with its expiry window (`expired_fraction`, the first pass and the cancel rule's
"could not have expired"), then, with the item's early-leave rate over the day before (`LeaveWindow`, carried with
the lifetimes), the share where selling and expiring compete (`expired_given_vanished`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


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


PRIOR_LEAVES = 5.0   # pseudo early leaves pulling a thin item's early-leave rate toward its market's (BZ44)
RATE_WINDOW_MS = 24 * 3_600_000   # an ending's rate reads the gaps that ended in the day before it (BZ44)


def expired_given_vanished(window: tuple[int, int], gap: tuple[int, int], rate_per_ms: float) -> float:
    """The share of an auction that expired, given it vanished in a gap that overlaps its expiry window (BZ44).

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


@dataclass(frozen=True)
class LeaveWindow:
    """The early-leave tallies of the gaps that ended in the last RATE_WINDOW_MS, with their sums (BZ44).

    Carried from snapshot to snapshot (`engine.lifetimes.step`): each linked gap adds its tally and drops those that
    ended RATE_WINDOW_MS or more before it, so `rate` reads exactly the gaps ending in (end − RATE_WINDOW_MS, end],
    which no later snapshot changes. The sums are integers (exposure in whole ms), so adding and dropping is exact.
    A thin item is pulled toward its market's pooled rate (commodities and the rest pooled apart, each tally row by
    the flag its listings carried then, D7) by PRIOR_LEAVES pseudo leaves: (leaves + PRIOR_LEAVES) ÷ (exposure +
    PRIOR_LEAVES × pooled exposure ÷ pooled leaves); an item with no exposure in the window takes the pooled rate.
    """

    tallies: tuple[tuple[int, dict[int, tuple[int, int, bool]]], ...] = ()   # (gap end ms, key -> tally row), ascending
    own: dict[int, tuple[int, int]] = field(default_factory=dict)            # key -> (listing-ms exposed, leaves)
    pools: dict[bool, tuple[int, int]] = field(default_factory=lambda: {True: (0, 0), False: (0, 0)})

    def advance(self, end_ms: int, tally: dict[int, tuple[int, int, bool]]) -> LeaveWindow:
        """The window after one more linked gap.

        Args:
            end_ms: The gap's end, UTC ms, later than every tally held.
            tally: Its rows, from `tally_early_leaves`.

        Returns:
            A new window holding the tallies that ended in (end_ms − RATE_WINDOW_MS, end_ms]; this one is unchanged.

        Raises:
            ValueError: end_ms is not later than the newest tally held (the drop below assumes time order).
        """
        if self.tallies and end_ms <= self.tallies[-1][0]:
            raise ValueError(f"a gap ending at {end_ms} is not after the window's newest at {self.tallies[-1][0]}")
        own, pools = dict(self.own), dict(self.pools)
        keep = tuple(t for t in self.tallies if t[0] > end_ms - RATE_WINDOW_MS)
        for _, rows in self.tallies[:len(self.tallies) - len(keep)]:
            _add(own, pools, rows, -1)
        _add(own, pools, tally, 1)
        return LeaveWindow(keep + ((end_ms, tally),), own, pools)

    def rate(self, key: int, commodity: bool) -> float:
        """An item's rate for an ending in the newest gap.

        Args:
            key: The ending's item key.
            commodity: Whether its listing carried the commodity flag (picks the pool).

        Returns:
            Listings per listing-ms; 0 when the item's pool saw no exposure or no leave in the window.
        """
        pool_exposed, pool_left = self.pools[commodity]
        if pool_exposed <= 0 or pool_left == 0:
            return 0.0
        own_exposed, own_left = self.own.get(key, (0, 0))
        return (own_left + PRIOR_LEAVES) / (own_exposed + PRIOR_LEAVES * pool_exposed / pool_left)


def _add(own: dict[int, tuple[int, int]], pools: dict[bool, tuple[int, int]],
         rows: dict[int, tuple[int, int, bool]], sign: int) -> None:
    """Add (sign 1) or remove (sign −1) one gap's tally rows from a window's sums, in place.

    Args:
        own: Key -> (exposure ms, leaves), updated; a key whose sums reach (0, 0) is dropped.
        pools: Flag -> (exposure ms, leaves), updated.
        rows: Key -> (exposure ms, leaves, commodity flag).
        sign: 1 or −1.
    """
    for key, (exposed, left, commodity) in rows.items():
        e, n = own.get(key, (0, 0))
        e, n = e + sign * exposed, n + sign * left
        if e or n:
            own[key] = (e, n)
        else:
            own.pop(key, None)
        pe, pn = pools[commodity]
        pools[commodity] = (pe + sign * exposed, pn + sign * left)


def tally_early_leaves(live: dict[int, list], vanished: list[tuple], gap_ms: int,
                       at_ms: int) -> dict[int, tuple[int, int]]:
    """One gap's early-leave tally: listings that could not expire in it, and those of them that left (BZ44).

    Args:
        live: Auction id -> state as of the gap's start (its expiry window third, its commodity flag fifth).
        vanished: The gap's endings from `vanished_in` (a cancel is censored: exposure, no leave).
        gap_ms: The gap's length, ms.
        at_ms: The gap's end.

    Returns:
        Item key -> (listing-ms exposed, whole ms; listings that left early; whether any of its tallied listings
        carried the commodity flag, the row's pool, D7), by key.
    """
    gone = {auction: cancelled for auction, _, _, _, cancelled, *_ in vanished}
    tally: dict[int, list] = {}
    for auction, state in sorted(live.items()):
        if state[2][0] < at_ms:                       # it could expire in this gap: not evidence of a sale rate
            continue
        row = tally.setdefault(state[0], [0, 0, False])
        row[0] += gap_ms
        row[1] += auction in gone and not gone[auction]
        row[2] = row[2] or state[4]
    return {key: (row[0], row[1], row[2]) for key, row in sorted(tally.items())}
