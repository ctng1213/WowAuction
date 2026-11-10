"""Inference against truth: what the cloud analysis infers from simulated raw, beside the simulator's truth (SPEC §8.10).

Each `sm*` function runs one SM row's world through the unchanged cloud job (or, where the row tests a report
estimator, through the lifetimes alone) and returns its measures: the engine's number, the truth's, and the row's
pass band. The tests (`tests/tools/sim/test_inference.py`) assert the bands; `tools/sim_report.py` prints the same
measures as the inference report. The worlds are hand-shaped (plan §1.28) with the seller behaviour measured on the
31 real hours (plan §1.27(b)): durations, positions against the cheapest price at first sight, and stack sizes.

What a snapshot can see is the comparison for sales (plan §1.28): units bought after their listing was first in a
snapshot, over the gaps the engine's 7-day window links, with the V41 prior applied as the engine applies it
(plan §1.29). A listing bought out before any snapshot never reaches the analysis.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from dataclasses import dataclass
from pathlib import Path

from ahsync.engine import sellprob
from ahsync.engine.volume import MAX_GAP_MS, Lifetimes, VolumePrior, lifetimes, posterior_rate
from ahsync.intel.contract import MarketIntelligence
from lifetimes_report import relist_estimate
from sim.market import DAY_MS, HOUR_MS, Item, World
from sim.runner import Run, market_books, simulate
from sim.truth import Truth

START_MS = 1_793_923_200_000             # Fri 2026-11-06 00:00 UTC, the collected raw's first day
# Measured on the 31 real hours (plan §1.27(b)): durations; new commodity listings' price ÷ the cheapest other at
# first sight (0.8% under, 42.8% at, 18.9% up to 5% above, 16.1% to 15%, 15.1% to 50%, 6.4% over); first-seen
# commodity stack sizes, rounded to five sizes; gear's 81% at the cheapest price.
REAL_DURATIONS = ((2, 0.10), (8, 0.77), (24, 0.13))
REAL_PRICE_MIX = ((0.008, 0.90, 1.00), (0.428, 1.00, 1.00), (0.189, 1.00, 1.05), (0.161, 1.05, 1.15),
                  (0.151, 1.15, 1.50), (0.064, 1.50, 2.50))
REAL_STACKS = ((1, 0.30), (2, 0.15), (5, 0.20), (10, 0.10), (20, 0.25))
GEAR_PRICE_MIX = ((0.81, 1.00, 1.00), (0.19, 1.00, 1.50))
ANCHOR = 1.1                             # sellers price off at most 1.1 × fair, so the floor mean-reverts (§1.27(c))
SM2_POSITIONS = ((0.1, 0.90, 0.90), (0.2, 0.98, 0.98), (0.4, 1.00, 1.00), (0.3, 1.10, 1.10))
SM4_SHIFT = 3                            # the relist estimator's placebo: gaps later (tools/lifetimes_report.py)
SM5_SAMPLES = (0, 10, 30, 100, 300)      # tier-1 endings behind the blend
PLANNED_BLEND = (0.5, 30, 0.9, 300)      # SPEC §2.4's planned weight: 0.5 at 30 endings, rising to 0.9 at 300 (M33b)
COMMODITY_CANCEL, GEAR_CANCEL = 0.10, 0.10   # SM3: posts cancelled, for about 10.8% and 20% of early leaves
GOLD = 10_000                            # copper


@dataclass(frozen=True)
class Measure:
    """One number the analysis inferred, against the truth."""

    row: str                             # the scenario ID
    what: str                            # the quantity, in words
    engine: float | None                 # what the analysis says
    truth: float | None                  # what happened
    band: str                            # the row's pass band, or "reported"
    passed: bool | None = None           # None for a reported number

    @property
    def gap(self) -> float | None:
        """engine ÷ truth − 1; None without both or with a zero truth.

        Returns:
            The relative gap.
        """
        if self.engine is None or not self.truth:
            return None
        return self.engine / self.truth - 1


def commodity(key: int, name: str, fair: int, buys: float, listings: float, **shape: Any) -> Item:
    """A commodity whose sellers price, post and stack as the real raw's do (plan §1.27(b)).

    Args:
        key: Item key.
        name: Item name.
        fair: Fair price, copper.
        buys: Buyer arrivals a day.
        listings: Seller posts a day.
        **shape: Any other `Item` field, overriding the real-shaped default.

    Returns:
        The Item.
    """
    fields = dict(durations=REAL_DURATIONS, price_mix=REAL_PRICE_MIX, stacks=REAL_STACKS, anchor=ANCHOR,
                  max_units=10, vendor_sell=max(1, fair // 4))
    fields.update(shape)
    return Item(key, name, True, fair, buys, listings, **fields)


def gear(key: int, name: str, fair: int, buys: float, listings: float, **shape: Any) -> Item:
    """A gear item (a single unit an auction, bought whole) shaped like the real raw's gear.

    Args:
        key: Item key.
        name: Item name.
        fair: Fair price, copper.
        buys: Buyer arrivals a day.
        listings: Seller posts a day.
        **shape: Any other `Item` field.

    Returns:
        The Item.
    """
    fields = dict(stack=1, durations=REAL_DURATIONS, price_mix=GEAR_PRICE_MIX, anchor=ANCHOR,
                  vendor_sell=max(1, fair // 5), item_class=4, quality=2)
    fields.update(shape)
    return Item(key, name, False, fair, buys, listings, **fields)


def window_gaps(snapshots: Sequence[int], now_ms: int) -> list[tuple[int, int]]:
    """The gaps the engine's 7-day volume window links (SPEC §3.3.1): the later snapshot in days now−6 to now.

    Args:
        snapshots: Snapshot times, ascending, UTC ms.
        now_ms: The document's now.

    Returns:
        (earlier, later) pairs at most MAX_GAP_MS apart.
    """
    week = range((now_ms - 7 * DAY_MS) // DAY_MS + 1, now_ms // DAY_MS + 1)
    return [(a, b) for a, b in zip(snapshots, snapshots[1:])
            if b <= now_ms and b // DAY_MS in week and b - a <= MAX_GAP_MS]


def sold_rates(run: Run, key: int, prior: VolumePrior = VolumePrior()) -> tuple[float, float]:
    """An item's true sales per day over the engine's window: what snapshots could see, and all of it.

    Args:
        run: A finished run.
        key: Item key.
        prior: The V41 prior, applied to the visible rate as the engine applies it (plan §1.29).

    Returns:
        (visible units a day with the prior, all units a day without it).
    """
    gaps = window_gaps(run.snapshots, run.snapshots[-1])
    days = sum(b - a for a, b in gaps) / DAY_MS
    seen = sum(run.truth.seen_sold_units(key, a, b, run.snapshots) for a, b in gaps)
    every = sum(run.truth.sold_units(key, a, b) for a, b in gaps)
    return posterior_rate(seen, days, prior), every / days


def seen_endings(truth: Truth, key: int, snapshots: Sequence[int], from_ms: int, to_ms: int) -> list:
    """How the listings a snapshot saw ended, in a span.

    Args:
        truth: The truth log.
        key: Item key.
        snapshots: Snapshot times, ascending.
        from_ms: Start, exclusive.
        to_ms: End, inclusive.

    Returns:
        The ending events (bought out, cancelled or expired) of seen auctions, in log order.
    """
    seen = truth.seen(key, snapshots)
    return [e for e in truth.between(key, from_ms, to_ms) if e.left == 0 and e.kind != "post" and e.auction in seen]


def engine_seen(doc: MarketIntelligence, key: int) -> float | None:
    """The engine's sold/day without its estimate of the sales before first sight (plan §1.28).

    Args:
        doc: A daily document.
        key: Item key.

    Returns:
        sold_per_day − unseen_per_day; None without a sales rate.
    """
    i = doc.items[key]
    return None if i.sold_per_day is None else i.sold_per_day - (i.unseen_per_day or 0.0)


def within(engine: float | None, truth: float | None, relative: float | None = None,
           points: float | None = None) -> bool:
    """Whether an engine number is inside a band around the truth.

    Args:
        engine: The engine's number.
        truth: The truth.
        relative: The band as a share of the truth (0.05 = ±5%).
        points: The band in absolute units (0.03 = ±3 points of a share).

    Returns:
        False without both numbers.
    """
    if engine is None or truth is None:
        return False
    limit = relative * abs(truth) if relative is not None else points
    return abs(engine - truth) <= (limit or 0.0)


def expired_measure(row: str, run: Run, key: int, points: float) -> Measure:
    """An item's 30-day expired share against the truth's over the listings a snapshot saw.

    Args:
        row: The scenario ID.
        run: A finished run.
        key: Item key.
        points: The band, ± points.

    Returns:
        The Measure.
    """
    now = run.snapshots[-1]
    ends = seen_endings(run.truth, key, run.snapshots, now - 30 * DAY_MS, now)
    truth = sum(e.kind == "expire" for e in ends) / len(ends) if ends else None
    engine = run.documents[-1].items[key].expired_share
    return Measure(row, f"{key} expired share", engine, truth, f"±{points * 100:.0f} points",
                   within(engine, truth, points=points))


def sm1(folder: Path, seed: int = 1) -> list[Measure]:
    """SM1: a steady market of three real-shaped commodities, 14 days.

    Args:
        folder: An empty folder for the run.
        seed: The world's seed.

    Returns:
        Per item: visible sold/day (±8%, plan §1.31), all sold/day (reported) and the expired share (±3 points).
    """
    items = (commodity(2589, "Linen Cloth", 50, 400, 120, cancel_share=0.06),
             commodity(4306, "Silk Cloth", 150, 250, 80, cancel_share=0.06, max_units=8),
             commodity(2592, "Wool Cloth", 100, 150, 60, cancel_share=0.06, max_units=6))
    run = simulate(World(seed, START_MS, 14, items), folder)
    doc, out = run.documents[-1], []
    for item in items:
        seen, every = sold_rates(run, item.key)
        engine = engine_seen(doc, item.key)
        out += [Measure("SM1", f"{item.key} sold/day seen", engine, seen, "±8%", within(engine, seen, 0.08)),
                Measure("SM1", f"{item.key} sold/day all", doc.items[item.key].sold_per_day, every, "reported"),
                expired_measure("SM1", run, item.key, 0.03)]
    return out


def undercut_truth(truth: Truth, key: int, now_ms: int, position: float, only: frozenset[int] | None = None
                   ) -> float | None:
    """u(p) as it happened: of the units newly listed in days now−6 to now, the share at or below a position.

    Args:
        truth: The truth log.
        key: Item key.
        now_ms: Now.
        position: The post's price ÷ the lowest price now.
        only: Only these auctions' posts (the seen ones), or all when None.

    Returns:
        The share; None without a listing that had a competitor.
    """
    first = ((now_ms - 7 * DAY_MS) // DAY_MS + 1) * DAY_MS - 1
    rows = truth.positions(key, first, now_ms, only)
    total = sum(units for _, units in rows)
    return sum(units for ratio, units in rows if ratio <= position) / total if total else None


def cancel_truth(run: Run, key: int, days: int = 30) -> float | None:
    """Of the seen listings that left early (bought out or cancelled) in the lookback, the share cancelled.

    Args:
        run: A finished run.
        key: Item key.
        days: The lookback, days (the market's cancel share reads 30, `sell_through.CANCEL_DAYS`).

    Returns:
        The share; None without an early leave.
    """
    now = run.snapshots[-1]
    early = [e for e in seen_endings(run.truth, key, run.snapshots, now - days * DAY_MS, now) if e.kind != "expire"]
    return sum(e.kind == "cancel" for e in early) / len(early) if early else None


def sm2(folder: Path, seed: int = 1) -> list[Measure]:
    """SM2: an undercut war (two bots cancel and repost 1 c under the floor hourly) over planted positions.

    Args:
        folder: An empty folder for the run.
        seed: The world's seed.

    Returns:
        Visible sold/day (±10%), the commodity cancel share over seen early leaves (±3 points), and u(p) at the
        post-now position against every new listing (±0.05) and against the seen ones (reported).
    """
    item = commodity(2589, "Linen Cloth", 50, 400, 120, price_mix=SM2_POSITIONS, cancel_share=0.1, bots=2)
    run = simulate(World(seed, START_MS, 8, (item,)), folder)
    doc, now = run.documents[-1], run.snapshots[-1]
    i = doc.items[item.key]
    seen_rate, _ = sold_rates(run, item.key)
    cancels = cancel_truth(run, item.key)
    position = i.post_price / i.cur if i.post_price and i.cur else 1.0
    u_all = undercut_truth(run.truth, item.key, now, position)
    u_seen = undercut_truth(run.truth, item.key, now, position, run.truth.seen(item.key, run.snapshots))
    engine = engine_seen(doc, item.key)
    return [Measure("SM2", "sold/day seen", engine, seen_rate, "±10%", within(engine, seen_rate, 0.10)),
            Measure("SM2", "cancel share", doc.cancel_share, cancels, "±3 points",
                    within(doc.cancel_share, cancels, points=0.03)),
            Measure("SM2", f"u(p) at {position:.3f}", i.undercut_share, u_all, "±0.05",
                    within(i.undercut_share, u_all, points=0.05)),
            Measure("SM2", f"u(p) at {position:.3f}, seen listings", i.undercut_share, u_seen, "reported")]


def tier1_truth(truth: Truth, key: int, snapshots: Sequence[int], now_ms: int, days: int = 7
                ) -> tuple[float | None, float | None, int]:
    """Sell-through at the lowest price as it happened, over the listings a snapshot saw (BZ41's quantity).

    Args:
        truth: The truth log.
        key: Item key.
        snapshots: Snapshot times, ascending.
        now_ms: Now.
        days: The lookback, in days (days now−days+1 to now).

    Returns:
        (share bought, share of the early leaves that were cancels, listings) of the seen listings posted at or
        below the lowest other price (or alone) that ended in the lookback; shares None without such a listing.
    """
    first = ((now_ms - days * DAY_MS) // DAY_MS + 1) * DAY_MS - 1
    ratios = truth.post_ratios(key, 0, now_ms)
    seen = truth.seen(key, snapshots)
    ends = [e for e in truth.between(key, first, now_ms) if e.left == 0 and e.kind != "post" and e.auction in seen
            and ratios.get(e.auction, (1.0, 0))[0] <= sellprob.TIER_LIMITS[0]]
    early = [e for e in ends if e.kind != "expire"]
    bought = sum(e.kind == "buy" for e in ends)
    return (bought / len(ends) if ends else None,
            sum(e.kind == "cancel" for e in early) / len(early) if early else None, len(ends))


def sm3(folder: Path, seed: int = 1) -> list[Measure]:
    """SM3: gear cancelled at about a true 20% of its early leaves while commodities cancel at about 10.8%.

    Args:
        folder: An empty folder for the run.
        seed: The world's seed.

    Returns:
        The commodity cancel share measured against the truth; gear's true cancel share; gear's 7-day
        sell-through against the truth and whether it is marked est. (the row's check); and its error split in
        two (reported, for M33b): the part the cancel estimate predicts, left early × (gear's true share − the
        measured commodity share), and the rest, gear sales in their expiry gap counted mostly expired (BZ44).
    """
    items = (commodity(2589, "Linen Cloth", 50, 400, 120, cancel_share=COMMODITY_CANCEL),
             gear(6585, "Scouting Gloves", 9_000, 60, 60, cancel_share=GEAR_CANCEL))
    run = simulate(World(seed, START_MS, 8, items), folder)
    doc, now = run.documents[-1], run.snapshots[-1]
    st_true, c_gear, n = tier1_truth(run.truth, 6585, run.snapshots, now)
    st, how = doc.items[6585].sell_through[0], doc.items[6585].sell_through_how[0]
    measured = doc.cancel_share or 0.0
    cancel_part = st / (1 - measured) * ((c_gear or 0.0) - measured) if st is not None else None
    error = st - st_true if st is not None and st_true is not None else None
    rest = error - cancel_part if error is not None and cancel_part is not None else None
    return [Measure("SM3", "commodity cancel share", doc.cancel_share, cancel_truth(run, 2589), "reported"),
            Measure("SM3", "gear cancel share, tier 1", None, c_gear, "reported"),
            Measure("SM3", f"gear sell-through 7d ({n} endings)", st, st_true, "marked est.", how == "e"),
            Measure("SM3", "gear error from unseen cancels", cancel_part, None, "reported"),
            Measure("SM3", "gear error from expiry-gap sales", rest, None, "reported")]


def sm4_world(seed: int, relist: float) -> World:
    """SM4's market: thirty gear items whose sellers cancel 40% of their posts and relist a share of those at once.

    Args:
        seed: The world's seed.
        relist: Share of cancels relisted (Item.relist_share).

    Returns:
        The World, 7 days.
    """
    items = tuple(gear(7000 + n, f"Gear {n}", 9_000, 6, 8, cancel_share=0.4, relist_share=relist) for n in range(30))
    return World(seed, START_MS, 7, items)


def sm4(folder: Path, seed: int = 1, relists: Sequence[float] = (0.0, 0.27, 0.66)) -> list[Measure]:
    """SM4: relists at known true rates; the report's excess-rate estimator (`lifetimes_report.relist_estimate`).

    Args:
        folder: Unused (the lifetimes are built from the snapshots in memory); kept for the common signature.
        seed: The world's seed.
        relists: Shares of cancels relisted, one world each.

    Returns:
        Per world, the estimated relisted share of gear's early leaves against the true one (±5 points).
    """
    out = []
    for relist in relists:
        world = sm4_world(seed, relist)
        truth, books = market_books(world)
        found = relist_estimate(lifetimes(books), lambda key: True, SM4_SHIFT)
        early = relisted = 0
        for item in world.items:
            e, r = truth.relists(item.key, world.start_ms, books[-1].at_ms, HOUR_MS)
            early, relisted = early + e, relisted + r
        true_share = relisted / early if early else None
        engine = found.relisted if found is not None else None
        out.append(Measure("SM4", f"relisted share (cancels relisted {relist:.0%})", engine, true_share,
                           "±5 points", within(engine, true_share, points=0.05)))
    return out


def tier_evidence(lt: Lifetimes, key: int, limits: Sequence[float] = sellprob.TIER_LIMITS
                  ) -> dict[int, list[float]]:
    """Each tier's ended listings as the lifetimes see them (BZ28's quantity, M33b): sold share per listing.

    Args:
        lt: The lifetimes (`volume.lifetimes`).
        key: Item key.
        limits: Tier edges, price ÷ the lowest other price at first sight (SPEC §3.3.2; SM5 varies them).

    Returns:
        Tier (1 to len(limits) + 1) -> per ended listing posted after a linked gap, in ending order: the share
        that left early and was not a cancel (1 − expired, or 0 for a cancel).
    """
    posted = {auction: (price, lowest) for auction, k, _, _, price, lowest in lt.listed if k == key}
    out: dict[int, list[float]] = {}
    for e in sorted((e for e in lt.endings if e.auction in posted), key=lambda e: (e.gap[1], e.auction)):
        price, lowest = posted[e.auction]
        ratio = price / lowest if lowest else 1.0
        tier = next((n for n, limit in enumerate(limits, start=1) if ratio <= limit), len(limits) + 1)
        out.setdefault(tier, []).append(0.0 if e.cancelled else 1.0 - e.expired)
    return out


def tier_truth(truth: Truth, key: int, snapshots: Sequence[int], limits: Sequence[float] = sellprob.TIER_LIMITS
               ) -> dict[int, float]:
    """Each tier's true P: of the seen listings posted at that position (against the book at posting), the share
    bought out.

    Args:
        truth: The truth log.
        key: Item key.
        snapshots: Snapshot times, ascending.
        limits: Tier edges.

    Returns:
        Tier -> share bought out, over the seen listings that ended.
    """
    ratios = truth.post_ratios(key, 0, snapshots[-1])
    counts: dict[int, list[int]] = {}
    for e in seen_endings(truth, key, snapshots, 0, snapshots[-1]):
        ratio = ratios.get(e.auction, (1.0, 0))[0]
        tier = next((n for n, limit in enumerate(limits, start=1) if ratio <= limit), len(limits) + 1)
        row = counts.setdefault(tier, [0, 0])
        row[0] += e.kind == "buy"
        row[1] += 1
    return {tier: bought / ended for tier, (bought, ended) in sorted(counts.items())}


def planned_blend(p_queue: float, evidence: float, n: int, plan: Sequence[float] = PLANNED_BLEND) -> float:
    """SPEC §2.4's planned blend for M33b, with its constants as parameters (SM5): e = cap₁ × min(1, n ÷ n₁) up to
    n₁ listings, then rising to cap₂ at n₂.

    Args:
        p_queue: The queue model's P.
        evidence: The tier's measured sell-through.
        n: Listings behind it.
        plan: (cap₁, n₁, cap₂, n₂).

    Returns:
        (1 − e) × p_queue + e × evidence.
    """
    cap1, n1, cap2, n2 = plan
    e = cap1 * min(1.0, n / n1) if n <= n1 else cap1 + (cap2 - cap1) * min(1.0, (n - n1) / (n2 - n1))
    return (1 - e) * p_queue + e * evidence


def sm5(folder: Path, seed: int = 1) -> list[Measure]:
    """SM5: tier sell-through with a known true P per tier, and the blend from the queue model to the evidence.

    Args:
        folder: An empty folder for the run.
        seed: The world's seed.

    Returns:
        Per tier, the lifetimes' sell-through against the true P (±0.05, tiers with 30 listings or more); for
        tier 1 at each of SM5_SAMPLES listings, the built blend (`sellprob.blend`, cap 0.5) and the planned one
        (cap 0.9 at 300) against the true P (reported).
    """
    item = commodity(2589, "Linen Cloth", 50, 400, 120, cancel_share=0.06)
    world = World(seed, START_MS, 8, (item,))
    run = simulate(world, folder)
    _, books = market_books(world)
    evidence = tier_evidence(lifetimes(books), item.key)
    truth = tier_truth(run.truth, item.key, run.snapshots)
    out = [Measure("SM5", f"tier {tier} sell-through ({len(rows)} listings)", sum(rows) / len(rows),
                   truth.get(tier), "reported") for tier, rows in sorted(evidence.items())]
    p_queue, t1 = run.documents[-1].items[item.key].chance, evidence.get(1, [])
    weights = []                             # (built, planned): the share of the way from the model to the evidence
    for n in SM5_SAMPLES:
        sample = t1[:n]
        st = sum(sample) / len(sample) if sample else None
        built = sellprob.blend(p_queue, st, len(sample))
        planned = planned_blend(p_queue, st, len(sample)) if st is not None else p_queue
        apart = (st - p_queue) if st is not None else 0.0
        weights.append(((built - p_queue) / apart, (planned - p_queue) / apart) if apart else (0.0, 0.0))
        out += [Measure("SM5", f"tier 1 blend at {len(sample)} (built)", built, truth.get(1), "reported"),
                Measure("SM5", f"tier 1 blend at {len(sample)} (planned)", planned, truth.get(1), "reported")]
    rising = all(b >= a - 1e-9 for column in zip(*weights) for a, b in zip(column, column[1:]))
    out.append(Measure("SM5", "weight on the evidence at the most listings: built, planned", weights[-1][0],
                       weights[-1][1], "rises with listings", rising))
    return out


def sold_price_truth(truth: Truth, key: int, from_ms: int, to_ms: int) -> int | None:
    """The units-weighted median price an item really sold at in a span (what the sold-price fix would publish).

    Args:
        truth: The truth log.
        key: Item key.
        from_ms: Start, exclusive.
        to_ms: End, inclusive.

    Returns:
        Copper: the lower median unit of the buys sorted by price (an even count takes the lower middle); None
        without a buy.
    """
    units = sorted((e.price, e.units) for e in truth.between(key, from_ms, to_ms) if e.kind == "buy")
    total = sum(n for _, n in units)
    if not total:
        return None
    middle, seen = (total + 1) // 2, 0
    for price, n in units:
        seen += n
        if seen >= middle:
            return price
    return None  # unreachable: seen reaches total
