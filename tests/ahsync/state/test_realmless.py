"""state/realmless.py and its fold in the importer: a character stored without a realm (SPEC §2.2.1, RL4)."""
from __future__ import annotations

import sqlite3

import pytest

from ahsync.db.sqlite import Database
from ahsync.state.importer import INGESTED_UNTIL_PREFIX, ApplyResult, apply_data, ingested_until
from ahsync.state.realmless import realmless_folds
from ahsync.state.savedvars import read_ahprofit
from tests.support.fake_clock import FakeClock
from tests.support.savedvars_builder import REALMS, savedvars_text

T = 1_760_000_000_000
HOUR = 3_600_000
REAL = "Amaya-Grommash"


@pytest.fixture
def db(tmp_path):
    database = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    yield database
    database.close()


def apply_db(db: Database, table: dict) -> ApplyResult:
    """Apply an AHProfitDB given as a Python dict."""
    return apply_data(db, read_ahprofit(savedvars_text(table)), REALMS, T + 2 * HOUR)


def character(realm: str, updated: int) -> dict:
    """Amaya's character row on a realm ("" when the client named none)."""
    return {"name": "Amaya", "realm": realm, "faction": "Horde", "class": "MAGE", "level": 60, "gold": 100,
            "updated": updated}


def craft(char_id: str, at: int) -> dict:
    """A craft event by a character."""
    return {"id": f"{char_id}:{at}:0", "type": "craft", "char": char_id, "at": at, "spell": 3275, "count": 1}


def both_amayas() -> dict:
    """The file before the addon's fold: Amaya under her real id and under "Amaya-", each with a craft and bags."""
    return {"characters": {REAL: character("Grommash", T - 10 * HOUR), "Amaya-": character("", T)},
            "inventory": {"Amaya-": {"bags": "2840,5,0;9999,2,0", "bagsAt": T},
                          REAL: {"bags": "2589,1,0", "bagsAt": T - 10 * HOUR}},
            "events": [craft("Amaya-", T), craft(REAL, T - 10 * HOUR)]}


AFTER_FOLD = {"characters": {REAL: character("Grommash", T + HOUR)},      # the addon folded and sold the 9999s
              "inventory": {REAL: {"bags": "2840,5,0", "bagsAt": T + HOUR}}}


def column(db: Database, sql: str) -> list:
    """The first column of a query's rows."""
    return [r[0] for r in db.conn.execute(sql)]


def test_RL4_a_realm_less_character_folds_into_its_real_id_once_the_file_no_longer_lists_it(db):
    apply_db(db, both_amayas())
    assert column(db, "SELECT DISTINCT char_id FROM crafts ORDER BY 1") == ["Amaya-", REAL]   # both still listed
    assert ingested_until(db) == {"Amaya-": T, REAL: T - 10 * HOUR}
    result = apply_db(db, AFTER_FOLD)
    assert column(db, "SELECT id FROM characters") == [REAL]
    assert column(db, "SELECT char_id FROM crafts ORDER BY at") == [REAL, REAL]          # each kept its id
    assert db.conn.execute("SELECT char_id, location, key, qty FROM inventory").fetchall() == [(REAL, "bags", 2840, 5)]
    assert ingested_until(db) == {REAL: T - 10 * HOUR}                       # its own, never the later one
    assert db.get_meta(INGESTED_UNTIL_PREFIX + "Amaya-") is None
    assert {2589, 9999} <= result.dirty_keys
    untracked = db.conn.execute("SELECT char_id, key, delta FROM untracked ORDER BY key").fetchall()
    assert untracked == [(REAL, 9999, -2)]       # against the newer "Amaya-" bags, not the 10-hour-old ones


def test_RL4_a_file_that_doesnt_list_the_real_id_folds_nothing(db):
    apply_db(db, both_amayas())
    apply_db(db, {"characters": {"Brugan-Grommash": dict(character("Grommash", T), name="Brugan")}})   # another account
    assert column(db, "SELECT id FROM characters ORDER BY id") == ["Amaya-", REAL, "Brugan-Grommash"]


@pytest.mark.parametrize("error", [ValueError, sqlite3.OperationalError])
def test_RL4_a_fold_that_fails_is_undone_alone_and_the_file_is_still_stored(db, monkeypatch, error):
    apply_db(db, both_amayas())

    def broken(*_args):
        raise error("disk said no")
    monkeypatch.setattr(db, "move_character", broken)
    result = apply_db(db, AFTER_FOLD)
    assert "fold of 'Amaya-' into 'Amaya-Grommash': disk said no" in result.problems
    assert column(db, "SELECT id FROM characters ORDER BY id") == ["Amaya-", REAL]
    assert db.conn.execute("SELECT updated FROM characters WHERE id = ?", (REAL,)).fetchone()[0] == T + HOUR


def test_RL4_the_fold_waits_for_the_one_file_that_shows_the_addon_folded():
    mage = ("MAGE", "Horde")
    rows = [("Amaya-", "", *mage), (REAL, "Grommash", *mage), ("Brugan-", "", "WARRIOR", "Horde"), ("Odd", "", *mage)]
    assert realmless_folds(rows, {REAL}) == [("Amaya-", REAL)]              # Brugan: no real id; Odd: not "Name-"
    assert realmless_folds(rows, {REAL, "Amaya-"}) == []                    # the addon hasn't folded yet
    assert realmless_folds(rows, {"Mira-Grommash"}) == []                   # another account's file
    assert realmless_folds(rows + [("Amaya-Stormrage", "Stormrage", *mage)], {REAL}) == []   # two realms: can't tell
    priest = [("Amaya-", "", *mage), (REAL, "Grommash", "PRIEST", "Alliance")]
    assert realmless_folds(priest, {REAL}) == []                            # another Amaya: the addon's match too
