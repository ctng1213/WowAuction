"""Tests for ahsync/state/gathering.py: gather events, farming sessions and manual rates (SPEC §3.2, §3.4E)."""
from __future__ import annotations

import pytest

from ahsync.db.sqlite import Database
from ahsync.engine.gathering import observed_rates
from ahsync.engine.sessions import GatherEvent, SessionSettings, sessions
from ahsync.history.prune import prune
from ahsync.state.gathering import SETTINGS_META, decode_loot, encode_loot
from ahsync.state.importer import apply_data, ingested_until
from ahsync.state.savedvars import read_ahprofit
from tests.support import gathering_world as gw
from tests.support.savedvars_builder import REALMS, savedvars_text

DAY = 86_400_000
SETTINGS = SessionSettings()
TABLES = ("gather_events", "farming_sessions", "farming_session_items")


@pytest.fixture
def db(tmp_path):
    database = Database.open(tmp_path / "ahsync.db", gw.NOW, None)
    yield database
    database.close()


def event_row(e: GatherEvent) -> dict:
    """A gather event as the addon writes it into AHProfitDB.events."""
    return {"id": e.event_id, "type": "gather", "char": e.char_id, "at": e.at_ms, "source": e.source,
            "login": e.login_ms, "loot": encode_loot(e.loot)}


def read(db: Database, events, now_ms: int = gw.NOW):
    """Apply a SavedVariables file holding these gather events."""
    text = savedvars_text({"events": [event_row(e) for e in events]})
    return apply_data(db, read_ahprofit(text), REALMS, now_ms, (), SETTINGS)


def tables(db: Database) -> dict[str, list[tuple]]:
    """The three Gathering tables a read writes, sorted."""
    return {t: sorted(db.conn.execute(f"SELECT * FROM {t}").fetchall()) for t in TABLES}


def test_GA13_the_same_gather_event_in_two_reads_is_one_row_and_stays_after_the_addon_prunes_it(db):
    first = GatherEvent(f"{gw.BRUGAN}:1760947500000:0", gw.BRUGAN, 1_760_947_500_000, 1_760_940_000_000,
                        ((gw.COPPER, 3),), "mining")
    assert read(db, [first]).events_stored == 1
    after_one = tables(db)
    read(db, [first])
    read(db, [])                                                                  # pruned by the addon (A4)
    assert db.conn.execute("SELECT event_id, char_id, at, source, login_ms, loot FROM gather_events").fetchall() == [
        (first.event_id, gw.BRUGAN, first.at_ms, "mining", first.login_ms, "2770,3")]
    assert tables(db) == after_one
    assert ingested_until(db) == {gw.BRUGAN: first.at_ms}                         # the handshake covers it


def test_FM41_the_same_file_read_twice_and_after_pruning_holds_the_same_sessions(db):
    read(db, gw.EVENTS)
    once = tables(db)
    assert len(once["farming_sessions"]) == 7 and len(once["gather_events"]) == len(gw.EVENTS)
    read(db, gw.EVENTS)
    assert tables(db) == once
    until = ingested_until(db)
    read(db, [e for e in gw.EVENTS if e.at_ms > until[e.char_id]])                # what the addon keeps (none)
    assert tables(db) == once
    stored = db.farming_sessions([gw.AMAYA, gw.BRUGAN])
    assert observed_rates(stored)[(gw.BRUGAN, gw.COPPER)].items_per_hour == 38
    assert stored == sessions(gw.EVENTS, gw.NOW, SETTINGS.gap_ms, 20)[0]          # ids = first event ids


def test_only_ended_confirmed_sessions_are_stored_and_the_page_derives_the_rest(db):
    early = gw.at(gw.SUN, "14:30")                                                # mid-session: 14:02-14:59 open
    read(db, [e for e in gw.EVENTS if e.at_ms <= early], now_ms=early)
    assert [s.start_ms for s in db.farming_sessions([gw.BRUGAN])] == [gw.at(gw.SAT, "19:00"), gw.at(gw.SAT, "21:10")]
    tail = db.events_after_sessions([gw.BRUGAN])
    assert tail and min(e.at_ms for e in tail) == gw.at(gw.SUN, "14:02")
    assert db.gather_events(gw.BRUGAN, gw.at(gw.SUN, "14:20")) == [e for e in tail if e.at_ms > gw.at(gw.SUN, "14:20")]
    assert db.events_after_sessions([]) == [] and db.farming_sessions([]) == []


def test_GA27_manual_rates_are_set_replaced_and_cleared_and_never_reach_the_config(db):
    with db.transaction():
        db.set_manual_rate(gw.AMAYA, gw.EARTHROOT, 12, 100)
        db.set_manual_rate(gw.AMAYA, gw.EARTHROOT, 15, 200)
        db.set_manual_rate(gw.BRUGAN, gw.COPPER, 15, 300)
    assert db.conn.execute("SELECT * FROM manual_rates ORDER BY char_id").fetchall() == [
        (gw.AMAYA, gw.EARTHROOT, 15, 200), (gw.BRUGAN, gw.COPPER, 15, 300)]
    assert db.manual_rates([gw.AMAYA, gw.BRUGAN]) == {(gw.AMAYA, gw.EARTHROOT): 15, (gw.BRUGAN, gw.COPPER): 15}
    with db.transaction():
        assert db.clear_manual_rate(gw.AMAYA, gw.EARTHROOT) is True
        assert db.clear_manual_rate(gw.AMAYA, gw.EARTHROOT) is False
    assert db.manual_rates([gw.AMAYA]) == {}


def test_changed_session_settings_rebuild_every_characters_sessions_at_start(db):
    read(db, gw.EVENTS)
    with db.transaction():
        assert db.ensure_session_settings(SETTINGS, gw.NOW) is True               # first start: recorded
        assert db.ensure_session_settings(SETTINGS, gw.NOW) is False
    assert db.get_meta(SETTINGS_META) == "20:15"
    with db.transaction():
        assert db.ensure_session_settings(SessionSettings(25, 15), gw.NOW) is True
    assert len(db.farming_sessions([gw.AMAYA, gw.BRUGAN])) == 2                   # only 55 and 28 units reach 25


def test_gather_events_and_sessions_are_kept_90_days(db):
    read(db, gw.EVENTS)
    report = prune(db, gw.at(gw.SUN, "20:30") + 90 * DAY)                         # cutoff Sun 20:30
    assert report.deleted["farming_sessions"] == 5 and report.deleted["gather_events"] > 0
    left = db.farming_sessions([gw.AMAYA, gw.BRUGAN])
    assert [s.start_ms for s in left] == [gw.at(gw.SUN, "20:40"), gw.at(gw.MON, "08:05")]
    orphans = db.conn.execute("SELECT COUNT(*) FROM farming_session_items WHERE session_id NOT IN "
                              "(SELECT session_id FROM farming_sessions)").fetchone()[0]
    assert orphans == 0


def test_loot_text_round_trips_summed_and_by_key():
    assert encode_loot([(2771, 1), (2770, 2), (2770, 1)]) == "2770,3;2771,1"
    assert decode_loot("2770,3;2771,1") == ((2770, 3), (2771, 1)) and decode_loot("") == ()


def test_GA33_the_stored_events_give_each_items_sources_a_dropped_candidates_included(db):
    read(db, gw.EVENTS)
    sources = db.looted_sources([gw.AMAYA, gw.BRUGAN])
    assert (sources[gw.LIGHT_LEATHER], sources[gw.RUINED_SCRAPS], sources[gw.COPPER]) == (
        ("skinning",), ("monster",), ("mining",))
    assert sources[gw.SMALL_EGG] == ("monster",)                     # Amaya's dropped Mon 09:05 candidate
    assert gw.LIGHT_LEATHER not in db.looted_sources([gw.AMAYA]) and db.looted_sources([]) == {}
    assert sources == gw.looted_sources(gw.EVENTS)                   # the test world's reader agrees

