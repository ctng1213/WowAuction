"""Tests for ahsync/store/refresh_store.py: full-refresh reads, staging and the atomic publish."""
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


def test_items_and_priced_keys(db):
    sql(db, "INSERT INTO items(key, item_id, name, quality, ilvl, class, vendor_buy, vendor_sell, stack) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (2840, 2840, "Copper Bar", 1, 10, 7, 40, 10, 20),
        (9, 9, None, None, None, None, None, None, None))
    assert db.items()[2840].name == "Copper Bar" and db.items()[9].name == "9"
    sql(db, "INSERT INTO candles_h1(market, key, t, close, src) VALUES (?, ?, ?, ?, ?)", (M, 2840, 5 * DAY, 100, "ahf"))
    sql(db, "INSERT INTO current(market, key, price, qty, at, src) VALUES (?, ?, ?, ?, ?, ?)", (M, 7, 5, 1, 6 * DAY, "ahf"))
    assert db.priced_keys(M, 0) == [7, 2840] and db.priced_keys(M, 7 * DAY) == []


def test_daily_closes_prefer_local(db):
    sql(db, "INSERT INTO candles_d1(market, key, t, close, src) VALUES (?, ?, ?, ?, ?)",
        (M, 1, DAY, 100, "ahf"), (M, 1, DAY, 90, "local"), (M, 1, 2 * DAY, 120, "ahf"), (M, 1, 3 * DAY, None, "ahf"))
    assert db.daily_closes(M, 1, 0, 9 * DAY) == [(DAY, 90), (2 * DAY, 120)]


def test_volume_rows_by_day_and_source(db):
    sql(db, "INSERT INTO volume_d1(market, key, day, listed, sold, expired, src) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (M, 1, 10, 5, 4, 1, "books"), (M, 1, 10, 6, 5, None, "ahf"), (M, 1, 3, 1, 1, 1, "books"))
    assert db.volume_rows(M, 1, 5) == {10: {"ahf": (5, 0.0, 6), "books": (4, 1, 5)}}


def test_newest_complete_book_listing_the_item(db):
    sql(db, "INSERT INTO scans(scan_id, market, src, at, kind, coverage) VALUES (?, ?, ?, ?, ?, ?)",
        ("old", M, "local", 1 * DAY, "full", 1.0), ("new", M, "local", 2 * DAY, "full", 1.0),
        ("part", M, "local", 3 * DAY, "partial", 0.4))
    sql(db, "INSERT INTO book_entries(scan_id, key, price, stack, band, count) VALUES (?, ?, ?, ?, ?, ?)",
        ("old", 1, 50, 20, 3, 1), ("new", 1, 55, 20, 4, 2), ("new", 1, 45, 5, 3, 1), ("part", 1, 40, 1, 3, 1))
    assert db.newest_book(M, 1, 0) == (2 * DAY, [(45, 5, 3, 1), (55, 20, 4, 2)])
    assert db.newest_book(M, 2, 0) is None


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


def test_publish_swaps_staged_rows_in_one_step_per_market(db):
    with db.transaction():
        db.stage_view("craft", M, {"rows": [1]}, 10)
        db.stage_item_stats(M, 1, {"med7": 5}, 10)
    assert db.view("craft", M) is None                 # staged, not live
    db.publish_refresh([M])
    assert db.view("craft", M) == ({"rows": [1]}, 10)
    with db.transaction():
        db.stage_view("craft", OTHER, {"rows": [9]}, 11)
    db.publish_refresh([M])                            # nothing staged for M: M's page is emptied
    assert db.view("craft", M) is None and db.view("craft", OTHER) is None
    assert db.count("item_stats") == 0 and db.count("views_new") == 1


def test_an_interrupted_refresh_leaves_the_live_views(db):
    with db.transaction():
        db.stage_view("craft", M, {"rows": [1]}, 10)
    db.publish_refresh([M])
    with db.transaction():
        db.stage_view("craft", M, {"rows": [2]}, 20)
    db.drop_staging()                                  # what start-up does after a crash (FM7)
    assert db.view("craft", M) == ({"rows": [1]}, 10)


def test_skip_action_changes_only_open_actions(db):
    sql(db, "INSERT INTO actions(action_id, status) VALUES (?, ?)", ("a1", "pending"), ("a2", "done"))
    assert db.skip_action("a1", 99) is True
    assert db.skip_action("a2", 99) is False and db.skip_action("nope", 99) is False
    assert db.conn.execute("SELECT status, done_at FROM actions WHERE action_id = 'a1'").fetchone() == ("skipped", 99)


def test_skill_of_a_character(db):
    sql(db, "INSERT INTO professions(char_id, profession, skill, max_skill) VALUES (?, ?, ?, ?)",
        ("A-R", "Mining", 60, 75))
    assert db.skill_of("A-R", "Mining") == 60 and db.skill_of("A-R", "Herbalism") is None
