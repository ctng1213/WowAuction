"""Tests for tools/lifetimes_report.py: M33 step 1's report of the observed lifetime distributions (SPEC §2.4)."""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import lifetimes_report as rep  # noqa: E402

from ahsync.collect.blizzard import raw_key  # noqa: E402
from ahsync.engine.volume import Auction, IdBook, lifetimes  # noqa: E402
from tests.support.cloud_world import A_MS, B_MS, C_MS, LINEN, M, SNAP_A, SNAP_B, SNAP_C, SPAULDERS  # noqa: E402

H = 3_600_000


def raw_folder(tmp_path: Path) -> Path:
    """A market's raw folder with the cloud world's three snapshots and one refused envelope."""
    for at_ms, envelope in ((A_MS, SNAP_A), (B_MS, SNAP_B), (C_MS, SNAP_C)):
        path = tmp_path / raw_key(M, at_ms)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(envelope)
    (tmp_path / "raw" / M / "2026" / "11" / "06" / "000000.json.gz").write_bytes(b"not gzip")
    return tmp_path / "raw" / M


def test_the_report_reads_every_envelope_and_counts_what_it_saw(tmp_path):
    books, skipped = rep.load_books(raw_folder(tmp_path))
    assert [b.at_ms for b in books] == [A_MS, B_MS, C_MS] and skipped == 1
    text = rep.report(books, skipped, frozenset({SPAULDERS}))
    assert "3 snapshots over" in text and "(1 refused or empty)" in text
    assert "1 endings, 1 partial fills, 1 new listings" in text                   # Spaulders gone, Linen 14 -> 10, 2470 new
    assert "| posting unknown | 1 | 0 | 1 | 0 | 1.000 |" in text                 # the short Spaulders expired


def test_band_against_age_counts_only_auctions_whose_posting_is_known():
    t = 1_000 * H
    books = [IdBook(t, (Auction(1, LINEN, 5, 100, 4),)),
             IdBook(t + H, (Auction(1, LINEN, 5, 100, 4), Auction(2, LINEN, 5, 100, 3))),
             IdBook(t + 2 * H, (Auction(2, LINEN, 5, 100, 3),))]
    seen = rep.sightings(books)
    assert seen.known == frozenset({2}) and seen.new_by_band == {3: 1}
    assert seen.by_band_age == {(3, 0): 1, (3, 1): 1}


def test_the_placebo_estimates_relisting_as_the_excess_over_a_later_gap():
    t = 1_000 * H
    books = [IdBook(t + k * H, ((Auction(1, LINEN, 5, 100, 4),) if k == 0 else ())
                    + ((Auction(10 + k, LINEN, 5, 90, 4),) if k == 1 else ())) for k in range(5)]
    rows = rep.relist_rows(lifetimes(books), frozenset())
    assert rows == ["| other | 1 | 0 | 1.0 | 1.000 | 0.000 | 1.000 |"]   # the one with 3 later gaps matched only in its own
    found = rep.relist_estimate(lifetimes(books), lambda key: True)
    assert (found.endings, found.late, found.early, found.relisted) == (1, 0, 1.0, 1.0)
    assert rep.relist_estimate(lifetimes(books), lambda key: False) is None


def test_stacks_bucket_and_gear_comes_from_the_catalog(tmp_path):
    assert [rep.stack_kind(q) for q in (1, 20, 7)] == ["1", "round", "other"]
    document = tmp_path / "doc.json.gz"
    document.write_bytes(gzip.compress(json.dumps({"catalog": {"8180": ["S", 2, 4, 412, None, "forever"],
                                                               "2589": ["L", 1, 7, 13, None, "forever"]}}).encode()))
    assert rep.gear_of(document) == frozenset({8180}) and rep.gear_of(None) == frozenset()


def test_main_writes_the_markdown(tmp_path):
    out = tmp_path / "report.md"
    assert rep.main(["--raw", str(raw_folder(tmp_path)), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# M33 step 1")
