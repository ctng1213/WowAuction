"""The sold-price comparison for the 2026-11-21 review (`docs/plans/sold-price.md`, SP1b; SPEC §2.4 Sold prices).

    python tools/sold_price_report.py --document <document.json.gz> --out docs/plans/sold-price-report.md

Reads one published summary (schema 16 or later, read-only) and sets each item's `sold_med7`, the units-weighted
median of what sold over 7 days, against the ask-based numbers the plan prices from today: med7 (the median hourly
close, the cheapest ask each hour), p90_30 and the post price. The summary says how often the two agree and where
they disagree most; the table lists the items, the widest disagreement first. The units sold before any snapshot saw
them (`unseen_per_day`, BZ45) have no price, so each row shows their share of the week's sales too.

The report changes no number in the product; it is the input to SP2's switch, which the user decides.
"""
from __future__ import annotations

import argparse
import gzip
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ahsync.engine.views import money  # noqa: E402
from ahsync.intel.contract import MarketIntelligence, from_json  # noqa: E402

SOLD_SINCE = 16                 # the summary schema that publishes sold_med7 (BZ54)
AGREE = 0.10                    # within ±10%: the two medians agree
OVERSTATE = 1.20                # med7 at least 20% above sold_med7: the asks overstate what sells


@dataclass(frozen=True)
class Row:
    """One item's sold median beside its ask numbers."""

    key: int
    name: str
    sold_med7: int                  # copper
    sold_units7: float              # units behind it
    med7: int | None                # copper
    p90: int | None                 # copper
    post_price: int | None          # copper
    unseen_share: float | None      # of the week's sales, the share sold before any snapshot saw them; None unknown

    @property
    def ratio(self) -> float | None:
        """med7 ÷ sold_med7; None without both (sold_med7 > 0 when the item has a row)."""
        return None if self.med7 is None or self.sold_med7 <= 0 else self.med7 / self.sold_med7


def load(path: Path) -> MarketIntelligence:
    """A published summary, read.

    Args:
        path: A gzipped intelligence document.

    Returns:
        The intelligence.
    """
    return from_json(gzip.decompress(path.read_bytes()).decode("utf-8"))


def rows_of(intel: MarketIntelligence, min_units: float) -> list[Row]:
    """The items with sold prices, the widest disagreement between med7 and sold_med7 first.

    Args:
        intel: The summary.
        min_units: Leave out items with fewer sold units behind sold_med7.

    Returns:
        Rows ordered by |log(med7 ÷ sold_med7)| descending (items without med7 last), then by key.
    """
    rows = []
    for key in sorted(intel.items):
        i = intel.items[key]
        if i.sold_med7 is None or i.sold_units7 < min_units:
            continue
        unseen = None if i.unseen_per_day is None or i.sold_per_day is None or i.sold_per_day <= 0 \
            else i.unseen_per_day / i.sold_per_day
        name = str(intel.catalog[key][0]) if key in intel.catalog else str(key)
        rows.append(Row(key, name, i.sold_med7, i.sold_units7, i.med7, i.p90_30, i.post_price, unseen))
    return sorted(rows, key=lambda r: (r.ratio is None, -abs(math.log(r.ratio)) if r.ratio else 0.0, r.key))


def summary(rows: Sequence[Row]) -> list[str]:
    """The comparison in a few lines.

    Args:
        rows: Every row.

    Returns:
        Markdown lines: items compared, how many agree within ±10%, how many have asks 20%+ above what sold, how
        many below, and the median ratio and unseen share.
    """
    ratios = [r.ratio for r in rows if r.ratio is not None]
    if not ratios:
        return ["No item has both a sold median and a med7 yet."]
    agree = sum(abs(x - 1) <= AGREE for x in ratios)
    over = sum(x >= OVERSTATE for x in ratios)
    under = sum(x <= 1 / OVERSTATE for x in ratios)
    unseen = [r.unseen_share for r in rows if r.unseen_share is not None]
    middle = median_share(ratios)
    lines = [f"- Items with a sold median and a med7: **{len(ratios)}** (of {len(rows)} with sold prices).",
             f"- med7 within ±10% of the sold median: **{agree}** ({agree / len(ratios):.0%}).",
             f"- med7 20% or more **above** what sold (asks overstate): **{over}** ({over / len(ratios):.0%}).",
             f"- med7 20% or more **below** what sold: **{under}** ({under / len(ratios):.0%}).",
             f"- Median med7 ÷ sold median: {middle:.2f}."]
    if unseen:
        lines.append(f"- Median share of a week's sales no snapshot saw (no price): {median_share(unseen):.0%}.")
    return lines


def median_share(values: Sequence[float]) -> float:
    """The lower median of shares or ratios.

    Args:
        values: At least one number.

    Returns:
        The middle value, the lower one of an even count.
    """
    return sorted(values)[(len(values) - 1) // 2]


def cell(copper: int | None) -> str:
    """A money cell, "—" when unknown.

    Args:
        copper: Copper, or None.

    Returns:
        The formatted money.
    """
    return "—" if copper is None else money(copper)


def report(intel: MarketIntelligence, min_units: float, top: int) -> str:
    """The report, as markdown.

    Args:
        intel: The summary.
        min_units: Leave out items with fewer sold units behind sold_med7.
        top: Rows in the table.

    Returns:
        The markdown text.
    """
    rows = rows_of(intel, min_units)
    out = [f"# Sold prices against asks: {intel.market}", "",
           f"Summary schema {intel.schema_version}, prices to {intel.observations_until_ms or intel.built_ms} ms; "
           f"items with at least {min_units:g} sold units in 7 days.", "", *summary(rows), "",
           "| Item | Sold median 7d | Units | med7 | med7 ÷ sold | p90 30d | Post price | Unseen share |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows[:top]:
        ratio = "—" if r.ratio is None else f"{r.ratio:.2f}"
        unseen = "—" if r.unseen_share is None else f"{r.unseen_share:.0%}"
        out.append(f"| {r.name} ({r.key}) | {cell(r.sold_med7)} | {r.sold_units7:.1f} | {cell(r.med7)} | {ratio} | "
                   f"{cell(r.p90)} | {cell(r.post_price)} | {unseen} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write the report.

    Args:
        argv: Command-line arguments (--document, --out, --min-units, --top).

    Returns:
        Exit code: 0, or 2 for a summary older than schema 16 (it has no sold prices).
    """
    parser = argparse.ArgumentParser(description="Sold medians against ask medians, from one published summary.")
    parser.add_argument("--document", type=Path, required=True, help="a published summary (.json.gz)")
    parser.add_argument("--out", type=Path, help="the markdown file to write (default: print)")
    parser.add_argument("--min-units", type=float, default=1.0, help="sold units an item needs to be listed")
    parser.add_argument("--top", type=int, default=50, help="rows in the table")
    args = parser.parse_args(argv)
    intel = load(args.document)
    if intel.schema_version < SOLD_SINCE:
        print(f"{args.document}: summary schema {intel.schema_version} has no sold prices (needs {SOLD_SINCE}).",
              file=sys.stderr)
        return 2
    text = report(intel, args.min_units, args.top)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
