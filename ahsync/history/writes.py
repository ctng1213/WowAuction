"""Writes of local observations into the Historical Store (SPEC §4.2), mixed into db.sqlite.Database."""
from __future__ import annotations

import sqlite3

class HistoryWrites:
    """Local `current` and book volume writes; needs `self.conn`. Run inside the caller's transaction."""

    conn: sqlite3.Connection

    def upsert_local_current(self, market: str, rows: list[tuple[int, int, int, int]]) -> None:
        """Local `cur` from a scan; a row only replaces an older one (an old file read late changes nothing).

        Args:
            market: Market id.
            rows: (key, lowest unit price, units at it, scan time ms).
        """
        self.conn.executemany(
            "INSERT INTO current(market, key, price, qty, at, src) VALUES (?, ?, ?, ?, ?, 'local') "
            "ON CONFLICT(market, key, src) DO UPDATE SET price = excluded.price, qty = excluded.qty, "
            "at = excluded.at WHERE excluded.at >= current.at", [(market, *row) for row in rows])

    def replace_volume_day(self, market: str, day: int, src: str, rows: dict[int, tuple[float, float, float]]) -> None:
        """Write one day's volume for one source whole (idempotent: recomputed, never added to).

        Args:
            market: Market id.
            day: Day number.
            src: Volume source, e.g. "books".
            rows: key -> (sold, expired, listed).
        """
        self.conn.execute("DELETE FROM volume_d1 WHERE market = ? AND day = ? AND src = ?", (market, day, src))
        self.conn.executemany(
            "INSERT INTO volume_d1(market, key, day, listed, sold, expired, src, conf) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(market, key, day, listed, sold, expired, src, "medium")
             for key, (sold, expired, listed) in sorted(rows.items())])
