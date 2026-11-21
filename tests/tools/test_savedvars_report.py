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


def test_a_character_line_lists_its_cooldown_ready_times(capsys, monkeypatch):
    from ahsync.state.savedvars import read_ahprofit
    from tests.support.savedvars_builder import savedvars_text
    monkeypatch.setattr(rep, "local_time", lambda ms: f"t{ms}")
    char = {"name": "Amaya", "realm": "Grommash", "faction": "Horde", "class": "MAGE", "level": 60, "gold": 0,
            "updated": 1}
    data = read_ahprofit(savedvars_text({"characters": {"Amaya-Grommash": char},
                                         "cooldowns": {"Amaya-Grommash": "17187,5,c,1;19566,9,g,2"}}))
    rep.report_character(data, "Amaya-Grommash", [])
    assert "  cooldowns: 17187 cast ready t5, 19566 game ready t9\n" in capsys.readouterr().out
    rep.report_character(read_ahprofit(savedvars_text({"characters": {"Amaya-Grommash": char}})), "Amaya-Grommash", [])
    assert "  cooldowns: none read\n" in capsys.readouterr().out
