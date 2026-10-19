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

    def replace_sales_hours(self, market: str, from_ms: int, to_ms: int, rates: dict[int, dict[int, float]]) -> None:
        """Write a range of hours' sales rates whole (idempotent: recomputed, never added to; SPEC §3.3 sales_h1).

        Args:
            market: Market id.
            from_ms: First hour start replaced, inclusive.
            to_ms: Last hour start replaced, inclusive.
            rates: key -> hour start ms -> units sold per hour; hours outside the range are ignored.
        """
        self.conn.execute("DELETE FROM sales_h1 WHERE market = ? AND t BETWEEN ? AND ?", (market, from_ms, to_ms))
        self.conn.executemany(
            "INSERT INTO sales_h1(market, key, t, sold_per_hour) VALUES (?, ?, ?, ?)",
            [(market, key, t, rate) for key, by_hour in sorted(rates.items())
             for t, rate in sorted(by_hour.items()) if from_ms <= t <= to_ms])

    def replace_volume_day(self, market: str, day: int, src: str, rows: dict[int, tuple[float, float, float]],
                           method: int | None = None) -> None:
        """Write one day's volume for one source whole (idempotent: recomputed, never added to).

        Args:
            market: Market id.
            day: Day number.
            src: Volume source, e.g. "books".
            rows: key -> (sold, expired, listed).
            method: The estimation method's version for an estimated source (V24); None for a confirmed one.
        """
        self.conn.execute("DELETE FROM volume_d1 WHERE market = ? AND day = ? AND src = ?", (market, day, src))
        self.conn.executemany(
            "INSERT INTO volume_d1(market, key, day, listed, sold, expired, src, conf, method) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(market, key, day, listed, sold, expired, src, "medium", method)
             for key, (sold, expired, listed) in sorted(rows.items())])

    def stale_volume_days(self, market: str, src: str, method: int) -> list[int]:
        """Days whose rows of an estimated source come from an older method (V24).

        Args:
            market: Market id.
            src: The estimated source, e.g. "books".
            method: The current method version.

        Returns:
            Day numbers, ascending; a row without a version counts as version 1.
        """
        return [d for (d,) in self.conn.execute(
            "SELECT DISTINCT day FROM volume_d1 WHERE market = ? AND src = ? AND COALESCE(method, 1) < ? ORDER BY day",
            (market, src, method))]
