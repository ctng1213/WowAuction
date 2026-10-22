"""Farming sessions: one character's gather events grouped by time and login (SPEC §3.4E.4; GA14-GA18, GA24).

Pure: the events come in already read (state/gathering.py), the clock as `now_ms`. A candidate collects one
character's events in time order, whatever their source; it ends when the next event is `gap_ms` or more after
its last one, or carries a different login (a logout or a game restart), or, for the newest candidate, when now is
`gap_ms` past its last event. A candidate whose units reach `confirm_units` (every unit, catalog or not) is a
confirmed session; one that ends under it is dropped (its events stay stored). The newest candidate that has not
ended is the open candidate: shown on the rate card, never a rate and never stored. The same events always give
the same sessions, so a re-import changes nothing (FM41).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

GAP_ENDED, LOGOUT_ENDED = "gap", "logout"      # what ended a session (SPEC §3.2 farming_sessions.ended)
MINUTE_MS = 60_000


@dataclass(frozen=True)
class SessionSettings:
    """How sessions are cut (SPEC §3.6 session_confirm_units, session_gap_minutes)."""

    confirm_units: int = 20                    # units that confirm a session
    gap_minutes: int = 15                      # minutes without loot that end one

    @property
    def gap_ms(self) -> int:
        """The gap in ms.

        Returns:
            gap_minutes × 60,000.
        """
        return self.gap_minutes * MINUTE_MS


@dataclass(frozen=True)
class GatherEvent:
    """One loot window of farming loot, as stored (SPEC §2.2.1 `gather`, §3.2 gather_events)."""

    event_id: str                              # "charId:timeMs:seq"
    char_id: str
    at_ms: int                                 # UTC ms
    login_ms: int                              # the character's initial login, UTC ms
    loot: tuple[tuple[int, int], ...]          # (item key, units), by key
    source: str = ""                           # "mining" | "herbalism" | "skinning" | "fishing" | "monster"


@dataclass(frozen=True)
class Session:
    """A confirmed farming session (SPEC §3.4E.4, §3.2 farming_sessions and farming_session_items)."""

    session_id: str                            # its first event's id
    char_id: str
    start_ms: int                              # first event, UTC ms
    end_ms: int                                # last event, UTC ms
    items: tuple[tuple[int, int], ...]         # (item key, units), by key, catalog or not
    ended: str                                 # GAP_ENDED | LOGOUT_ENDED

    @property
    def units(self) -> int:
        """Every unit looted in the session.

        Returns:
            The sum of its items' units.
        """
        return sum(n for _, n in self.items)

    @property
    def duration_ms(self) -> int:
        """Last event − first event; the quiet minutes that ended it are not counted (SPEC §3.4E.4).

        Returns:
            Duration in ms (0 for a single loot window: no rate, GA17).
        """
        return self.end_ms - self.start_ms


@dataclass(frozen=True)
class Candidate:
    """The open candidate: the newest of a character's candidates, not ended yet (SPEC §3.4E.4, GA24)."""

    char_id: str
    start_ms: int                              # its first event, UTC ms
    last_ms: int                               # its newest event, UTC ms
    items: tuple[tuple[int, int], ...]         # (item key, units) so far, by key

    @property
    def units(self) -> int:
        """Every unit looted so far.

        Returns:
            The sum of its items' units.
        """
        return sum(n for _, n in self.items)


def _seq(event_id: str) -> int:
    """An event id's seq, so events in the same second keep the order they were captured in.

    Args:
        event_id: "charId:timeMs:seq".

    Returns:
        The seq as a number; -1 when the id doesn't end in one.
    """
    tail = event_id.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else -1


def _summed(events: Sequence[GatherEvent]) -> tuple[tuple[int, int], ...]:
    """The units of a run of events, per item.

    Args:
        events: The events.

    Returns:
        (item key, units) by key.
    """
    units: dict[int, int] = {}
    for e in events:
        for key, qty in e.loot:
            units[key] = units.get(key, 0) + qty
    return tuple(sorted(units.items()))


def _runs(events: Sequence[GatherEvent], gap_ms: int) -> list[tuple[list[GatherEvent], str | None]]:
    """One character's events split into candidates, with what ended each one.

    Args:
        events: The character's events, in time order (at least one).
        gap_ms: The silence that ends a candidate, ms.

    Returns:
        (the candidate's events, LOGOUT_ENDED | GAP_ENDED, or None for the last one, whose end depends on now).
        A different login ends a candidate even when the gap is also long: it was a logout.
    """
    runs: list[tuple[list[GatherEvent], str | None]] = []
    current: list[GatherEvent] = []
    for e in events:
        if current and e.login_ms != current[-1].login_ms:
            runs.append((current, LOGOUT_ENDED))
            current = []
        elif current and e.at_ms - current[-1].at_ms >= gap_ms:
            runs.append((current, GAP_ENDED))
            current = []
        current.append(e)
    runs.append((current, None))
    return runs


def sessions(events: Iterable[GatherEvent], now_ms: int, gap_ms: int,
             confirm_units: int) -> tuple[list[Session], dict[str, Candidate]]:
    """Group gather events into confirmed farming sessions and open candidates (SPEC §3.4E.4).

    Args:
        events: Gather events of any characters, in any order; each character is grouped alone (GA18).
        now_ms: Now, UTC ms: the newest candidate has ended once now is gap_ms past its last event.
        gap_ms: session_gap_minutes in ms: a next event this long or more after the last one ends the candidate.
        confirm_units: session_confirm_units: the units that confirm a session.

    Returns:
        (confirmed sessions, by character id then start time then id; character id -> its open candidate,
        whatever its units). Events at the same time keep their capture order (time, seq, id).
    """
    by_char: dict[str, list[GatherEvent]] = {}
    for e in events:
        by_char.setdefault(e.char_id, []).append(e)
    found: list[Session] = []
    open_: dict[str, Candidate] = {}
    for char_id in sorted(by_char):
        ordered = sorted(by_char[char_id], key=lambda e: (e.at_ms, _seq(e.event_id), e.event_id))
        for run, ended in _runs(ordered, gap_ms):
            items = _summed(run)
            if ended is None and now_ms - run[-1].at_ms < gap_ms:
                open_[char_id] = Candidate(char_id, run[0].at_ms, run[-1].at_ms, items)
                continue
            if sum(n for _, n in items) >= confirm_units:
                found.append(Session(run[0].event_id, char_id, run[0].at_ms, run[-1].at_ms, items,
                                     ended or GAP_ENDED))
    return found, open_
