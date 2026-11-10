"""Tests for tools/sold_price_report.py: sold medians against ask medians from one summary."""
from __future__ import annotations

import gzip
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import sold_price_report as rep  # noqa: E402
from ahsync.intel.contract import ItemIntel, MarketIntelligence, to_json  # noqa: E402

BASE = ItemIntel(1, 45, 40, 3_600_000, 50, 52, 40, 60, 1.5, 38.0, 4.0, 40.0, "blizzard", {"blizzard": 7}, 44, 20, 0.8,
                 False, 40.0, 70, 400, 0, ())


def summary_file(tmp_path: Path, schema: int | None = None) -> Path:
    """A summary of four items: asks far above what sold, agreeing, below, and one with no sold prices."""
    items = {1: replace(BASE, key=1, med7=150_000, sold_med7=20_000, sold_units7=7.0, sold_per_day=1.0,
                        unseen_per_day=0.25),
             2: replace(BASE, key=2, med7=105, sold_med7=100, sold_units7=700.0),
             3: replace(BASE, key=3, med7=60, sold_med7=100, sold_units7=50.0),
             4: replace(BASE, key=4, med7=80)}
    intel = MarketIntelligence("m", 1_000, 900, 8, items, catalog={1: ("Scouting Gloves", 2, 4, 0, 1, "forever")})
    path = tmp_path / "document.json.gz"
    path.write_bytes(gzip.compress(to_json(intel, schema).encode("utf-8")))
    return path


def test_the_report_ranks_the_widest_disagreement_first_and_counts_agreement(tmp_path):
    out = tmp_path / "report.md"
    assert rep.main(["--document", str(summary_file(tmp_path)), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    table = [line for line in text.splitlines() if line.startswith("| ") and "(" in line]
    assert table[0].startswith("| Scouting Gloves (1) | 2g | 7.0 | 15g | 7.50 |") and table[0].endswith("| 25% |")
    assert [line.split(" | ")[0] for line in table[1:]] == ["| 3 (3)", "| 2 (2)"]          # |log 0.6| > |log 1.05|
    assert "within ±10% of the sold median: **1** (33%)" in text and "**above** what sold (asks overstate): **1**" in text
    assert "**below** what sold: **1**" in text and "no snapshot saw (no price): 25%" in text


def test_a_summary_without_sold_prices_is_refused_and_min_units_filters(tmp_path, capsys):
    assert rep.main(["--document", str(summary_file(tmp_path, 15))]) == 2
    assert rep.main(["--document", str(summary_file(tmp_path)), "--min-units", "100"]) == 0
    printed = capsys.readouterr().out
    assert "| 2 (2) |" in printed and "Scouting Gloves" not in printed
    assert rep.summary([]) == ["No item has both a sold median and a med7 yet."]
