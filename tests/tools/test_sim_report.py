"""Tests for tools/sim_report.py: the simulator's inference report (SPEC §8.10)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import sim_report as rep  # noqa: E402
from sim.inference import Measure  # noqa: E402


def test_a_measure_prints_as_a_row_with_its_gap_and_result():
    assert rep.number(None) == "—" and rep.number(0.12345) == "0.123" and rep.number(149_500) == "149500.0"
    assert rep.line(2, Measure("SM1", "sold/day", 105, 100, "±5%", True)) == \
        "| SM1 | 2 | sold/day | 105.0 | 100.0 | +5.0% | ±5% | pass |"
    assert rep.line(1, Measure("SM3", "share", None, 0.2, "reported")).endswith("| — | 0.200 | — | reported |  |")


def test_the_inference_report_runs_the_chosen_rows_per_seed(tmp_path):
    out = tmp_path / "report.md"
    assert rep.main(["inference", "--rows", "SM10,SM11", "--seeds", "1,2", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Inference against truth")
    assert text.count("| SM10 |") == 6 and text.count("| SM11 | 2 |") == 2 and "| pass |" in text
    assert rep.main(["inference", "--rows", "SM99"]) == 2


def test_the_decision_report_lists_every_benchmark_per_seed(tmp_path):
    out = tmp_path / "decision.md"
    assert rep.main(["decision", "--seeds", "1", "--rows", "starter", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Decision quality") and [line.split(" | ")[1] for line in text.splitlines()
                                                      if line.startswith("| 1 |")] == ["plan", "naive", "nothing",
                                                                                         "oracle"]

    assert rep.main(["decision", "--rows", "SD99"]) == 2


def test_an_sd_row_prints_its_lines_with_their_marks(tmp_path, monkeypatch):
    from sim import decision
    from sim.decision import Line
    monkeypatch.setitem(decision.SD_ROWS, "SD31", lambda folder, seed: [Line("SD31", "buys", "0", "0", True),
                                                                        Line("SD31", "offers", "2", "0", False),
                                                                        Line("SD31", "sales", "—", "≥ 1", None)])
    text = rep.decision_report(["SD31"], [1], tmp_path)
    assert "| SD31 | 1 | buys | 0 | 0 | met |" in text and "| SD31 | 1 | offers | 2 | 0 | **missed** |" in text
    assert "| SD31 | 1 | sales | — | ≥ 1 | not exercised |" in text      # the world never tested it
