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
    assert sorted(doc["views"]) == ["characters", "craft", "disenchant", "flip", "gathering", "history", "inventory",
                                    "on_ah", "portfolio", "rules", "today", "week"]
    assert doc["actions"] and sorted(doc["addon_files"]) == ["MarketIntelligence.lua", "PortfolioPlan.lua"]


def test_the_gathering_page_keeps_priced_or_rated_rows_and_digests_the_rest():
    def row(key, price, rate="—"):
        return {"key": key, "price": price, "rate": rate}
    view = {"keys": [1, 2, 3], "cells": {"1": {"eligible": False}, "2": {"eligible": False}}, "foot": []}
    body = {"rows": [row(1, 20), row(2, None), row(3, None, "4")], "rate_cards": {"1": "a", "2": "b", "3": "c"},
            "by_char": {"": {"keys": [1, 2, 3], "cells": {}, "foot": []}, "A-R": view},
            "details": {"1": "d1", "2": "d2", "3": "d3"}, "header": "h"}
    out = snapshot.compact_gathering(body)
    assert [r["key"] for r in out["rows"]] == [1, 3] and out["rate_cards"] == {"1": "a", "3": "c"}
    assert out["other_rows"]["count"] == 1 and len(out["other_rows"]["sha256"]) == 64
    assert out["by_char"]["A-R"]["keys"] == [1, 3] and out["by_char"]["A-R"]["cells"] == {"1": {"eligible": False}}
    assert out["by_char"]["A-R"]["other"]["count"] == 1
    assert out["details"] == {"1": "d1", "3": "d3"} and out["other_details"]["count"] == 1 and out["header"] == "h"
    changed = snapshot.compact_gathering({**body, "details": {**body["details"], "2": "other"}})
    assert changed["other_details"]["sha256"] != out["other_details"]["sha256"]


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
