"""Tests for tools/snapshot.py: the whole-plan snapshot (SPEC §8.6, WP1)."""
from __future__ import annotations

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
    assert snapshot.render({"b": [1, 2], "a": {"c": [{"d": None}]}}) == \
        '{\n "a": {\n  "c": [\n   {\n    "d": null\n   }\n  ]\n },\n "b": [1, 2]\n}'
