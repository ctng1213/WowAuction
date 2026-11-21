"""Sold prices against asks by hour of the day, from collected raw (`docs/plans/sold-price.md` §1d; the 2026-11-21 review).

    python tools/hourly_prices_report.py --raw <raw/market folder> [--catalog <document.json.gz>] \
        [--utc-offset -5] [--out docs/plans/hourly-prices.md]

The hour-of-week price pattern (`how`, SPEC §3.3.4) is built from asks: the cheapest listing each hour. The user
expects buyers to pay least overnight and most at raid time, and one day of published rings hinted that asks do not
move that way. This report measures both on every stored day: per commodity and local day, each hour's
units-weighted median sold price ÷ that day's, and each hour's cheapest ask ÷ the day's median cheapest ask; then,
per hour of the day, the median of those ratios over every item and day, with the share of the day's units sold in
that hour. A sold price is `lifetime_prices.sold_prices`' (SPEC §2.4 Sold prices), credited to the hour of the
snapshot that first missed the auction; gaps over 2 h are left out, since their hour is not known.

Commodities only: gear sells too rarely for a day's hours to compare. The snapshots are read one at a time
(`lifetimes.step`), so a month of raw fits in memory. It changes no number in the product: it decides whether the
pattern should be built from sold prices (plan §1d item 21).
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]

from ahsync.collect.blizzard import BlizzardError, http_date_ms, open_envelope, parse_snapshot  # noqa: E402
from ahsync.collect.provider import load_bonus_suffixes  # noqa: E402
from ahsync.engine.lifetime_prices import sold_prices, weighted_median  # noqa: E402
from ahsync.engine.lifetimes import LifeState, Lifetimes, step  # noqa: E402
from ahsync.engine.prices import median  # noqa: E402
from ahsync.engine.volume import HOUR_MS, SALES_PAIR_MAX_GAP_MS, IdBook  # noqa: E402
from sales_report import names_of  # noqa: E402

MIN_DAY_UNITS = 20.0             # sold units an item needs in a local day for that day to count
MIN_DAY_HOURS = 8                # ... and hours of that day with a sale
NIGHT, EVENING = range(0, 6), range(16, 22)      # the user's example: 12am-6am cheap, 4pm-10pm dear (local hours)
SCALE = 1000                     # ratios are compared in thousandths, so medians stay integer (SPEC §3.3)


@dataclass
class Hourly:
    """What the raw showed, per commodity and clock hour (UTC hour number, floor(ms ÷ 3,600,000))."""

    snapshots: int = 0
    skipped: int = 0
    asks: dict[int, dict[int, int]] = field(default_factory=dict)                 # key -> hour -> cheapest ask, copper
    sold: dict[int, dict[int, dict[int, float]]] = field(default_factory=dict)    # key -> hour -> price -> units


def iter_books(raw: Path, hourly: Hourly) -> Iterator[IdBook]:
    """Every raw envelope under a folder as an IdBook, oldest first, one in memory at a time.

    Args:
        raw: A market's raw folder (…/raw/<market>), its envelopes in <YYYY>/<MM>/<DD>/<HHMMSS>.json.gz.
        hourly: Counts the envelopes refused or empty in `skipped`.

    Yields:
        The snapshots in path order, which is time order.
    """
    suffixes = load_bonus_suffixes()
    for path in sorted(raw.rglob("*.json.gz")):
        try:
            stamp, regular, commodities = open_envelope(path.read_bytes())
            observation = parse_snapshot("report", regular, commodities, http_date_ms(stamp), suffixes).observation
        except BlizzardError:
            observation = None
        if observation is None:
            hourly.skipped += 1
            continue
        yield IdBook(observation.at_ms, observation.auctions)


def collect(books: Iterable[IdBook], hourly: Hourly | None = None) -> Hourly:
    """The cheapest ask and the sold prices of every commodity, per clock hour.

    Args:
        books: Snapshots of one market, oldest first; read once.
        hourly: The record to fill (the one `iter_books` counts into); a new one when None.

    Returns:
        The record: an hour's ask is the cheapest in the last snapshot of that hour; a gap's sold prices are
        credited to the hour of its later snapshot, gaps over SALES_PAIR_MAX_GAP_MS left out.
    """
    out = hourly if hourly is not None else Hourly()
    state = LifeState()
    for book in books:
        out.snapshots += 1
        hour = book.at_ms // HOUR_MS
        cheapest: dict[int, int] = {}
        for auction in book.auctions:
            if auction.commodity and auction.price < cheapest.get(auction.key, auction.price + 1):
                cheapest[auction.key] = auction.price
        for key, price in cheapest.items():
            out.asks.setdefault(key, {})[hour] = price
        state, record = step(state, book)
        if record is None or record.gap[1] - record.gap[0] > SALES_PAIR_MAX_GAP_MS:
            continue
        gap = Lifetimes((record.gap,), {record.gap: record.seen}, record.endings, record.fills, record.listed, 0)
        for key, by_price in sold_prices(gap).get(record.gap, {}).items():
            if key in record.commodities:
                into = out.sold.setdefault(key, {}).setdefault(hour, {})
                for price, units in by_price.items():
                    into[price] = into.get(price, 0.0) + units
    return out


@dataclass(frozen=True)
class DayShape:
    """One commodity's local day: each hour's sold price and ask against the day's, and the units sold."""

    key: int
    day: int                                  # local day number
    units: float                              # sold units in the day
    sold: dict[int, int]                      # local hour 0-23 -> the hour's sold median ÷ the day's, thousandths
    asks: dict[int, int]                      # local hour 0-23 -> the hour's cheapest ask ÷ the day's median ask
    share: dict[int, float]                   # local hour 0-23 -> the hour's share of the day's sold units


def day_shapes(hourly: Hourly, utc_offset: int, min_units: float = MIN_DAY_UNITS,
               min_hours: int = MIN_DAY_HOURS) -> list[DayShape]:
    """Every commodity-day with enough sales, as ratios to its own day.

    Args:
        hourly: From `collect`.
        utc_offset: Local time's hours from UTC (−5 for US Eastern in winter).
        min_units: Sold units a day needs.
        min_hours: Hours with a sale a day needs.

    Returns:
        The shapes, by key then day; a day short of either minimum is left out.
    """
    out = []
    for key in sorted(hourly.sold):
        by_day: dict[int, dict[int, dict[int, float]]] = {}
        for hour, by_price in hourly.sold[key].items():
            local = hour + utc_offset
            by_day.setdefault(local // 24, {})[local % 24] = by_price
        for day in sorted(by_day):
            hours = by_day[day]
            level, units = weighted_median((p, u) for by_price in hours.values() for p, u in by_price.items())
            if level is None or units < min_units or len(hours) < min_hours:
                continue
            day_asks = {h: hourly.asks.get(key, {}).get(day * 24 + h - utc_offset) for h in range(24)}
            ask_level = median([a for a in day_asks.values() if a is not None])
            medians = {h: weighted_median(by_price.items())[0] for h, by_price in hours.items()}
            # an hour whose units round to nothing (a sliver of an ending that mostly expired) has no median
            sold = {h: m * SCALE // level for h, m in medians.items() if m is not None}
            asks = {h: a * SCALE // ask_level for h, a in day_asks.items() if a is not None and ask_level}
            share = {h: sum(by_price.values()) / units for h, by_price in hours.items()}
            out.append(DayShape(key, day, units, sold, asks, share))
    return out


def hour_rows(shapes: Sequence[DayShape]) -> list[tuple[int, int | None, int | None, float, int]]:
    """Per local hour: the median sold ratio, the median ask ratio and the typical share of a day's units.

    Args:
        shapes: From `day_shapes`.

    Returns:
        24 rows (hour, median sold ratio in thousandths or None, median ask ratio or None, the mean share of the
        day's units over every item-day, item-days with a sale in that hour).
    """
    rows = []
    for hour in range(24):
        sold = [s.sold[hour] for s in shapes if hour in s.sold]
        asks = [s.asks[hour] for s in shapes if hour in s.asks]
        share = sum(s.share.get(hour, 0.0) for s in shapes) / len(shapes) if shapes else 0.0
        rows.append((hour, median(sold), median(asks), share, len(sold)))
    return rows


def spans(shape: DayShape, hours: Iterable[int]) -> tuple[int | None, int | None]:
    """One item-day's median sold and ask ratios over a span of hours.

    Args:
        shape: The item-day.
        hours: Local hours.

    Returns:
        (median sold ratio, median ask ratio), thousandths; None where the span has no value.
    """
    wanted = list(hours)
    return (median([shape.sold[h] for h in wanted if h in shape.sold]),
            median([shape.asks[h] for h in wanted if h in shape.asks]))


def item_rows(shapes: Sequence[DayShape], top: int) -> list[tuple[int, float, int, int | None, int | None]]:
    """The busiest items' evening against night, sold and asked.

    Args:
        shapes: From `day_shapes`.
        top: Items to list.

    Returns:
        (key, sold units over its days, days, evening ÷ night sold in thousandths, evening ÷ night asks), the
        most units first; a ratio is None when an item has no day with both spans.
    """
    by_key: dict[int, list[DayShape]] = {}
    for shape in shapes:
        by_key.setdefault(shape.key, []).append(shape)
    rows = []
    for key, days in by_key.items():
        sold, asks = [], []
        for shape in days:
            (night_sold, night_ask), (eve_sold, eve_ask) = spans(shape, NIGHT), spans(shape, EVENING)
            if night_sold and eve_sold:
                sold.append(eve_sold * SCALE // night_sold)
            if night_ask and eve_ask:
                asks.append(eve_ask * SCALE // night_ask)
        rows.append((key, sum(s.units for s in days), len(days), median(sold), median(asks)))
    return sorted(rows, key=lambda r: (-r[1], r[0]))[:top]


def ratio(thousandths: int | None) -> str:
    """A ratio cell, "—" when unknown.

    Args:
        thousandths: The ratio × 1000, or None.

    Returns:
        "1.043" for 1043.
    """
    return "—" if thousandths is None else f"{thousandths / SCALE:.3f}"


def report(hourly: Hourly, utc_offset: int, names: dict[int, str], top: int = 25) -> str:
    """The report, as markdown.

    Args:
        hourly: From `collect`.
        utc_offset: Local time's hours from UTC.
        names: Item key -> name; a key without one prints as its number.
        top: Items in the per-item table.

    Returns:
        The markdown text.
    """
    shapes = day_shapes(hourly, utc_offset)
    out = ["# Sold prices against asks by hour of the day", "",
           f"{hourly.snapshots} snapshots ({hourly.skipped} refused or empty); {len(shapes)} commodity-days of "
           f"{len({s.key for s in shapes})} items with at least {MIN_DAY_UNITS:g} sold units and {MIN_DAY_HOURS} "
           f"hours with a sale; local time is UTC{utc_offset:+d}.", "",
           "Each ratio is an hour against the same item's own day, so 1.000 is that day's level; the median over "
           "every item and day.", "",
           "| Local hour | Sold price ÷ the day's | Cheapest ask ÷ the day's | Share of the day's units | Item-days |",
           "|---|---|---|---|---|"]
    out += [f"| {hour:02d}:00 | {ratio(sold)} | {ratio(asks)} | {share:.1%} | {count} |"
            for hour, sold, asks, share, count in hour_rows(shapes)]
    out += ["", f"## Evening ({EVENING.start}:00–{EVENING.stop}:00) against night ({NIGHT.start}:00–{NIGHT.stop}:00), "
            "the busiest items", "", "The median over the item's days of its evening ratio ÷ its night ratio.", "",
            "| Item | Sold units | Days | Sold: evening ÷ night | Asks: evening ÷ night |", "|---|---|---|---|---|"]
    out += [f"| {names.get(key, key)} ({key}) | {units:.0f} | {days} | {ratio(sold)} | {ratio(asks)} |"
            for key, units, days, sold, asks in item_rows(shapes, top)]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write the report.

    Args:
        argv: Command-line arguments (--raw, --catalog, --utc-offset, --top, --out).

    Returns:
        Exit code 0.
    """
    parser = argparse.ArgumentParser(description="Sold prices against asks by hour of the day, from collected raw.")
    parser.add_argument("--raw", type=Path, required=True, help="a market's raw folder (…/raw/<market>)")
    parser.add_argument("--catalog", type=Path, help="a published summary (.json.gz), for item names")
    parser.add_argument("--utc-offset", type=int, default=-5, help="local time's hours from UTC (default -5)")
    parser.add_argument("--top", type=int, default=25, help="items in the per-item table")
    parser.add_argument("--out", type=Path, help="the markdown file to write (default: print)")
    args = parser.parse_args(argv)
    hourly = Hourly()
    text = report(collect(iter_books(args.raw, hourly), hourly), args.utc_offset, names_of(args.catalog), args.top)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
