"""Tests for ahsync/export/plan_lua.py: PortfolioPlan.lua (SPEC §3.5, EX3)."""
from __future__ import annotations

from ahsync.export import plan_lua

HOUR = 3_600_000
T = 1_792_000_000_000
AMAYA = "Amaya-Grommash"
POST = ("forever.pvp.horde.us:1:0", AMAYA, "post", 2589, 20, 55, T + 5 * HOUR, 1045.5, None, T + 10 * HOUR, "pending",
        0.925, T + 13 * HOUR, None, 24)


def test_EX3_open_actions_rules_with_their_clears_and_ingested_until_per_character():
    text = plan_lua.plan_lua([POST], [(2589, "no_buy", T), (4306, "none", T + HOUR)], {AMAYA: T + 2 * HOUR}, T + 3 * HOUR)
    action = f"forever.pvp.horde.us:1:0;post;2589;20;55;{T + 5 * HOUR};1046;;{T + 10 * HOUR};pending;93;{T + 13 * HOUR};;24;;"
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
    assert plan_lua.action_text(hold) == f"id;hold;1;1;;{T};;parent;;late;;;;;;"


def test_EX7_a_vendor_buy_ends_in_v_and_an_ah_buy_in_an_empty_field():
    wood = ("w", AMAYA, "buy", 4470, 7, 38, T, 0.0, None, None, "pending", None, None, "vendor")
    linen = ("l", AMAYA, "buy", 2589, 20, 45, T, 0.0, None, None, "pending", None, None, "ah")
    assert plan_lua.action_text(wood) == f"w;buy;4470;7;38;{T};0;;;pending;;;v;;;"
    assert plan_lua.action_text(linen) == f"l;buy;2589;20;45;{T};0;;;pending;;;;;;"


def test_EX9_a_post_carries_its_auction_duration_as_a_14th_field():
    bars = ("b", AMAYA, "post", 2840, 5, 900, T, 10.0, None, None, "pending", 0.5, None, None, 8)
    buy = ("u", AMAYA, "buy", 2840, 5, 800, T, 0.0, None, None, "pending", None, None, "ah", None)
    assert plan_lua.action_text(POST).endswith(";24;;") and plan_lua.action_text(bars).endswith(";8;;")
    assert plan_lua.action_text(buy).endswith(";;;;")


def test_EX16_a_mail_carries_its_recipient_as_a_15th_field():
    mail = ("m", "Brugan-Grommash", "mail", 2840, 6, None, T, 0.0, "c", None, "pending", None, None, None, None, AMAYA)
    assert plan_lua.action_text(mail) == f"m;mail;2840;6;;{T};0;c;;pending;;;;;{AMAYA};"


def test_EX22_a_post_carries_what_its_net_takes_off_as_a_16th_field():
    leather = ("h", AMAYA, "post", 4234, 10, 60, T, 35.0, None, None, "pending", 0.7, None, None, 24, None, 400)
    linen = ("l", AMAYA, "post", 2589, 20, 18, T, 305.3, None, None, "pending", 0.88, None, None, 24, None, None)
    buy = ("u", AMAYA, "buy", 4234, 10, 40, T, 35.0, None, None, "pending", None, None, "ah", None, None, 0)
    assert plan_lua.action_text(leather) == f"h;post;4234;10;60;{T};35;;;pending;70;;;24;;400"   # 435 expected − 35 net
    assert plan_lua.action_text(linen).endswith(";24;;") and plan_lua.action_text(buy).endswith(";;;;")


def test_EX8_records_go_outside_the_checksum():
    plain = plan_lua.plan_lua([POST], [], {}, T)
    text = plan_lua.plan_lua([POST], [], {}, T, {2318: "1;Your record (30 d): 0 of 3 sold"})
    assert '    [2318] = "1;Your record (30 d): 0 of 3 sold",' in text and "  records = {\n  }," in plain
    assert plain.split("checksum = ")[1].split(",")[0] == text.split("checksum = ")[1].split(",")[0]


def test_EX12_reserves_with_times_go_outside_the_checksum_by_character_and_key():
    reserves = [(AMAYA, 4306, 0, T + 2 * HOUR), (AMAYA, 2589, -1, T), (AMAYA, 2840, 5, T + HOUR)]
    plain = plan_lua.plan_lua([POST], [], {}, T)
    text = plan_lua.plan_lua([POST], [], {}, T, None, reserves)
    assert f'    ["{AMAYA}"] = "2589,-1,{T};2840,5,{T + HOUR};4306,0,{T + 2 * HOUR}",' in text
    assert "  reserves = {\n  }," in plain
    assert plain.split("checksum = ")[1].split(",")[0] == text.split("checksum = ")[1].split(",")[0]
