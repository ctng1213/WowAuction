"""The simulator's markets fitted from collected raw (SPEC §8.10; docs/plans/market-simulator.md §1.8, §1.19).

    python tools/sim/fit.py --raw data/cloud-live/raw/forever.normal.horde.us [--keys 2589,2592]

`observe` reads snapshots, real or simulated, and keeps only what a snapshot can show: where each new listing sits
against the cheapest other price when first seen, its band and units then, how many new listings an item gets an
hour, and the lifetimes' sold, unseen, expired and cancelled units (`engine.volume.lifetimes`). `fit_item` maps
one item's observations to a market `Item` directly. What a snapshot under-sees (sellers who undercut the cheapest
price and are bought before any snapshot, listings bought out before their first snapshot, the buyers' rate) is
not fitted: calibrating it by simulation diverged, since a busy item's observed sales are not monotone in its true
demand (plan §1.27(d)), so the SM worlds are hand-shaped with the measured durations, positions and stack sizes
(§1.28). The engine's constants are never fitted here (§1.8).
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[1])]

from ahsync.engine.volume import HOUR_MS, IdBook, life_flows, lifetimes
from sim.market import Item

NEW_GAP_MS = 90 * 60_000          # a listing first seen after a gap up to this long was posted in it: its band is fresh
# Where a new listing sits against the lowest other price of its item when first seen: (name, upper ratio bound).
POSITION_BINS = (("below", 1.0), ("at", 1.0), ("5%", 1.05), ("15%", 1.15), ("50%", 1.5), ("over", float("inf")))
# The seller's price ratio drawn for each bin when the mix is rebuilt (`price_mix`): (low, high); low == high exact.
BIN_RATIOS = {"below": (0.9, 1.0), "at": (1.0, 1.0), "5%": (1.0, 1.05), "15%": (1.05, 1.15), "50%": (1.15, 1.5),
              "over": (1.5, 2.5)}
BAND_HOURS = {1: 2, 2: 2, 3: 8, 4: 24}    # a fresh band's duration: SHORT and MEDIUM 2 h, LONG 8 h, VERY_LONG 24 h
MAX_STACK = 200                   # a commodity post larger than this is kept at this size (a buyer's want stays sane)
MIN_FLOOR = 3                     # copper; items whose cheapest price is lower (vendor junk at 1 c) are not fitted


@dataclass(frozen=True)
class ItemStats:
    """One item's observations over the linked hours (units and listings, not rates)."""

    key: int
    commodity: bool
    new_listings: int             # listings first seen after a gap of at most NEW_GAP_MS
    sold: float                   # lifetimes' sold units (partial fills, early leaves, the unseen estimate)
    unseen: float                 # of sold, units estimated sold before first sight (BZ45)
    expired: float                # units
    cancels: float                # commodity cancels (BZ40), listings
    fills: tuple[int, ...]        # units of each partial fill
    stacks: Counter               # units at first sight -> new listings
    floors: tuple[int, ...]       # the cheapest price in each snapshot that listed it, copper


@dataclass(frozen=True)
class Observed:
    """What a run of snapshots shows, market-wide and per item."""

    hours: float                  # hours of linked gaps
    positions: dict[bool, Counter]    # commodity? -> POSITION_BINS name (or "only") -> new listings
    bands: dict[bool, Counter]        # commodity? -> band at first sight -> new listings
    items: dict[int, ItemStats]

    def share(self, commodity: bool, name: str) -> float:
        """The share of new listings in one position bin, of those with another auction to compare with.

        Args:
            commodity: Commodities (True) or other items.
            name: A POSITION_BINS name.

        Returns:
            The share, 0 when nothing was compared.
        """
        counts = self.positions[commodity]
        compared = sum(v for k, v in counts.items() if k != "only")
        return counts[name] / compared if compared else 0.0

    def per_day(self, key: int, field: str) -> float:
        """One item's observation per day of linked hours.

        Args:
            key: Item key.
            field: An ItemStats count field ("new_listings", "sold", "unseen", "expired", "cancels").

        Returns:
            Per day; 0 without linked hours.
        """
        return getattr(self.items[key], field) * 24 / self.hours if self.hours else 0.0


def position_bin(price: int, others: list[int]) -> str:
    """A new listing's POSITION_BINS name.

    Args:
        price: Its unit price, copper.
        others: The other auctions' unit prices of its item in the same snapshot.

    Returns:
        "only" without another auction, else the first bin whose bound the ratio to the lowest is under (equal
        for "at").
    """
    if not others:
        return "only"
    ratio = price / min(others)
    if ratio < 1:
        return "below"
    if ratio == 1:
        return "at"
    return next(name for name, bound in POSITION_BINS[2:] if ratio <= bound)


def observe(books: list[IdBook], keys: frozenset[int] | None = None) -> Observed:
    """What the snapshots show (the same for real and simulated raw).

    Args:
        books: Snapshots of one market, oldest first.
        keys: Items to keep per-item stats for; None keeps every item.

    Returns:
        The Observed: positions and bands of every new listing, per-item stats for the kept items.
    """
    positions, bands = {True: Counter(), False: Counter()}, {True: Counter(), False: Counter()}
    new, stacks, floors = Counter(), {}, {}
    seen: set[int] = set()
    prev_ms = None
    for book in books:
        fresh = prev_ms is not None and book.at_ms - prev_ms <= NEW_GAP_MS
        prices: dict[int, list[int]] = {}
        for a in book.auctions:
            prices.setdefault(a.key, []).append(a.price)
        for key, listed in prices.items():
            if keys is None or key in keys:
                floors.setdefault(key, []).append(min(listed))
        for a in sorted(book.auctions, key=lambda a: a.id) if fresh else ():
            if a.id in seen:
                continue
            others = list(prices[a.key])
            others.remove(a.price)
            positions[a.commodity][position_bin(a.price, others)] += 1
            bands[a.commodity][a.band] += 1
            if keys is None or a.key in keys:
                new[a.key] += 1
                stacks.setdefault(a.key, Counter())[a.qty] += 1
        seen.update(a.id for a in book.auctions)
        prev_ms = book.at_ms
    hours, items = _lifetime_stats(books, keys, new, stacks, floors)
    return Observed(hours, positions, bands, items)


def _lifetime_stats(books: list[IdBook], keys: frozenset[int] | None, new: Counter, stacks: dict[int, Counter],
                    floors: dict[int, list[int]]) -> tuple[float, dict[int, ItemStats]]:
    """The lifetimes' per-item units, joined to the first-sight counts.

    Args:
        books: The snapshots.
        keys: Items to keep; None for all.
        new: Item key -> new listings.
        stacks: Item key -> units at first sight -> new listings.
        floors: Item key -> the cheapest price per snapshot.

    Returns:
        (linked hours, item key -> ItemStats) for every kept item seen in a snapshot.
    """
    lt = lifetimes(books)
    sums: dict[int, list[float]] = {}
    for per in life_flows(lt).values():
        for key, f in per.items():
            if keys is None or key in keys:
                s = sums.setdefault(key, [0.0] * 4)
                s[0] += f.sold
                s[1] += f.unseen
                s[2] += f.expired
                s[3] += f.cancels
    fills: dict[int, list[int]] = {}
    for _, key, units, _ in lt.fills:
        fills.setdefault(key, []).append(units)
    hours = sum(later - earlier for earlier, later in lt.gaps) / HOUR_MS
    items = {key: ItemStats(key, key in lt.commodities, new[key], *sums.get(key, [0.0] * 4),
                            fills=tuple(fills.get(key, ())), stacks=stacks.get(key, Counter()),
                            floors=tuple(floors[key]))
             for key in sorted(floors)}
    return hours, items


def price_mix(observed: Observed, commodity: bool, undercut: float | None = None) -> tuple[tuple[float, float, float], ...]:
    """The sellers' price mix from the observed positions, the share under the cheapest price replaced.

    Args:
        observed: The observations.
        commodity: Commodities or other items.
        undercut: The true share of posts under the cheapest price; None keeps the observed share.

    Returns:
        (weight, low, high) parts for `Item.price_mix`, weights summing to 1, in POSITION_BINS order.
    """
    weights = {name: observed.share(commodity, name) for name, _ in POSITION_BINS}
    if undercut is not None:
        rest = 1.0 - weights["below"]
        weights = {name: undercut if name == "below" else w * (1 - undercut) / rest for name, w in weights.items()}
    return tuple((weights[name], *BIN_RATIOS[name]) for name, _ in POSITION_BINS if weights[name] > 0)


def durations(observed: Observed, commodity: bool) -> tuple[tuple[int, float], ...]:
    """The duration mix from the bands of fresh listings.

    Args:
        observed: The observations.
        commodity: Commodities or other items.

    Returns:
        (hours, weight) for `Item.durations`, by hours.
    """
    counts = Counter()
    for band, n in observed.bands[commodity].items():
        counts[BAND_HOURS[band]] += n
    total = sum(counts.values())
    return tuple((h, counts[h] / total) for h in sorted(counts)) if total else ((24, 1.0),)


def fit_item(observed: Observed, key: int, undercut: float | None = None, name: str = "") -> Item:
    """One item's market, mapped directly from its observations.

    Args:
        observed: The observations.
        key: The item, one of observed.items.
        undercut: The true share of posts under the cheapest price; None keeps the observed share.
        name: The item's name; its key when "".

    Returns:
        The Item: the fair price at the median cheapest price, sellers at the observed posting rate with the
        observed stacks, durations, cancel share and price mix, and buyers buying the observed sold units in
        wants averaging the median partial fill.
    """
    stats = observed.items[key]
    fill = statistics.median(stats.fills) if stats.fills else 1
    want = max(1, int(2 * fill - 1))                       # a want uniform in 1..want averages (1 + want) / 2
    listings = observed.per_day(key, "new_listings")
    total = sum(stats.stacks.values())
    stacks = tuple((min(units, MAX_STACK), n / total) for units, n in sorted(stats.stacks.items())) if total else ()
    return Item(key, name or str(key), stats.commodity, int(statistics.median(stats.floors)),
                buys_per_day=observed.per_day(key, "sold") / ((1 + want) / 2 if stats.commodity else 1),
                listings_per_day=listings, stack=1, durations=durations(observed, stats.commodity),
                cancel_share=stats.cancels / stats.new_listings if stats.new_listings else 0.0,
                max_units=want if stats.commodity else 0, price_mix=price_mix(observed, stats.commodity, undercut),
                stacks=_merged(stacks) if stats.commodity else ())


def _merged(stacks: tuple[tuple[int, float], ...]) -> tuple[tuple[int, float], ...]:
    """Stack weights with repeated sizes (capped at MAX_STACK) summed.

    Args:
        stacks: (units, weight), units ascending.

    Returns:
        The same with one entry per size.
    """
    out: dict[int, float] = {}
    for units, w in stacks:
        out[units] = out.get(units, 0.0) + w
    return tuple(sorted(out.items()))


def summary(observed: Observed, keys: list[int]) -> list[str]:
    """A markdown summary of observations: the market-wide mixes, then the given items' rates.

    Args:
        observed: The observations.
        keys: Items to list.

    Returns:
        Lines of markdown.
    """
    lines = [f"Linked hours: {observed.hours:.1f}", "", "| Position at first sight | commodities | other |",
             "|---|---|---|"]
    lines += [f"| {name} | {observed.share(True, name):.3f} | {observed.share(False, name):.3f} |"
              for name, _ in POSITION_BINS]
    lines += ["", "| Duration | commodities | other |", "|---|---|---|"]
    mixes = {c: dict(durations(observed, c)) for c in (True, False)}
    lines += [f"| {h} h | {mixes[True].get(h, 0):.3f} | {mixes[False].get(h, 0):.3f} |" for h in (2, 8, 24)]
    lines += ["", "| Item | new listings/day | sold/day | unseen share | expired/day | cancels/listing | median fill |"
              " median floor (c) |", "|---|---|---|---|---|---|---|---|"]
    for key in keys:
        s = observed.items[key]
        lines.append(f"| {key} | {observed.per_day(key, 'new_listings'):.0f} | {observed.per_day(key, 'sold'):.0f} | "
                     f"{s.unseen / s.sold if s.sold else 0:.2f} | {observed.per_day(key, 'expired'):.0f} | "
                     f"{s.cancels / s.new_listings if s.new_listings else 0:.3f} | "
                     f"{statistics.median(s.fills) if s.fills else '—'} | {statistics.median(s.floors):.0f} |")
    return lines


def busiest(observed: Observed, count: int, commodity: bool = True) -> list[int]:
    """The items selling the most units, without vendor junk.

    Args:
        observed: The observations.
        count: How many.
        commodity: Commodities or other items.

    Returns:
        Item keys, most sold first (ties by key).
    """
    pool = [s for s in observed.items.values()
            if s.commodity == commodity and statistics.median(s.floors) >= MIN_FLOOR and s.sold > 0]
    return [s.key for s in sorted(pool, key=lambda s: (-s.sold, s.key))[:count]]


def main(argv: list[str] | None = None) -> int:
    """Print the observations of a raw folder as markdown.

    Args:
        argv: Command-line arguments; sys.argv when None.

    Returns:
        The exit code.
    """
    from lifetimes_report import load_books

    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--raw", type=Path, required=True, help="a market's raw folder")
    parser.add_argument("--keys", default="", help="comma-separated item keys to list (default: the 10 busiest)")
    args = parser.parse_args(argv)
    observed = observe(load_books(args.raw)[0])
    keys = [int(k) for k in args.keys.split(",") if k] or busiest(observed, 10)
    print("\n".join(summary(observed, keys)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
