"""Step 1 of M33: the observed distributions behind the lifetime rules, from the raw collected so far (SPEC §2.4).

    python tools/lifetimes_report.py --raw data/cloud-live/raw/forever.normal.horde.us \
        --catalog data/cloud-live/document.json.gz --out docs/plans/blizzard-cloud-lifetimes.md

Reads every raw envelope under --raw (read-only), follows the auctions with `engine.volume.lifetimes`, and writes a
markdown report: band against age since posting (the band bounds, BZ24), clashes with the rules, short at first
sight, how endings split between sold and expired, and relist matches by kind and stack (the relist decision).
The report is a temporary plan file; it changes no number in the product.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ahsync.collect.blizzard import BlizzardError, http_date_ms, open_envelope, parse_snapshot
from ahsync.collect.provider import load_bonus_suffixes
from ahsync.engine.capacity import GEAR_CLASSES
from ahsync.engine.volume import HOUR_MS, MAX_GAP_MS, Ending, IdBook, Lifetimes, lifetimes

BANDS = {1: "SHORT", 2: "MEDIUM", 3: "LONG", 4: "VERY_LONG"}
MAX_AGE_H = 25


@dataclass(frozen=True)
class Sightings:
    """Every sighting of an auction whose posting gap is known, by band and whole hours since that gap's end."""

    by_band_age: Counter          # (band, age hours) -> sightings
    new_by_band: Counter          # band at first sight -> new listings
    known: frozenset[int]         # auction ids with a known posting gap


def load_books(raw: Path) -> tuple[list[IdBook], int]:
    """Every raw envelope under a folder as an IdBook, oldest first.

    Args:
        raw: A market's raw folder (…/raw/<market>), its envelopes in <YYYY>/<MM>/<DD>/<HHMMSS>.json.gz.

    Returns:
        (the snapshots, how many envelopes were refused or empty).
    """
    suffixes, books, skipped = load_bonus_suffixes(), [], 0
    for path in sorted(raw.rglob("*.json.gz")):
        try:
            stamp, regular, commodities = open_envelope(path.read_bytes())
            observation = parse_snapshot("report", regular, commodities, http_date_ms(stamp), suffixes).observation
        except BlizzardError:
            observation = None
        if observation is None:
            skipped += 1
            continue
        books.append(IdBook(observation.at_ms, observation.auctions))
    return sorted(books, key=lambda b: b.at_ms), skipped


def gear_of(document: Path | None) -> frozenset[int]:
    """Gear keys from a published document's catalog.

    Args:
        document: A gzipped intelligence document, or None.

    Returns:
        Keys whose item class is gear; empty without a document.
    """
    if document is None:
        return frozenset()
    catalog = json.loads(gzip.decompress(document.read_bytes()))["catalog"]
    return frozenset(int(k) for k, row in catalog.items() if row[2] in GEAR_CLASSES)


def sightings(books: list[IdBook], max_gap_ms: int = MAX_GAP_MS) -> Sightings:
    """Band against age for auctions first seen right after a linked gap (their posting gap is known).

    Args:
        books: Snapshots, oldest first.
        max_gap_ms: Largest gap between linked snapshots, in ms.

    Returns:
        The Sightings; age is whole hours from the end of the posting gap, capped at MAX_AGE_H.
    """
    posted: dict[int, int] = {}                  # auction id -> end of its posting gap, ms
    by_band_age, new_by_band = Counter(), Counter()
    previous: set[int] | None = None
    prev_at = None
    for book in books:
        linked = prev_at is not None and book.at_ms - prev_at <= max_gap_ms
        for a in book.auctions:
            if a.id not in posted and linked and previous is not None and a.id not in previous:
                posted[a.id] = book.at_ms
                new_by_band[a.band] += 1
            if a.id in posted:
                by_band_age[(a.band, min(MAX_AGE_H, (book.at_ms - posted[a.id]) // HOUR_MS))] += 1
        previous, prev_at = {a.id for a in book.auctions}, book.at_ms
    return Sightings(by_band_age, new_by_band, frozenset(posted))


def stack_kind(qty: int) -> str:
    """A stack size's bucket for relist candidates (round stacks match weakly, SPEC §2.4 item 5).

    Args:
        qty: Units.

    Returns:
        "1", "round" (5, 10, 20, 100, ... a multiple of 5) or "other".
    """
    return "1" if qty == 1 else "round" if qty % 5 == 0 else "other"


def fate_table(lt: Lifetimes, known: frozenset[int]) -> list[str]:
    """How endings split, for auctions with a known posting gap and the rest.

    Args:
        lt: The lifetimes.
        known: Auction ids with a known posting gap.

    Returns:
        Markdown table rows.
    """
    rows = []
    for label, cohort in (("posting known", [e for e in lt.endings if e.auction in known]),
                          ("posting unknown", [e for e in lt.endings if e.auction not in known])):
        n = len(cohort)
        sold = sum(1 for e in cohort if e.overlap == 0.0)          # could not have expired (the raw overlap)
        expired = sum(1 for e in cohort if e.overlap == 1.0)
        share = sum(e.expired for e in cohort) / n if n else 0.0
        rows.append(f"| {label} | {n} | {sold} | {expired} | {n - sold - expired} | {share:.3f} |")
    return rows


def available(new_in: dict[int, Counter], gap_end_ms: int, e: Ending) -> bool:
    """Whether a gap saw a new listing of an ending's key and units (the placebo's test, not the one-to-one match).

    Args:
        new_in: Gap end ms -> (key, units) -> new listings first seen at it.
        gap_end_ms: The gap's later snapshot time.
        e: The ending.

    Returns:
        True when at least one such listing appeared.
    """
    return new_in.get(gap_end_ms, Counter())[(e.key, e.qty)] > 0


@dataclass(frozen=True)
class RelistEstimate:
    """Relist matches against a placebo for a set of endings (SPEC §2.4 item 5)."""

    endings: int                 # endings with a gap `shift` later, all counted
    late: int                    # of those, vanished in a gap overlapping their expiry window (their last hour)
    early: float                 # estimated early leavers among them: Σ (1 − BZ44's expired share)
    rate: float                  # share with a new listing of the same key and units in the vanish gap
    base: float                  # the same share `shift` gaps later (the placebo)
    relisted: float              # estimated relisted share of the early leavers, 0-1


def relist_estimate(lt: Lifetimes, pick: Callable[[int], bool], shift: int = 3) -> RelistEstimate | None:
    """Relist matches against a placebo, for the endings of the items `pick` keeps.

    Both rates ask the same question, whether any new listing of the ending's key and units appeared in that gap.
    If cancel-and-repost were common, the vanish gap would hold a match more often than a later gap. Every ending
    counts in the matches (only a listing that left early can have a real one); the excess matches, ÷ (1 −
    baseline), are divided by the estimated early leavers, each ending counting 1 − its BZ44 expired share, so
    the expiries among the endings neither dilute the share nor need dropping (the user, 2026-11-08; SM4).

    Args:
        lt: The lifetimes.
        pick: Item key -> whether its endings count.
        shift: How many gaps later the placebo looks.

    Returns:
        The estimate over the endings with a gap `shift` later; None when there is no such ending or no early
        leaver is expected among them.
    """
    new_in: dict[int, Counter] = {}
    for _, key, qty, gap, *_ in lt.listed:
        new_in.setdefault(gap[1], Counter())[(key, qty)] += 1
    times = [g[1] for g in lt.gaps]
    index = {t: i for i, t in enumerate(times)}
    later = [e for e in lt.endings if pick(e.key) and index[e.gap[1]] + shift < len(times)]
    early = sum(1.0 - e.expired for e in later)
    if not later or early <= 0:
        return None
    rate = sum(available(new_in, times[index[e.gap[1]]], e) for e in later) / len(later)   # the same test in both
    base = sum(available(new_in, times[index[e.gap[1]] + shift], e) for e in later) / len(later)
    excess = max(0.0, rate - base) * len(later) / (1.0 - base) if base < 1.0 else 0.0
    return RelistEstimate(len(later), sum(e.overlap > 0 for e in later), early, rate, base, min(1.0, excess / early))


def relist_rows(lt: Lifetimes, gear: frozenset[int], shift: int = 3) -> list[str]:
    """The relist estimate for gear and for everything else, as report rows.

    Args:
        lt: The lifetimes.
        gear: Gear keys.
        shift: How many gaps later the placebo looks.

    Returns:
        Markdown table rows: kind, endings, of those in their last hour, estimated early leavers, match rate,
        placebo rate, estimated relisted share.
    """
    rows = []
    for label, pick in (("gear", lambda k: k in gear), ("other", lambda k: k not in gear)):
        r = relist_estimate(lt, pick, shift)
        if r is not None:
            rows.append(f"| {label} | {r.endings} | {r.late} | {r.early:.1f} | {r.rate:.3f} | {r.base:.3f} | "
                        f"{r.relisted:.3f} |")
    return rows


def report(books: list[IdBook], skipped: int, gear: frozenset[int]) -> str:
    """The markdown report.

    Args:
        books: Snapshots, oldest first.
        skipped: Envelopes refused or empty.
        gear: Gear keys.

    Returns:
        Markdown text.
    """
    lt, seen = lifetimes(books), sightings(books)
    span_h = (books[-1].at_ms - books[0].at_ms) / HOUR_MS if books else 0.0
    sightings_total = sum(seen.by_band_age.values())
    lines = ["# M33 step 1: auction lifetimes report (temporary)", "",
             f"{len(books)} snapshots over {span_h:.1f} h ({skipped} refused or empty), {len(lt.gaps)} linked gaps, "
             f"{len(lt.endings)} endings, {len(lt.fills)} partial fills, {len(lt.listed)} new listings.", "",
             "## Band against hours since posting (BZ24)", "",
             "Sightings of auctions whose posting gap is known; hour 0 is the snapshot that first saw them.", "",
             "| band | " + " | ".join(str(h) for h in range(MAX_AGE_H + 1)) + " |",
             "|---|" + "---|" * (MAX_AGE_H + 1)]
    for band in (4, 3, 2, 1):
        lines.append(f"| {BANDS[band]} | " + " | ".join(str(seen.by_band_age.get((band, h), 0)) for h in range(MAX_AGE_H + 1)) + " |")
    new_total = sum(seen.new_by_band.values())
    lines += ["", "## Rules against the data", "",
              f"- Band clashes with the expiry window: {lt.clashes} of {sightings_total} sightings of known-posting auctions"
              f" (all sightings are checked; short-at-first-sight is not counted).",
              "- Band at first sight of new listings: " + ", ".join(
                  f"{BANDS[b]} {seen.new_by_band.get(b, 0)} ({seen.new_by_band.get(b, 0) / new_total:.1%})" if new_total else ""
                  for b in (4, 3, 2, 1)), "",
              "## Endings: sold or expired", "",
              "| cohort | endings | sold (0) | expired (1) | split | mean expired share |", "|---|---|---|---|---|---|",
              *fate_table(lt, seen.known), "",
              "## Relist matches against a placebo (same key and units, new in the same gap; 3 gaps later)", "",
              "| kind | endings | in their last hour | est. early leavers | matched in the gap | matched 3 gaps later "
              "| estimated relisted share |",
              "|---|---|---|---|---|---|---|", *relist_rows(lt, gear), "",
              "| matched, by stack | endings |", "|---|---|"]
    stacks = Counter(stack_kind(e.qty) for e in lt.endings if e.matched)
    lines += [f"| {stack} | {n} |" for stack, n in sorted(stacks.items())]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write the report.

    Args:
        argv: Command-line arguments (--raw, --catalog, --out).

    Returns:
        Exit code 0.
    """
    parser = argparse.ArgumentParser(description="The observed distributions behind the lifetime rules (M33 step 1).")
    parser.add_argument("--raw", type=Path, required=True, help="a market's raw folder")
    parser.add_argument("--catalog", type=Path, help="a published document (.json.gz) for the gear keys")
    parser.add_argument("--out", type=Path, required=True, help="the markdown file to write")
    args = parser.parse_args(argv)
    books, skipped = load_books(args.raw)
    args.out.write_text(report(books, skipped, gear_of(args.catalog)), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
