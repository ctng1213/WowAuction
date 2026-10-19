"""Sales volume: units sold, expired and listed per day, from the best source per day (SPEC §3.3.1).

Every daily row has a source (`src`, e.g. "books", "ids", "mail") and a `Cover`: its kind (by method) and the
hours of the day it observed. Kinds, best first: auction-id diffs ("ids"), group diffs of order books ("groups":
your scans, AHF books, AHF's volume file) and your own auction mail ("own", a lower bound on the market). Within
a kind the source that observed more of the day wins. A partly observed day counts as the share of a day's sales
it saw, weighted by the hourly sales pattern. Quantity drops ("qty_drop") are listing turnover, never sales
(SPEC §3.3.1 item 4, §3.3.6): they have no kind, so `daily_volume` ignores them.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ahsync.engine.prices import median

HOUR_MS = 3_600_000
SUPPLY_DAYS = 30            # median_daily_supply looks back this far (SPEC §3.3.6)
MAX_GAP_MS = 6 * HOUR_MS
SALES_PAIR_MAX_GAP_MS = 2 * HOUR_MS     # hourly sales rates need books this close (SPEC §3.3 sales_how)
CROSS_SOURCE_MIN_GAP_MS = 15 * 60_000   # books of different sources closer than this never pair (SPEC §3.3.1, V39)
VOLUME_METHOD_VERSION = 4     # bump when the estimation changes; stored data is then recomputed (SPEC §3.3.6)
KINDS = ("ids", "groups", "own")       # volume kinds in trust order (SPEC §3.3.1)
LEGACY_KIND = {"ahf": "groups", "books": "groups", "mail": "own"}   # rows stored without a cover (V24)
# Time-left bands [lo, hi) in hours, by WoW band code (SPEC §3.3.1). UNVERIFIED for Forever (§6 item 5).
# Very long ends at the longest auction duration (fees.DURATIONS_H; 24 h on Classic Era).
BAND_HOURS = {1: (0.0, 0.5), 2: (0.5, 2.0), 3: (2.0, 8.0), 4: (8.0, 24.0)}


@dataclass(frozen=True)
class BookEntry:
    """One row of an order book: auctions of one item at one price and stack size, in one band."""

    key: int          # AHF item key
    price: int        # unit buyout, copper
    stack: int        # units per auction
    band: int         # time-left code 1-4
    count: int        # auctions


@dataclass(frozen=True)
class Book:
    """A complete order book (SPEC §3.3.1: only complete, full scans feed volume)."""

    at_ms: int
    entries: tuple[BookEntry, ...]
    src: str | None = None        # "local", "ahf" or a provider's name; None = unknown (same source as any other None)


@dataclass(frozen=True)
class Volume:
    """Units sold, expired and newly listed for one item between two books, or in one day."""

    sold: float
    expired: float
    listed: float


@dataclass(frozen=True)
class Cover:
    """How one volume source observed one day (SPEC §3.3.1)."""

    kind: str                                   # "ids" | "groups" | "own"
    covered_s: tuple[int, ...] | None = None    # seconds observed per UTC hour of the day (24 values; a pair
                                                # crossing midnight folds into the same hours); None = the whole day


@dataclass(frozen=True)
class VolumePrior:
    """A Gamma-Poisson prior on an item's sales rate (SPEC §3.3.1 "A short sample", V41).

    It counts as `hours` of observation that saw `per_day` units sold per day, so two hours with no sales give a
    small rate, not 0, and two hours with one sale don't give 12/day.
    """

    per_day: float = 1.0       # config volume_prior_per_day: units per day the prior believes in
    hours: float = 2           # config volume_prior_hours: how many hours of observation it is worth

    @property
    def days(self) -> float:
        """The prior's weight in day-equivalents.

        Returns:
            hours ÷ 24.
        """
        return self.hours / 24


NO_PRIOR = VolumePrior(0.0, 0)    # the raw estimate: sold ÷ day-equivalents observed


def posterior_rate(units: float, days: float, prior: VolumePrior) -> float:
    """Posterior mean of a daily rate: (units + prior per day × prior days) ÷ (days + prior days) (SPEC §3.3.1).

    Args:
        units: Units seen over the observed time.
        days: Day-equivalents observed (> 0).
        prior: The prior; NO_PRIOR gives the raw units ÷ days.

    Returns:
        Units per day.
    """
    return (units + prior.per_day * prior.days) / (days + prior.days)


@dataclass(frozen=True)
class DailyVolume:
    """Per-day average volume over a window, and which source each day used."""

    sold_per_day: float | None        # None when no day in the window has any source
    expired_per_day: float | None
    listed_per_day: float | None
    days_by_source: dict[str, int] = field(default_factory=dict)
    volume_score: float = 0.0         # the confidence component for volume, 0-1 (SPEC §3.3.5)
    main_source: str | None = None    # the source used on most days (ties: the greater name); None without one
    main_kind: str | None = None      # its kind ("ids" | "groups" | "own") on the first day it was used (D57)


def expiring_fraction(band: int, gap_hours: float) -> float:
    """Expected share of a band's auctions that ran out during a gap (SPEC §3.3.1).

    Args:
        band: Time-left code 1-4; an auction is equally likely anywhere in its band.
        gap_hours: Time between the two books, in hours.

    Returns:
        clamp((gap − lo) ÷ (hi − lo), 0, 1).
    """
    lo, hi = BAND_HOURS[band]
    return min(1.0, max(0.0, (gap_hours - lo) / (hi - lo)))


def estimate_volume(prev: Book, next_: Book, max_gap_ms: int = MAX_GAP_MS) -> dict[int, Volume] | None:
    """Estimate units sold, expired and newly listed between two order books (SPEC §3.3.1 source 2).

    Units are diffed per price level (item, unit price): stack size and band are not part of the group, so a
    partly bought commodity auction (200 -> 150 units) counts 50 sold, not 200 gone plus 150 new (V42).

    Args:
        prev: The earlier complete order book.
        next_: The later complete order book of the same market.
        max_gap_ms: Largest allowed time between the books, in ms.

    Returns:
        Item key -> Volume(sold, expired, listed) in units, or None when the books are more than max_gap_ms
        apart. Per level: gone = max(0, before − after), new = max(0, after − before), expired = min(gone,
        units expiring by band), sold = gone − expired. Known errors: a cancel counts as sold; a repost at a
        new price counts as sold at the old level and listed at the new; a repost at the same price as nothing.
    """
    gap_ms = next_.at_ms - prev.at_ms
    if gap_ms > max_gap_ms:
        return None
    gap_hours = gap_ms / HOUR_MS
    before: dict[tuple[int, int], int] = {}
    expiring: dict[tuple[int, int], float] = {}
    for e in prev.entries:
        level = (e.key, e.price)                 # stack and band are not part of the level (V6, V42, V43)
        before[level] = before.get(level, 0) + e.stack * e.count
        expiring[level] = expiring.get(level, 0.0) + e.stack * e.count * expiring_fraction(e.band, gap_hours)
    after: dict[tuple[int, int], int] = {}
    for e in next_.entries:
        level = (e.key, e.price)
        after[level] = after.get(level, 0) + e.stack * e.count
    totals: dict[int, list[float]] = {}
    for level in sorted(set(before) | set(after)):
        units_before, units_after = before.get(level, 0), after.get(level, 0)
        gone, new = max(0, units_before - units_after), max(0, units_after - units_before)
        expired = min(gone, expiring.get(level, 0.0))
        total = totals.setdefault(level[0], [0.0, 0.0, 0.0])
        total[0] += gone - expired
        total[1] += expired
        total[2] += new
    return {key: Volume(sold, expired, listed) for key, (sold, expired, listed) in sorted(totals.items())}


@dataclass(frozen=True)
class Auction:
    """One auction of a snapshot that has auction ids (a retail-shaped API, SPEC §3.3.1 kind "ids")."""

    id: int           # the auction's id, unique within the market while it is listed
    key: int          # item key
    qty: int          # units left in it (a commodity auction shrinks as it partly sells)
    price: int        # unit buyout, copper
    band: int         # time-left code 1-4


@dataclass(frozen=True)
class IdBook:
    """A complete snapshot with auction ids."""

    at_ms: int
    auctions: tuple[Auction, ...]


def estimate_volume_ids(prev: IdBook, next_: IdBook, max_gap_ms: int = MAX_GAP_MS) -> dict[int, Volume] | None:
    """Units sold, expired and newly listed between two snapshots with auction ids (SPEC §3.3.1, V33).

    An id still listed with fewer units sold the difference (a partial commodity buy). An id that is gone
    expired with its band's chance of running out in the gap, else it sold. An id not seen before is new.

    Args:
        prev: The earlier snapshot.
        next_: The later snapshot of the same market.
        max_gap_ms: Largest allowed time between them, in ms.

    Returns:
        Item key -> Volume(sold, expired, listed) in units, or None when they are too far apart. Known error: a
        cancelled auction still counts as sold, since cancels and buys both remove an id.
    """
    gap_ms = next_.at_ms - prev.at_ms
    if gap_ms > max_gap_ms:
        return None
    gap_hours = gap_ms / HOUR_MS
    later = {a.id: a for a in next_.auctions}
    earlier = {a.id for a in prev.auctions}
    totals: dict[int, list[float]] = {}
    for a in sorted(prev.auctions, key=lambda a: a.id):
        total = totals.setdefault(a.key, [0.0, 0.0, 0.0])
        b = later.get(a.id)
        if b is not None:
            total[0] += max(0, a.qty - b.qty)
            continue
        expired = a.qty * expiring_fraction(a.band, gap_hours)
        total[0] += a.qty - expired
        total[1] += expired
    for b in sorted(next_.auctions, key=lambda b: b.id):
        total = totals.setdefault(b.key, [0.0, 0.0, 0.0])
        if b.id not in earlier:
            total[2] += b.qty
    return {key: Volume(sold, expired, listed) for key, (sold, expired, listed) in sorted(totals.items())}


def group_book(snapshot: IdBook) -> Book:
    """A snapshot with ids as an order book: its auctions counted per item, price, stack and band (SPEC §3.3.1).

    The grouped book feeds prices, hourly sales and group diffs like any other complete book.

    Args:
        snapshot: The snapshot.

    Returns:
        The Book, entries sorted by key, price, stack and band.
    """
    counts: dict[tuple[int, int, int, int], int] = {}
    for a in snapshot.auctions:
        group = (a.key, a.price, a.qty, a.band)
        counts[group] = counts.get(group, 0) + 1
    return Book(snapshot.at_ms, tuple(BookEntry(*group, count) for group, count in sorted(counts.items())))


def covered_seconds(pairs: Sequence[tuple[int, int]]) -> tuple[int, ...]:
    """The seconds a day's book pairs observed in each UTC hour of the day (SPEC §3.3.1, V29).

    Args:
        pairs: (earlier book ms, later book ms) of each pair counted for the day.

    Returns:
        24 values, hour 0 first; a pair that starts the day before folds its hours into the same hours of
        the day (the weekday pattern of its day is used for them).
    """
    covered_ms = [0] * 24
    for start, end in pairs:
        t = start
        while t < end:
            step = min(end, (t // HOUR_MS + 1) * HOUR_MS) - t
            covered_ms[t // HOUR_MS % 24] += step
            t += step
    return tuple(ms // 1000 for ms in covered_ms)


def book_pairs(books: Sequence[tuple[int, str | None]], max_gap_ms: int) -> list[tuple[int, int]]:
    """Which consecutive complete books pair for volume (SPEC §3.3.1, V39-V40).

    One walk in time order keeps a current book. A next book of a different source less than
    CROSS_SOURCE_MIN_GAP_MS after the current one is dropped: it is never part of a pair, and the current book
    stays. Any other next book pairs with the current one when 0 < gap <= max_gap_ms, and becomes the current
    book either way. So every interval between kept books is covered by exactly one pair.

    Args:
        books: (time ms, source or None) of complete books of one market, oldest first.
        max_gap_ms: Largest gap between a pair's books, in ms.

    Returns:
        (earlier index, later index) into books for each pair, oldest first.
    """
    pairs: list[tuple[int, int]] = []
    current = 0
    for i in range(1, len(books)):
        gap_ms = books[i][0] - books[current][0]
        if books[i][1] != books[current][1] and gap_ms < CROSS_SOURCE_MIN_GAP_MS:
            continue
        if 0 < gap_ms <= max_gap_ms:
            pairs.append((current, i))
        current = i
    return pairs


def hourly_sales(books: Sequence[Book], max_gap_ms: int = SALES_PAIR_MAX_GAP_MS) -> dict[int, dict[int, float]]:
    """Sales rate per clock hour from consecutive complete books (SPEC §3.3 sales_how, V25-V26, V40).

    Args:
        books: Complete books of one market, oldest first; their `src` decides cross-source pairs.
        max_gap_ms: Largest gap between a pair's books, in ms.

    Returns:
        Item key -> hour start ms -> units sold per hour. Books pair as `book_pairs` says (books of different
        sources less than 15 min apart never pair). Each pair counts for the hour of its earlier book; an hour
        pools the sold units and gaps of all its pairs. An item that appears in an hour's books but sold nothing
        has rate 0 (observed, not missing).
    """
    sold: dict[int, dict[int, float]] = {}
    gap_by_hour: dict[int, int] = {}
    for i, j in book_pairs([(b.at_ms, b.src) for b in books], max_gap_ms):
        prev, next_ = books[i], books[j]
        hour_ms = prev.at_ms // HOUR_MS * HOUR_MS
        gap_by_hour[hour_ms] = gap_by_hour.get(hour_ms, 0) + next_.at_ms - prev.at_ms
        for key, v in estimate_volume(prev, next_, max_gap_ms).items():
            by_hour = sold.setdefault(key, {})
            by_hour[hour_ms] = by_hour.get(hour_ms, 0.0) + v.sold
    return {key: {hour: units * HOUR_MS / gap_by_hour[hour] for hour, units in sorted(by_hour.items())}
            for key, by_hour in sorted(sold.items())}


def listing_turnover(prev_qty: int, next_qty: int, gap_ms: int, max_gap_ms: int = MAX_GAP_MS) -> int | None:
    """Units that left the AH between two AHF quantity observations (SPEC §3.3.1 item 4, §3.3.6).

    Sold, expired and cancelled can't be told apart from totals, so this is informational
    `observed_listing_turnover`, never sales volume.

    Args:
        prev_qty: Units listed at the earlier observation.
        next_qty: Units listed at the later one.
        gap_ms: Time between them, in ms.
        max_gap_ms: Largest gap for which the bound is useful.

    Returns:
        max(0, prev − next) in units; None when the observations are too far apart.
    """
    if gap_ms > max_gap_ms:
        return None
    return max(0, prev_qty - next_qty)


def observed_turnover(hourly_supply: Sequence[tuple[int, int]], now_ms: int, days: int = 7) -> float | None:
    """Units per day that left the AH between consecutive quantity observations (SPEC §3.3.6, V37). Info only.

    Args:
        hourly_supply: (hour start ms, units listed) observations, any order.
        now_ms: Now; observations in (now − days, now] count.
        days: How many days back.

    Returns:
        The units that left (listing_turnover of each pair at most 6 h apart) ÷ the hours those pairs span × 24;
        None without such a pair. Sold, expired and cancelled can't be told apart: never sales.
    """
    seen = sorted((t, units) for t, units in hourly_supply if now_ms - days * 86_400_000 < t <= now_ms)
    left = gap_ms = 0
    for (t0, q0), (t1, q1) in zip(seen, seen[1:]):
        units = listing_turnover(q0, q1, t1 - t0)
        if units is not None and t1 > t0:
            left += units
            gap_ms += t1 - t0
    return left * 86_400_000 / gap_ms if gap_ms else None


def supply_to_volume(current_supply: int | None, sales_per_day: float | None) -> float | None:
    """Days of supply: units listed now ÷ estimated sales per day (SPEC §3.3.6, V22).

    Not a time-to-sell guarantee, and never made up: an unknown input or zero sales
    gives None ("—"), not 0 or infinity.

    Args:
        current_supply: Units on the AH in the newest observation, or None when never seen.
        sales_per_day: Estimated units sold per day, or None when unavailable.

    Returns:
        The ratio in days; None when either input is unknown or sales are 0.
    """
    if current_supply is None or not sales_per_day:
        return None
    return current_supply / sales_per_day


def calibration_factor(confirmed_sold: float, estimated_sold: float) -> float | None:
    """Correction factor for book estimates (SPEC §3.3.1 sources 2-3).

    Args:
        confirmed_sold: Units confirmed sold by a better source (AHF volume, or your mail).
        estimated_sold: Units the order-book diffs estimated for the same days or auctions.

    Returns:
        confirmed ÷ estimated; None when nothing was estimated.
    """
    if estimated_sold <= 0:
        return None
    return confirmed_sold / estimated_sold


def cover_of(src: str, covers: dict[str, Cover]) -> Cover | None:
    """A stored row's cover: its own, else its source's legacy kind for a whole day.

    Args:
        src: The row's source.
        covers: src -> Cover on its day.

    Returns:
        The Cover; None for a source with no kind (listing turnover).
    """
    return covers.get(src) or (Cover(LEGACY_KIND[src]) if src in LEGACY_KIND else None)


def day_shares(cover: Cover, day: int, sales_how: Sequence[float] | None) -> tuple[float, float]:
    """The share of a day's sales, and of its hours, that a source observed (SPEC §3.3.1, V30-V31).

    Args:
        cover: The source's cover for the day.
        day: Day number (its weekday picks the pattern's slots).
        sales_how: The item's 168-slot hourly sales profile (Monday 00:00 UTC first), or None for flat.

    Returns:
        (sales share, time share): each 1.0 for a whole day; the sales share weights each hour by the pattern,
        falling back to the time share when the day's slots are all 0. Both can pass 1.0 when pairs overlap
        across midnight: they are day-equivalents observed, not probabilities.
    """
    if cover.covered_s is None:
        return 1.0, 1.0
    hours = [s / 3600 for s in cover.covered_s]
    time_share = sum(hours) / 24
    first = (day + 3) % 7 * 24
    weights = sales_how[first:first + 24] if sales_how is not None else []
    total = sum(weights)
    if total <= 0:
        return time_share, time_share
    return sum(w * h for w, h in zip(weights, hours)) / total, time_share


def _ranked(sources: dict[str, Volume], covers: dict[str, Cover], day: int,
            sales_how: Sequence[float] | None) -> list[tuple]:
    """A day's sources with a kind and something observed, best first: kind, then most observed, then name (V32).

    Args:
        sources: src -> Volume on the day.
        covers: src -> Cover on the day.
        day: Day number.
        sales_how: The hourly sales profile, or None.

    Returns:
        (kind rank, −sales share, src, cover, sales share, time share) tuples, best first.
    """
    ranked = []
    for src in sorted(sources):
        cover = cover_of(src, covers)
        if cover is None or cover.kind not in KINDS:
            continue
        share, time_share = day_shares(cover, day, sales_how)
        if share > 0:
            ranked.append((KINDS.index(cover.kind), -share, src, cover, share, time_share))
    return sorted(ranked, key=lambda r: r[:3])


def day_score(cover: Cover, time_share: float) -> float:
    """A day's volume confidence by how it was observed (SPEC §3.3.5).

    Args:
        cover: The day's chosen source.
        time_share: The share of the day's hours it observed.

    Returns:
        ids 1.0; groups 0.3 + 0.5 × min(1, time share) (a whole day 0.8); own mail 0.4 (a lower bound).
    """
    if cover.kind == "ids":
        return 1.0
    if cover.kind == "groups":
        return 0.3 + 0.5 * min(1.0, time_share)
    return 0.4


def ids_factor(rows: dict[int, dict[str, Volume]], cover: dict[int, dict[str, Cover]],
               sales_how: Sequence[float] | None) -> float | None:
    """Calibration of group diffs from days that also have id diffs (SPEC §3.3.1, V16).

    Args:
        rows: day -> src -> Volume, the item's whole stored history.
        cover: day -> src -> Cover.
        sales_how: The hourly sales profile, or None.

    Returns:
        id-diff sold ÷ group-diff sold (each source's best of the day, scaled to a whole day) over those days;
        None without such a day.
    """
    confirmed = estimated = 0.0
    for day in sorted(rows):
        best: dict[str, tuple] = {}
        for r in _ranked(rows[day], cover.get(day, {}), day, sales_how):
            best.setdefault(r[3].kind, r)
        if "ids" in best and "groups" in best:
            confirmed += rows[day][best["ids"][2]].sold / best["ids"][4]
            estimated += rows[day][best["groups"][2]].sold / best["groups"][4]
    return calibration_factor(confirmed, estimated)


def daily_volume(rows: dict[int, dict[str, Volume]], days: Sequence[int],
                 cover: dict[int, dict[str, Cover]] | None = None,
                 sales_how: Sequence[float] | None = None, prior: VolumePrior = NO_PRIOR) -> DailyVolume:
    """Sold, expired and listed per day over a window, from each day's best source (SPEC §3.3.1).

    Args:
        rows: day -> src -> Volume for the item's whole stored history; days with both id diffs and group
            diffs calibrate the group diffs, even outside the window.
        days: The window's day numbers (floor(ms / 86400000)).
        cover: day -> src -> Cover; a row without one is an older stored row (LEGACY_KIND, whole day).
        sales_how: The item's 168-slot hourly sales profile, to weight partly observed days; None for flat.
        prior: The sales-rate prior (V41); NO_PRIOR, the default, gives the raw rates.

    Returns:
        Sold per day = (the window's sold units + prior per day × prior days) ÷ (the day-equivalents of sales
        observed + prior days): a 2 h sample is 2/24 of a day when sales are flat; listed per day likewise by
        hours observed; expired per day is the plain mean by hours observed (it only feeds the informational
        sell-through). Group diffs are
        multiplied by the id calibration, and never fall below your own mail's sold units for the part of the
        day observed. All None when no day has a source. main_source / main_kind name the source used on most
        days and its kind, for the label shown with sales (D57).
    """
    cover = cover or {}
    factor = ids_factor(rows, cover, sales_how) or 1.0
    sold = expired = listed = shares = time_shares = score = 0.0
    used: dict[str, int] = {}
    kinds: dict[str, str] = {}
    for day in days:
        sources = rows.get(day, {})
        ranked = _ranked(sources, cover.get(day, {}), day, sales_how)
        if not ranked:
            continue
        _, _, src, c, share, time_share = ranked[0]
        v = sources[src]
        day_sold = v.sold * (factor if c.kind == "groups" else 1.0)
        own = [sources[r[2]].sold for r in ranked if r[3].kind == "own"]
        if c.kind != "own" and own:
            day_sold = max(day_sold, max(own) * share)          # your own sales are a floor (V34)
        sold += day_sold
        expired += v.expired
        listed += v.listed
        shares += share
        time_shares += time_share
        score += day_score(c, time_share)
        used[src] = used.get(src, 0) + 1
        kinds.setdefault(src, c.kind)
    if not used:
        return DailyVolume(None, None, None, {})
    main = max(used, key=lambda s: (used[s], s))
    return DailyVolume(posterior_rate(sold, shares, prior), expired / time_shares,
                       posterior_rate(listed, time_shares, prior), used,
                       min(1.0, score / max(1, len(days))), main, kinds[main])


def median_daily_supply(hourly_supply: Sequence[tuple[int, int]], now_ms: int, days: int = SUPPLY_DAYS) -> int | None:
    """The item's typical listed supply: the median over days of each day's median observation (SPEC §3.3.6, V23).

    Args:
        hourly_supply: (hour start ms, units listed) observations, any order.
        now_ms: Now; days in (now − days, now] count.
        days: How many days back.

    Returns:
        Units; None without an observation. Days without one are left out, never counted as 0.
    """
    by_day: dict[int, list[int]] = {}
    for t, units in hourly_supply:
        if now_ms - days * 86_400_000 < t <= now_ms:
            by_day.setdefault(t // 86_400_000, []).append(units)
    return median([median(units) for _, units in sorted(by_day.items())])


@dataclass(frozen=True)
class Backtest:
    """How estimated daily sales compare with confirmed ones (SPEC §8.8, K4)."""

    days: int                      # item-days with both
    median_ratio: float | None     # median of estimated ÷ confirmed; None without a pair
    within: int                    # item-days whose ratio is within the tolerance of 1


def backtest(ratios: Sequence[float], tolerance: float = 0.25) -> Backtest:
    """Summarize estimated ÷ confirmed daily sales (SPEC §8.8 volume backtest, K4).

    Args:
        ratios: One ratio per item and day with both an estimate and a confirmed count above 0.
        tolerance: How far from 1 still counts as close (0.25: within ±25%).

    Returns:
        The count, the median ratio (mean of the middle two for an even count) and how many are within tolerance.
    """
    ordered = sorted(ratios)
    if not ordered:
        return Backtest(0, None, 0)
    mid = len(ordered) // 2
    median_ratio = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return Backtest(len(ordered), median_ratio, sum(1 for r in ordered if abs(r - 1) <= tolerance + 1e-12))

