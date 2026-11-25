"""Measurements for the sales-rate decisions before M33b (plan §1, 2026-11-07), from the raw collected so far.

    python tools/sales_report.py --raw data/cloud-live/raw/forever.normal.horde.us \
        --catalog data/cloud-live/document.json.gz --out docs/plans/blizzard-cloud-sales.md

Reads every raw envelope under --raw (read-only) and compares consecutive snapshots no more than 2 h apart. An
auction counts as an early leave when it vanished while its last band (LONG or VERY_LONG) said at least 2 h were left,
so it cannot have expired: it was bought or cancelled. The report answers, with numbers:

1. Do commodity purchases take the cheapest units first? (A partial fill while a cheaper auction of the item survived
   unchanged says no.)
2. If they do, how many early-leaving commodity units cannot have been bought (a cheaper auction survived unchanged),
   so were cancelled: the cancel floor, with Silk Cloth as the case.
3. How the early-leave rate depends on listing age.
4. How many items have enough endings for item, item × tier and item × hour of week × tier estimates.
5. What early leavers' prices say against the hourly ask closes (a sold-price median against the ask median).
6. How noisy a 6 h activity count is on busy items, against Poisson.
7. The nothing-ahead truth (sold-price plan §1d item 36(a)): of the auctions that were the cheapest of their item
   when seen, the share that left early within 8 h, by their price against the item's sold level (the early
   leavers' units-weighted median over the span), gear and commodities apart: what SM21's acceptance is checked
   against. The share counts only auctions VERY_LONG then (24 h posts), whose 8 h a hidden expiry can't end (item 42);
   and, for gear, the share of auctions gone within their first observed gap (a proxy for the sales the
   snapshots never see, item 36(b)).

The report is a temporary plan file; it changes no number in the product.
"""
from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ahsync.collect.blizzard import BlizzardError, open_envelope  # noqa: E402
from ahsync.engine.sellprob import TIER_LIMITS  # noqa: E402
from ahsync.engine.volume import HOUR_MS, Auction, IdBook  # noqa: E402
from lifetimes_report import gear_of, load_books  # noqa: E402

EARLY_GAP_MS = 2 * HOUR_MS        # a vanish within 2 h of a LONG or VERY_LONG sighting cannot be an expiry
EARLY_BANDS = (3, 4)              # LONG (2-12 h left) and VERY_LONG (over 12 h)
VERY_LONG_BAND = 4                # over 12 h left: only a 24 h auction (SPEC §2.4)
MAX_AGE_H = 24
EVIDENCE_K = 30                   # endings a cell needs before its own estimate dominates (n ÷ (n + k) ≥ 0.5)
SLOTS = 168
BLOCK_MS = 6 * HOUR_MS
SILK = 4306
WINDOW_MS = 8 * HOUR_MS           # section 7: sold within 8 h of being the cheapest (the posting-now window)
DAY_MS = 24 * HOUR_MS
LEVEL_DAYS = 1                    # section 7's level: the early leavers of the day and this many days each side
LEVEL_MIN_LEAVES = 10
LEVEL_BANDS = ((0.0, 1.0), (1.0, 1.1), (1.1, 1.25), (1.25, 1.5), (1.5, 2.0), (2.0, float("inf")))   # price ÷ the level


@dataclass(frozen=True)
class Leave:
    """An auction that left early between two snapshots: bought or cancelled, never expired."""

    key: int
    units: int
    price: int                    # unit buyout, copper
    at_ms: int                    # the later snapshot
    age_h: int | None             # whole hours since its posting gap ended; None when its posting is unknown
    tier: int                     # 1-4 by its price against the item's cheapest listing (sellprob.TIER_LIMITS)
    cheaper_kept: bool            # a cheaper auction of the item survived the gap with its units unchanged


@dataclass(frozen=True)
class Fill:
    """A partial fill: an auction that lost units between two snapshots but stayed listed."""

    key: int
    units: int
    cheaper_kept: bool            # a cheaper auction of the item survived the gap unchanged (not cheapest-first)


@dataclass
class Gaps:
    """Everything the report reads from consecutive snapshots."""

    leaves: list[Leave]
    fills: list[Fill]
    at_risk: Counter              # (commodity, age hours) -> auctions that could leave early in a gap
    left: Counter                 # (commodity, age hours) -> of those, the ones that left
    asks: dict[int, list[int]]    # item key -> its cheapest unit price in each snapshot
    span_ms: int                  # first to last snapshot


def tier_of(price: int, cheapest: int) -> int:
    """A price's tier against the item's cheapest listing (SPEC §3.3.2 T1-T4).

    Args:
        price: Unit price, copper.
        cheapest: The item's cheapest unit price in the same snapshot, copper.

    Returns:
        1 (at the cheapest) to 4 (more than 15% above it).
    """
    ratio = price / cheapest
    return next((i + 1 for i, limit in enumerate(TIER_LIMITS) if ratio <= limit), len(TIER_LIMITS) + 1)


def cheaper_kept(price: int, key: int, before: dict[int, list[Auction]], after: dict[int, Auction]) -> bool:
    """Whether a cheaper auction of the item survived a gap with its units unchanged.

    Args:
        price: The unit price in question, copper.
        key: The item key.
        before: Item key -> its auctions in the earlier snapshot.
        after: Auction id -> auction in the later snapshot.

    Returns:
        True when one did: under cheapest-first buying, nothing at `price` could have been bought.
    """
    return any(b.price < price and b.id in after and after[b.id].qty == b.qty for b in before.get(key, ()))


def gaps(books: list[IdBook], commodities: frozenset[int]) -> Gaps:
    """Read leaves, fills, exposure by age and ask closes from consecutive snapshots.

    Args:
        books: Snapshots, oldest first.
        commodities: Item keys listed as commodities.

    Returns:
        The Gaps.
    """
    out = Gaps([], [], Counter(), Counter(), defaultdict(list), books[-1].at_ms - books[0].at_ms if books else 0)
    posted: dict[int, int] = {}
    prev: IdBook | None = None
    for book in books:
        by_key: dict[int, list[Auction]] = defaultdict(list)
        for a in book.auctions:
            by_key[a.key].append(a)
        for key, auctions in by_key.items():
            out.asks[key].append(min(a.price for a in auctions))
        if prev is not None and book.at_ms - prev.at_ms <= EARLY_GAP_MS:
            _read_gap(prev, book, commodities, posted, out)
        if prev is not None:
            seen = {a.id for a in prev.auctions}
            linked = book.at_ms - prev.at_ms <= EARLY_GAP_MS
            for a in book.auctions:
                if linked and a.id not in seen and a.id not in posted:
                    posted[a.id] = book.at_ms
        prev = book
    return out


def _read_gap(prev: IdBook, book: IdBook, commodities: frozenset[int], posted: dict[int, int], out: Gaps) -> None:
    """Add one gap's early leaves, partial fills and exposure to `out`.

    Args:
        prev: The earlier snapshot.
        book: The later snapshot, at most EARLY_GAP_MS after it.
        commodities: Commodity item keys.
        posted: Auction id -> end of its posting gap, ms (for listing age).
        out: Filled in place.
    """
    after = {a.id: a for a in book.auctions}
    before: dict[int, list[Auction]] = defaultdict(list)
    for a in prev.auctions:
        before[a.key].append(a)
    cheapest = {key: min(a.price for a in auctions) for key, auctions in before.items()}
    for a in prev.auctions:
        commodity = a.key in commodities
        if a.id in after:
            if after[a.id].qty < a.qty:
                out.fills.append(Fill(a.key, a.qty - after[a.id].qty, cheaper_kept(a.price, a.key, before, after)))
            age = _age(a.id, prev.at_ms, posted)
            if a.band in EARLY_BANDS and age is not None:
                out.at_risk[(commodity, age)] += 1
            continue
        if a.band not in EARLY_BANDS:
            continue                                   # it may have expired
        age = _age(a.id, prev.at_ms, posted)
        if age is not None:
            out.at_risk[(commodity, age)] += 1
            out.left[(commodity, age)] += 1
        out.leaves.append(Leave(a.key, a.qty, a.price, book.at_ms, age, tier_of(a.price, cheapest[a.key]),
                                cheaper_kept(a.price, a.key, before, after)))


def _age(auction: int, at_ms: int, posted: dict[int, int]) -> int | None:
    """Whole hours since an auction's posting gap ended, capped at MAX_AGE_H.

    Args:
        auction: Its id.
        at_ms: The snapshot it was seen in.
        posted: Auction id -> end of its posting gap, ms.

    Returns:
        Hours; None when its posting is unknown (listed before the first linked gap).
    """
    return min(MAX_AGE_H, (at_ms - posted[auction]) // HOUR_MS) if auction in posted else None


def commodity_keys(raw: Path) -> frozenset[int]:
    """The item keys listed as commodities in the newest readable envelope.

    Args:
        raw: A market's raw folder.

    Returns:
        Item ids from the envelope's commodities half (commodities have no random suffix, so the id is the key).
    """
    for path in sorted(raw.rglob("*.json.gz"), reverse=True):
        try:
            _, _, commodities = open_envelope(path.read_bytes())
        except BlizzardError:
            continue
        if commodities:
            return frozenset(a["item"]["id"] for a in json.loads(commodities).get("auctions", []))
    return frozenset()


def names_of(document: Path | None) -> dict[int, str]:
    """Item names from a published document's catalog.

    Args:
        document: A gzipped intelligence document, or None.

    Returns:
        Item key -> name; empty without a document.
    """
    if document is None:
        return {}
    catalog = json.loads(gzip.decompress(document.read_bytes()))["catalog"]
    return {int(k): row[0] for k, row in catalog.items() if row[0]}


def cheapest_first_rows(g: Gaps, commodities: frozenset[int]) -> list[str]:
    """Question 1: partial fills and early leaves with a cheaper auction kept unchanged.

    Args:
        g: The gaps.
        commodities: Commodity keys.

    Returns:
        Markdown table rows: kind, fills, fills with a cheaper auction kept, the share.
    """
    rows = []
    for label, pick in (("commodities", lambda k: k in commodities), ("regular", lambda k: k not in commodities)):
        fills = [f for f in g.fills if pick(f.key)]
        kept = sum(1 for f in fills if f.cheaper_kept)
        rows.append(f"| {label} | {len(fills)} | {kept} | {kept / len(fills):.1%} |" if fills else f"| {label} | 0 | 0 | — |")
    return rows


def cancel_rows(g: Gaps, commodities: frozenset[int], names: dict[int, str], top: int = 12) -> list[str]:
    """Question 2: early-leaving commodity units whose item kept a cheaper auction unchanged, overall and per item.

    Args:
        g: The gaps.
        commodities: Commodity keys.
        names: Item names.
        top: Items listed, by early-leaving units (Silk Cloth always).

    Returns:
        Markdown table rows: item, early-leaving units, of them with a cheaper auction kept, the share, per day.
    """
    days = g.span_ms / (24 * HOUR_MS) or 1.0
    units, kept = Counter(), Counter()
    for leave in g.leaves:
        if leave.key in commodities:
            units[leave.key] += leave.units
            kept[leave.key] += leave.units if leave.cheaper_kept else 0
    total, total_kept = sum(units.values()), sum(kept.values())
    rows = [f"| all commodities | {total} | {total_kept} | {total_kept / total:.1%} | {total / days:.0f} |" if total else
            "| all commodities | 0 | 0 | — | 0 |"]
    keys = [k for k, _ in units.most_common(top)]
    if SILK in units and SILK not in keys:
        keys.append(SILK)
    for key in keys:
        rows.append(f"| {names.get(key, key)} | {units[key]} | {kept[key]} | {kept[key] / units[key]:.1%} "
                    f"| {units[key] / days:.0f} |")
    return rows


def age_rows(g: Gaps) -> list[str]:
    """Question 3: the share of at-risk auctions that left early in a gap, by listing age.

    Args:
        g: The gaps.

    Returns:
        Markdown table rows: age hours, then at-risk and left-early share for commodities and for regular auctions.
    """
    rows = []
    for age in range(MAX_AGE_H + 1):
        cells = []
        for commodity in (True, False):
            n, left = g.at_risk[(commodity, age)], g.left[(commodity, age)]
            cells.append(f"{n} | {left / n:.1%}" if n else "0 | —")
        rows.append(f"| {age}{'+' if age == MAX_AGE_H else ''} | " + " | ".join(cells) + " |")
    return rows


def evidence_rows(g: Gaps, weeks: tuple[int, ...] = (1, 4)) -> list[str]:
    """Question 4: how many items reach EVIDENCE_K endings per cell at each level, after a number of weeks.

    Weekly endings are the span's early leaves scaled to 168 h; an hour-of-week cell gets a tier's weekly count ÷ 168
    per week, as if spread evenly (busy hours do better, quiet ones worse).

    Args:
        g: The gaps.
        weeks: Weeks of data to project.

    Returns:
        Markdown table rows: weeks, then items with enough at item, item × tier (any tier) and item × hour × tier.
    """
    scale = SLOTS * HOUR_MS / g.span_ms if g.span_ms else 0.0
    per_item, per_tier = Counter(), Counter()
    for leave in g.leaves:
        per_item[leave.key] += 1
        per_tier[(leave.key, leave.tier)] += 1
    rows = []
    for w in weeks:
        item = sum(1 for n in per_item.values() if n * scale * w >= EVIDENCE_K)
        tier = len({k for (k, _), n in per_tier.items() if n * scale * w >= EVIDENCE_K})
        slot = len({k for (k, _), n in per_tier.items() if n * scale * w / SLOTS >= EVIDENCE_K})
        rows.append(f"| {w} | {len(per_item)} | {item} | {tier} | {slot} |")
    return rows


def weighted_median(prices: list[tuple[int, int]]) -> int:
    """The units-weighted median price, the lower middle on a tie.

    Args:
        prices: (unit price copper, units), at least one.

    Returns:
        Copper.
    """
    ordered, half, seen = sorted(prices), sum(u for _, u in prices) / 2, 0
    for price, units in ordered:
        seen += units
        if seen >= half:
            return price
    return ordered[-1][0]


def sold_vs_ask(g: Gaps, commodities: frozenset[int], min_leaves: int = 10) -> dict[int, tuple[int, int, int]]:
    """Question 5's numbers: each item's early leavers' median price against its median hourly ask close.

    Commodity leaves with a cheaper auction kept are left out (they were not bought, if cheapest-first holds).

    Args:
        g: The gaps.
        commodities: Commodity keys.
        min_leaves: Fewest early leaves an item needs.

    Returns:
        Item key -> (leaves, sold median copper, ask median copper).
    """
    prices: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for leave in g.leaves:
        if not (leave.key in commodities and leave.cheaper_kept):
            prices[leave.key].append((leave.price, leave.units))
    return {key: (len(p), weighted_median(p), int(statistics.median(g.asks[key])))
            for key, p in prices.items() if len(p) >= min_leaves and g.asks.get(key)}


def sold_ask_rows(pairs: dict[int, tuple[int, int, int]], names: dict[int, str], commodities: frozenset[int],
                  top: int = 10) -> list[str]:
    """Question 5: how far the sold median sits from the ask median, as a distribution and the largest gaps.

    Args:
        pairs: From sold_vs_ask.
        names: Item names.
        commodities: Commodity keys.
        top: Items listed with the largest gap.

    Returns:
        Markdown lines: a bucket table, then a table of the largest gaps.
    """
    buckets = Counter()
    for _, sold, ask in pairs.values():
        r = sold / ask - 1
        buckets["sold below ask by over 20%" if r < -0.2 else "within ±20%" if r <= 0.2 else "sold above ask by over 20%"] += 1
    lines = ["| sold median against ask median | items |", "|---|---|"]
    lines += [f"| {b} | {buckets[b]} |" for b in ("sold below ask by over 20%", "within ±20%", "sold above ask by over 20%")]
    lines += ["", "| item | kind | early leaves | sold median (c) | ask median (c) | sold ÷ ask |", "|---|---|---|---|---|---|"]
    worst = sorted(pairs.items(), key=lambda kv: (-abs(kv[1][1] / kv[1][2] - 1), kv[0]))[:top]
    for key, (n, sold, ask) in worst:
        kind = "commodity" if key in commodities else "regular"
        lines.append(f"| {names.get(key, key)} | {kind} | {n} | {sold} | {ask} | {sold / ask:.2f} |")
    return lines


@dataclass(frozen=True)
class Exposure:
    """One auction's 8 h from the snapshot that first found it the cheapest of its item (section 7)."""

    key: int
    price: int                    # unit buyout, copper
    sold: bool                    # it left early within WINDOW_MS of that snapshot
    first_gap: bool               # it was gone in the very first gap after it was first seen at all
    at_ms: int = 0                # the snapshot that first found it the cheapest
    clean: bool = False           # VERY_LONG then (over 12 h left): its 8 h can't end in a late band, so no expiry hides


def exposures(books: list[IdBook]) -> list[Exposure]:
    """Every auction that was the cheapest of its item when seen, followed for 8 h (section 7).

    An auction counts once, from the first snapshot it was the cheapest in (ties count). It is "sold" when it left
    early (a LONG or VERY_LONG band at its last sighting, gone within a linked gap) within WINDOW_MS of that
    snapshot; "not sold" when still listed WINDOW_MS later or gone with a short band (it may have expired); left out
    when the span ends or an unlinked gap comes first. Only a clean exposure (VERY_LONG when it became the cheapest)
    has an honest "not sold": durations are 2, 8 and 24 h (SPEC §2.4), so a 2 h post is never LONG and an 8 h post
    leaves LONG after 6 h, and a sale of either then looks like an expiry (sold-price plan §1d item 42).

    Args:
        books: Snapshots, oldest first.

    Returns:
        The exposures.
    """
    open_: dict[int, list] = {}              # auction id -> [key, price, from_ms, band, first_seen_ms, clean]
    first_seen: dict[int, int] = {}
    out: list[Exposure] = []
    prev: IdBook | None = None
    for book in books:
        linked = prev is not None and book.at_ms - prev.at_ms <= EARLY_GAP_MS
        present = {a.id: a for a in book.auctions}
        for auction, (key, price, from_ms, band, seen_ms, clean) in list(open_.items()):
            if auction in present:
                open_[auction][3] = present[auction].band
                if book.at_ms >= from_ms + WINDOW_MS:
                    out.append(Exposure(key, price, False, False, from_ms, clean))
                    del open_[auction]
            elif linked:
                sold = band in EARLY_BANDS and prev.at_ms < from_ms + WINDOW_MS
                out.append(Exposure(key, price, sold, seen_ms == from_ms and prev.at_ms == from_ms, from_ms, clean))
                del open_[auction]
            else:
                del open_[auction]
        by_key: dict[int, int] = {}
        for a in book.auctions:
            by_key[a.key] = min(by_key.get(a.key, a.price), a.price)
            first_seen.setdefault(a.id, book.at_ms)
        for a in book.auctions:
            if a.price == by_key[a.key] and a.id not in open_:
                open_[a.id] = [a.key, a.price, book.at_ms, a.band, first_seen[a.id], a.band == VERY_LONG_BAND]
        prev = book
    return out


def local_levels(g: Gaps, commodities: frozenset[int]) -> dict[tuple[int, int], int]:
    """Section 7's level per item and day: the early leavers' units-weighted median over the day and LEVEL_DAYS
    each side (a fortnight's drift would otherwise put an early listing above a late level).

    Commodity leaves with a cheaper auction kept are left out (cancels under cheapest-first).

    Args:
        g: The gaps.
        commodities: Commodity keys.

    Returns:
        (item key, day number) -> copper, for the days with LEVEL_MIN_LEAVES leaves or more in their window.
    """
    by_day: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for leave in g.leaves:
        if not (leave.key in commodities and leave.cheaper_kept):
            by_day[(leave.key, leave.at_ms // DAY_MS)].append((leave.price, leave.units))
    days: dict[int, set[int]] = defaultdict(set)
    for key, day in by_day:
        days[key].add(day)
    out = {}
    for key, mine in days.items():
        for day in range(min(mine) - LEVEL_DAYS, max(mine) + LEVEL_DAYS + 1):
            window = [p for d in range(day - LEVEL_DAYS, day + LEVEL_DAYS + 1) for p in by_day.get((key, d), ())]
            if len(window) >= LEVEL_MIN_LEAVES:
                out[(key, day)] = weighted_median(window)
    return out


def nothing_ahead_rows(ex: list[Exposure], levels: dict[tuple[int, int], int], commodities: frozenset[int]) -> list[str]:
    """Section 7's table: per shape and price band, the auctions that were cheapest and the share sold within 8 h.

    The share counts clean exposures only (VERY_LONG when they became the cheapest); the share over every band is
    printed beside it, biased low because a late sale of a 2 h or 8 h post looks like an expiry (item 42).

    Args:
        ex: From exposures.
        levels: (item key, day) -> its sold level then, copper (local_levels); exposures without one are left out.
        commodities: Commodity keys.

    Returns:
        Markdown rows.
    """
    cells: dict[tuple[bool, int], list[int]] = defaultdict(lambda: [0, 0, 0, 0])   # all, all sold, clean, clean sold
    items: dict[bool, set[int]] = defaultdict(set)
    for e in ex:
        level = levels.get((e.key, e.at_ms // DAY_MS))
        if not level:
            continue
        ratio = e.price / level
        band = next(i for i, (lo, hi) in enumerate(LEVEL_BANDS) if lo <= ratio < hi)
        cell = cells[(e.key in commodities, band)]
        cell[0] += 1
        cell[1] += e.sold
        cell[2] += e.clean
        cell[3] += e.clean and e.sold
        items[e.key in commodities].add(e.key)
    rows = []
    for commodity in (False, True):
        for i, (lo, hi) in enumerate(LEVEL_BANDS):
            n, sold, clean, clean_sold = cells.get((commodity, i), (0, 0, 0, 0))
            label = f"under {hi:.2f}×" if lo == 0 else (f"{lo:.2f}× and over" if hi == float("inf") else f"{lo:.2f}–{hi:.2f}×")
            share = f"{clean_sold / clean:.2f}" if clean else "—"
            low = f"{sold / n:.2f}" if n else "—"
            rows.append(f"| {'commodities' if commodity else 'gear and the rest'} ({len(items[commodity])} items) | {label} | "
                        f"{clean} | {clean_sold} | {share} | {n} | {low} |")
    return rows


def first_gap_rows(ex: list[Exposure], gear: frozenset[int] | None, commodities: frozenset[int]) -> list[str]:
    """Section 7's second table: the auctions gone in their first observed gap, a proxy for the unseen sales.

    Args:
        ex: From exposures (the auctions cheapest when first seen are the ones that can leave in that gap).
        gear: Gear keys from the catalog, or None (then everything that is no commodity).
        commodities: Commodity keys.

    Returns:
        Markdown rows: gear, and commodities.
    """
    rows = []
    for name, pick in (("gear", lambda k: (k in gear) if gear is not None else (k not in commodities)),
                       ("commodities", lambda k: k in commodities)):
        mine = [e for e in ex if pick(e.key)]
        gone = sum(1 for e in mine if e.first_gap)
        rows.append(f"| {name} | {len(mine)} | {gone} | {gone / len(mine):.2f} |" if mine else f"| {name} | 0 | 0 | — |")
    return rows


def noise_rows(g: Gaps, names: dict[int, str], top: int = 8) -> list[str]:
    """Question 6: early leaves per 6 h block on the busiest items, their spread against Poisson's.

    Args:
        g: The gaps.
        names: Item names.
        top: Items listed, by early leaves.

    Returns:
        Markdown table rows: item, blocks, mean per block, observed CV, Poisson CV (1 ÷ √mean).
    """
    if not g.leaves:
        return []
    start = min(leave.at_ms for leave in g.leaves)
    blocks = int(g.span_ms // BLOCK_MS) or 1
    counts: dict[int, Counter] = defaultdict(Counter)
    for leave in g.leaves:
        counts[leave.key][min(blocks - 1, (leave.at_ms - start) // BLOCK_MS)] += 1
    busiest = sorted(counts, key=lambda k: (-sum(counts[k].values()), k))[:top]
    rows = []
    for key in busiest:
        series = [counts[key][b] for b in range(blocks)]
        mean = statistics.fmean(series)
        cv = statistics.pstdev(series) / mean if mean else 0.0
        rows.append(f"| {names.get(key, key)} | {blocks} | {mean:.0f} | {cv:.2f} | {1 / mean ** 0.5 if mean else 0:.2f} |")
    return rows


def report(books: list[IdBook], skipped: int, commodities: frozenset[int], names: dict[int, str],
           gear: frozenset[int] | None = None) -> str:
    """The markdown report.

    Args:
        books: Snapshots, oldest first.
        skipped: Envelopes refused or empty.
        commodities: Commodity keys.
        names: Item names.
        gear: Gear keys from the catalog (section 7's second table); None for everything that is no commodity.

    Returns:
        Markdown text.
    """
    g = gaps(books, commodities)
    ex = exposures(books)
    span_h = g.span_ms / HOUR_MS
    return "\n".join([
        "# Sales-rate measurements before M33b (temporary)", "",
        f"{len(books)} snapshots over {span_h:.1f} h ({skipped} refused or empty); {len(g.leaves)} early leaves "
        f"(gone within 2 h of a LONG or VERY_LONG sighting: bought or cancelled, never expired), {len(g.fills)} partial "
        f"fills; {len(commodities)} commodity items.", "",
        "## 1. Do commodity purchases take the cheapest units first?", "",
        "A partial fill while a cheaper auction of the same item survived the gap unchanged contradicts cheapest-first.",
        "", "| kind | partial fills | with a cheaper auction kept | share |", "|---|---|---|---|",
        *cheapest_first_rows(g, commodities), "",
        "## 2. The commodity cancel floor", "",
        "Early-leaving commodity units whose item kept a cheaper auction unchanged through the gap. If cheapest-first "
        "holds (1), these were cancelled, not bought.", "",
        "| item | early-leaving units | with a cheaper auction kept | share | units per day |", "|---|---|---|---|---|",
        *cancel_rows(g, commodities, names), "",
        "## 3. Early leaves by listing age", "",
        "Auctions whose posting gap is known, at risk in a gap when LONG or VERY_LONG; the share that left early.", "",
        "| age h | commodities at risk | left | regular at risk | left |", "|---|---|---|---|---|",
        *age_rows(g), "",
        f"## 4. Items with {EVIDENCE_K} endings per cell", "",
        "Projected from this span's early leaves; hour-of-week cells assume an even spread over the week.", "",
        "| weeks of data | items with early leaves | item level | item × tier (some tier) | item × hour × tier (some tier) |",
        "|---|---|---|---|---|", *evidence_rows(g), "",
        "## 5. Early leavers' price against the hourly ask close", "",
        "Units-weighted median price of early leavers (commodity leaves with a cheaper auction kept left out) against "
        "the median of the item's cheapest listing per snapshot; items with 10 or more early leaves.", "",
        *sold_ask_rows(sold_vs_ask(g, commodities), names, commodities), "",
        "## 6. Noise of a 6 h activity count", "",
        "| item | 6 h blocks | early leaves per block | observed CV | Poisson CV |", "|---|---|---|---|---|",
        *noise_rows(g, names), "",
        "## 7. The nothing-ahead truth: the cheapest listing's chance within 8 h, by its price against the sold level", "",
        "Each auction once, from the first snapshot it was the cheapest of its item in; sold = left early (bought or "
        "cancelled) within 8 h; the level is the item's early leavers' units-weighted median over that day and the day "
        "each side (10 or more leaves). This is what SM21's price acceptance is checked against (sold-price plan §1d "
        "item 36(a)); cancels are in the sold share (section 2's floor says how many). The share counts only auctions "
        "VERY_LONG when they became the cheapest (24 h posts): a 2 h post is never LONG and an 8 h post leaves LONG "
        "after 6 h, so their sales then look like expiries and the share over every band reads low (item 42).",
        "", "| shape | price ÷ the level | clean auctions | sold within 8 h | share | all auctions | share, every band (low) |",
        "|---|---|---|---|---|---|---|",
        *nothing_ahead_rows(ex, local_levels(g, commodities), commodities), "",
        "Auctions gone within their first observed gap: the nearer to 1, the more of an item's sales the snapshots never "
        "see at all (item 36(b): a listing bought before any snapshot is invisible, so sold/day reads low).", "",
        "| shape | auctions cheapest when first seen | gone in that first gap | share |", "|---|---|---|---|",
        *first_gap_rows([e for e in ex], gear, commodities), ""])


def main(argv: list[str] | None = None) -> int:
    """Write the report.

    Args:
        argv: Command-line arguments (--raw, --catalog, --out).

    Returns:
        Exit code 0.
    """
    parser = argparse.ArgumentParser(description="Measurements for the sales-rate decisions before M33b.")
    parser.add_argument("--raw", type=Path, required=True, help="a market's raw folder")
    parser.add_argument("--catalog", type=Path, help="a published document (.json.gz) for item names")
    parser.add_argument("--out", type=Path, required=True, help="the markdown file to write")
    args = parser.parse_args(argv)
    books, skipped = load_books(args.raw)
    args.out.write_text(report(books, skipped, commodity_keys(args.raw), names_of(args.catalog),
                                gear_of(args.catalog) if args.catalog else None), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
