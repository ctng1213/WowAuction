"""Auction lifetimes: every auction id followed from first sight to its end (SPEC §2.4 Auction lifetimes).

Pure. `step` extends or finishes the auctions of a carried `LifeState` with one more snapshot and returns what that
gap learned as a `GapRecord`; nothing in a record depends on a later snapshot. `lifetimes` folds `step` over a list
of snapshots (the day's pass, the reports and the simulator's fits), so a pass and the carried state are one
computation (docs/plans/carry-forward-lifetimes.md, D3; VI8). `life_flows`, `gap_flows` and `sold_by_hour` sum the
records per item, gap and hour. Moved out of `engine/volume.py` in CF1.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from ahsync.engine.expiry import (RATE_WINDOW_MS, LeaveWindow, expired_fraction, expired_given_vanished,
                                  tally_early_leaves)
from ahsync.engine.unseen import unseen_sales
from ahsync.engine.volume import HOUR_MS, ID_BAND_HOURS, MAX_GAP_MS, Auction, IdBook


@dataclass(frozen=True, slots=True)
class Ending:
    """How one auction left the AH (SPEC §2.4 Auction lifetimes, BZ22, BZ26, BZ27)."""

    auction: int
    key: int
    qty: int                      # units when last seen
    gap: tuple[int, int]          # (last seen ms, first absent ms)
    expired: float                # the share of it that expired, 0-1; the rest left early, counted sold
    matched: bool                 # a new listing of the same key and units appeared in the gap: a possible relist,
                                  # measured, never subtracted (SPEC §2.4 item 5; the user, 2026-11-07)
    cancelled: bool = False       # a commodity that left early while a cheaper one of its item stayed: no sale (BZ40)
    tier1: bool | None = None     # posted at or below the lowest other price; None when its posting wasn't seen (BZ41)
    overlap: float = 0.0          # the gap's overlap with its expiry window alone, 0-1 (`expired_fraction`): 0 = it
                                  # could not have expired; `expired` weighs selling against it (BZ44)
    price: int | None = None      # unit buyout, copper, at its last sighting: a sale's price; None = unknown, no
                                  # sold price (SPEC §2.4, BZ52)


@dataclass(frozen=True)
class Lifetimes:
    """Every auction followed through consecutive id snapshots (SPEC §2.4 Auction lifetimes)."""

    gaps: tuple[tuple[int, int], ...]                      # (earlier ms, later ms) of linked snapshots, ascending
    seen: dict[tuple[int, int], frozenset[int]]            # gap -> item keys listed in either of its snapshots
    endings: tuple[Ending, ...]
    fills: tuple[tuple[int, int, int, tuple[int, int], int], ...]   # (auction, key, units bought, gap, unit price)
    # (auction, key, units, gap, unit price, lowest other price of the key in that snapshot or None): new listings
    # first seen after a linked gap; the price against the book feeds the undercut share (SPEC §2.4, BZ29)
    listed: tuple[tuple[int, int, int, tuple[int, int], int, int | None], ...]
    clashes: int                  # sightings whose band contradicted the expiry window (checks the band bounds, BZ24)
    commodities: frozenset[int] = frozenset()   # item keys seen in Blizzard's commodities list (BZ40, BZ41)
    first_seen: tuple[tuple[int, int, tuple[int, int], bool], ...] = ()   # (auction, key, posting gap, commodity), kept or not (BZ45)
    records: tuple[GapRecord, ...] = ()          # the kept gaps' records, from which the fields above are gathered


@dataclass(frozen=True, slots=True)
class LifeFlow:
    """One item's lifetimes over one gap: units, and listings that ended (SPEC §2.4)."""

    sold: float = 0.0             # units: partial fills plus what left early
    expired: float = 0.0          # units
    listed: float = 0.0           # units of new listings
    ended: float = 0.0            # listings that ended
    expired_listings: float = 0.0
    matched: float = 0.0          # ended listings matched to a new listing of the same key and units (BZ27)
    cancelled: float = 0.0        # units of commodity cancels (BZ40): in neither sold nor expired
    cancels: float = 0.0          # listings cancelled (BZ40)
    t1_ended: float = 0.0         # ended listings that were posted at the lowest price (BZ41)
    t1_left: float = 0.0          # of those, the share that left early and was not a cancel, summed (BZ41)
    commodity: bool = False       # the item was seen in the commodities list (BZ41: measured, not estimated)
    unseen: float = 0.0           # of `sold`, units estimated sold before their listings were first seen (BZ45)


# How long an auction runs, from its band when first seen just after posting (SPEC §2.4; §2.2's 2, 8 and 24 h).
# Provisional until a week of lifetimes is measured in M33b (BZ24, §6 item 5).
DURATION_MS = {4: 24 * HOUR_MS, 3: 8 * HOUR_MS, 2: 2 * HOUR_MS, 1: 2 * HOUR_MS}
# How far before a day its lifetimes must start so the day's result is exact (SPEC §2.4): an ending in the day was last
# seen at most MAX_GAP_MS before it, posted at most 24 h before that, and its posting gap began up to MAX_GAP_MS earlier.
# A day's pass starts this early: an ending's life, posting and narrowing gaps, then its early-leave rate's window (BZ44).
LIFETIME_LOOKBACK_MS = 24 * HOUR_MS + 3 * MAX_GAP_MS + RATE_WINDOW_MS


def band_window(at_ms: int, band: int) -> tuple[int, int]:
    """When an auction seen with a band can expire (Blizzard's band bounds, ID_BAND_HOURS; SPEC §2.4).

    Args:
        at_ms: The sighting's time.
        band: Its time-left code 1-4.

    Returns:
        (start, end] in ms.
    """
    lo, hi = ID_BAND_HOURS[band]
    return at_ms + int(lo * HOUR_MS), at_ms + int(hi * HOUR_MS)


def narrow(window: tuple[int, int], at_ms: int, band: int) -> tuple[tuple[int, int], bool]:
    """An expiry window narrowed by one more sighting's band (SPEC §2.4 item 2, BZ26).

    Args:
        window: The expiry window so far, (start, end] ms.
        at_ms: The sighting's time.
        band: Its time-left code.

    Returns:
        (the narrowed window, False); when the two don't overlap, (the sighting's own band window, True): the
        newest evidence wins and the clash is counted.
    """
    lo, hi = band_window(at_ms, band)
    start, end = max(window[0], lo), min(window[1], hi)
    return ((start, end), False) if start < end else ((lo, hi), True)


def match_relists(vanished: list[tuple], new: list[Auction], gap: tuple[int, int]) -> list[Ending]:
    """Endings of one gap, each matched to a new listing of the same item and units if one is free (BZ27).

    A cancel looks like a sale and the API names no seller; the only clue is a new id for the same key (item and
    bonus list) and units in the same gap. Measured on house 114 such a listing is as likely three hours later
    (0.70) as in the vanish gap (0.68), so a match is counted, never subtracted. Each new listing matches at most
    one ending, lowest ids first; only an ending that may have left early (expired < 1) is matched.

    Args:
        vanished: (auction, key, units, expired share, cancelled, tier1, unit price) of the auctions gone in the
            gap, by auction id (`cancelled`, `tier1` and `price` as Ending's).
        new: The auctions first seen after it, by auction id.
        gap: (earlier ms, later ms).

    Returns:
        The Endings, in auction id order.
    """
    free: dict[tuple[int, int], list[int]] = {}
    for a in new:
        free.setdefault((a.key, a.qty), []).append(a.id)
    endings = []
    for auction, key, qty, expired, cancelled, tier1, price in vanished:
        ids = free.get((key, qty))
        matched = bool(ids) and expired < 1.0
        if matched:
            ids.pop(0)
        endings.append(Ending(auction, key, qty, gap, expired, matched, cancelled, tier1, price=price))
    return endings


def lowest_two(auctions: Sequence[Auction]) -> dict[int, list[tuple[int, int]]]:
    """Each key's two cheapest auctions in a snapshot.

    Args:
        auctions: The snapshot's auctions.

    Returns:
        key -> up to two (unit price, auction id), cheapest first (ties: lower id).
    """
    out: dict[int, list[tuple[int, int]]] = {}
    for a in auctions:
        best = out.setdefault(a.key, [])
        best.append((a.price, a.id))
        if len(best) > 2:
            best.sort()
            best.pop()
    return {key: sorted(best) for key, best in out.items()}


def lowest_other(best: list[tuple[int, int]], auction: int) -> int | None:
    """The cheapest price of the key's other auctions (the book a new listing joined, SPEC §2.4).

    Args:
        best: The key's two cheapest (price, id), from `lowest_two`.
        auction: The new listing's id.

    Returns:
        Copper per unit; None when it is the only auction of its key.
    """
    others = [price for price, i in best if i != auction]
    return others[0] if others else None


def vanished_in(live: Mapping[int, tuple], current: Sequence[Auction], at_ms: int) -> list[tuple]:
    """The auctions gone from a snapshot, each with its expired share and whether it was a cancel (BZ22, BZ40).

    A commodity that left early (expired share 0: it could not have expired) while a cheaper auction of its item
    survived the gap with its units unchanged was cancelled: commodity buys take the cheapest units first (measured,
    `docs/plans/blizzard-cloud-sales.md`), so a buyer would have taken the cheaper one. Equal prices are no proof.

    Args:
        live: Auction id -> (key, units, expiry window, unit price, commodity, tier1, posting gap), as of the
            earlier snapshot (`LifeState.live`).
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
    for auction, (key, units, window, price, commodity, tier1, _) in sorted(live.items()):
        if auction in ids:
            continue
        expired = expired_fraction(window, at_ms)
        cancelled = commodity and expired == 0.0 and kept.get(key, price) < price
        out.append((auction, key, units, expired, cancelled, tier1, price))
    return out


@dataclass(frozen=True)
class LifeState:
    """What the lifetimes carry from one snapshot to the next (SPEC §2.4 Auction lifetimes, carry-forward D3).

    `live` maps each auction of the newest snapshot to (key, units, expiry window (start, end] ms, unit price at its
    last sighting, commodity flag, tier1 or None, posting gap or None): the posting gap is the linked gap it was
    first seen after, None when it was first seen in the first snapshot or after a hole. The keys of the newest
    snapshot are the keys of `live`.
    """

    prev_at: int | None = None                       # the newest snapshot stepped, UTC ms; None before the first
    live: Mapping[int, tuple] = field(default_factory=dict)
    leaves: LeaveWindow = field(default_factory=LeaveWindow)   # the early-leave tallies of the last day (BZ44)


@dataclass(frozen=True)
class GapRecord:
    """What one linked gap learned: everything the day's and hours' rows need from it (SPEC §2.4).

    Final when written: nothing in it depends on a later snapshot, so the fold of a day's records equals the pass.
    """

    gap: tuple[int, int]                             # (earlier ms, later ms)
    seen: frozenset[int]                             # item keys listed in either snapshot
    endings: tuple[Ending, ...]                      # by auction id, with their final expired share (BZ44)
    fills: tuple[tuple[int, int, int, tuple[int, int], int], ...]          # as Lifetimes.fills
    listed: tuple[tuple[int, int, int, tuple[int, int], int, int | None], ...]   # as Lifetimes.listed
    first_seen: tuple[tuple[int, int, tuple[int, int], bool], ...]         # listings first seen at its end (BZ45)
    unseen: dict[int, float]                         # key -> units estimated sold before first sight, credited here
    commodities: frozenset[int]                      # keys flagged commodity in it (D7: the flags known at the time)
    clashes: int                                     # sightings whose band contradicted the expiry window (BZ24)


@dataclass
class _Sightings:
    """The later snapshot of a gap read against the state: the new live auctions and what the sightings showed."""

    live: dict[int, tuple]
    new: list[Auction]
    fills: list[tuple[int, int, int, tuple[int, int], int]]
    listed: list[tuple[int, int, int, tuple[int, int], int, int | None]]
    first_seen: list[tuple[int, int, tuple[int, int], bool]]
    clashes: int


def step(state: LifeState, book: IdBook, max_gap_ms: int = MAX_GAP_MS) -> tuple[LifeState, GapRecord | None]:
    """Extend or finish every auction of the state with one more snapshot (SPEC §2.4 Auction lifetimes).

    A snapshot more than max_gap_ms after the state's doesn't link: nothing carries over the
    hole, and every auction starts again with its band's window. Otherwise each auction gone since the state is
    finished (`vanished_in`, its expired share weighed with the item's early-leave rate over the day before,
    BZ44), each still listed narrows its window and may show a partial fill, and each new one is placed against
    the book (BZ41) and given its posting window. The state is never changed.

    Args:
        state: The lifetimes so far; `LifeState()` before the first snapshot.
        book: The next snapshot, later than the state's, its auction ids unique (`provider.snapshot_problems`
            refuses a snapshot listing an id twice, FM33).
        max_gap_ms: Largest gap between linked snapshots, ms.

    Returns:
        (the state after the snapshot, the gap's record or None when the snapshot didn't link).

    Raises:
        ValueError: The snapshot is not later than the state's (a repeat or a late snapshot is the caller's to
            replay, never a silent restart).
    """
    if state.prev_at is not None and book.at_ms <= state.prev_at:
        raise ValueError(f"snapshot at {book.at_ms} is not after the lifetimes' state at {state.prev_at}")
    current = sorted(book.auctions, key=lambda a: a.id)
    if state.prev_at is None or book.at_ms - state.prev_at > max_gap_ms:
        live = {a.id: (a.key, a.qty, band_window(book.at_ms, a.band), a.price, a.commodity, None, None) for a in current}
        return LifeState(book.at_ms, live, state.leaves), None
    old, gap = state.live, (state.prev_at, book.at_ms)
    vanished = vanished_in(old, current, book.at_ms)
    leaves = state.leaves.advance(book.at_ms, tally_early_leaves(old, vanished, gap[1] - gap[0], gap[1]))
    sight = _sightings(old, current, gap)
    endings = tuple(replace(e, overlap=e.expired, expired=expired_given_vanished(
                        old[e.auction][2], e.gap, leaves.rate(e.key, old[e.auction][4])))
                    if 0.0 < e.expired else e for e in match_relists(vanished, sight.new, gap))
    commodities = frozenset({v[1] for v in vanished if old[v[0]][4]} | {a.key for a in current if a.commodity})
    record = GapRecord(gap, frozenset(s[0] for s in old.values()) | frozenset(a.key for a in current), endings,
                       tuple(sight.fills), tuple(sight.listed), tuple(sight.first_seen),
                       _unseen(old, gap, endings, sight.fills), commodities, sight.clashes)
    return LifeState(book.at_ms, sight.live, leaves), record


def _sightings(old: Mapping[int, tuple], current: list[Auction], gap: tuple[int, int]) -> _Sightings:
    """Read a gap's later snapshot against the auctions live at its start (SPEC §2.4 items 1–3, BZ26, BZ41).

    Args:
        old: The state's live auctions, as `LifeState.live`.
        current: The later snapshot's auctions, by id.
        gap: (earlier ms, later ms).

    Returns:
        The live auctions after the snapshot, the new listings, partial fills (units bought at the auction's
        price), new listings with their lowest other price, first sightings, and band clashes (a new listing first
        seen SHORT is expected to clash, SPEC §2.4 item 2, so it isn't counted).
    """
    at = gap[1]
    lowest = lowest_two(current)                     # every posting's tier, kept or not (BZ41)
    out = _Sightings({}, [], [], [], [], 0)
    for a in current:
        state = old.get(a.id)
        if state is None:
            out.new.append(a)
            other = lowest_other(lowest[a.key], a.id)
            out.listed.append((a.id, a.key, a.qty, gap, a.price, other))
            out.first_seen.append((a.id, a.key, gap, a.commodity))
            window, clash = narrow((gap[0] + DURATION_MS[a.band], at + DURATION_MS[a.band]), at, a.band)
            out.clashes += clash and a.band != 1
            out.live[a.id] = (a.key, a.qty, window, a.price, a.commodity, other is None or a.price <= other, gap)
            continue
        key, units, window, _, commodity, tier1, posted = state
        if a.qty < units:
            out.fills.append((a.id, a.key, units - a.qty, gap, a.price))
            units = a.qty
        window, clash = narrow(window, at, a.band)
        out.clashes += clash
        # a snapshot stored before M33b reads False (review, M33b-1); the last sighting's price is the one it sold at (BZ52)
        out.live[a.id] = (key, units, window, a.price, commodity or a.commodity, tier1, posted)
    return out


def _unseen(old: Mapping[int, tuple], gap: tuple[int, int], endings: tuple[Ending, ...],
            fills: list[tuple[int, int, int, tuple[int, int], int]]) -> dict[int, float]:
    """The units sold before first sight by the listings first seen at a gap's start, credited to it (BZ45).

    Args:
        old: The state's live auctions; those whose posting gap ends at the gap's start were first seen there,
            each with its own commodity flag from that sighting.
        gap: (earlier ms, later ms).
        endings: The gap's endings, with their final expired share.
        fills: The gap's partial fills.

    Returns:
        Item key -> units (`unseen.unseen_sales`); empty when nothing was first seen at its start or none of it sold.
    """
    posted = sorted((auction, s[0], s[6], s[4]) for auction, s in old.items() if s[6] is not None and s[6][1] == gap[0])
    early = {(e.auction, e.gap): e.qty * (1.0 - e.expired) for e in endings if not e.cancelled}
    return unseen_sales([gap], posted, fills, early).get(gap, {})


def lifetimes(books: Iterable[IdBook], max_gap_ms: int = MAX_GAP_MS, keep_from_ms: int | None = None) -> Lifetimes:
    """Follow every auction id from first sight to its end: `step` folded over the snapshots (SPEC §2.4, D3).

    Args:
        books: Snapshots of one market, oldest first; read once, so a generator keeps one in memory at a time.
        max_gap_ms: Largest gap between linked snapshots, in ms.
        keep_from_ms: Keep the records (gaps, endings, fills, listings) only of gaps ending at or after this; the
            earlier snapshots still build each auction's window and the early-leave rates. None keeps everything.

    Returns:
        The Lifetimes; auctions still listed at the end have no ending. Clashes and first sightings count every
        linked gap, kept or not.
    """
    state, kept, first_seen, clashes = LifeState(), [], [], 0
    for book in books:
        state, record = step(state, book, max_gap_ms)
        if record is None:
            continue
        clashes += record.clashes
        first_seen.extend(record.first_seen)
        if keep_from_ms is None or record.gap[1] >= keep_from_ms:
            kept.append(record)
    return Lifetimes(tuple(r.gap for r in kept), {r.gap: r.seen for r in kept},
                     tuple(e for r in kept for e in r.endings), tuple(f for r in kept for f in r.fills),
                     tuple(x for r in kept for x in r.listed), clashes,
                     frozenset().union(*(r.commodities for r in kept)), tuple(first_seen), tuple(kept))


def life_flows(lt: Lifetimes) -> dict[tuple[int, int], dict[int, LifeFlow]]:
    """Each gap's lifetimes summed per item (SPEC §2.4 "What the lifetimes feed").

    Args:
        lt: From `lifetimes`.

    Returns:
        gap -> item key -> LifeFlow, as `gap_flows` gives each kept gap.

    Raises:
        ValueError: `lt` has gaps but no records (it wasn't made by `lifetimes`).
    """
    if lt.gaps and not lt.records:
        raise ValueError("life_flows needs the records of lifetimes()")
    return {r.gap: gap_flows(r) for r in lt.records}


def gap_flows(record: GapRecord) -> dict[int, LifeFlow]:
    """One gap's lifetimes summed per item (SPEC §2.4 "What the lifetimes feed").

    Args:
        record: From `step`.

    Returns:
        Item key -> LifeFlow, for every item listed in either snapshot of the gap (zeros included, so an observed
        hour with no sale reads 0, not missing). An ending's units split into expired (its expired share) and left
        early, which is sold unless it was a commodity cancel (BZ40); tier-1 endings are tallied for the
        sell-through (BZ41); `sold` includes the commodity units estimated sold before first sight, also kept as
        `unseen` (BZ45); `commodity` is the gap's own flag (D7).
    """
    totals = {key: [0.0] * 10 for key in sorted(record.seen)}
    for e in record.endings:
        row = totals[e.key]
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
    for _, key, units, _, _ in record.fills:
        totals[key][0] += units
    for _, key, units, *_ in record.listed:
        totals[key][2] += units
    for key, units in record.unseen.items():
        totals[key][0] += units                              # sold includes the estimate (BZ45)
    return {key: LifeFlow(*row, commodity=key in record.commodities, unseen=record.unseen.get(key, 0.0))
            for key, row in totals.items()}


def gap_hours(gap: tuple[int, int]) -> list[int]:
    """The clock hours a gap's sales are credited to (SPEC §2.4 item 4, BZ30).

    Args:
        gap: (earlier ms, later ms).

    Returns:
        Hour starts in ms: from the earlier snapshot's hour, as many as the gap is long in whole hours (rounded
        half up, at least one).
    """
    count = max(1, math.floor((gap[1] - gap[0]) / HOUR_MS + 0.5))
    first = gap[0] // HOUR_MS * HOUR_MS
    return [first + k * HOUR_MS for k in range(count)]


def sold_by_hour(flows: dict[tuple[int, int], dict[int, LifeFlow]], hours: set[int] | None = None) -> dict[int, dict[int, float]]:
    """Sales rate per clock hour from lifetimes: the vanish-hour credit (SPEC §2.4 item 4, §3.3 sales_how, BZ30).

    A gap's sold units are spread evenly over its hours (`gap_hours`), each hour taking an even share of the gap's
    time too; an hour credited by several gaps pools their units and time, as `hourly_sales` pools book pairs.

    Args:
        flows: From `life_flows`.
        hours: Only these hour starts (ms); None for every credited hour.

    Returns:
        Item key -> hour start ms -> units sold per hour; 0 for an item listed in the gap that sold nothing.
    """
    units: dict[int, dict[int, float]] = {}
    time_ms: dict[int, float] = {}
    for gap in sorted(flows):
        credited = gap_hours(gap)
        share = (gap[1] - gap[0]) / len(credited)
        for hour in credited:
            if hours is not None and hour not in hours:
                continue
            time_ms[hour] = time_ms.get(hour, 0.0) + share
            for key, flow in flows[gap].items():
                by_hour = units.setdefault(key, {})
                by_hour[hour] = by_hour.get(hour, 0.0) + flow.sold / len(credited)
    return {key: {hour: u * HOUR_MS / time_ms[hour] for hour, u in sorted(by_hour.items())}
            for key, by_hour in sorted(units.items())}


