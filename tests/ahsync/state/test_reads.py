"""Tests for ahsync/state/reads.py: character state reads."""
from __future__ import annotations

import pytest

from ahsync.db.sqlite import Database
from tests.support.fake_clock import FakeClock

M, OTHER = "forever.pvp.horde.us", "forever.pvp.alliance.us"
DAY = 86_400_000


@pytest.fixture
def db(tmp_path):
    """A fresh database."""
    database = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    yield database
    database.close()


def sql(db: Database, statement: str, *rows: tuple) -> None:
    """Insert rows with one statement."""
    with db.transaction():
        db.conn.executemany(statement, rows)


def test_characters_skills_recipes_and_stock(db):
    sql(db, "INSERT INTO characters(id, market, gold) VALUES (?, ?, ?)", ("B-R", M, 500), ("A-R", M, None),
        ("C-R", OTHER, 1))
    assert db.characters_in(M) == [("A-R", 0), ("B-R", 500)]
    sql(db, "INSERT INTO professions(char_id, profession, skill, max_skill) VALUES (?, ?, ?, ?)",
        ("A-R", "Enchanting", 142, 150), ("B-R", "Enchanting", 60, 75))
    assert db.best_skill(["A-R", "B-R"], "Enchanting") == 142
    assert db.best_skill(["A-R"], "Mining") is None and db.best_skill([], "Enchanting") is None
    sql(db, "INSERT INTO recipes(profession, spell_id, product_key, yield_min, yield_max) VALUES (?, ?, ?, ?, ?)",
        ("Blacksmithing", 2663, 6217, 1, None))
    sql(db, "INSERT INTO reagents(profession, spell_id, key, count) VALUES (?, ?, ?, ?)",
        ("Blacksmithing", 2663, 2840, 2))
    sql(db, "INSERT INTO known_recipes(char_id, spell_id) VALUES (?, ?)", ("B-R", 2663), ("A-R", 2663))
    [recipe] = db.known_recipes(["A-R", "B-R"])
    assert (recipe.spell, recipe.yield_max, recipe.reagents, recipe.crafters) == (2663, 1, ((2840, 2),), ("A-R", "B-R"))
    assert db.known_recipes([]) == []
    sql(db, "INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, ?, ?, ?, ?)",
        ("A-R", "bags", 2840, 4, 0), ("B-R", "bank", 2840, 2, 0), ("A-R", "bags", 6948, 1, 1))
    assert db.owned_units(["A-R", "B-R"]) == {2840: 6} and db.owned_units([]) == {}

    sql(db, "INSERT INTO vendor_reagents(key) VALUES (?)", (2320,))
    assert db.vendor_reagent_keys() == {2320}


def test_skill_of_a_character(db):
    sql(db, "INSERT INTO professions(char_id, profession, skill, max_skill) VALUES (?, ?, ?, ?)",
        ("A-R", "Mining", 60, 75))
    assert db.skill_of("A-R", "Mining") == 60 and db.skill_of("A-R", "Herbalism") is None


def test_R10_a_bound_item_is_never_a_lot_for_the_sell_plan(db):
    sql(db, "INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, ?, ?, ?, ?)",
        ("A-R", "bags", 2840, 4, 0), ("A-R", "mail", 2840, 3, 0), ("B-R", "bank", 2840, 2, 0), ("A-R", "bags", 6948, 1, 1))
    # The bound Hearthstone (6948) is no lot; bags and mail of one character add up.
    assert db.owned_lots(["A-R", "B-R"]) == [("A-R", 2840, 7), ("B-R", 2840, 2)] and db.owned_lots([]) == []


def test_R20_kept_units_are_never_lots_or_mats_on_hand(db):
    sql(db, "INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, ?, ?, ?, ?)",
        ("A-R", "bags", 2589, 15, 0), ("A-R", "bank", 2589, 5, 0), ("A-R", "bags", 4306, 7, 0), ("B-R", "bags", 2589, 4, 0))
    sql(db, "INSERT INTO reserves(char_id, key, qty) VALUES (?, ?, ?)", ("A-R", 2589, 10), ("A-R", 4306, -1))
    # A keeps 10 of her 20 Linen and all her Silk; B keeps nothing, so A's reserve never touches B's 4.
    assert db.owned_lots(["A-R", "B-R"]) == [("A-R", 2589, 10), ("B-R", 2589, 4)]
    assert db.owned_units(["A-R", "B-R"]) == {2589: 14} and db.owned_units([]) == {}
    assert db.reserves(["A-R", "B-R"]) == {("A-R", 2589): 10, ("A-R", 4306): -1} and db.reserves([]) == {}
    sql(db, "INSERT INTO reserves(char_id, key, qty) VALUES (?, ?, ?)", ("B-R", 2589, 30))   # more than B holds
    assert db.owned_lots(["B-R"]) == []


def test_inventory_rows_include_bound_ones_and_cost_basis_skips_empty_rows(db):
    sql(db, "INSERT INTO inventory(char_id, location, key, qty, bound) VALUES (?, ?, ?, ?, ?)",
        ("B-R", "bank", 2840, 2, 0), ("A-R", "mail", 2840, 3, 0), ("A-R", "bags", 6948, 1, 1), ("C-R", "bags", 1, 1, 0))
    assert db.holdings(["A-R", "B-R"]) == [("A-R", "bags", 6948, 1, True), ("A-R", "mail", 2840, 3, False),
                                           ("B-R", "bank", 2840, 2, False)]
    assert db.holdings([]) == []
    sql(db, "INSERT INTO cost_basis(key, qty, total_cost) VALUES (?, ?, ?)", (2589, 20, 760), (2840, 0, 0))
    assert db.cost_basis() == {2589: (20, 760)}


def test_every_character_with_or_without_a_market(db):
    sql(db, "INSERT INTO characters(id, realm, faction, market, updated) VALUES (?, ?, ?, ?, ?)",
        ("B-R", "R", "Horde", None, 5), ("A-R", "R", "Alliance", M, None))
    assert db.characters() == [("A-R", "R", "Alliance", M, None), ("B-R", "R", "Horde", None, 5)]


def test_live_item_rules_leave_out_cleared_ones(db):
    sql(db, "INSERT INTO item_rules(key, rule, set_at, source) VALUES (?, ?, ?, ?)",
        (2589, "no_buy", 1, "addon"), (2840, "none", 2, "dashboard"), (4306, "ignore", 3, "dashboard"))
    assert db.item_rules() == {2589: "no_buy", 4306: "ignore"}


def test_own_auctions_carry_the_deposit_of_their_open_post(db):
    sql(db, "INSERT INTO own_auctions(char_id, key, price, stack, band, count, seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("A-R", 6217, 4100, 1, 4, 1, 9), ("A-R", 2589, 45, 5, 3, None, 9), ("C-R", 2589, 45, 5, 3, 1, 9))
    sql(db, "INSERT INTO posts(post_id, char_id, key, stack, price, deposit, at, outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("p1", "A-R", 6217, 1, 4100, 7, 1, "expired"), ("p2", "A-R", 6217, 1, 4100, 9, 2, None))
    assert db.own_auctions(["A-R"]) == [("A-R", 2589, 45, 5, 3, 1, None, None), ("A-R", 6217, 4100, 1, 4, 1, 9, None)]
    assert db.own_auctions([]) == []


def test_snapshot_times_per_place_leave_out_the_auctions_snapshot(db):
    sql(db, "INSERT INTO snapshots(char_id, location, at) VALUES (?, ?, ?)",
        ("A-R", "bags", 5), ("A-R", "auctions", 6), ("B-R", "bank", 7), ("C-R", "bags", 8))
    assert db.snapshot_times(["A-R", "B-R"]) == {("A-R", "bags"): 5, ("B-R", "bank"): 7}
    assert db.snapshot_times([]) == {}


def test_character_sheets_carry_professions_and_recipe_counts(db):
    sql(db, "INSERT INTO characters(id, class, level, faction, market, gold, updated) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("B-R", "WARRIOR", 34, "Horde", M, 9, 5), ("A-R", "MAGE", 38, "Horde", M, 7, 6))
    sql(db, "INSERT INTO professions(char_id, profession, skill, max_skill) VALUES (?, ?, ?, ?)",
        ("A-R", "Tailoring", 160, 225), ("A-R", "Enchanting", 142, 225))
    sql(db, "INSERT INTO known_recipes(char_id, spell_id) VALUES (?, ?)", ("A-R", 1), ("A-R", 2))
    assert db.character_sheets(["A-R", "B-R"]) == [
        ("A-R", "MAGE", 38, "Horde", M, 7, 2, 6, (("Enchanting", 142, 225), ("Tailoring", 160, 225))),
        ("B-R", "WARRIOR", 34, "Horde", M, 9, 0, 5, ())]
    assert db.character_sheets([]) == []
