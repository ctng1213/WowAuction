"""The batch lifetimes pass and the ingest that ran it, as they were before CF1 and CF2 (frozen; VI8's reference).

`engine.lifetimes.lifetimes` is the fold of `step` since CF1 (docs/plans/carry-forward-lifetimes.md, D3). VI8 checks
that fold against this copy of the pass it replaced, so the two must give byte-identical records on worlds where a
key's commodity flag never changes (D7: the fold pools by the flags known at the time, this pass by the union over
every snapshot it read). Copied verbatim from `ahsync/engine/volume.py` and `ahsync/engine/expiry.py` at `main`
148e390, and `batch_ingest` with its day recompute from `ahsync/collect/observations.py` at `main` cf248d6 (the
history before the carried state: one `lifetimes` pass per day touched); only the names of the helpers they share
with the code are imported. Never edit it to make VI8 or a CF2 scenario pass. One known difference from the
cf248d6 ingest: `batch_recompute_days` calls this file's pre-CF1 `lifetimes`, which pools the commodity flag over
every snapshot it read, where cf248d6's pass pooled by the flags known at the time (D7); the two agree whenever a
key's flag never changes, as in every world the tests build with it.
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import replace

from ahsync.engine.expiry import PRIOR_LEAVES, RATE_WINDOW_MS, expired_fraction, expired_given_vanished
from ahsync.engine.lifetimes import (DURATION_MS, Lifetimes, LifeFlow, band_window, lowest_other, lowest_two,
                                     match_relists, narrow)
from ahsync.collect.local_scans import (HOUR_MS, book_days, recompute_book_volume, recompute_sales_hours,
                                       store_scan_prices)
from ahsync.collect.provider import SnapshotObservation, snapshot_problems
from ahsync.collect.snapshots import SnapshotStore
from ahsync.db.sqlite import BookScan, Database
from ahsync.engine.lifetime_prices import new_listing_positions, sold_prices
from ahsync.engine.lifetimes import LIFETIME_LOOKBACK_MS, gap_hours, sold_by_hour
from ahsync.engine.unseen import unseen_sales
from ahsync.engine.volume import (ID_METHOD_VERSION, MAX_GAP_MS, SALES_PAIR_MAX_GAP_MS, Auction, IdBook,
                                  covered_seconds, group_book)

DAY_MS = 86_400_000


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


# --- the ingest before CF2: each snapshot recomputes every day it touches with one pass (frozen from cf248d6) ---

def batch_ingest(db: Database, store: SnapshotStore, obs: SnapshotObservation, id_volume: bool = True) -> set[int]:
    """Store a provider's snapshot and everything derived from it (V36).

    Args:
        db: Database.
        store: The raw snapshot store.
        obs: The snapshot.
        id_volume: Recompute the id volume of the days it touches now; False when the caller ingests a batch and
            calls `recompute_id_days` once for all of them (the cloud's daily run: each day's lifetimes read 36 h before it).

    Returns:
        Item keys whose prices or volume changed (without the id volume when id_volume is False).

    Raises:
        ValueError: The snapshot is malformed (provider.snapshot_problems); nothing is stored.
    """
    problems = snapshot_problems(obs)
    if problems:
        raise ValueError(f"{obs.src} snapshot at {obs.at_ms} refused: " + "; ".join(problems))
    store.put(obs)
    entries = [(e.key, e.price, e.stack, e.band, e.count) for e in group_book(IdBook(obs.at_ms, obs.auctions)).entries]
    scan_id = f"{obs.src}:{obs.market}:{obs.at_ms}"
    with db.transaction():
        db.store_book(BookScan(scan_id, obs.market, obs.src, None, obs.at_ms), entries)
        dirty = store_scan_prices(db, obs.market, scan_id, obs.at_ms, obs.src)
        db.add_observed_hours(obs.market, obs.src, [obs.at_ms // HOUR_MS])   # a snapshot is always complete (SRC16)
        recompute_sales_hours(db, obs.market, [obs.at_ms])
        for day in sorted(book_days(db, obs.market, [obs.at_ms])):
            dirty |= set(recompute_book_volume(db, obs.market, day))
        if id_volume:
            dirty |= batch_recompute_days(db, store, obs.market, obs.src, id_days(store, obs.market, obs.src, obs.at_ms))
    return dirty


def id_days(store: SnapshotStore, market: str, src: str, at_ms: int) -> set[int]:
    """The days whose id volume a new snapshot changes.

    Args:
        store: The snapshot store.
        market: Market id.
        src: Source name.
        at_ms: The new snapshot's time.

    Returns:
        From the day 2 h before it (its book pairs rewrite those hours of `sales_h1`, which the ids own) to the day of
        the last snapshot up to LIFETIME_LOOKBACK_MS after it (its sightings shape the windows of auctions ending
        that late, M33a review).
    """
    later = store.times(market, src, at_ms + 1, at_ms + LIFETIME_LOOKBACK_MS)
    last = later[-1] if later else at_ms
    return set(range((at_ms - SALES_PAIR_MAX_GAP_MS) // DAY_MS, last // DAY_MS + 1))


def batch_recompute_days(db: Database, store: SnapshotStore, market: str, src: str, days: set[int]) -> set[int]:
    """Some days' id volume, ended listings, sold prices and hourly sales of one source, from auction lifetimes (SPEC §2.4).

    One `lifetimes` pass per day, over the snapshots from LIFETIME_LOOKBACK_MS (66 h: an ending's life and posting gaps,
    plus the day of gaps its early-leave rate reads, BZ44) before it to MAX_GAP_MS
    after it, keeping only the records of gaps that end from MAX_GAP_MS before the day on, so memory is one day's.
    A gap counts for the day of its later snapshot (its cover too); its sold units are credited to its clock hours,
    which replace those hours' book-pair rates in `sales_h1` (ids rank above groups, §3.3.1); every gap crediting
    a written hour is in the pass, so an hour pooled across midnight comes out the same in any order. A day's
    result does not depend on which other days are recomputed (an ending in it needs at most 36 h before it).

    Args:
        db: Database, inside the caller's transaction.
        store: The snapshot store.
        market: Market id.
        src: Source name.
        days: Day numbers.

    Returns:
        The item keys written (each day's rows are replaced whole).
    """
    written: set[int] = set()
    for day in sorted(days):
        day_ms = day * DAY_MS
        times = store.times(market, src, day_ms - LIFETIME_LOOKBACK_MS, day_ms + DAY_MS - 1 + MAX_GAP_MS)
        lt = lifetimes((store.load(market, src, at) for at in times), keep_from_ms=day_ms - MAX_GAP_MS)
        flows, positions, sold = life_flows(lt), new_listing_positions(lt), sold_prices(lt)
        gaps = [g for g in sorted(flows) if g[1] // DAY_MS == day]
        volume: dict[int, list[float]] = {}
        ended: dict[int, list[float]] = {}
        unseen: dict[int, float] = {}
        for gap in gaps:
            for key, f in flows[gap].items():
                row = volume.setdefault(key, [0.0, 0.0, 0.0])
                row[0] += f.sold
                row[1] += f.expired
                row[2] += f.listed
                unseen[key] = unseen.get(key, 0.0) + f.unseen
                if f.ended:
                    tally = ended.setdefault(key, [0.0] * 8)
                    for i, value in enumerate((f.ended, f.expired_listings, f.matched, f.cancels, f.cancelled,
                                               f.t1_ended, f.t1_left)):
                        tally[i] += value
                    tally[7] = max(tally[7], float(f.commodity))
        db.replace_volume_day(market, day, src, {k: tuple(v) for k, v in sorted(volume.items())}, ID_METHOD_VERSION,
                              ("ids", covered_seconds(gaps)) if gaps else None, unseen)
        db.replace_lifetimes_day(market, day, src, {k: tuple(v) for k, v in sorted(ended.items())})
        db.replace_new_listings_day(market, day, src, merge_positions(positions.get(g, {}) for g in gaps))
        db.replace_sold_prices_day(market, day, src, merge_positions(sold.get(g, {}) for g in gaps))   # BZ52
        hours = {h for gap in gaps for h in gap_hours(gap)}
        rates = sold_by_hour(flows, hours)
        for first, last in _runs(sorted(hours)):
            db.replace_sales_hours(market, first, last, rates)
        written |= set(volume)
    return written


def merge_positions(per_gap: Iterable[dict[int, dict[int, float]]]) -> dict[int, dict[int, float]]:
    """Several gaps' new-listing positions (or sold prices, BZ52) summed per item and position (or price).

    Args:
        per_gap: key -> position -> units, one mapping per gap.

    Returns:
        key -> position -> units.
    """
    out: dict[int, dict[int, float]] = {}
    for by_key in per_gap:
        for key, by_position in by_key.items():
            mine = out.setdefault(key, {})
            for position, units in by_position.items():
                mine[position] = mine.get(position, 0.0) + units
    return out


def _runs(hours: list[int]) -> list[tuple[int, int]]:
    """Consecutive hour starts merged into ranges.

    Args:
        hours: Hour starts in ms, ascending.

    Returns:
        (first, last) hour starts of each run of consecutive hours.
    """
    runs: list[list[int]] = []
    for hour in hours:
        if runs and hour == runs[-1][1] + HOUR_MS:
            runs[-1][1] = hour
        else:
            runs.append([hour, hour])
    return [(a, b) for a, b in runs]
