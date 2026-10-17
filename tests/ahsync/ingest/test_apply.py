"""Tests for ahsync/ingest/apply.py: storing SavedVariables (SPEC §2.2, §3.2, §3.4.2, §5.2; FM11-FM13, O10, O18, FI2)."""
from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from ahsync.ingest import apply as apply_module
from ahsync.ingest.apply import (
    INGESTED_UNTIL, ApplyResult, apply_data, ingest_file, ingested_until, market_for, read_and_apply,
)
from ahsync.ingest.savedvars import read_ahprofit
from ahsync.store.db import Database
from tests.support.fake_clock import FakeClock
from tests.support.savedvars_builder import REALMS, generate_sessions, savedvars_text

ROOT = Path(__file__).resolve().parents[3]
A1_TEXT = (ROOT / "tests" / "fixtures" / "savedvars" / "a1.lua").read_text(encoding="utf-8")
A1_NEWEST_EVENT_MS = 1760003660000          # the one post in a1.lua
AMAYA, BRUGAN = "Amaya-Grommash", "Brugan-Grommash"
T = 1_760_000_000_000
HOUR = 3_600_000
PVP_HORDE = "forever.pvp.horde.us"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db(tmp_path, clock):
    database = Database.open(tmp_path / "ahsync.db", clock.now_ms(), None)
    yield database
    database.close()


def apply_text(db: Database, text: str, realms=None, now_ms: int = T) -> ApplyResult:
    """Parse a SavedVariables text and apply it."""
    return apply_data(db, read_ahprofit(text), REALMS if realms is None else realms, now_ms)


def apply_db(db: Database, table: dict, realms=None) -> ApplyResult:
    """Apply an AHProfitDB given as a Python dict."""
    return apply_text(db, savedvars_text(table), realms)


def rows(db: Database, sql: str, params: tuple = ()) -> list[tuple]:
    """Fetch rows for assertions."""
    return db.conn.execute(sql, params).fetchall()


def write_savedvars(root: Path, text: str, clock: FakeClock, age_ms: int = 10_000, account: str = "ACC") -> Path:
    """Write WTF/Account/<account>/SavedVariables/AHProfit.lua with a modification time age_ms before now."""
    path = root / "WTF" / "Account" / account / "SavedVariables" / "AHProfit.lua"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    set_age(path, clock, age_ms)
    return path


def set_age(path: Path, clock: FakeClock, age_ms: int) -> None:
    """Set a file's modification time to age_ms before the fake clock's now."""
    mtime_ns = (clock.now_ms() - age_ms) * 1_000_000
    os.utime(path, ns=(mtime_ns, mtime_ns))


def dump(db: Database) -> dict[str, list[tuple]]:
    """Every table's rows, sorted, for whole-database comparisons."""
    tables = [r[0] for r in rows(db, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    return {t: sorted(rows(db, f"SELECT * FROM {t}"), key=repr) for t in tables}


def event(event_id: str, kind: str, at: int, char: str = AMAYA, **fields) -> dict:
    """One event row of an AHProfitDB dict."""
    return {"id": event_id, "type": kind, "char": char, "at": at} | fields


CHAR = {AMAYA: {"name": "Amaya", "realm": "Grommash", "faction": "Horde", "class": "MAGE", "level": 60,
                "gold": 100, "updated": T}}


# ---------------------------------------------------------------- golden file

def test_a1_golden_file_is_stored_in_every_table(db):
    result = apply_text(db, A1_TEXT)

    assert result.problems == ()
    assert rows(db, "SELECT id, market, gold FROM characters ORDER BY id") == [
        (AMAYA, PVP_HORDE, 1234567), (BRUGAN, PVP_HORDE, 456789)]
    assert db.count("professions") == 4
    # Classic APIs give no skill or cooldown: stored as NULL (unknown), never 0.
    assert rows(db, "SELECT spell_id, product_key, skill_req, cooldown_s FROM recipes WHERE spell_id = 7418") == [
        (7418, None, None, None)]
    assert rows(db, "SELECT key, count FROM reagents WHERE spell_id = 7421 ORDER BY key") == [
        (6217, 1), (10938, 1), (10940, 1)]
    assert db.count("known_recipes") == 5 and db.count("vendor_reagents") == 2
    # The random-suffix item is stored as its negative key.
    assert rows(db, "SELECT key, qty, bound FROM inventory WHERE char_id = ? AND location = 'bags' ORDER BY key",
                (AMAYA,)) == [(-6585, 1, 0), (2589, 20, 0), (6948, 1, 1), (10938, 2, 0), (10940, 3, 0)]
    # Brugan's visited, empty bank has a snapshot time but no rows; his mail was never opened.
    assert rows(db, "SELECT location, at FROM snapshots WHERE char_id = ? ORDER BY location", (BRUGAN,)) == [
        ("bags", 1760003600000), ("bank", 1760003610000)]
    assert rows(db, "SELECT post_id, market, key, stack, price, confirmed FROM posts") == [
        (f"{BRUGAN}:1760003660000:0", PVP_HORDE, 6217, 5, 4100, 1)]
    assert ingested_until(db) == A1_NEWEST_EVENT_MS
    assert 6217 in result.dirty_keys and 2589 in result.dirty_keys
    assert -6585 not in result.dirty_keys       # unknown-suffix keys are never valued (SPEC §2.2.1)


# ---------------------------------------------------------------- scenarios

def test_FM11_savedvariables_ends_mid_table(tmp_path, db, clock):
    path = write_savedvars(tmp_path, A1_TEXT, clock)
    assert read_and_apply(path, db, clock, REALMS).ingested
    before = dump(db)

    path.write_text(A1_TEXT[: len(A1_TEXT) // 2], encoding="utf-8")       # WoW died while writing
    path.with_name("AHProfit.lua.bak").write_text(A1_TEXT, encoding="utf-8")
    set_age(path, clock, 10_000)
    result = read_and_apply(path, db, clock, REALMS)

    assert result.ingested and result.read_from == path.with_name("AHProfit.lua.bak")
    assert result.ingested_until_ms == A1_NEWEST_EVENT_MS == ingested_until(db)
    assert db.status("savedvars:ACC").kind == "savedvars_backup_used"
    assert {t: r for t, r in dump(db).items() if t != "status"} == {t: r for t, r in before.items() if t != "status"}

    path.with_name("AHProfit.lua.bak").write_text("AHProfitDB = {", encoding="utf-8")   # the .bak fails too
    result = read_and_apply(path, db, clock, REALMS)
    assert not result.ingested and result.reason == "unparsable"
    assert ingested_until(db) == A1_NEWEST_EVENT_MS                      # nothing pruned in game
    assert db.status("savedvars:ACC").level == "error"


def test_FM12_the_same_savedvariables_ingested_twice(db):
    first = apply_text(db, A1_TEXT)
    counts = {t: len(r) for t, r in dump(db).items()}

    second = apply_text(db, A1_TEXT)

    assert {t: len(r) for t, r in dump(db).items()} == counts
    assert db.count("posts") == 1
    assert first.dirty_keys and second.dirty_keys == frozenset()          # nothing changed, nothing to update
    assert ingested_until(db) == A1_NEWEST_EVENT_MS


def test_FM13_savedvariables_format_version_newer_than_ahsync(tmp_path, db, clock):
    path = write_savedvars(tmp_path, A1_TEXT.replace('["formatVersion"] = 1', '["formatVersion"] = 2'), clock)
    path.with_name("AHProfit.lua.bak").write_text(A1_TEXT, encoding="utf-8")   # not used: it's the same addon

    result = read_and_apply(path, db, clock, REALMS)

    assert not result.ingested and result.reason == "newer_format"
    assert db.count("characters") == 0 and ingested_until(db) is None
    status = db.status("savedvars:ACC")
    assert (status.level, status.kind, status.data) == ("error", "update_ahsync", {"account": "ACC"})


def test_O10_addon_sets_no_buy_at_t_plus_2h_dashboard_cleared_it_at_t_plus_1h(db):
    db.upsert_item_rule(2589, "none", T + 1 * HOUR, "dashboard")

    result = apply_db(db, {"rules": {2589: {"rule": "no_buy", "setAt": T + 2 * HOUR}}})

    assert rows(db, "SELECT key, rule, set_at, source FROM item_rules") == [(2589, "no_buy", T + 2 * HOUR, "addon")]
    assert 2589 in result.dirty_keys


@pytest.mark.parametrize("dashboard_first", [False, True])
def test_O18_addon_set_no_buy_at_t_dashboard_clears_it_at_t_plus_2h(db, dashboard_first):
    addon_file = {"rules": {2589: {"rule": "no_buy", "setAt": T}}}
    if dashboard_first:
        db.upsert_item_rule(2589, "none", T + 2 * HOUR, "dashboard")
        assert 2589 not in apply_db(db, addon_file).dirty_keys            # the older rule changes nothing
    else:
        apply_db(db, addon_file)
        db.upsert_item_rule(2589, "none", T + 2 * HOUR, "dashboard")
    apply_db(db, addon_file)                                              # the addon's next save, before Data.lua

    # The tombstone beats the older rule; Data.lua (M5) carries it to the addon.
    assert rows(db, "SELECT key, rule, set_at, source FROM item_rules") == [(2589, "none", T + 2 * HOUR, "dashboard")]


def _ingest_all(files: list, order: list[int]) -> dict[str, list[tuple]]:
    """Ingest parsed files in an order into a fresh in-memory database; return its dump."""
    database = Database.open(Path(":memory:"), T, None)
    try:
        for index in order:
            apply_data(database, files[index], REALMS, T)
        return dump(database)
    finally:
        database.close()


def test_FI2_any_order_and_repeats_of_savedvariables_files_give_the_same_database():
    for seed in range(1000):            # SPEC §8.7: at least 1,000 cases
        files = [read_ahprofit(text) for text in generate_sessions(seed)]
        assert all(f.problems == () for f in files), f"seed {seed}: {[f.problems for f in files]}"
        in_order = list(range(len(files)))
        expected = _ingest_all(files, in_order)
        shuffled = in_order[:]
        random.Random(seed).shuffle(shuffled)
        for order in (in_order + in_order, shuffled, shuffled + in_order[::-1]):
            assert _ingest_all(files, order) == expected, f"seed {seed}, order {order}"


# ---------------------------------------------------------------- markets and characters

def test_market_comes_from_faction_and_the_realms_ruleset():
    assert market_for("Horde", "Grommash", {"Grommash": "pvp"}) == "forever.pvp.horde.us"
    assert market_for("Alliance", "Grommash", {"Grommash": "normal"}) == "forever.normal.alliance.us"
    assert market_for("Horde", "Elsewhere", {"Grommash": "pvp"}) is None
    assert market_for("Neutral", "Grommash", {"Grommash": "pvp"}) is None


def test_unknown_realm_stores_a_null_market_and_a_status_line_until_configured(db):
    apply_text(db, A1_TEXT, realms={})
    assert rows(db, "SELECT DISTINCT market FROM characters") == [(None,)]
    status = db.status("realm:Grommash")
    assert (status.level, status.kind, status.data) == ("warn", "unknown_realm",
                                                        {"realm": "Grommash", "faction": "Horde"})

    apply_text(db, A1_TEXT)                                   # [realms] now has Grommash
    assert rows(db, "SELECT DISTINCT market FROM characters") == [(PVP_HORDE,)]
    assert db.status("realm:Grommash") is None


def test_an_older_file_never_reverts_a_character_and_level_only_rises(db):
    apply_db(db, {"characters": {AMAYA: CHAR[AMAYA] | {"gold": 500, "updated": T + HOUR}}})
    apply_db(db, {"characters": {AMAYA: CHAR[AMAYA] | {"gold": 100, "level": 61}}})   # older save, higher level?
    assert rows(db, "SELECT gold, level, updated FROM characters") == [(500, 61, T + HOUR)]


# ---------------------------------------------------------------- recipes and professions

def test_skills_only_rise_and_known_recipes_are_add_only(db):
    prof = {AMAYA: {"Enchanting": {"skill": 142, "max": 150}}}
    apply_db(db, {"professions": prof, "knownRecipes": {AMAYA: "7418;7421"}})
    apply_db(db, {"professions": {AMAYA: {"Enchanting": {"skill": 100, "max": 150}}},
                  "knownRecipes": {AMAYA: "7418"}})
    assert rows(db, "SELECT skill, max_skill FROM professions") == [(142, 150)]
    assert rows(db, "SELECT spell_id FROM known_recipes ORDER BY spell_id") == [(7418,), (7421,)]


def test_a_known_skill_or_cooldown_is_never_replaced_by_unknown_and_reagent_changes_dirty(db):
    recipe = {"product": 6217, "yieldMin": 1, "yieldMax": 1, "skill": 30, "cooldown": 0, "reagents": "2840,2"}
    apply_db(db, {"recipes": {"Blacksmithing": {2663: recipe}}})
    unknown = {k: v for k, v in recipe.items() if k not in ("skill", "cooldown")}
    assert apply_db(db, {"recipes": {"Blacksmithing": {2663: unknown}}}).dirty_keys == frozenset()
    assert rows(db, "SELECT skill_req, cooldown_s FROM recipes") == [(30, 0)]

    changed = apply_db(db, {"recipes": {"Blacksmithing": {2663: recipe | {"reagents": "2840,3"}}}})
    assert changed.dirty_keys == {6217, 2840}
    assert rows(db, "SELECT key, count FROM reagents") == [(2840, 3)]


def test_a_newly_known_recipe_and_a_new_vendor_reagent_dirty_their_items(db):
    apply_db(db, {"recipes": {"Blacksmithing": {2663: {"product": 6217, "yieldMin": 1, "yieldMax": 1,
                                                      "reagents": "2840,2"}}}})
    result = apply_db(db, {"knownRecipes": {BRUGAN: "2663"}, "vendorReagents": "2320"})
    assert result.dirty_keys == {6217, 2840, 2320}


# ---------------------------------------------------------------- snapshots

def test_an_older_inventory_snapshot_never_overwrites_a_newer_one(db):
    apply_db(db, {"inventory": {AMAYA: {"bags": "2589,20,0", "bagsAt": T + 60_000}}})
    result = apply_db(db, {"inventory": {AMAYA: {"bags": "2589,5,0;2592,1,0", "bagsAt": T}}})
    assert result.dirty_keys == frozenset()
    assert rows(db, "SELECT key, qty FROM inventory") == [(2589, 20)]


def test_a_newer_snapshot_replaces_its_place_and_dirties_only_changed_items(db):
    apply_db(db, {"inventory": {AMAYA: {"bags": "2589,20,0;2840,6,0", "bagsAt": T,
                                        "bank": "4306,10,0", "bankAt": T}}})
    result = apply_db(db, {"inventory": {AMAYA: {"bags": "2589,20,0;2592,1,1", "bagsAt": T + 1000}}})
    assert result.dirty_keys == {2840, 2592}
    assert rows(db, "SELECT location, key, qty, bound FROM inventory ORDER BY location, key") == [
        ("bags", 2589, 20, 0), ("bags", 2592, 1, 1), ("bank", 4306, 10, 0)]   # the bank wasn't in this save

    apply_db(db, {"inventory": {AMAYA: {"bank": "", "bankAt": T + 2000}}})     # visited and empty
    assert db.count("inventory", "location = 'bank'") == 0


def test_own_auctions_are_replaced_whole_keeping_band_and_summing_duplicates(db):
    apply_db(db, {"ownAuctions": {AMAYA: {"at": T, "rows": "2857,4100,5,3,1;2589,40,20,4,2"}}})
    result = apply_db(db, {"ownAuctions": {AMAYA: {"at": T + 1000,
                                                   "rows": "2857,4100,5,3,1;2857,4100,5,3,1;2857,4100,5,1,1"}}})
    assert result.dirty_keys == {2857, 2589}
    assert rows(db, "SELECT key, price, stack, band, count, seen FROM own_auctions ORDER BY band") == [
        (2857, 4100, 5, 1, 1, T + 1000), (2857, 4100, 5, 3, 2, T + 1000)]


# ---------------------------------------------------------------- scans

def test_scans_are_stored_as_local_books_with_their_coverage(db):
    scan = {"id": f"{AMAYA}:{T}:0", "char": AMAYA, "at": T, "kind": "partial", "coverage": 0.5,
            "rows": "2589,38,20,3,4;2589,45,20,4,1"}
    result = apply_db(db, {"characters": CHAR, "scans": [scan]})
    assert rows(db, "SELECT scan_id, market, src, char_id, at, kind, coverage FROM scans") == [
        (f"{AMAYA}:{T}:0", PVP_HORDE, "local", AMAYA, T, "partial", 0.5)]
    assert db.count("book_entries") == 2 and result.dirty_keys == {2589}
    assert apply_db(db, {"scans": [scan]}).dirty_keys == frozenset()


def test_one_bad_row_is_set_aside_with_a_status_line_and_the_rest_is_applied(tmp_path, db, clock):
    # Cira's realm isn't configured, so her scan has no market; the parser also rejects one rule.
    table = {"characters": {"Cira-Elsewhere": CHAR[AMAYA] | {"name": "Cira", "realm": "Elsewhere"}},
             "scans": [{"id": "Cira-Elsewhere:1:0", "char": "Cira-Elsewhere", "at": T, "kind": "full",
                        "coverage": 1, "rows": "2589,38,20,3,4"}],
             "rules": {2589: {"rule": "sometimes", "setAt": T}, 2592: {"rule": "ignore", "setAt": T}},
             "events": [event(f"Cira-Elsewhere:{T}:0", "craft", T, char="Cira-Elsewhere", spell=2663, count=1)]}
    path = write_savedvars(tmp_path, savedvars_text(table), clock)

    result = read_and_apply(path, db, clock, REALMS)

    assert result.ingested and len(result.problems) == 2
    assert "market of Cira-Elsewhere is unknown" in result.problems[1]
    assert db.count("scans") == 0 and db.count("crafts") == 1 and db.count("item_rules") == 1
    status = db.status("savedvars_rows:ACC")
    assert (status.kind, status.data["count"]) == ("savedvars_rows_skipped", 2)

    path.write_text(savedvars_text({"rules": {}}), encoding="utf-8")
    set_age(path, clock, 10_000)
    read_and_apply(path, db, clock, REALMS)
    assert db.status("savedvars_rows:ACC") is None and db.status("savedvars:ACC") is None


# ---------------------------------------------------------------- events

def test_each_event_type_is_stored_in_its_table(db):
    events = [
        event(f"{AMAYA}:{T}:0", "purchase", T, key=2589, qty=5, price=40, source="ah", confirmed=True),
        event(f"{AMAYA}:{T}:1", "purchase", T, key=2589, qty=5, price=40, source="ah", confirmed=True),
        event(f"{AMAYA}:{T + 1000}:0", "craft", T + 1000, spell=2663, count=2),
        event(f"{AMAYA}:{T + 2000}:0", "mail_sent", T + 2000, to=BRUGAN, key=2592, qty=3),
        event(f"{AMAYA}:{T + 3000}:0", "auction_mail", T + 3000, outcome="sold", key=6217, qty=5, price=4100),
        event(f"{AMAYA}:{T + 3000}:1", "auction_mail", T + 3000, outcome="sold", key=6217, qty=5, price=4100),
        event(f"{AMAYA}:{T + 4000}:0", "auction_mail", T + 4000, outcome="expired", key=-6585, qty=1),
    ]
    recipe = {"Blacksmithing": {2663: {"product": 6217, "yieldMin": 1, "yieldMax": 1, "reagents": "2840,2"}}}
    result = apply_db(db, {"recipes": recipe, "events": events})

    assert result.problems == ()
    # Two buys of the same item in the same second are two purchases: the event id is the key.
    assert rows(db, "SELECT event_id, key, qty, price, source, confirmed FROM purchases ORDER BY event_id") == [
        (f"{AMAYA}:{T}:0", 2589, 5, 40, "ah", 1), (f"{AMAYA}:{T}:1", 2589, 5, 40, "ah", 1)]
    assert rows(db, "SELECT spell_id, count, at FROM crafts") == [(2663, 2, T + 1000)]
    assert rows(db, "SELECT char_id, to_char, key, qty FROM mail_sent") == [(AMAYA, BRUGAN, 2592, 3)]
    assert rows(db, "SELECT key, qty, price, outcome FROM auction_mail ORDER BY event_id") == [
        (6217, 5, 4100, "sold"), (6217, 5, 4100, "sold"), (-6585, 1, None, "expired")]
    assert result.dirty_keys == {2589, 6217, 2840, 2592}
    assert result.newest_event_ms == T + 4000 == ingested_until(db)


def test_an_unconfirmed_attempt_is_upgraded_by_a_later_confirmed_copy_and_never_downgraded(db):
    attempt = event(f"{AMAYA}:{T}:0", "post", T, key=6217, stack=5, price=4100, durationH=24, deposit=60,
                    confirmed=False)
    apply_db(db, {"events": [attempt]})
    assert rows(db, "SELECT confirmed FROM posts") == [(0,)]

    confirmed = apply_db(db, {"events": [attempt | {"confirmed": True}]})
    assert rows(db, "SELECT confirmed FROM posts") == [(1,)] and confirmed.dirty_keys == {6217}

    assert apply_db(db, {"events": [attempt]}).dirty_keys == frozenset()       # an older file comes in late
    assert rows(db, "SELECT confirmed FROM posts") == [(1,)]


def test_a_skip_marks_an_open_action_skipped_and_is_kept_when_the_action_is_unknown(db):
    db.conn.executemany("INSERT INTO actions(action_id, key, status) VALUES(?, ?, ?)",
                        [("a1", 2589, "pending"), ("a2", 2592, "done")])
    skips = [event(f"{AMAYA}:{T}:0", "skip", T, action="a1"), event(f"{AMAYA}:{T}:1", "skip", T, action="a2"),
             event(f"{AMAYA}:{T}:2", "skip", T, action="later")]

    result = apply_db(db, {"events": skips})

    assert rows(db, "SELECT action_id, status FROM actions ORDER BY action_id") == [("a1", "skipped"), ("a2", "done")]
    assert rows(db, "SELECT action_id FROM skips ORDER BY action_id") == [("a1",), ("a2",), ("later",)]
    assert result.dirty_keys == {2589}


def test_ingested_until_only_moves_forward(db):
    apply_db(db, {"events": [event(f"{AMAYA}:{T + HOUR}:0", "craft", T + HOUR, spell=1, count=1)]})
    apply_db(db, {"events": [event(f"{AMAYA}:{T}:0", "craft", T, spell=1, count=1)]})
    apply_db(db, {"rules": {}})
    assert db.get_meta(INGESTED_UNTIL) == str(T + HOUR)


def test_ingested_until_is_written_only_after_the_commit(db, monkeypatch):
    def crash(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(Database, "upsert_item_rule", crash)
    with pytest.raises(OSError):
        apply_db(db, {"events": [event(f"{AMAYA}:{T}:0", "craft", T, spell=1, count=1)],
                      "rules": {2589: {"rule": "no_buy", "setAt": T}}})
    assert ingested_until(db) is None and db.count("crafts") == 0           # rolled back, nothing pruned in game


# ---------------------------------------------------------------- reading files

def test_a_file_changed_less_than_2s_ago_is_not_read_yet(tmp_path, db, clock):
    path = write_savedvars(tmp_path, A1_TEXT, clock, age_ms=1_000)
    result = read_and_apply(path, db, clock, REALMS)
    assert (result.ingested, result.reason) == (False, "unstable") and db.count("characters") == 0
    clock.advance(1_000)
    assert read_and_apply(path, db, clock, REALMS).ingested


def test_a_file_rewritten_while_being_read_is_not_ingested(tmp_path, db, clock):
    path = write_savedvars(tmp_path, A1_TEXT, clock)
    mtimes = iter([clock.now_ms() - 10_000, clock.now_ms()])
    result = read_and_apply(path, db, clock, REALMS, stat_mtime_ms=lambda _p: next(mtimes))
    assert (result.ingested, result.reason) == (False, "unstable") and db.count("characters") == 0


def test_a_missing_file_is_reported_as_missing(tmp_path, db, clock):
    result = read_and_apply(tmp_path / "AHProfit.lua", db, clock, REALMS)
    assert (result.ingested, result.reason) == (False, "missing")


def test_ingest_file_passes_the_dirty_keys_to_the_incremental_update_hook(tmp_path, db, clock, monkeypatch):
    calls = []
    monkeypatch.setattr(apply_module, "incremental_update", lambda _db, keys, now: calls.append((keys, now)))
    path = write_savedvars(tmp_path, A1_TEXT, clock)

    result = ingest_file(path, db, clock, REALMS)
    ingest_file(path, db, clock, REALMS)                       # unchanged: no update

    assert calls == [(result.dirty_keys, clock.now_ms())]
    assert result.refresh_requested_at_ms is None and result.mtime_ms == clock.now_ms() - 10_000


def test_the_incremental_update_hook_is_a_no_op_until_the_engine_lands(db):
    assert apply_module.incremental_update(db, frozenset({2589}), T) is None
