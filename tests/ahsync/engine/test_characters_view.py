"""Tests for ahsync/engine/characters_view.py: the Characters page (SPEC §3.5, D49)."""
from __future__ import annotations

from ahsync.engine.characters_view import CharacterSheet, characters_view

HOUR = 3_600_000
NOW = 1_760_000_000_000
M = "forever.pvp.horde.us"


def test_D49_one_card_per_character_with_its_budget_and_professions():
    amaya = CharacterSheet("Amaya-Grommash", "MAGE", 38, "Horde", M, 210_000, None, 37, NOW - 2 * HOUR,
                           (("Enchanting", 142, 225), ("Tailoring", 160, 225)))
    brugan = CharacterSheet("Brugan-Grommash", "WARRIOR", 34, "Horde", M, 104_000, 100_000, 24, NOW - HOUR)
    cira = CharacterSheet("Cira-Elsewhere", None, None, "Horde", None, None, None, 0, None, (("Fishing", None, None),))
    page = characters_view([cira, brugan, amaya], NOW)
    a, b, c = page["cards"]
    assert (a["title"], a["subtitle"], a["lines"]) == ("Amaya", "Mage · 38", [
        ["Realm", "Horde · Grommash (pvp)"], ["Market", M], ["Gold (budget)", "21g"], ["Known recipes", "37"],
        ["Last seen", "2 h ago"]])
    assert a["professions"] == [{"name": "Enchanting", "skill": "142 / 225", "pct": 63, "gathering": False},
                                {"name": "Tailoring", "skill": "160 / 225", "pct": 71, "gathering": False}]
    assert b["lines"][2] == ["Budget", "10g (set in config.toml)"]
    assert (c["subtitle"], c["lines"][0], c["lines"][1], c["lines"][4]) == (
        "", ["Realm", "Horde · Elsewhere"], ["Market", "none · realm not in config.toml"], ["Last seen", "—"])
    assert c["professions"] == [{"name": "Fishing", "skill": "— / —", "pct": 0, "gathering": True}]
    assert page["empty"] == ""
    assert characters_view([], NOW) == {"cards": [], "empty": "No characters yet: log in with AHProfit enabled, then /reload."}


def test_D86_every_captured_profession_has_its_bar_the_gathering_ones_tagged():
    # the professions capture keeps them by name (state.reads); the card lists crafting first, then gathering
    amaya = CharacterSheet("Amaya-Grommash", "MAGE", 38, "Horde", M, 210_000, None, 37, NOW, (
        ("Enchanting", 142, 225), ("Fishing", 20, 75), ("Herbalism", 45, 75), ("Tailoring", 160, 225)))
    brugan = CharacterSheet("Brugan-Grommash", "WARRIOR", 34, "Horde", M, 104_000, None, 24, NOW, (
        ("Alchemy", 225, 225), ("Blacksmithing", 112, 150), ("Mining", 68, 75), ("Skinning", 40, 75)))
    a, b = characters_view([amaya, brugan], NOW)["cards"]
    assert [(p["name"], p["skill"], p["gathering"]) for p in a["professions"]] == [
        ("Enchanting", "142 / 225", False), ("Tailoring", "160 / 225", False), ("Fishing", "20 / 75", True),
        ("Herbalism", "45 / 75", True)]
    assert [(p["name"], p["skill"], p["pct"], p["gathering"]) for p in b["professions"]] == [
        ("Alchemy", "225 / 225", 100, False), ("Blacksmithing", "112 / 150", 75, False), ("Mining", "68 / 75", 91, True),
        ("Skinning", "40 / 75", 53, True)]                    # 68 ÷ 75 = 90.7% → 91; 40 ÷ 75 = 53.3% → 53

