"""Tests for tools/savedvars_report.py: what the addon captured in game, and what is still missing (SPEC §6)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import savedvars_report as rep  # noqa: E402


def test_each_missing_piece_is_one_hint_naming_its_characters():
    gaps = [("recipes", "De"), ("bank", "De"), ("recipes", "Willie"), ("recipes", "De"), ("vendor", None)]
    assert rep.gap_lines(gaps) == [("recipes", "De, Willie"), ("bank", "De"), ("vendor", "")]
