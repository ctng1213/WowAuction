"""Tests for tools/bench.py's cloud market mode (plan summary-memory SM0)."""
from __future__ import annotations

import gzip
import sys
from pathlib import Path

from ahsync.intel.contract import MarketIntelligence, to_json

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import bench  # noqa: E402

M = "forever.normal.horde.us"


def test_the_summary_mode_polls_two_hours_and_reports_the_peak(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(bench, "peak_memory_mb", lambda: 1.0)        # the test process's peak is the suite's
    document = tmp_path / "doc.json.gz"
    document.write_bytes(gzip.compress(to_json(MarketIntelligence(M, 1_000, 2_000, 4, {})).encode("utf-8")))
    work = tmp_path / "work"
    assert bench.publish_summaries(document, work) == (M, 2_000)
    assert bench.summary_only(work, M, 2_000) == 0
    printed = capsys.readouterr().out
    assert "hour 1 poll" in printed and "hour 2 dashboard read" in printed and "-> OK" in printed
