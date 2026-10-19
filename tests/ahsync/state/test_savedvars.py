"""Tests for ahsync/ingest/savedvars.py: reading AHProfitDB into records (SPEC §2.2.1)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ahsync.state.savedvars import (
    AuctionRow, Character, Event, InventoryLocation, InventoryRow, ItemRule, KnownRecipe,
    OwnAuctionSnapshot, Profession, Reagent, Recipe, RejectedRow, SavedVarsError, SavedVarsParseError, Scan,
    UnsupportedFormatError, base_item_id, is_unknown_suffix, read_ahprofit, read_ahprofit_file,
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
    names = {n.key: (n.name, n.quality) for n in data.item_names}           # every item seen in an inventory
    assert len(names) == 10 and names[6948] == ("Hearthstone", 1) and names[-6585] == ("Scouting Gloves", 1)
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
    # Skill and cooldown are unknown on Classic APIs, so the file omits them and they read as None.
    assert data.recipes == (
        Recipe("Blacksmithing", 2660, 2862, 1, 1, None, None, (Reagent(2835, 1),)),
        Recipe("Blacksmithing", 2662, 2852, 1, 1, None, None, (Reagent(2840, 4),)),
        Recipe("Blacksmithing", 2663, 6217, 1, 1, None, None, (Reagent(2840, 2),)),
        Recipe("Enchanting", 7418, None, 1, 1, None, None, (Reagent(10940, 1),)),
        Recipe("Enchanting", 7421, 6218, 1, 1, None, None,
               (Reagent(6217, 1), Reagent(10938, 1), Reagent(10940, 1))),
    )
    assert data.known_recipes == (
        KnownRecipe(AMAYA, 7418), KnownRecipe(AMAYA, 7421),
        KnownRecipe(BRUGAN, 2660), KnownRecipe(BRUGAN, 2662), KnownRecipe(BRUGAN, 2663),
    )
    # The limited-supply pattern (6270) is not a vendor reagent.
    assert data.vendor_reagents == (2320, 2321)
    # Linen 15 + 5 in bags -> 20; the Hearthstone is bound; mail Strange Dust 2 + 1 -> 3.
    # Scouting Gloves of the Monkey (item 6585, random suffix) is key -6585: v unknown.
    # Each place has its own time: Amaya logs in at T, bank T+60 s, mail T+120 s.
    # Brugan logs in at T+3600 s and opens an empty bank 10 s later; he never opens mail.
    assert data.inventories == (
        InventoryLocation(AMAYA, "bags", 1760000000000, (
            InventoryRow(AMAYA, "bags", -6585, 1, False),
            InventoryRow(AMAYA, "bags", 2589, 20, False),
            InventoryRow(AMAYA, "bags", 6948, 1, True),
            InventoryRow(AMAYA, "bags", 10938, 2, False),
            InventoryRow(AMAYA, "bags", 10940, 3, False),
        )),
        InventoryLocation(AMAYA, "bank", 1760000060000, (
            InventoryRow(AMAYA, "bank", 2589, 7, False),
            InventoryRow(AMAYA, "bank", 4306, 10, False),
        )),
        InventoryLocation(AMAYA, "mail", 1760000120000, (
            InventoryRow(AMAYA, "mail", 2592, 20, False),
            InventoryRow(AMAYA, "mail", 10940, 3, False),
        )),
        InventoryLocation(BRUGAN, "bags", 1760003600000, (
            InventoryRow(BRUGAN, "bags", 2835, 12, False),
            InventoryRow(BRUGAN, "bags", 2840, 6, False),
            InventoryRow(BRUGAN, "bags", 6217, 5, False),
            InventoryRow(BRUGAN, "bags", 6948, 1, True),
        )),
        InventoryLocation(BRUGAN, "bank", 1760003610000, ()),
    )
    assert is_unknown_suffix(data.inventories[0].rows[0].key)
    assert base_item_id(data.inventories[0].rows[0].key) == 6585
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
    data = read_ahprofit(db_text('inventory = { ["Amaya-Grommash"] = { bags = "", bagsAt = 5 } },'))
    assert data.inventories == (InventoryLocation(AMAYA, "bags", 5, ()),)


def test_item_keys_base_ids_and_unknown_suffixes():
    assert (is_unknown_suffix(-6585), base_item_id(-6585)) == (True, 6585)
    assert (is_unknown_suffix(2589), base_item_id(2589)) == (False, 2589)
    assert base_item_id(2147483648 * 28592 + 2589) == 2589


def test_cooldown_zero_means_no_cooldown_and_omitted_means_unknown():
    body = ('recipes = { Alchemy = { [11479] = { product = 3577, yieldMin = 1, yieldMax = 1, skill = 225,'
            ' cooldown = 86400, reagents = "3575,1" }, [2330] = { yieldMin = 1, yieldMax = 1, cooldown = 0 } } },')
    data = read_ahprofit(db_text(body))
    assert [(r.spell_id, r.skill_req, r.cooldown_s) for r in data.recipes] == [(2330, None, 0), (11479, 225, 86400)]


def test_a_bad_reagent_string_sets_aside_only_that_recipe():
    body = ('recipes = { Blacksmithing = { [2662] = { yieldMin = 1, yieldMax = 1, reagents = "2840,x" },'
            ' [2663] = { product = 6217, yieldMin = 1, yieldMax = 1, reagents = "2840,2" } } },')
    data = read_ahprofit(db_text(body))
    assert [r.spell_id for r in data.recipes] == [2663]
    assert data.problems == ("recipes['Blacksmithing'][2662]: reagents: bad row '2840,x' (expected 2 integers)",)


def test_a_bad_profession_row_sets_aside_only_that_profession():
    data = read_ahprofit(db_text('professions = { ["X-Y"] = { Mining = 5, Tailoring = { skill = 1, max = 75 } } },'))
    assert data.professions == (Profession("X-Y", "Tailoring", 1, 75),)
    assert data.problems == ("professions['X-Y']['Mining']: profession is not a table: 5",)


def test_inventory_is_all_or_nothing_per_place():
    body = ('inventory = { ["X-Y"] = { bags = "2840,6,0;bad", bagsAt = 5, bank = "4306,10,0",'
            ' mail = "", mailAt = 7 } },')
    data = read_ahprofit(db_text(body))
    assert data.inventories == (InventoryLocation("X-Y", "mail", 7, ()),)
    assert data.problems == ("inventory['X-Y'].bags: bags: bad row 'bad' (expected 3 integers)",
                             "inventory['X-Y'].bank: bankAt is not a number: None")


def test_malformed_refresh_request_is_a_problem_and_reads_as_none():
    data = read_ahprofit("AHProfitDB = { formatVersion = 1, refreshRequestedAt = 'soon' }")
    assert data.refresh_requested_at_ms is None
    assert data.problems == ("refreshRequestedAt is not a number: 'soon'",)


@pytest.mark.parametrize("body, problem", [
    ('characters = { ["X-Y"] = { name = "X" } },', "characters['X-Y']: realm is not a string"),
    ('inventory = { ["X-Y"] = { bagsAt = 5, bags = "2840,20" } },',
     "inventory['X-Y'].bags: bags: bad row '2840,20'"),
    ('recipes = { Blacksmithing = { [2663] = { yieldMin = 1, yieldMax = 1, reagents = "2840,x" } } },',
     "recipes['Blacksmithing'][2663]: reagents: bad row"),
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


def test_rejected_scans_and_events_are_identified_by_character_and_time():
    body = ('events = { { id = "A:5000:0", type = "post", char = "A-R", at = 5000 },'
            ' { id = "x", type = "craft", char = 7, at = "soon" }, "junk",'
            ' { id = "A:6000:0", type = "craft", char = "A-R", at = 6000, spell = 1, count = 1 } },'
            ' scans = { { id = "s", char = "B-R", at = 7000.0, kind = "full", coverage = 2, rows = "" } },')
    data = read_ahprofit(db_text(body))
    assert len(data.events) == 1 and len(data.problems) == 4
    assert data.rejected == (RejectedRow("B-R", 7000), RejectedRow("A-R", 5000), RejectedRow(None, None),
                             RejectedRow(None, None))
    assert read_ahprofit(db_text("")).rejected == ()


def test_reads_a_file_with_crlf_line_endings_and_utf8_bytes(tmp_path):
    path = tmp_path / "AHProfit.lua.bak"
    path.write_bytes(db_text('characters = { ["Brügan-Grommash"] = { name = "Brügan", realm = "Grommash",'
                             ' faction = "Horde", class = "WARRIOR", level = 40, gold = 0, updated = 1 } },')
                     .replace("\n", "\r\n").encode("utf-8"))
    data = read_ahprofit_file(path)
    assert data.characters[0].name == "Brügan"


def test_reserves_are_read_per_character_and_a_bad_entry_is_set_aside_alone():
    data = read_ahprofit(db_text('["reserves"] = { ["Amaya-Grommash"] = "4306,-1;2589,10", ["Brugan-Grommash"] = "2589,0",'
                                 ' ["Cal-Grommash"] = "2589,1;2589,2", ["Dee-Grommash"] = "" },'))
    assert [(r.char_id, r.items) for r in data.reserves] == [(AMAYA, ((2589, 10), (4306, -1))), ("Dee-Grommash", ())]
    assert data.reserves_set_aside == (BRUGAN, "Cal-Grommash") and len(data.problems) == 2
    assert read_ahprofit(db_text("")).reserves is None
    assert read_ahprofit(db_text('["reserves"] = 5,')).reserves_set_aside == ()


def test_A41_the_addons_ends_field_in_own_auctions_is_ignored():
    data = read_ahprofit(db_text('["ownAuctions"] = { ["Amaya-Grommash"] = { at = 5, rows = "4471,80,1,3,1", ends = "4471,1,9" } },'))
    assert data.problems == () and [(s.char_id, len(s.rows)) for s in data.own_auctions] == [(AMAYA, 1)]

