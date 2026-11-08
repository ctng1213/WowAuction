"""Sell-through at the lowest price, per lookback, from auction lifetimes (SPEC §2.4, BZ40, BZ41).

For the 7, 14 and 30 days before now: of the listings posted at the lowest price (tier 1) that ended, the share
bought before they expired. Commodities are measured (their cancels are known, BZ40); for everything else a cancel
looks like a sale, so the left-early share is scaled by (1 − the market's commodity cancel share) and marked est.
A lookback with under MIN_LISTINGS endings borrows from its item class by n ÷ (n + MIN_LISTINGS).

Pure: data in, numbers out.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

DAY_MS = 86_400_000
LOOKBACKS = (7, 14, 30)          # days (SPEC §4.3 TT37)
MIN_LISTINGS = 30                # under this many tier-1 endings a lookback borrows from its class (BZ41)
CANCEL_DAYS = 30                 # the market's commodity cancel share looks back this far

# The stored per-day lifetimes row (history.reads, lifetimes_d1): its fields by position.
ENDED, EXPIRED, CANCELS, T1_ENDED, T1_LEFT, COMMODITY = 0, 1, 3, 5, 6, 7


@dataclass(frozen=True)
class Tally:
    """One item's tier-1 endings over one lookback (BZ41)."""

    ended: float                 # listings posted at the lowest price that ended
    left: float                  # of those, the share that left early and was not a cancel, summed
    commodity: bool              # measured; False: est. (cancels unseen)


@dataclass(frozen=True)
class SellThrough:
    """The sell-through of one lookback, as the tooltip and the item detail show it (TT37)."""

    rate: float | None           # 0-1; None for "—"
    how: str                     # "m" measured, "e" est. (cancels estimated), "c" class est.; "" with no rate


def days_of(now_ms: int, days: int) -> range:
    """The day numbers of a lookback ending today, as the 30-day expired share counts them.

    Args:
        now_ms: Now, UTC ms.
        days: The lookback's length in days.

    Returns:
        floor((now − days d) ÷ 1 d) + 1 to floor(now ÷ 1 d), inclusive.
    """
    return range((now_ms - days * DAY_MS) // DAY_MS + 1, now_ms // DAY_MS + 1)


def tally(lifetimes: Mapping[int, Sequence[float]], now_ms: int, days: int) -> Tally:
    """One item's tier-1 endings over a lookback.

    Args:
        lifetimes: day -> the stored lifetimes row (ended, expired, matched, cancels, cancelled, t1_ended, t1_left,
            commodity).
        now_ms: Now.
        days: The lookback, in days.

    Returns:
        The Tally; commodity when any day of the item's stored rows says so.
    """
    rows = [lifetimes[d] for d in days_of(now_ms, days) if d in lifetimes]
    commodity = any(row[COMMODITY] for row in lifetimes.values())
    return Tally(sum(r[T1_ENDED] for r in rows), sum(r[T1_LEFT] for r in rows), commodity)


def cancel_counts(lifetimes: Mapping[int, Sequence[float]], now_ms: int, days: int = CANCEL_DAYS
                  ) -> tuple[float, float]:
    """One item's part of the market's commodity cancel share (BZ40).

    Args:
        lifetimes: The item's day -> stored lifetimes row.
        now_ms: Now.
        days: The lookback, in days.

    Returns:
        (cancels, left early = ended − expired) over its commodity rows in the lookback, in listings.
    """
    cancels = left = 0.0
    for day in days_of(now_ms, days):
        row = lifetimes.get(day)
        if row is not None and row[COMMODITY]:
            cancels += row[CANCELS]
            left += row[ENDED] - row[EXPIRED]
    return cancels, left


def cancel_share(counts: Iterable[tuple[float, float]]) -> float | None:
    """The market's commodity cancel share: of the commodity listings that left early, the share cancelled (BZ40).

    Args:
        counts: Each item's (cancels, left early), from `cancel_counts`.

    Returns:
        Σ cancels ÷ Σ left early; None when no commodity listing left early.
    """
    cancels = left = 0.0
    for c, l in counts:
        cancels += c
        left += l
    return cancels / left if left > 0 else None


def adjusted_left(t: Tally, cancels: float | None) -> float:
    """Tier-1 listings bought, with a non-commodity's unseen cancels taken out (BZ41).

    Args:
        t: The item's tally.
        cancels: The market's commodity cancel share; None counts as 0.

    Returns:
        t.left for a commodity; else t.left × (1 − cancels).
    """
    return t.left if t.commodity else t.left * (1.0 - (cancels or 0.0))


def class_rates(tallies: Iterable[tuple[int | None, Tally]], cancels: float | None) -> dict[int, float]:
    """Each item class's pooled sell-through over one lookback, for thin items to borrow (BZ41).

    Args:
        tallies: (item class or None, the item's tally) for every item of the market.
        cancels: The market's commodity cancel share.

    Returns:
        Item class -> Σ adjusted left ÷ Σ ended over its items; a class with no endings, and items without a
        class, are left out.
    """
    sums: dict[int, list[float]] = {}
    for item_class, t in tallies:
        if item_class is None or t.ended <= 0:
            continue
        pooled = sums.setdefault(item_class, [0.0, 0.0])
        pooled[0] += adjusted_left(t, cancels)
        pooled[1] += t.ended
    return {c: left / ended for c, (left, ended) in sorted(sums.items())}


def sell_through(t: Tally, cancels: float | None, class_rate: float | None) -> SellThrough:
    """One lookback's sell-through for one item (BZ41).

    Args:
        t: The item's tally.
        cancels: The market's commodity cancel share.
        class_rate: Its class's pooled rate (`class_rates`), or None.

    Returns:
        At MIN_LISTINGS endings or more, its own rate ("m" for a commodity, "e" otherwise); below, with a class rate,
        (n × own + MIN_LISTINGS × class) ÷ (n + MIN_LISTINGS), "c"; below without one, no rate.
    """
    n = t.ended
    if n >= MIN_LISTINGS:
        return SellThrough(adjusted_left(t, cancels) / n, "m" if t.commodity else "e")
    if class_rate is None:
        return SellThrough(None, "")
    own = adjusted_left(t, cancels)               # n × own rate: the own rate's numerator
    return SellThrough((own + MIN_LISTINGS * class_rate) / (n + MIN_LISTINGS), "c")
