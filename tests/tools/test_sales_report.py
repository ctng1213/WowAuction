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


def test_the_report_has_every_section():
    text = rep.report(books(), 0, frozenset({SILK}), {SILK: "Silk Cloth"})
    for heading in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6."):
        assert heading in text
    assert "4 snapshots over 3.0 h" in text
