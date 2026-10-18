"""Tests for ahsync/export/plan_lua.py: PortfolioPlan.lua (SPEC §3.5, EX3)."""
from __future__ import annotations

from ahsync.export import plan_lua

HOUR = 3_600_000
T = 1_792_000_000_000
AMAYA = "Amaya-Grommash"
POST = ("forever.pvp.horde.us:1:0", AMAYA, "post", 2589, 20, 55, T + 5 * HOUR, 1045.5, None, T + 10 * HOUR, "pending",
        0.925, T + 13 * HOUR, None)


def test_EX3_open_actions_rules_with_their_clears_and_ingested_until_per_character():
    text = plan_lua.plan_lua([POST], [(2589, "no_buy", T), (4306, "none", T + HOUR)], {AMAYA: T + 2 * HOUR}, T + 3 * HOUR)
    action = f"forever.pvp.horde.us:1:0;post;2589;20;55;{T + 5 * HOUR};1046;;{T + 10 * HOUR};pending;93;{T + 13 * HOUR};"
    assert f'    ["{AMAYA}"] = {{\n      "{action}",\n    }},' in text
    assert f'    [2589] = "no_buy,{T}",' in text and f'    [4306] = "none,{T + HOUR}",' in text
    assert f'  ingestedUntil = {{\n    ["{AMAYA}"] = {T + 2 * HOUR},\n  }},' in text
    rules = len(f"no_buy,{T}") + len(f"none,{T + HOUR}")
    assert f'checksum = "3:{len(action) + rules}"' in text and f"basedOnUntil = {T + 2 * HOUR}," in text
    assert "schemaVersion = 1," in text and f"generatedAt = {T + 3 * HOUR}," in text


def test_an_empty_plan_and_unknown_fields():
    text = plan_lua.plan_lua([], [], {}, T)
    assert "basedOnUntil = nil," in text and 'checksum = "0:0"' in text
    hold = ("id", AMAYA, "hold", 1, 1, None, T, None, "parent", None, "late", None, None, None)
    assert plan_lua.action_text(hold) == f"id;hold;1;1;;{T};;parent;;late;;;"


def test_EX7_a_vendor_buy_ends_in_v_and_an_ah_buy_in_an_empty_field():
    wood = ("w", AMAYA, "buy", 4470, 7, 38, T, 0.0, None, None, "pending", None, None, "vendor")
    linen = ("l", AMAYA, "buy", 2589, 20, 45, T, 0.0, None, None, "pending", None, None, "ah")
    assert plan_lua.action_text(wood) == f"w;buy;4470;7;38;{T};0;;;pending;;;v"
    assert plan_lua.action_text(linen) == f"l;buy;2589;20;45;{T};0;;;pending;;;"
