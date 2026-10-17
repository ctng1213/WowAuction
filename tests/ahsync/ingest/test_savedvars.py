"""Tests for ahsync/ingest/savedvars.py: reading AHProfitDB into records (SPEC §2.2.1)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ahsync.ingest.savedvars import (
    AuctionRow, Character, Event, InventoryRow, InventorySnapshot, ItemRule, KnownRecipe,
    OwnAuctionSnapshot, Profession, Reagent, Recipe, SavedVarsError, SavedVarsParseError, Scan,
    UnsupportedFormatError, read_ahprofit, read_ahprofit_file,
)

ROOT = Path(__file__).resolve().parents[3]
A1_GOLDEN = ROOT / "tests" / "fixtures" / "savedvars" / "a1.lua"

AMAYA, BRUGAN = "Amaya-Grommash", "Brugan-Grommash"


def db_text(body: str, version: int = 1) -> str:
    """Wrap table fields in an AHProfitDB file.

    Args:
        body: Lua table fields, e.g. '["rules"] = {},'.
        version: formatVersion to write.

    Returns:
        SavedVariables file text.
    """
    return f'\nAHProfitDB = {{\n\t["formatVersion"] = {version},\n{body}\n}}\n'


# ---------------------------------------------------------------- A1 round trip

def test_A1_capture_writes_savedvariables_that_ingest_parses_back_to_the_same_records():
    # The Lua spec (tests/addon/Core/DB_spec.lua A1) writes this file from Capture/* with stubs.
    data = read_ahprofit_file(A1_GOLDEN)

    assert data.problems == ()
    assert (data.format_version, data.addon_version, data.refresh_requested_at_ms) == (1, "0.1.0", None)
    assert data.characters == (
        Character(AMAYA, "Amaya", "Grommash", "Horde", "MAGE", 60, 1234567, 1760000000000),
        Character(BRUGAN, "Brugan", "Grommash", "Horde", "WARRIOR", 40, 456789, 1760003600000),
    )
    # Staves is a weapon skill, not a profession, so it isn't captured.
    assert data.professions == (
        Profession(AMAYA, "Cooking", 45, 75),
        Profession(AMAYA, "Enchanting", 142, 150),
        Profession(BRUGAN, "Blacksmithing", 115, 150),
        Profession(BRUGAN, "Mining", 120, 150),
    )
    # Copper Rod (6217) from 2 Copper Bar (2840); the enchant makes no item; reagents ascend by key.
    assert data.recipes == (
        Recipe("Blacksmithing", 2660, 2862, 1, 1, 0, 0, (Reagent(2835, 1),)),
        Recipe("Blacksmithing", 2662, 2852, 1, 1, 0, 0, (Reagent(2840, 4),)),
        Recipe("Blacksmithing", 2663, 6217, 1, 1, 0, 0, (Reagent(2840, 2),)),
        Recipe("Enchanting", 7418, None, 1, 1, 0, 0, (Reagent(10940, 1),)),
        Recipe("Enchanting", 7421, 6218, 1, 1, 0, 0, (Reagent(6217, 1), Reagent(10938, 1), Reagent(10940, 1))),
    )
    assert data.known_recipes == (
        KnownRecipe(AMAYA, 7418), KnownRecipe(AMAYA, 7421),
        KnownRecipe(BRUGAN, 2660), KnownRecipe(BRUGAN, 2662), KnownRecipe(BRUGAN, 2663),
    )
    # The limited-supply pattern (6270) is not a vendor reagent.
    assert data.vendor_reagents == (2320, 2321)
    # Linen 15 + 5 in bags -> 20; the Hearthstone is bound; mail Strange Dust 2 + 1 -> 3.
    # Amaya's snapshot time is her mailbox visit (login + 120 s); Brugan never opened bank or mail.
    assert data.inventories == (
        InventorySnapshot(AMAYA, 1760000120000, ("bags", "bank", "mail"), (
            InventoryRow(AMAYA, "bags", 2589, 20, False),
            InventoryRow(AMAYA, "bags", 6948, 1, True),
            InventoryRow(AMAYA, "bags", 10938, 2, False),
            InventoryRow(AMAYA, "bags", 10940, 3, False),
            InventoryRow(AMAYA, "bank", 2589, 7, False),
            InventoryRow(AMAYA, "bank", 4306, 10, False),
            InventoryRow(AMAYA, "mail", 2592, 20, False),
            InventoryRow(AMAYA, "mail", 10940, 3, False),
        )),
        InventorySnapshot(BRUGAN, 1760003600000, ("bags",), (
            InventoryRow(BRUGAN, "bags", 2835, 12, False),
            InventoryRow(BRUGAN, "bags", 2840, 6, False),
            InventoryRow(BRUGAN, "bags", 6217, 5, False),
            InventoryRow(BRUGAN, "bags", 6948, 1, True),
        )),
    )
    assert data.events == (
        Event(f"{BRUGAN}:1760003660000:0", "post", BRUGAN, 1760003660000, key=6217, stack=5,
              price_copper=4100, duration_h=24, deposit_copper=60, confirmed=True),
    )
    assert (data.own_auctions, data.scans, data.rules) == ((), (), ())


# ---------------------------------------------------------------- AHProfitDB header

def test_newer_format_version_is_refused_with_update_message():
    with pytest.raises(UnsupportedFormatError, match="Update AHSync"):
        read_ahprofit(db_text("", version=2))


@pytest.mark.parametrize("text", [
    "OtherDB = {}",
    "AHProfitDB = 5",
    "AHProfitDB = { characters = {} }",
    "AHProfitDB = { formatVersion = 0 }",
    "AHProfitDB = { formatVersion = 1.5 }",
    "AHProfitDB = { formatVersion = true }",
])
def test_missing_or_invalid_header_raises(text):
    with pytest.raises(SavedVarsError):
        read_ahprofit(text)


def test_half_written_file_raises_a_parse_error():
    with pytest.raises(SavedVarsParseError):
        read_ahprofit(db_text("")[:-3])


def test_minimal_database_reads_as_empty():
    data = read_ahprofit("AHProfitDB = { formatVersion = 1, refreshRequestedAt = 1760000000000 }")
    assert data.refresh_requested_at_ms == 1760000000000
    assert data.addon_version is None
    assert (data.characters, data.recipes, data.vendor_reagents, data.events, data.problems) == ((), (), (), (), ())


def test_integral_float_fields_are_accepted_as_integers():
    data = read_ahprofit(db_text('["rules"] = { [2589.0] = { rule = "keep", setAt = 1760000000000.0 } },'))
    assert data.rules == (ItemRule(2589, "keep", 1760000000000),)


# ---------------------------------------------------------------- sections

def test_scans_events_rules_and_own_auctions_from_the_spec_example():
    body = '''
    ownAuctions = { ["Amaya-Grommash"] = { at = 1760000000000, rows = "2857,4100,5,3,1" } },
    scans = { { id = "Amaya-Grommash:1760000000000:0", char = "Amaya-Grommash", at = 1760000000000,
                kind = "full", coverage = 1, rows = "2589,38,20,3,4;2589,45,20,4,1" } },
    events = {
      { id = "A:1:0", type = "purchase", char = "A", at = 1000, key = 2589, qty = 20, price = 38,
        source = "ah", confirmed = false },
      { id = "A:1:1", type = "craft", char = "A", at = 1000, spell = 2663, count = 3 },
      { id = "A:1:2", type = "mail_sent", char = "A", at = 1000, to = "Brugan-Grommash", key = 6217, qty = 1 },
      { id = "A:1:3", type = "auction_mail", char = "A", at = 1000, outcome = "expired", key = 6217, qty = 5 },
      { id = "A:1:4", type = "auction_mail", char = "A", at = 1000, outcome = "sold", key = 6217, qty = 5,
        price = 4100 },
      { id = "A:1:5", type = "skip", char = "A", at = 1000, action = 17 },
    },
    rules = { [2589] = { rule = "no_buy", setAt = 1760000000000 }, [2592] = { rule = "none", setAt = 5 } },
    '''
    data = read_ahprofit(db_text(body))
    assert data.problems == ()
    assert data.own_auctions == (OwnAuctionSnapshot(AMAYA, 1760000000000, (AuctionRow(2857, 4100, 5, 3, 1),)),)
    assert data.scans == (Scan(f"{AMAYA}:1760000000000:0", AMAYA, 1760000000000, "full", 1.0,
                               (AuctionRow(2589, 38, 20, 3, 4), AuctionRow(2589, 45, 20, 4, 1))),)
    assert data.events == (
        Event("A:1:0", "purchase", "A", 1000, key=2589, qty=20, price_copper=38, source="ah", confirmed=False),
        Event("A:1:1", "craft", "A", 1000, spell_id=2663, count=3),
        Event("A:1:2", "mail_sent", "A", 1000, to_char_id=BRUGAN, key=6217, qty=1),
        Event("A:1:3", "auction_mail", "A", 1000, outcome="expired", key=6217, qty=5),
        Event("A:1:4", "auction_mail", "A", 1000, outcome="sold", key=6217, qty=5, price_copper=4100),
        Event("A:1:5", "skip", "A", 1000, action_id="17"),
    )
    assert data.rules == (ItemRule(2589, "no_buy", 1760000000000), ItemRule(2592, "none", 5))


def test_empty_inventory_place_differs_from_a_place_never_visited():
    data = read_ahprofit(db_text('inventory = { ["Amaya-Grommash"] = { at = 5, bags = "" } },'))
    assert data.inventories == (InventorySnapshot(AMAYA, 5, ("bags",), ()),)


@pytest.mark.parametrize("body, problem", [
    ('characters = { ["X-Y"] = { name = "X" } },', "characters['X-Y']: realm is not a string"),
    ('inventory = { ["X-Y"] = { at = 5, bags = "2840,20" } },', "inventory['X-Y']: bags: bad row '2840,20'"),
    ('recipes = { Blacksmithing = { [2663] = { yieldMin = 1, yieldMax = 1, reagents = "2840,x" } } },',
     "recipes['Blacksmithing']: reagents: bad row"),
    ('events = { { id = "e", type = "dance", char = "A", at = 1 } },', "events[1]: type is 'dance'"),
    ('events = { { id = "e", type = "craft", char = "A", at = 1, spell = 1 } },', "craft event has no count"),
    ('events = { { id = "e", type = "auction_mail", char = "A", at = 1, outcome = "won", key = 1, qty = 1 } },',
     "auction_mail won has no price"),
    ('events = { { id = "e", type = "post", char = "A", at = 1, key = 1, stack = 1, price = 1, durationH = 24,'
     ' deposit = 1, confirmed = "yes" } },', "confirmed is not a boolean"),
    ('events = { { id = "e", type = "purchase", char = "A", at = 1, key = 1, qty = 1, price = 1.5,'
     ' source = "ah", confirmed = true } },', "price is not an integer"),
    ('events = { { id = "e", type = "skip", char = "A", at = 1, action = {} } },', "action is not a string"),
    ('scans = { { id = "s", char = "A", at = 1, kind = "full", coverage = 2, rows = "" } },',
     "coverage is not a number in 0..1"),
    ('rules = { [2589] = { rule = "maybe", setAt = 1 } },', "rule is 'maybe'"),
    ('professions = { ["X-Y"] = { Mining = 5 } },', "profession is not a table"),
    ('knownRecipes = { [7] = "1;2" },', "character id is not a string"),
    ('ownAuctions = { ["X-Y"] = { at = "now", rows = "" } },', "at is not a number"),
    ('vendorReagents = "2320;x",', "vendorReagents: bad row 'x'"),
    ('characters = "Amaya",', "characters: not a table"),
])
def test_bad_rows_are_set_aside_with_a_problem_and_the_rest_carries_on(body, problem):
    # A good section next to the bad one still reads; it must not be the same section.
    if body.startswith("rules"):
        good = 'vendorReagents = "2320",'
    else:
        good = 'rules = { [1] = { rule = "keep", setAt = 1 } },'
    data = read_ahprofit(db_text(body + "\n" + good))
    assert len(data.problems) == 1
    assert problem in data.problems[0]
    assert data.rules == (ItemRule(1, "keep", 1),) or data.vendor_reagents == (2320,)


def test_reads_a_file_with_crlf_line_endings_and_utf8_bytes(tmp_path):
    path = tmp_path / "AHProfit.lua.bak"
    path.write_bytes(db_text('characters = { ["Brügan-Grommash"] = { name = "Brügan", realm = "Grommash",'
                             ' faction = "Horde", class = "WARRIOR", level = 40, gold = 0, updated = 1 } },')
                     .replace("\n", "\r\n").encode("utf-8"))
    data = read_ahprofit_file(path)
    assert data.characters[0].name == "Brügan"
