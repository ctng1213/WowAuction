"""Tests for tools/snapshot.py: the whole-plan snapshot (SPEC §8.6, WP1)."""
from __future__ import annotations

import io
import json

import tools.snapshot as snapshot


def test_WP1_the_whole_plan_on_the_fixture_world_matches_its_snapshot_byte_for_byte():
    first, second = snapshot.snapshot(), snapshot.snapshot()
    assert first == second                                              # determinism: two runs, same bytes
    expected = snapshot.EXPECTED.read_text(encoding="utf-8")
    assert first == expected, "whole-plan snapshot changed: review, then python tools/snapshot.py --update"


def test_the_snapshot_holds_every_page_the_plan_and_both_addon_files():
    doc = json.loads(snapshot.EXPECTED.read_text(encoding="utf-8"))
    assert sorted(doc["views"]) == ["characters", "craft", "disenchant", "flip", "history", "inventory", "on_ah",
                                    "rules", "today", "week"]
    assert doc["actions"] and sorted(doc["addon_files"]) == ["MarketIntelligence.lua", "PortfolioPlan.lua"]


def test_heat_map_cells_are_digested_and_long_files_trimmed():
    cells = [{"level": 1, "title": "Mon 00:00"}, {"level": 0, "title": "Mon 01:00"}]
    digest = snapshot.compact_cells({"heat": [{"cells": cells}], "n": 3})
    assert digest["n"] == 3 and digest["heat"][0]["cells"]["levels"] == "10"
    assert digest["heat"][0]["cells"]["first"] == "Mon 00:00" and len(digest["heat"][0]["cells"]["titles_sha256"]) == 64
    assert snapshot.trimmed("a\nb\nc\nd", keep=2) == "a\nb\n-- (1 lines trimmed by tools/snapshot.py)\nd"
    assert snapshot.trimmed("a\nb", keep=2) == "a\nb"
    market = "a\nb\nI[1]=1\nK[1]=\"x\"\nR[2]=\"y\"\nend\nAHProfitMarket = {\n  checksum = 1,\n}"
    assert snapshot.trimmed(market, keep=2) == \
        "a\nb\n-- (2 lines trimmed by tools/snapshot.py)\nK[1]=\"x\"\nR[2]=\"y\"\nAHProfitMarket = {\n  checksum = 1,\n}"
    assert snapshot.render({"b": [1, 2], "a": {"c": [{"d": None}]}}) == \
        '{\n "a": {\n  "c": [\n   {\n    "d": null\n   }\n  ]\n },\n "b": [1, 2]\n}'


def test_the_summary_prints_on_a_console_that_cannot_encode_it():
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252")                   # a Windows console without PYTHONIOENCODING
    snapshot.utf8_console(console, object())                            # a stream without reconfigure is skipped
    print("help: \"Best selling windows\" ≥ 75%", file=console)
    console.flush()
    assert "≥".encode("utf-8") in raw.getvalue()
