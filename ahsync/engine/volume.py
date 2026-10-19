"""Sales volume: units sold, expired and listed per day, from the best source per day (SPEC §3.3.1).

Sources, best first: AHF Premium volume ("ahf"), own order-book diffs ("books"),
and own auction mail ("mail"). AHF quantity drops ("qty_drop") are listing turnover,
never sales (SPEC §3.3.1 item 4, §3.3.6): `daily_volume` ignores them.
All are computed and stored; `daily_volume` picks the best one per day.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ahsync.engine.prices import median

HOUR_MS = 3_600_000
SUPPLY_DAYS = 30            # median_daily_supply looks back this far (SPEC §3.3.6)
MAX_GAP_MS = 6 * HOUR_MS
SALES_PAIR_MAX_GAP_MS = 2 * HOUR_MS     # hourly sales rates need books this close (SPEC §3.3 sales_how)
VOLUME_METHOD_VERSION = 1     # bump when the estimation changes; stored data is then recomputed (SPEC §3.3.6)
SOURCES = ("ahf", "books", "mail")     # sales sources in trust order (SPEC §3.3.1); "qty_drop" is not one
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


@dataclass(frozen=True)
class Volume:
    """Units sold, expired and newly listed for one item between two books, or in one day."""

    sold: float
    expired: float
    listed: float


@dataclass(frozen=True)
class DailyVolume:
    """Per-day average volume over a window, and which source each day used."""

    sold_per_day: float | None        # None when no day in the window has any source
    expired_per_day: float | None
    listed_per_day: float | None
    days_by_source: dict[str, int] = field(default_factory=dict)


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
    """Estimate units sold, expired and newly listed between two order books (SPEC §3.3.1).

    Args:
        prev: The earlier complete order book.
        next_: The later complete order book of the same market.
        max_gap_ms: Largest allowed time between the books, in ms.

    Returns:
        Item key -> Volume(sold, expired, listed) in units, or None when the
        books are more than max_gap_ms apart. Known errors: a cancelled auction
        counts as sold, and a repost at a new price as sold plus new.
    """
    gap_ms = next_.at_ms - prev.at_ms
    if gap_ms > max_gap_ms:
        return None
    gap_hours = gap_ms / HOUR_MS
    prev_count: dict[tuple[int, int, int], int] = {}
    expiring: dict[tuple[int, int, int], float] = {}
    for e in prev.entries:
        group = (e.key, e.price, e.stack)        # band is not part of the group (V6)
        prev_count[group] = prev_count.get(group, 0) + e.count
        expiring[group] = expiring.get(group, 0.0) + e.count * expiring_fraction(e.band, gap_hours)
    next_count: dict[tuple[int, int, int], int] = {}
    for e in next_.entries:
        group = (e.key, e.price, e.stack)
        next_count[group] = next_count.get(group, 0) + e.count
    totals: dict[int, list[float]] = {}
    for group in sorted(set(prev_count) | set(next_count)):
        before, after = prev_count.get(group, 0), next_count.get(group, 0)
        gone, new = max(0, before - after), max(0, after - before)
        expired = min(gone, expiring.get(group, 0.0))
        stack = group[2]
        total = totals.setdefault(group[0], [0.0, 0.0, 0.0])
        total[0] += (gone - expired) * stack
        total[1] += expired * stack
        total[2] += new * stack
    return {key: Volume(sold, expired, listed) for key, (sold, expired, listed) in sorted(totals.items())}


def hourly_sales(books: Sequence[Book], max_gap_ms: int = SALES_PAIR_MAX_GAP_MS) -> dict[int, dict[int, float]]:
    """Sales rate per clock hour from consecutive complete books (SPEC §3.3 sales_how, V25-V26).

    Args:
        books: Complete books of one market, oldest first.
        max_gap_ms: Largest gap between a pair's books, in ms.

    Returns:
        Item key -> hour start ms -> units sold per hour. Each pair counts for the hour
        of its earlier book; an hour pools the sold units and gaps of all its pairs. An
        item that appears in an hour's books but sold nothing has rate 0 (observed, not missing).
    """
    sold: dict[int, dict[int, float]] = {}
    gap_by_hour: dict[int, int] = {}
    for prev, next_ in zip(books, books[1:]):
        gap_ms = next_.at_ms - prev.at_ms
        volume = estimate_volume(prev, next_, max_gap_ms) if gap_ms > 0 else None
        if volume is None:
            continue
        hour_ms = prev.at_ms // HOUR_MS * HOUR_MS
        gap_by_hour[hour_ms] = gap_by_hour.get(hour_ms, 0) + gap_ms
        for key, v in volume.items():
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


def daily_volume(rows: dict[int, dict[str, Volume]], days: Sequence[int],
                 mail_factor: float | None = None) -> DailyVolume:
    """Average daily volume over a window, using each day's best source (SPEC §3.3.1).

    Args:
        rows: day -> source -> Volume for the item's whole stored history (all sources);
            every day with both AHF and book volume calibrates the estimates, even
            outside the window (the factor is stored, SPEC §3.3.1).
        days: The window's day numbers (floor(ms / 86400000)).
        mail_factor: Calibration from your own sold mail, used only when there is
            no AHF overlap to calibrate with (the two are never multiplied).

    Returns:
        The averages over the days that have any source, with the "books" estimate's
        sold units multiplied by the calibration factor; all None when no day has data.
    """
    overlap = [d for d in sorted(rows) if {"ahf", "books"} <= rows[d].keys()]
    overlap_ahf = sum(rows[d]["ahf"].sold for d in overlap)
    overlap_books = sum(rows[d]["books"].sold for d in overlap)
    ahf_factor = calibration_factor(overlap_ahf, overlap_books)
    if ahf_factor is not None:
        factor = ahf_factor               # AHF covers the whole market: preferred (V18)
    else:
        factor = mail_factor if mail_factor is not None else 1.0
    sold = expired = listed = 0.0
    used: dict[str, int] = {}
    for day in days:
        sources = rows.get(day, {})
        source = next((s for s in SOURCES if s in sources), None)
        if source is None:
            continue
        v = sources[source]
        sold += v.sold * (factor if source == "books" else 1.0)
        expired += v.expired
        listed += v.listed
        used[source] = used.get(source, 0) + 1
    n = sum(used.values())
    if n == 0:
        return DailyVolume(None, None, None, {})
    return DailyVolume(sold / n, expired / n, listed / n, used)


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

