"""The batch lifetimes pass as it was before CF1 (frozen; the reference for VI8, SPEC §8.7).

`engine.lifetimes.lifetimes` is the fold of `step` since CF1 (docs/plans/carry-forward-lifetimes.md, D3). VI8 checks
that fold against this copy of the pass it replaced, so the two must give byte-identical records on worlds where a
key's commodity flag never changes (D7: the fold pools by the flags known at the time, this pass by the union over
every snapshot it read). Copied verbatim from `ahsync/engine/volume.py` and `ahsync/engine/expiry.py` at `main`
148e390; only the names of the helpers it shares with the engine are imported. Never edit it to make VI8 pass.
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import replace

from ahsync.engine.expiry import PRIOR_LEAVES, RATE_WINDOW_MS, expired_fraction, expired_given_vanished
from ahsync.engine.lifetimes import (DURATION_MS, Lifetimes, LifeFlow, band_window, lowest_other, lowest_two,
                                     match_relists, narrow)
from ahsync.engine.unseen import unseen_sales
from ahsync.engine.volume import MAX_GAP_MS, Auction, IdBook


class EarlyLeaveRates:
    """Items' early-leave rates for endings, from the gaps that ended in the RATE_WINDOW_MS before each (BZ44).

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
    """One gap's early-leave tally: listings that could not expire in it, and those of them that left (BZ44).

    Args:
        live: Auction id -> state as of the gap's start (its expiry window third).
        vanished: The gap's endings from `vanished_in` (a cancel is censored: exposure, no leave).
        gap_ms: The gap's length, ms.
        at_ms: The gap's end.

    Returns:
        Item key -> (listing-ms exposed, whole ms; listings that left early), by key.
    """
    gone = {auction: cancelled for auction, _, _, _, cancelled, *_ in vanished}
    tally: dict[int, list] = {}
    for auction, state in sorted(live.items()):
        if state[2][0] < at_ms:                       # it could expire in this gap: not evidence of a sale rate
            continue
        row = tally.setdefault(state[0], [0, 0])
        row[0] += gap_ms
        row[1] += auction in gone and not gone[auction]
    return {key: (row[0], row[1]) for key, row in sorted(tally.items())}


def vanished_in(live: dict[int, list], current: Sequence[Auction], at_ms: int) -> list[tuple]:
    """The auctions gone from a snapshot, each with its expired share and whether it was a cancel (BZ22, BZ40).

    A commodity that left early (expired share 0: it could not have expired) while a cheaper auction of its item
    survived the gap with its units unchanged was cancelled: commodity buys take the cheapest units first (measured,
    `docs/plans/blizzard-cloud-sales.md`), so a buyer would have taken the cheaper one. Equal prices are no proof.

    Args:
        live: Auction id -> [key, units, expiry window, unit price, commodity, tier1], as of the earlier snapshot.
        current: The later snapshot's auctions, by id.
        at_ms: The later snapshot's time.

    Returns:
        (auction, key, units, expired share, cancelled, tier1, unit price) per auction gone, by auction id.
    """
    kept: dict[int, int] = {}                      # key -> cheapest price among auctions unchanged across the gap
    ids = set()
    for a in current:
        ids.add(a.id)
        state = live.get(a.id)
        if state is not None and state[1] == a.qty and a.price < kept.get(a.key, a.price + 1):
            kept[a.key] = a.price
    out = []
    for auction, (key, units, window, price, commodity, tier1) in sorted(live.items()):
        if auction in ids:
            continue
        expired = expired_fraction(window, at_ms)
        cancelled = commodity and expired == 0.0 and kept.get(key, price) < price
        out.append((auction, key, units, expired, cancelled, tier1, price))
    return out


def lifetimes(books: Iterable[IdBook], max_gap_ms: int = MAX_GAP_MS, keep_from_ms: int | None = None) -> Lifetimes:
    """Follow every auction id from first sight to its end (SPEC §2.4 Auction lifetimes, BZ22, BZ26, BZ27).

    Snapshots more than max_gap_ms apart don't link: nothing carries over the hole. An auction first seen right
    after a linked gap was posted in it, so its expiry window is that gap plus the duration its band implies
    (DURATION_MS); one seen in the first snapshot, or after a hole, gets its band's window. Every later sighting
    narrows the window (`narrow`; Blizzard's band bounds, ID_BAND_HOURS). A drop in units is a partial fill, sold
    in that gap. An auction gone in a gap expired by `expired_fraction` and otherwise left early (sold), unless it
    was a commodity cancel (`vanished_in`, BZ40), with a possible relist counted (`match_relists`). A listing first
    seen after a linked gap is marked tier 1 when posted at or below its item's lowest other price (BZ41).

    Args:
        books: Snapshots of one market, oldest first; read once, so a generator keeps one in memory at a time.
        max_gap_ms: Largest gap between linked snapshots, in ms.
        keep_from_ms: Keep the records (gaps, endings, fills, listings) only of gaps ending at or after this; the
            earlier snapshots still build each auction's window. None keeps everything.

    Returns:
        The Lifetimes; auctions still listed at the end have no ending.
    """
    live: dict[int, list] = {}                     # auction id -> [key, units, expiry window, price, commodity, tier1]
    gaps, endings, fills, listed, first_seen, seen = [], [], [], [], [], {}
    clashes, prev_at, prev_keys, commodities = 0, None, frozenset(), set()
    tallies: list[tuple[int, dict]] = []          # (gap end, key -> (exposure ms, early leaves)) per gap (BZ44)
    pooled_commodities: set[int] = set()          # every pass snapshot's commodities: the rate's pools (BZ44)
    windows: dict[int, tuple[int, int]] = {}      # auction -> its expiry window when it vanished
    for book in books:
        linked = prev_at is not None and 0 < book.at_ms - prev_at <= max_gap_ms
        gap = (prev_at, book.at_ms) if linked else None
        keep = linked and (keep_from_ms is None or book.at_ms >= keep_from_ms)   # earlier gaps only build windows
        current = sorted(book.auctions, key=lambda a: a.id)
        pooled_commodities.update(a.key for a in current if a.commodity)
        vanished = []
        if linked:
            vanished = vanished_in(live, current, book.at_ms)
            tallies.append((book.at_ms, tally_early_leaves(live, vanished, book.at_ms - prev_at, book.at_ms)))
            for auction, *_ in vanished:
                if keep and live[auction][4]:
                    commodities.add(live[auction][0])  # its last auctions may vanish here, never seen in a kept book
                windows[auction] = live[auction][2]
                del live[auction]
        else:
            live = {}                              # a hole: nothing is known across it
        new = []
        lowest = lowest_two(current) if linked else {}   # every posting's tier, kept or not (BZ41)
        for a in current:
            state = live.get(a.id)
            if state is None:
                tier1 = None
                if linked:
                    new.append(a)
                    other = lowest_other(lowest[a.key], a.id)
                    tier1 = other is None or a.price <= other
                    if keep:
                        listed.append((a.id, a.key, a.qty, gap, a.price, other))
                    first_seen.append((a.id, a.key, gap, a.commodity))
                    posted = (prev_at + DURATION_MS[a.band], book.at_ms + DURATION_MS[a.band])
                    window, clash = narrow(posted, book.at_ms, a.band)
                    clashes += clash and a.band != 1   # short at first sight is expected to clash (SPEC §2.4 item 2)
                else:
                    window = band_window(book.at_ms, a.band)
                live[a.id] = [a.key, a.qty, window, a.price, a.commodity, tier1]
                continue
            state[4] = state[4] or a.commodity       # a snapshot stored before M33b reads False (review, M33b-1)
            state[3] = a.price                       # the last sighting's price is the one it sold at (BZ52)
            if a.qty < state[1]:
                if keep:
                    fills.append((a.id, a.key, state[1] - a.qty, gap, a.price))
                state[1] = a.qty
            state[2], clash = narrow(state[2], book.at_ms, a.band)
            clashes += clash
        keys = frozenset(a.key for a in current)
        if keep:
            commodities.update(a.key for a in current if a.commodity)
            gaps.append(gap)
            seen[gap] = prev_keys | keys
            endings.extend(match_relists(vanished, new, gap))
        prev_at, prev_keys = book.at_ms, keys
    rates = EarlyLeaveRates(tallies, pooled_commodities)
    endings = [replace(e, overlap=e.expired, expired=expired_given_vanished(windows[e.auction], e.gap,
                                                                           rates.rate(e.key, e.gap[1])))
               if 0.0 < e.expired else e for e in endings]
    return Lifetimes(tuple(gaps), seen, tuple(endings), tuple(fills), tuple(listed), clashes, frozenset(commodities), tuple(first_seen))


def life_flows(lt: Lifetimes) -> dict[tuple[int, int], dict[int, LifeFlow]]:
    """Each gap's lifetimes summed per item (SPEC §2.4 "What the lifetimes feed").

    Args:
        lt: From `lifetimes`.

    Returns:
        gap -> item key -> LifeFlow, for every item listed in either snapshot of the gap (zeros included, so an
        observed hour with no sale reads 0, not missing). An ending's units split into expired (its expired
        share) and left early, which is sold unless it was a commodity cancel (BZ40); tier-1 endings are tallied
        for the sell-through (BZ41); `sold` includes the commodity units estimated sold before first sight, also
        kept as `unseen` (BZ45).
    """
    totals: dict[tuple[int, int], dict[int, list[float]]] = {
        gap: {key: [0.0] * 10 for key in sorted(lt.seen[gap])} for gap in lt.gaps}
    for e in lt.endings:
        row = totals[e.gap][e.key]
        left = 0.0 if e.cancelled else 1.0 - e.expired       # a cancel is neither sold nor expired (BZ40)
        row[0] += e.qty * left
        row[1] += e.qty * e.expired
        row[3] += 1.0
        row[4] += e.expired
        row[5] += 1.0 if e.matched else 0.0
        if e.cancelled:
            row[6] += e.qty
            row[7] += 1.0
        if e.tier1:
            row[8] += 1.0
            row[9] += left
    for _, key, units, gap, _ in lt.fills:
        totals[gap][key][0] += units
    for _, key, units, gap, *_ in lt.listed:
        totals[gap][key][2] += units
    early = {(e.auction, e.gap): e.qty * (1.0 - e.expired) for e in lt.endings if not e.cancelled}
    unseen = unseen_sales(lt.gaps, lt.first_seen, lt.fills, early)
    for gap, by_key in unseen.items():
        for key, units in by_key.items():
            totals[gap][key][0] += units                       # sold includes the estimate (BZ45)
    return {gap: {key: LifeFlow(*row, commodity=key in lt.commodities, unseen=unseen.get(gap, {}).get(key, 0.0))
                  for key, row in by_key.items()} for gap, by_key in totals.items()}
