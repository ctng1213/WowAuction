"""Reads of the Historical Store (SPEC §5.0): the `HistoryReader` contract and its SQLite implementation.

Analysis depends only on `HistoryReader` (AR1, AR4), so it runs on any implementation: this SQLite
one locally, or a cloud store later.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class ItemInfo:
    """Item metadata from AHF /items (SPEC §3.2 items)."""

    key: int
    name: str
    quality: int | None
    ilvl: int | None
    item_class: int | None       # WoW item class: 2 weapon, 4 armor
    vendor_buy: int | None
    vendor_sell: int | None
    stack: int | None


@dataclass(frozen=True)
class CurrentPrice:
    """The newest lowest buyout for an item, whichever source saw it last (SPEC §3.3 `cur`)."""

    key: int
    price: int | None   # copper; None = nothing listed
    qty: int
    at: int             # observation time, ms
    src: str            # 'ahf' | 'local'


class HistoryReader(Protocol):
    """What historical analysis may read (SPEC §5.0 contracts). Times are UTC ms, money copper."""

    def items(self) -> dict[int, ItemInfo]:
        """Item metadata.

        Returns:
            Item key -> ItemInfo.
        """

    def priced_keys(self, market: str, since_ms: int) -> list[int]:
        """Items with price data since a time.

        Args:
            market: Market id.
            since_ms: Oldest observation to consider.

        Returns:
            Sorted item keys.
        """

    def hourly_closes(self, market: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """One close per item-hour, local preferred.

        Args:
            market: Market id.
            key: Item key.
            from_ms: Window start.
            to_ms: Window end.

        Returns:
            (hour start ms, close copper), ascending.
        """

    def hourly_supply(self, market: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """Units listed per item-hour, local preferred.

        Args:
            market: Market id.
            key: Item key.
            from_ms: Window start.
            to_ms: Window end.

        Returns:
            (hour start ms, units listed), ascending; hours never observed are left out.
        """

    def hourly_sales(self, market: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, float]]:
        """Book-pair sales rate per item-hour (SPEC §3.3 sales_how).

        Args:
            market: Market id.
            key: Item key.
            from_ms: Window start.
            to_ms: Window end.

        Returns:
            (hour start ms, units sold per hour), ascending; hours without a pair are left out.
        """

    def daily_closes(self, market: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """One close per item-day, local preferred.

        Args:
            market: Market id.
            key: Item key.
            from_ms: Window start.
            to_ms: Window end.

        Returns:
            (day start ms, close copper), ascending.
        """

    def current_price(self, market: str, key: int) -> CurrentPrice | None:
        """The newest lowest buyout.

        Args:
            market: Market id.
            key: Item key.

        Returns:
            The observation, or None when unseen.
        """

    def volume_rows(self, market: str, key: int, from_day: int) -> dict[int, dict[str, tuple[float, float, float]]]:
        """Stored daily volume per source.

        Args:
            market: Market id.
            key: Item key.
            from_day: Oldest day number.

        Returns:
            day -> source -> (sold, expired, listed).
        """

    def newest_book(self, market: str, key: int, since_ms: int) -> tuple[int, list[tuple[int, int, int, int]]] | None:
        """The newest complete order book listing the item.

        Args:
            market: Market id.
            key: Item key.
            since_ms: Oldest scan to accept.

        Returns:
            (scan time ms, [(price, stack, band, count), ...]), or None.
        """


class HistoryReads:
    """SQLite implementation of HistoryReader (mixed into db.sqlite.Database); needs `self.conn`."""

    conn: sqlite3.Connection

    def items(self) -> dict[int, ItemInfo]:
        """All item metadata.

        Returns:
            Item key -> ItemInfo.
        """
        rows = self.conn.execute("SELECT key, name, quality, ilvl, class, vendor_buy, vendor_sell, stack FROM items")
        return {r[0]: ItemInfo(r[0], r[1] or str(r[0]), *r[2:]) for r in rows}

    def priced_keys(self, market: str, since_ms: int) -> list[int]:
        """Item keys with any price data in a market since a time.

        Args:
            market: Market id.
            since_ms: Oldest candle or current time to consider, UTC ms.

        Returns:
            Sorted item keys.
        """
        rows = self.conn.execute(
            "SELECT key FROM candles_h1 WHERE market = ? AND t >= ? UNION "
            "SELECT key FROM candles_d1 WHERE market = ? AND t >= ? UNION "
            "SELECT key FROM current WHERE market = ? AND at >= ? ORDER BY 1",
            (market, since_ms, market, since_ms, market, since_ms))
        return [r[0] for r in rows]

    def hourly_closes(self, market: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """One close per item-hour for statistics; a local close beats AHF's (SPEC §2.2, S9).

        Args:
            market: AHF market id.
            key: AHF item key.
            from_ms: First hour start to include, ms.
            to_ms: Last hour start to include, ms.

        Returns:
            (hour start ms, close copper), ascending; hours with no listed close are left out.
        """
        rows = self.conn.execute(
            "SELECT t, close FROM candles_h1 WHERE market = ? AND key = ? AND t BETWEEN ? AND ? "
            "AND close IS NOT NULL ORDER BY t, CASE src WHEN 'local' THEN 0 ELSE 1 END, src",
            (market, key, from_ms, to_ms)).fetchall()
        closes: list[tuple[int, int]] = []
        for t, close in rows:
            if not closes or closes[-1][0] != t:
                closes.append((t, close))
        return closes

    def hourly_supply(self, market: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """Units listed per item-hour for `supply_how`; a local row beats AHF's (SPEC §3.3.6).

        Args:
            market: Market id.
            key: Item key.
            from_ms: First hour start to include, ms.
            to_ms: Last hour start to include, ms.

        Returns:
            (hour start ms, units listed), ascending; an hour with nothing listed is 0, an unobserved one absent.
        """
        rows = self.conn.execute(
            "SELECT t, COALESCE(qty, 0) FROM candles_h1 WHERE market = ? AND key = ? AND t BETWEEN ? AND ? "
            "ORDER BY t, CASE src WHEN 'local' THEN 0 ELSE 1 END, src", (market, key, from_ms, to_ms)).fetchall()
        supply: list[tuple[int, int]] = []
        for t, qty in rows:
            if not supply or supply[-1][0] != t:
                supply.append((t, qty))
        return supply

    def hourly_sales(self, market: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, float]]:
        """Stored book-pair sales rates (SPEC §3.3 sales_h1).

        Args:
            market: Market id.
            key: Item key.
            from_ms: First hour start to include, ms.
            to_ms: Last hour start to include, ms.

        Returns:
            (hour start ms, units sold per hour), ascending.
        """
        return [tuple(r) for r in self.conn.execute(
            "SELECT t, sold_per_hour FROM sales_h1 WHERE market = ? AND key = ? AND t BETWEEN ? AND ? ORDER BY t",
            (market, key, from_ms, to_ms))]

    def daily_closes(self, market: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """Daily closes, one per day, local preferred over AHF (SPEC §2.2).

        Args:
            market: Market id.
            key: Item key.
            from_ms: Window start, inclusive.
            to_ms: Window end, inclusive.

        Returns:
            (day start ms, close copper), oldest first; days without a close are left out.
        """
        rows = self.conn.execute(
            "SELECT t, close, src FROM candles_d1 WHERE market = ? AND key = ? AND t BETWEEN ? AND ? "
            "AND close IS NOT NULL ORDER BY t, src = 'local' DESC", (market, key, from_ms, to_ms))
        out: dict[int, int] = {}
        for t, close, _ in rows:
            out.setdefault(t, close)
        return sorted(out.items())

    def current_price(self, market: str, key: int) -> CurrentPrice | None:
        """`cur` for an item: the newest observation across sources, keeping its src (S7).

        Args:
            market: AHF market id.
            key: AHF item key.

        Returns:
            The newest row (ties go to 'local', the exact observation), or None when unseen.
        """
        row = self.conn.execute(
            "SELECT key, price, qty, at, src FROM current WHERE market = ? AND key = ? "
            "ORDER BY at DESC, CASE src WHEN 'local' THEN 0 ELSE 1 END, src LIMIT 1", (market, key)).fetchone()
        return CurrentPrice(*row) if row else None

    def volume_rows(self, market: str, key: int, from_day: int) -> dict[int, dict[str, tuple[float, float, float]]]:
        """Stored daily volume per source (SPEC §3.3.1).

        Args:
            market: Market id.
            key: Item key.
            from_day: Oldest day number to read.

        Returns:
            day -> source -> (sold, expired, listed).
        """
        out: dict[int, dict[str, tuple[float, float, float]]] = {}
        for day, src, sold, expired, listed in self.conn.execute(
                "SELECT day, src, sold, expired, listed FROM volume_d1 WHERE market = ? AND key = ? AND day >= ? "
                "ORDER BY day, src", (market, key, from_day)):
            out.setdefault(day, {})[src] = (sold or 0.0, expired or 0.0, listed or 0.0)
        return out

    def newest_book(self, market: str, key: int, since_ms: int) -> tuple[int, list[tuple[int, int, int, int]]] | None:
        """The newest complete order book that lists the item (SPEC §3.3.2 book).

        Args:
            market: Market id.
            key: Item key.
            since_ms: Oldest scan to accept.

        Returns:
            (scan time ms, [(price, stack, band, count), ...] sorted by price), or None.
        """
        row = self.conn.execute(
            "SELECT s.scan_id, s.at FROM scans s JOIN book_entries b ON b.scan_id = s.scan_id "
            "WHERE s.market = ? AND b.key = ? AND s.at >= ? AND s.kind = 'full' AND s.coverage >= 1 "
            "ORDER BY s.at DESC, s.scan_id LIMIT 1", (market, key, since_ms)).fetchone()
        if row is None:
            return None
        entries = self.conn.execute(
            "SELECT price, stack, band, count FROM book_entries WHERE scan_id = ? AND key = ? "
            "ORDER BY price, stack, band", (row[0], key)).fetchall()
        return row[1], [tuple(e) for e in entries]

    def local_full_scans(self, market: str, from_ms: int, to_ms: int) -> list[tuple[str, int]]:
        """Complete local scans of a market in a time range.

        Args:
            market: Market id.
            from_ms: Start, inclusive.
            to_ms: End, inclusive.

        Returns:
            (scan id, time ms), oldest first (ties: scan id).
        """
        return [tuple(r) for r in self.conn.execute(
            "SELECT scan_id, at FROM scans WHERE market = ? AND src = 'local' AND kind = 'full' AND coverage >= 1 "
            "AND at BETWEEN ? AND ? ORDER BY at, scan_id", (market, from_ms, to_ms))]

    def complete_scans(self, market: str, from_ms: int, to_ms: int) -> list[tuple[str, int]]:
        """Complete books of a market from either source (local scans and AHF books) in a time range.

        Args:
            market: Market id.
            from_ms: Start, inclusive.
            to_ms: End, inclusive.

        Returns:
            (scan id, time ms), oldest first (ties: scan id).
        """
        return [tuple(r) for r in self.conn.execute(
            "SELECT scan_id, at FROM scans WHERE market = ? AND kind = 'full' AND coverage >= 1 "
            "AND at BETWEEN ? AND ? ORDER BY at, scan_id", (market, from_ms, to_ms))]

    def scan_entries(self, scan_id: str) -> list[tuple[int, int, int, int, int]]:
        """One stored book's rows.

        Args:
            scan_id: The scan.

        Returns:
            (key, unit price, stack, band, count), sorted.
        """
        return [tuple(r) for r in self.conn.execute(
            "SELECT key, price, stack, band, count FROM book_entries WHERE scan_id = ? ORDER BY key, price, stack, band",
            (scan_id,))]
