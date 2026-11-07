"""Writes of local observations into the Historical Store (SPEC §4.2), mixed into db.sqlite.Database."""
from __future__ import annotations

import sqlite3

class HistoryWrites:
    """Local `current` and book volume writes; needs `self.conn`. Run inside the caller's transaction."""

    conn: sqlite3.Connection

    def upsert_newer_current(self, market: str, rows: list[tuple[int, int, int, int]], src: str = "local") -> None:
        """`cur` from a book; a row only replaces an older one (an old file read late changes nothing).

        Args:
            market: Market id.
            rows: (key, lowest unit price, units at it, book time ms).
            src: The book's source: "local" for your scans, or a provider's name.
        """
        self.conn.executemany(
            "INSERT INTO current(market, key, price, qty, at, src) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(market, key, src) DO UPDATE SET price = excluded.price, qty = excluded.qty, "
            "at = excluded.at WHERE excluded.at >= current.at", [(market, *row, src) for row in rows])

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
                           method: int | None = None, cover: tuple[str, tuple[int, ...] | None] | None = None) -> None:
        """Write one day's volume for one source whole, with how it observed the day (idempotent; SPEC §3.3.1).

        Args:
            market: Market id.
            day: Day number.
            src: Volume source, e.g. "books".
            rows: key -> (sold, expired, listed).
            method: The estimation method's version for an estimated source (V24); None for a confirmed one.
            cover: (kind, seconds observed per UTC hour or None for the whole day); None leaves no cover row
                (the source's legacy kind then applies).
        """
        self.conn.execute("DELETE FROM volume_d1 WHERE market = ? AND day = ? AND src = ?", (market, day, src))
        self.conn.execute("DELETE FROM volume_cover WHERE market = ? AND day = ? AND src = ?", (market, day, src))
        if cover is not None:
            self.conn.execute(
                "INSERT INTO volume_cover(market, day, src, kind, covered) VALUES (?, ?, ?, ?, ?)",
                (market, day, src, cover[0], None if cover[1] is None else ",".join(map(str, cover[1]))))
        self.conn.executemany(
            "INSERT INTO volume_d1(market, key, day, listed, sold, expired, src, conf, method) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(market, key, day, listed, sold, expired, src, "medium", method)
             for key, (sold, expired, listed) in sorted(rows.items())])

    def replace_lifetimes_day(self, market: str, day: int, src: str,
                              rows: dict[int, tuple[float, float, float]]) -> None:
        """Write one day's ended listings for one source whole (idempotent; SPEC §2.4 Auction lifetimes).

        Args:
            market: Market id.
            day: Day number.
            src: The id source, e.g. "blizzard".
            rows: key -> (ended, expired, matched), each in listings.
        """
        self.conn.execute("DELETE FROM lifetimes_d1 WHERE market = ? AND day = ? AND src = ?", (market, day, src))
        self.conn.executemany(
            "INSERT INTO lifetimes_d1(market, key, day, src, ended, expired, matched) VALUES (?, ?, ?, ?, ?, ?, ?)", [(market, key, day, src, *row) for key, row in sorted(rows.items())])

    def replace_new_listings_day(self, market: str, day: int, src: str, rows: dict[int, dict[int, float]]) -> None:
        """Write one day's new-listing positions for one source whole (idempotent; SPEC §3.3.2 u(p), BZ29).

        Args:
            market: Market id.
            day: Day number.
            src: The id source, e.g. "blizzard".
            rows: key -> position (thousandths of the lowest other price) -> units (whole units: sums of quantities).
        """
        self.conn.execute("DELETE FROM new_listings_d1 WHERE market = ? AND day = ? AND src = ?", (market, day, src))
        self.conn.executemany(
            "INSERT INTO new_listings_d1(market, key, day, src, positions) VALUES (?, ?, ?, ?, ?)",
            [(market, key, day, src, ",".join(f"{pos}:{int(units)}" for pos, units in sorted(by_pos.items())))
             for key, by_pos in sorted(rows.items())])

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

    def upsert_ahledger_rows(self, market: str, updated_s: int, rows: list[tuple]) -> None:
        """Store AHledger's price table aggregates as they came; read by nothing (SPEC §2.3, §3.2, SRC11).

        Args:
            market: Market id.
            updated_s: The table's `updated`, Unix seconds.
            rows: (key, median, m7, m30, lo, hi, per_day_x10), copper and tenths a day, None for an empty field. A
                newer table replaces an item's row; an item missing from it keeps its last row.
        """
        self.conn.executemany(
            "INSERT OR REPLACE INTO ahledger_rows(market, key, updated, median, m7, m30, lo, hi, per_day_x10) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [(market, row[0], updated_s, *row[1:]) for row in rows])

    def ahledger_item_count(self, market: str, updated_s: int) -> int:
        """How many items a stored AHledger table holds (S18's "2,207 items").

        Args:
            market: Market id.
            updated_s: The table's `updated`, Unix seconds.

        Returns:
            Rows of that table for the market.
        """
        return self.conn.execute("SELECT COUNT(*) FROM ahledger_rows WHERE market = ? AND updated = ?",
                                 (market, updated_s)).fetchone()[0]

    def source_state(self, market: str, src: str) -> tuple[str | None, int | None, int | None, str | None]:
        """A remote source's state for a market (SPEC §3.2 source_state).

        Args:
            market: Market id.
            src: The remote source, e.g. "ahledger".

        Returns:
            (etag, its own `updated` ms, last fetch ms, backfill checkpoint JSON), each None when unknown.
        """
        row = self.conn.execute("SELECT etag, updated_ms, fetched_ms, checkpoint FROM source_state "
                                "WHERE market = ? AND src = ?", (market, src)).fetchone()
        return tuple(row) if row else (None, None, None, None)

    def set_source_state(self, market: str, src: str, **fields: object) -> None:
        """Update some of a remote source's state, keeping the rest (a failure never clears it, SPEC §3.2).

        Args:
            market: Market id.
            src: The remote source.
            **fields: Any of etag, updated_ms, fetched_ms, checkpoint; None stores NULL.

        Raises:
            ValueError: An unknown field.
        """
        unknown = set(fields) - {"etag", "updated_ms", "fetched_ms", "checkpoint"}
        if unknown:
            raise ValueError(f"not a source_state field: {sorted(unknown)}")
        self.conn.execute("INSERT OR IGNORE INTO source_state(market, src) VALUES (?, ?)", (market, src))
        names = sorted(fields)
        if names:
            self.conn.execute(f"UPDATE source_state SET {', '.join(f'{n} = ?' for n in names)} "
                              "WHERE market = ? AND src = ?", (*(fields[n] for n in names), market, src))
