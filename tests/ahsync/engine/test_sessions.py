"""Tests for ahsync/engine/sessions.py: gather events grouped into farming sessions (SPEC §3.4E.4)."""
from __future__ import annotations

import random

from ahsync.engine.sessions import Candidate, GatherEvent, Session, sessions
from tests.support import gathering_world as gw

MINUTE = 60_000
GAP = 15 * MINUTE
BRUGAN, AMAYA = gw.BRUGAN, gw.AMAYA
L1, L2 = 1_000, 2_000


def ev(char: str, t: int, loot: tuple, login: int = L1, seq: int = 0) -> GatherEvent:
    """One gather event with the addon's id."""
    return GatherEvent(f"{char}:{t}:{seq}", char, t, login, loot)


def run(char: str, first: int, minutes: list[int], loot: list[tuple], login: int = L1) -> list[GatherEvent]:
    """Events at first + each minute offset, one loot each."""
    return [ev(char, first + m * MINUTE, l, login) for m, l in zip(minutes, loot)]


def test_GA14_twenty_units_confirm_a_session_catalog_or_not_and_nineteen_are_dropped():
    sat17 = gw.at(gw.SAT, "17:00")
    loot = [((gw.LINEN, 2), (gw.BROKEN_FANG, 2)), ((gw.LINEN, 3), (gw.CHIPPED_CLAW, 3)),
            ((gw.BROKEN_FANG, 5),), ((gw.LINEN, 2), (gw.CHIPPED_CLAW, 3))]          # 7 + 7 + 6 = 20 units
    events = run(AMAYA, sat17, [0, 10, 20, 30], loot)
    found, open_ = sessions(events, sat17 + 2 * 60 * MINUTE, GAP, 20)
    assert found == [Session(events[0].event_id, AMAYA, sat17, sat17 + 30 * MINUTE,
                             ((gw.LINEN, 7), (gw.BROKEN_FANG, 7), (gw.CHIPPED_CLAW, 6)), "gap")]
    assert found[0].units == 20 and found[0].duration_ms == 30 * MINUTE and open_ == {}
    one_fewer = events[:3] + [ev(AMAYA, sat17 + 30 * MINUTE, ((gw.LINEN, 2), (gw.CHIPPED_CLAW, 2)))]   # 19 units
    assert sessions(one_fewer, sat17 + 2 * 60 * MINUTE, GAP, 20) == ([], {})        # dropped once it ended


def test_GA15_a_gap_of_15_minutes_ends_the_session_and_14_continues_it():
    sun14 = gw.at(gw.SUN, "14:02")
    body = run(BRUGAN, sun14, [0, 10, 20, 30, 40, 50, 57], [((gw.COPPER, 3), (gw.ROUGH_STONE, 1))] * 7)
    later = sun14 + 4 * 60 * MINUTE
    ends = body + [ev(BRUGAN, sun14 + 72 * MINUTE, ((gw.ROUGH_STONE, 2),))]          # 15:14, 15 min after 14:59
    found, _ = sessions(ends, later, GAP, 20)
    assert [(s.start_ms, s.end_ms, s.duration_ms // MINUTE, s.ended) for s in found] == [
        (sun14, sun14 + 57 * MINUTE, 57, "gap")]                                     # the 15 quiet min not counted
    goes_on = body + [ev(BRUGAN, sun14 + 71 * MINUTE, ((gw.ROUGH_STONE, 2),))]       # 15:13: 14 min later
    found, _ = sessions(goes_on, later, GAP, 20)
    assert [(s.end_ms, s.units) for s in found] == [(sun14 + 71 * MINUTE, 30)]


def test_GA16_a_different_login_ends_the_session_and_a_reload_keeps_it():
    mon8 = gw.at(gw.MON, "08:05")
    body = run(BRUGAN, mon8, [0, 10, 20, 30], [((gw.COPPER, 6),)] * 4)              # 08:05-08:35, 24 units, login L1
    relog = body + [ev(BRUGAN, mon8 + 35 * MINUTE, ((gw.COPPER, 3),), login=L2)]     # 08:40 after a relog at 08:38
    found, open_ = sessions(relog, mon8 + 40 * MINUTE, GAP, 20)
    assert [(s.end_ms, s.ended, s.units) for s in found] == [(mon8 + 30 * MINUTE, "logout", 24)]
    assert open_ == {BRUGAN: Candidate(BRUGAN, mon8 + 35 * MINUTE, mon8 + 35 * MINUTE, ((gw.COPPER, 3),))}
    reload = body + [ev(BRUGAN, mon8 + 35 * MINUTE, ((gw.COPPER, 3),), login=L1)]    # a /reload keeps the login
    found, open_ = sessions(reload, mon8 + 40 * MINUTE, GAP, 20)
    assert found == [] and open_[BRUGAN].units == 27 and open_[BRUGAN].start_ms == mon8


def test_GA17_a_single_loot_of_twenty_is_a_session_without_duration():
    one = [ev(BRUGAN, gw.at(gw.SUN, "09:00"), ((gw.COPPER, 20),))]
    found, _ = sessions(one, gw.NOW, GAP, 20)
    assert [(s.units, s.duration_ms) for s in found] == [(20, 0)]


def test_GA18_a_session_never_spans_characters():
    t = gw.at(gw.SAT, "19:00")
    events = (run(BRUGAN, t, [0, 5, 10], [((gw.COPPER, 4),)] * 3) +
              run(AMAYA, t + 5 * MINUTE, [0, 7, 15], [((gw.PEACEBLOOM, 4),), ((gw.PEACEBLOOM, 3),), ((gw.PEACEBLOOM, 3),)]))
    assert sessions(events, t + 3 * 60 * MINUTE, GAP, 20) == ([], {})               # 12 and 10 units, both dropped


def test_GA24_the_open_candidate_lasts_until_now_is_15_minutes_past_its_last_loot():
    w = gw.world()
    assert w.open == {BRUGAN: Candidate(BRUGAN, gw.at(gw.MON, "10:21"), gw.at(gw.MON, "10:30"),
                                        ((gw.COPPER, 9), (gw.ROUGH_STONE, 3)))}
    found, open_ = sessions(gw.EVENTS, gw.at(gw.MON, "10:45"), GAP, 20)
    assert open_ == {} and len(found) == 7                                            # ended under 20: dropped
    found, _ = sessions(gw.EVENTS, gw.at(gw.MON, "10:44"), GAP, 20)
    assert len(found) == 7


def test_the_mockup_world_gives_its_seven_sessions_with_how_each_ended():
    found, _ = sessions(gw.EVENTS, gw.NOW, GAP, 20)
    assert [(s.char_id.split("-")[0], s.start_ms, s.duration_ms // MINUTE, s.units, s.ended) for s in found] == [
        ("Amaya", gw.at(gw.SAT, "17:00"), 30, 20, "logout"), ("Amaya", gw.at(gw.SUN, "20:00"), 20, 20, "gap"),
        ("Amaya", gw.at(gw.SUN, "20:40"), 28, 24, "logout"), ("Brugan", gw.at(gw.SAT, "19:00"), 32, 22, "gap"),
        ("Brugan", gw.at(gw.SAT, "21:10"), 23, 20, "logout"), ("Brugan", gw.at(gw.SUN, "14:02"), 57, 28, "gap"),
        ("Brugan", gw.at(gw.MON, "08:05"), 30, 55, "logout")]
    assert sum(s.duration_ms for s in found) == 220 * MINUTE
    assert all(s.session_id == min((e for e in gw.EVENTS if e.char_id == s.char_id and e.at_ms == s.start_ms),
                                   key=lambda e: e.event_id).event_id for s in found)


def test_generated_events_give_sessions_that_never_overlap_and_keep_every_gap_under_the_limit():
    seed = 4242
    rng = random.Random(seed)
    for case in range(200):
        events, t = [], gw.NOW - 2 * 24 * 60 * MINUTE
        for i in range(rng.randint(1, 40)):
            t += rng.choice([0, 1, 5, 14, 15, 16, 40]) * MINUTE
            char = rng.choice([BRUGAN, AMAYA])
            events.append(ev(char, t, ((gw.COPPER, rng.randint(1, 9)),), login=rng.choice([L1, L1, L1, L2]), seq=i))
        found, open_ = sessions(events, t + rng.choice([0, 10, 20]) * MINUTE, GAP, 20)
        for s in found:
            inside = sorted(e.at_ms for e in events if e.char_id == s.char_id and s.start_ms <= e.at_ms <= s.end_ms)
            assert s.units >= 20 and all(b - a < GAP for a, b in zip(inside, inside[1:])), (seed, case)
        for a, b in zip(found, found[1:]):
            assert a.char_id != b.char_id or a.end_ms < b.start_ms, (seed, case)
        assert all(c.last_ms >= c.start_ms for c in open_.values()), (seed, case)


def test_events_in_the_same_second_keep_their_capture_order_and_any_input_order_gives_the_same_sessions():
    t = gw.at(gw.MON, "08:00")
    events = [ev(BRUGAN, t, ((gw.COPPER, 10),), seq=s) for s in (0, 2, 10)]        # seq 10 sorts after seq 2
    found, _ = sessions(events, gw.NOW, GAP, 20)
    assert found[0].session_id == f"{BRUGAN}:{t}:0" and found[0].units == 30
    odd = GatherEvent("odd-id", BRUGAN, t, L1, ((gw.COPPER, 1),))                 # an id without a seq sorts first
    assert sessions([*events, odd], gw.NOW, GAP, 20)[0][0].session_id == "odd-id"
    shuffled = list(gw.EVENTS)
    random.Random(17).shuffle(shuffled)
    assert sessions(shuffled, gw.NOW, GAP, 20) == sessions(gw.EVENTS, gw.NOW, GAP, 20)
