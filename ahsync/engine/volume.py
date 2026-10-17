"""Sales volume: units sold, expired and listed per day, from the best source per day (SPEC §3.3.1).

Sources, best first: AHF Premium volume ("ahf"), own order-book diffs ("books"),
and own auction mail ("mail"). AHF quantity drops ("qty_drop") are listing turnover,
never sales (SPEC §3.3.1 item 4, §3.3.6): `daily_volume` ignores them.
All are computed and stored; `daily_volume` picks the best one per day.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

HOUR_MS = 3_600_000
MAX_GAP_MS = 6 * HOUR_MS
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
