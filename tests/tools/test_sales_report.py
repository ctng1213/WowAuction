"""Tests for tools/sales_report.py: the measurements behind the sales-rate decisions before M33b (plan §1)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import sales_report as rep  # noqa: E402

from ahsync.engine.volume import Auction, IdBook  # noqa: E402

H = 3_600_000
T0 = 1_794_000_000_000
SILK, WOOL = 4306, 2592


def books() -> list[IdBook]:
    """Four snapshots an hour apart: a posting gap, a cancel under cheapest-first, a sale, an expiry, a fill."""
    return [
        IdBook(T0, (Auction(1, SILK, 5, 10, 4),)),
        IdBook(T0 + H, (Auction(1, SILK, 5, 10, 4), Auction(2, SILK, 5, 12, 4), Auction(3, SILK, 5, 15, 2),
                        Auction(4, WOOL, 1, 900, 3))),
        IdBook(T0 + 2 * H, (Auction(1, SILK, 5, 10, 4), Auction(5, SILK, 4, 11, 4))),     # 2 cancelled, 3 maybe expired, 4 sold
        IdBook(T0 + 3 * H, (Auction(1, SILK, 2, 10, 4), Auction(5, SILK, 4, 11, 4))),     # 1 partly filled, cheapest
    ]


def test_tiers_follow_the_spec_limits():
    assert [rep.tier_of(p, 100) for p in (100, 105, 106, 115, 116)] == [1, 2, 3, 3, 4]


def test_a_vanish_under_a_cheaper_unchanged_auction_is_marked_and_a_short_band_is_no_early_leave():
    g = rep.gaps(books(), frozenset({SILK}))
    assert [(leave.key, leave.price, leave.tier, leave.cheaper_kept, leave.age_h) for leave in g.leaves] == [
        (SILK, 12, 4, True, 0), (WOOL, 900, 1, False, 0)]                  # 12 ÷ 10: T4; auction 3 (MEDIUM) may have expired
    assert [(f.key, f.units, f.cheaper_kept) for f in g.fills] == [(SILK, 3, False)]
    assert g.at_risk[(True, 0)] == 2 and g.left[(True, 0)] == 1         # 2 and 5 at risk at age 0; 2 left
    assert g.asks[SILK] == [10, 10, 10, 10] and g.span_ms == 3 * H


def test_the_cancel_floor_and_cheapest_first_tables_count_units_and_fills():
    g = rep.gaps(books(), frozenset({SILK}))
    assert rep.cancel_rows(g, frozenset({SILK}), {SILK: "Silk Cloth"})[:2] == [
        "| all commodities | 5 | 5 | 100.0% | 40 |", "| Silk Cloth | 5 | 5 | 100.0% | 40 |"]
    assert rep.cheapest_first_rows(g, frozenset({SILK})) == ["| commodities | 1 | 0 | 0.0% |", "| regular | 0 | 0 | — |"]


def test_the_weighted_median_takes_the_lower_middle():
    assert rep.weighted_median([(10, 1), (20, 1)]) == 10
    assert rep.weighted_median([(10, 1), (20, 3)]) == 20


def test_the_nothing_ahead_truth_follows_the_cheapest_listing_for_eight_hours():
    ex = rep.exposures(books())
    wool = [e for e in ex if e.key == WOOL]
    assert wool == [rep.Exposure(WOOL, 900, True, True, T0 + H)]   # cheapest when first seen, gone in its first gap
    assert not [e for e in ex if e.key == SILK]                      # Silk's cheapest is still listed when the span ends
    rows = rep.nothing_ahead_rows(ex, {(WOOL, (T0 + H) // rep.DAY_MS): 900}, frozenset({SILK}))
    # Wool was LONG (an 8 h post): counted over every band, but not in the clean share
    assert "| gear and the rest (1 items) | 1.00–1.10× | 0 | 0 | — | 1 | 1.00 |" in rows
    assert all(row.endswith("| 0 | 0 | — | 0 | — |") for row in rows if "1.00–1.10×" not in row)
    assert rep.nothing_ahead_rows(ex, {}, frozenset({SILK}))[0].endswith("| 0 | 0 | — | 0 | — |")   # no level, none counted
    leaves = [rep.Leave(WOOL, 1, 900 + i, T0 + (i % 3) * rep.DAY_MS, None, 1, False) for i in range(12)]
    g = rep.Gaps(leaves, [], rep.Counter(), rep.Counter(), {}, 0)
    levels = rep.local_levels(g, frozenset())
    day = T0 // rep.DAY_MS                                                         # 4 leaves on each of 3 days
    assert levels[(WOOL, day + 1)] == rep.weighted_median([(900 + i, 1) for i in range(12)])   # the middle day sees all 12
    assert (WOOL, day) not in levels and (WOOL, day + 3) not in levels            # 8 and 4 leaves: under the 10
    assert rep.first_gap_rows(ex, frozenset({WOOL}), frozenset({SILK})) == ["| gear | 1 | 1 | 1.00 |", "| commodities | 0 | 0 | — |"]
    still_up = books() + [rep.IdBook(T0 + 10 * H, (rep.Auction(1, SILK, 2, 10, 4),))]    # seen 10 h on: not sold in 8 h
    assert [e for e in rep.exposures(still_up) if e.key == SILK] == [rep.Exposure(SILK, 10, False, False, T0, True)]
    gone_unlinked = books() + [rep.IdBook(T0 + 10 * H, ())]                               # gone across a 7 h gap
    assert not [e for e in rep.exposures(gone_unlinked) if e.key == SILK]                 # the unlinked gap drops it
    linked = books() + [rep.IdBook(T0 + k * H, (rep.Auction(1, SILK, 2, 10, 4),)) for k in range(4, 10)]
    assert [e for e in rep.exposures(linked) if e.key == SILK] == [rep.Exposure(SILK, 10, False, False, T0, True)]   # listed 8 h on


def test_the_clean_share_counts_only_auctions_very_long_when_they_became_the_cheapest():
    day = T0 // rep.DAY_MS
    gone = [IdBook(T0, (Auction(7, WOOL, 1, 100, 4), Auction(8, SILK, 1, 100, 2))), IdBook(T0 + H, ())]
    ex = rep.exposures(gone)
    assert [(e.key, e.sold, e.clean) for e in ex] == [(WOOL, True, True), (SILK, False, False)]   # a 2 h post: ambiguous
    rows = rep.nothing_ahead_rows(ex, {(WOOL, day): 100, (SILK, day): 100}, frozenset({SILK}))
    assert "| gear and the rest (1 items) | 1.00–1.10× | 1 | 1 | 1.00 | 1 | 1.00 |" in rows
    assert "| commodities (1 items) | 1.00–1.10× | 0 | 0 | — | 1 | 0.00 |" in rows


def test_the_report_has_every_section():
    text = rep.report(books(), 0, frozenset({SILK}), {SILK: "Silk Cloth"}, frozenset({WOOL}))
    for heading in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7."):
        assert heading in text
    assert "4 snapshots over 3.0 h" in text
