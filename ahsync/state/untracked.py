"""Record untracked changes while a SavedVariables file is stored (SPEC §2.2.1, FM21, D51).

`before` reads each character's holdings and bag snapshot time before the file's newer bag snapshot replaces
them; `record` reads them again once the file's events are stored and keeps what engine.untracked says no
event explains. Both run inside the importer's transaction.
"""
from __future__ import annotations

import sqlite3

from ahsync.engine.untracked import unexplained

Holdings = dict[int, int]


def _holdings(conn: sqlite3.Connection, char_id: str) -> Holdings:
    """A character's units per item in bags, bank and mail (bound ones too).

    Args:
        conn: Database connection.
        char_id: Character id.

    Returns:
        Item key -> units.
    """
    return dict(conn.execute("SELECT key, SUM(qty) FROM inventory WHERE char_id = ? GROUP BY key", (char_id,)).fetchall())


def before(conn: sqlite3.Connection, newer_bags: dict[str, int]) -> dict[str, tuple[int, Holdings]]:
    """The holdings to compare against, for characters whose file brings a newer bag snapshot.

    Args:
        conn: Database connection, inside the importer's transaction.
        newer_bags: Character id -> the file's bag snapshot time, ms.

    Returns:
        Character id -> (stored bag snapshot time, holdings), only for characters with an older stored one.
    """
    out = {}
    for char_id, at_ms in sorted(newer_bags.items()):
        row = conn.execute("SELECT at FROM snapshots WHERE char_id = ? AND location = 'bags'", (char_id,)).fetchone()
        if row is not None and row[0] < at_ms:
            out[char_id] = (row[0], _holdings(conn, char_id))
    return out


def _explained(conn: sqlite3.Connection, char_id: str, t0: int, t1: int) -> tuple[dict[int, float], set[int]]:
    """What the character's events in (t0, t1] explain, and the items moved by the AH or by mail.

    Args:
        conn: Database connection.
        char_id: Character id.
        t0: The earlier bag snapshot, ms.
        t1: The later one, ms.

    Returns:
        (item key -> units added, negative removed; items to leave out).
    """
    explained: dict[int, float] = {}

    def add(key: int, units: float) -> None:
        """Add units to an item's explained change.

        Args:
            key: Item key.
            units: Units (negative: removed).
        """
        explained[key] = explained.get(key, 0.0) + units
    window = (char_id, t0, t1)
    for key, qty in conn.execute("SELECT key, qty FROM purchases WHERE char_id = ? AND at > ? AND at <= ? "
                                 "AND confirmed = 1 AND source = 'vendor'", window):
        add(key, qty)
    for spell, count in conn.execute("SELECT spell_id, count FROM crafts WHERE char_id = ? AND at > ? AND at <= ?", window):
        recipe = conn.execute("SELECT MIN(product_key), MIN(yield_min), MIN(yield_max) FROM recipes WHERE spell_id = ?",
                              (spell,)).fetchone()
        if recipe and recipe[0] is not None:
            add(recipe[0], count * ((recipe[1] or 1) + (recipe[2] or recipe[1] or 1)) / 2)
        for key, n in conn.execute("SELECT key, MIN(count) FROM reagents WHERE spell_id = ? GROUP BY key", (spell,)):
            add(key, -n * count)
    for key, results in conn.execute("SELECT key, results FROM disenchants WHERE char_id = ? AND at > ? AND at <= ?", window):
        add(key, -1)
        for part in results.split(";"):
            k, n = part.split(",")
            add(int(k), int(n))
    skip = {k for (k,) in conn.execute(
        "SELECT key FROM posts WHERE char_id = ? AND at > ? AND at <= ? UNION "
        "SELECT key FROM auction_mail WHERE char_id = ? AND at > ? AND at <= ? UNION "
        "SELECT key FROM purchases WHERE char_id = ? AND at > ? AND at <= ? AND source != 'vendor' UNION "
        "SELECT key FROM mail_sent WHERE (char_id = ? OR to_char = ?) AND at > ? AND at <= ?",
        (*window, *window, *window, char_id, char_id, t0, t1))}
    return explained, skip


def record(conn: sqlite3.Connection, held_before: dict[str, tuple[int, Holdings]]) -> set[int]:
    """Store the untracked changes since each character's earlier bag snapshot (D51).

    Args:
        conn: Database connection, inside the importer's transaction, after the file's snapshots and events.
        held_before: From `before`.

    Returns:
        The item keys with an untracked change.
    """
    keys: set[int] = set()
    for char_id, (t0, old) in sorted(held_before.items()):
        row = conn.execute("SELECT at FROM snapshots WHERE char_id = ? AND location = 'bags'", (char_id,)).fetchone()
        if row is None or row[0] <= t0:
            continue
        explained, skip = _explained(conn, char_id, t0, row[0])
        for key, delta in unexplained(old, _holdings(conn, char_id), explained, skip).items():
            conn.execute("INSERT OR REPLACE INTO untracked(char_id, key, delta, at) VALUES (?, ?, ?, ?)",
                         (char_id, key, delta, row[0]))
            keys.add(key)
    return keys
