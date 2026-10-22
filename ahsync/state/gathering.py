"""The Gathering store (SPEC §3.2 gather_events, farming_sessions, farming_session_items, manual_rates; §3.4E), mixed
into db.sqlite.Database.

Gather events are upserted by event id, so reading one SavedVariables file twice adds nothing (GA13, FM41).
A character's confirmed sessions are rebuilt from her stored events, in the caller's transaction, whenever a
read brings new events for her (engine.sessions decides; the same events always give the same rows). Only
confirmed sessions that have ended are stored: the open candidate, and a candidate that ended since the last
rebuild because time passed, are derived at each page build from the events after her last stored session
(`events_after_sessions`). Manual rates are set and cleared from the dashboard (SEC12); a clear deletes the row.
Rates are never stored (§3.4E.5).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence

from ahsync.engine.sessions import GatherEvent, Session, SessionSettings, sessions

SETTINGS_META = "gathering_session_settings"      # "<confirm units>:<gap minutes>" the stored sessions were cut with


def encode_loot(loot: Iterable[tuple[int, int]]) -> str:
    """Loot as stored: "key,qty;key,qty" by key.

    Args:
        loot: (item key, units) pairs.

    Returns:
        The text, summed per key and ordered by key.
    """
    units: dict[int, int] = {}
    for key, qty in loot:
        units[key] = units.get(key, 0) + qty
    return ";".join(f"{k},{n}" for k, n in sorted(units.items()))


def decode_loot(text: str) -> tuple[tuple[int, int], ...]:
    """Stored loot back into pairs.

    Args:
        text: "key,qty;key,qty" ("" for none).

    Returns:
        (item key, units) by key.
    """
    pairs = (part.split(",") for part in text.split(";") if part)
    return tuple(sorted((int(k), int(n)) for k, n in pairs))


class GatheringStore:
    """Reads and writes of gather events, farming sessions and manual rates; needs `self.conn`."""

    conn: sqlite3.Connection

    def store_gather_event(self, e: GatherEvent) -> bool:
        """Store one gather event by its id; a stored event never changes (SPEC §5.2 rule 2, GA13).

        Args:
            e: The event (loot by key).

        Returns:
            True when it was new.
        """
        return self.conn.execute(
            "INSERT INTO gather_events(event_id, char_id, at, source, login_ms, loot) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(event_id) DO NOTHING",
            (e.event_id, e.char_id, e.at_ms, e.source, e.login_ms, encode_loot(e.loot))).rowcount > 0

    def gather_events(self, char_id: str, after_ms: int | None = None) -> list[GatherEvent]:
        """A character's stored gather events.

        Args:
            char_id: The character.
            after_ms: Only events after this time, ms; None for all.

        Returns:
            The events by time, then id.
        """
        rows = self.conn.execute(
            "SELECT event_id, char_id, at, login_ms, loot, source FROM gather_events WHERE char_id = ? AND at > ? "
            "ORDER BY at, event_id", (char_id, -1 if after_ms is None else after_ms)).fetchall()
        return [GatherEvent(i, c, at, login, decode_loot(loot), source) for i, c, at, login, loot, source in rows]

    def rebuild_sessions(self, char_id: str, settings: SessionSettings, now_ms: int) -> int:
        """Replace a character's stored sessions with those her stored events give now (SPEC §3.4E.4).

        Run inside the caller's transaction, so the rebuild is one unit. Only confirmed sessions that have ended
        are stored; the open candidate is not.

        Args:
            char_id: The character.
            settings: session_confirm_units and session_gap_minutes.
            now_ms: Now, ms (the newest candidate has ended once now is the gap past its last event).

        Returns:
            Sessions stored for her.
        """
        found, _ = sessions(self.gather_events(char_id), now_ms, settings.gap_ms, settings.confirm_units)
        self.conn.execute("DELETE FROM farming_session_items WHERE session_id IN "
                          "(SELECT session_id FROM farming_sessions WHERE char_id = ?)", (char_id,))
        self.conn.execute("DELETE FROM farming_sessions WHERE char_id = ?", (char_id,))
        self.conn.executemany(
            "INSERT INTO farming_sessions(session_id, char_id, start_ms, end_ms, units, ended) VALUES (?, ?, ?, ?, ?, ?)",
            [(s.session_id, s.char_id, s.start_ms, s.end_ms, s.units, s.ended) for s in found])
        self.conn.executemany("INSERT INTO farming_session_items(session_id, key, qty) VALUES (?, ?, ?)",
                              [(s.session_id, k, n) for s in found for k, n in s.items])
        return len(found)

    def ensure_session_settings(self, settings: SessionSettings, now_ms: int) -> bool:
        """Rebuild every character's sessions when the session settings changed since they were cut (SPEC §3.6).

        Run at start, inside a transaction.

        Args:
            settings: The configured session settings.
            now_ms: Now, ms.

        Returns:
            True when the sessions were rebuilt.
        """
        wanted = f"{settings.confirm_units}:{settings.gap_minutes}"
        if self.get_meta(SETTINGS_META) == wanted:                       # type: ignore[attr-defined]
            return False
        chars = [c for (c,) in self.conn.execute("SELECT DISTINCT char_id FROM gather_events ORDER BY char_id")]
        for char_id in chars:
            self.rebuild_sessions(char_id, settings, now_ms)
        self.set_meta(SETTINGS_META, wanted)                             # type: ignore[attr-defined]
        return True

    def farming_sessions(self, char_ids: Sequence[str]) -> list[Session]:
        """The stored confirmed sessions of some characters, with their items.

        Args:
            char_ids: The characters.

        Returns:
            Sessions by character, start time and id.
        """
        marks = ", ".join("?" for _ in char_ids)
        items: dict[str, list[tuple[int, int]]] = {}
        for sid, key, qty in self.conn.execute(
                f"SELECT i.session_id, i.key, i.qty FROM farming_session_items i JOIN farming_sessions s "
                f"ON s.session_id = i.session_id WHERE s.char_id IN ({marks}) ORDER BY i.session_id, i.key",
                tuple(char_ids)):
            items.setdefault(sid, []).append((key, qty))
        rows = self.conn.execute(f"SELECT session_id, char_id, start_ms, end_ms, ended FROM farming_sessions "
                                 f"WHERE char_id IN ({marks}) ORDER BY char_id, start_ms, session_id",
                                 tuple(char_ids)).fetchall()
        return [Session(sid, c, start, end, tuple(items.get(sid, ())), ended) for sid, c, start, end, ended in rows]

    def events_after_sessions(self, char_ids: Sequence[str]) -> list[GatherEvent]:
        """Each character's gather events after her last stored session: the open candidate, and any candidate that
        ended since the last rebuild, both derived at each page build (SPEC §3.4E.4).

        Args:
            char_ids: The characters.

        Returns:
            The events, by character, time and id.
        """
        out: list[GatherEvent] = []
        for char_id in sorted(char_ids):
            last = self.conn.execute("SELECT MAX(end_ms) FROM farming_sessions WHERE char_id = ?", (char_id,)).fetchone()
            out += self.gather_events(char_id, last[0])
        return out

    def looted_sources(self, char_ids: Sequence[str]) -> dict[int, tuple[str, ...]]:
        """The sources the characters' stored gather events looted each item from (observed categories, GA33).

        Every stored event counts, a dropped candidate's too: what opened the loot window is evidence of where the
        item comes from, whether or not the run became a session.

        Args:
            char_ids: The characters.

        Returns:
            Item key -> its distinct sources ("mining", "monster", ...), sorted.
        """
        marks = ", ".join("?" for _ in char_ids)
        found: dict[int, set[str]] = {}
        for source, loot in self.conn.execute(f"SELECT DISTINCT source, loot FROM gather_events "
                                              f"WHERE char_id IN ({marks})", tuple(char_ids)):
            for key, _ in decode_loot(loot):
                found.setdefault(key, set()).add(source)
        return {key: tuple(sorted(sources)) for key, sources in sorted(found.items())}

    def manual_rates(self, char_ids: Sequence[str]) -> dict[tuple[str, int], int]:
        """The manual rate overrides of some characters (SPEC §3.4E.5).

        Args:
            char_ids: The characters.

        Returns:
            (character id, item key) -> whole items per hour.
        """
        marks = ", ".join("?" for _ in char_ids)
        return {(c, k): n for c, k, n in self.conn.execute(
            f"SELECT char_id, key, items_per_hour FROM manual_rates WHERE char_id IN ({marks}) ORDER BY char_id, key",
            tuple(char_ids))}

    def set_manual_rate(self, char_id: str, key: int, items_per_hour: int, at_ms: int) -> bool:
        """Set a character's override for a material (SPEC §3.4E.5, GA27, SEC12); a newer one replaces it.

        Args:
            char_id: The character.
            key: The catalog item key.
            items_per_hour: Whole items per hour, 1-10,000 (checked by the endpoint).
            at_ms: When it was set, ms.

        Returns:
            True (the row is stored).
        """
        self.conn.execute("INSERT INTO manual_rates(char_id, key, items_per_hour, set_at) VALUES (?, ?, ?, ?) "
                          "ON CONFLICT(char_id, key) DO UPDATE SET items_per_hour = excluded.items_per_hour, "
                          "set_at = excluded.set_at", (char_id, key, items_per_hour, at_ms))
        return True

    def clear_manual_rate(self, char_id: str, key: int) -> bool:
        """Clear a character's override: the row is deleted, no tombstone (the dashboard is its only writer).

        Args:
            char_id: The character.
            key: The item key.

        Returns:
            True when there was one.
        """
        return self.conn.execute("DELETE FROM manual_rates WHERE char_id = ? AND key = ?", (char_id, key)).rowcount > 0

    def delete_farming_sessions_before(self, cutoff_ms: int) -> int:
        """Delete sessions that ended before a cutoff, with their items (SPEC §3.2 retention, 90 days).

        Args:
            cutoff_ms: Sessions with end_ms < cutoff_ms go.

        Returns:
            Sessions deleted.
        """
        self.conn.execute("DELETE FROM farming_session_items WHERE session_id IN "
                          "(SELECT session_id FROM farming_sessions WHERE end_ms < ?)", (cutoff_ms,))
        return self.conn.execute("DELETE FROM farming_sessions WHERE end_ms < ?", (cutoff_ms,)).rowcount
