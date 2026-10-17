"""Reads of the Historical Store (SPEC §5.0): the `HistoryReader` contract and its SQLite implementation.

Analysis depends only on `HistoryReader` (AR1, AR4), so it runs on any implementation: this SQLite
one locally, or a cloud store later.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
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


@dataclass(frozen=True)
class HistoryWindow:
    """Which history a market read covers (SPEC §3.3)."""

    from_ms: int            # oldest observation: items priced since then, and their series from then
    to_ms: int              # newest hour start to include
    volume_from_day: int    # oldest volume day number
    book_since_ms: int      # oldest complete book to accept


@dataclass(frozen=True)
class ItemHistory:
    """Everything analysis reads about one item, from one bulk market read (SPEC §5.0, AR7)."""

    key: int
    hourly_closes: list[tuple[int, int]]          # (hour start ms, close copper), ascending, local preferred
    hourly_supply: list[tuple[int, int]]          # (hour start ms, units listed), ascending, local preferred
    hourly_sales: list[tuple[int, float]]         # (hour start ms, units sold per hour), ascending
    daily_closes: list[tuple[int, int]]           # (day start ms, close copper), ascending, local preferred
    current: CurrentPrice | None                  # the newest lowest buyout
    volume: dict[int, dict[str, tuple[float, float, float]]]   # day -> source -> (sold, expired, listed)
    book: tuple[int, list[tuple[int, int, int, int]]] | None   # newest complete book: (at ms, [(price, stack, band, count)])


class HistoryReader(Protocol):
    """What historical analysis may read (SPEC §5.0 contracts). Times are UTC ms, money copper.

    Reads are per market, never per item (AR7): a remote store answers a whole market
    in a few round trips.
    """

    def items(self) -> dict[int, ItemInfo]:
        """Item metadata.

        Returns:
            Item key -> ItemInfo.
        """

    def market_history(self, market: str, window: HistoryWindow) -> Iterator[ItemHistory]:
        """Every priced item's history in one market, streamed in key order (AR7).

        Args:
            market: Market id.
            window: What to read.

        Returns:
            One ItemHistory per item with price data since window.from_ms, ascending by key;
            only one item needs to be in memory at a time.
        """


class HistoryReads:
    """SQLite implementation of HistoryReader (mixed into db.sqlite.Database); needs `self.conn`.

    The per-item reads below give the same values as `market_history` for one item; the
    bulk read is what analysis uses.
    """

    conn: sqlite3.Connection

    def items(self) -> dict[int, ItemInfo]:
        """All item metadata.

        Returns:
            Item key -> ItemInfo.
        """
        rows = self.conn.execute("SELECT key, name, quality, ilvl, class, vendor_buy, vendor_sell, stack FROM items")
        return {r[0]: ItemInfo(r[0], r[1] or str(r[0]), *r[2:]) for r in rows}

    def market_history(self, market: str, window: HistoryWindow) -> Iterator[ItemHistory]:
        """Every priced item's history, one query per table, merged by key (SPEC §5.0 bulk reads, AR7).

        Args:
            market: Market id.
            window: What to read.

        Returns:
            One ItemHistory per priced key, ascending; same values as the per-item reads.
        """
        keys = self.priced_keys(market, window.from_ms)
        w = (market, window.from_ms, window.to_ms)
        # Walk each table's primary key (market, key, t, ...), which is already in this order:
        # "+t" keeps the planner off the (market, t) index, whose rows would need a sort of the
        # whole market (measured: 10 s of 12 s for 2.16M hourly rows). The local-first preference
        # is applied per hour in Python for the same reason.
        h1 = _Groups(self.conn.execute(
            "SELECT key, t, close, qty, src FROM candles_h1 WHERE market = ? AND +t BETWEEN ? AND ? "
            "ORDER BY key, t, src", w))
        sales = _Groups(self.conn.execute(
            "SELECT key, t, sold_per_hour FROM sales_h1 WHERE market = ? AND +t BETWEEN ? AND ? ORDER BY key, t", w))
        d1 = _Groups(self.conn.execute(
            "SELECT key, t, close, src FROM candles_d1 WHERE market = ? AND +t BETWEEN ? AND ? AND close IS NOT NULL "
            "ORDER BY key, t, src", w))
        cur = _Groups(self.conn.execute(
            "SELECT key, key, price, qty, at, src FROM current WHERE market = ? "
            "ORDER BY key, at DESC, CASE src WHEN 'local' THEN 0 ELSE 1 END, src", (market,)))
        vol = _Groups(self.conn.execute(
            "SELECT key, day, src, sold, expired, listed FROM volume_d1 WHERE market = ? AND day >= ? "
            "ORDER BY key, day, src", (market, window.volume_from_day)))
        # The recent complete scans first (newest first, ties: scan id), then only their entries:
        # a join over every stored book would read and sort all of them.
        scans = self.conn.execute(
            "SELECT scan_id, at FROM scans WHERE market = ? AND at >= ? AND kind = 'full' AND coverage >= 1 "
            "ORDER BY at DESC, scan_id", (market, window.book_since_ms)).fetchall()
        rank = {scan_id: (i, at) for i, (scan_id, at) in enumerate(scans)}
        books = _Groups(self.conn.execute(
            f"SELECT key, scan_id, price, stack, band, count FROM book_entries WHERE scan_id IN "
            f"({','.join('?' * len(scans))}) ORDER BY key, price, stack, band", [s for s, _ in scans]))
        for key in keys:
            candles = h1.take(key)
            current = cur.take(key)
            yield ItemHistory(
                key, _local_first_per_time([(t, c, src) for t, c, _, src in candles if c is not None]),
                _local_first_per_time([(t, q or 0, src) for t, _, q, src in candles]),
                [tuple(r) for r in sales.take(key)], _local_first_per_time(d1.take(key)),
                CurrentPrice(*current[0]) if current else None, _volume_by_day(vol.take(key)),
                _newest_book(books.take(key), rank))

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


class _Groups:
    """Rows of one key-ordered query, handed out one key at a time (a merge join over sorted keys)."""

    def __init__(self, rows: Iterator[tuple]) -> None:
        """Start reading.

        Args:
            rows: Query rows whose first column is the item key, ascending.
        """
        self._rows = iter(rows)
        self._next: tuple | None = next(self._rows, None)

    def take(self, key: int) -> list[tuple]:
        """The rows of one key, without the key column; rows of smaller keys are skipped.

        Args:
            key: Item key; calls must come in ascending key order.

        Returns:
            Those rows in query order; empty when the key has none.
        """
        while self._next is not None and self._next[0] < key:
            self._next = next(self._rows, None)
        out = []
        while self._next is not None and self._next[0] == key:
            out.append(self._next[1:])
            self._next = next(self._rows, None)
        return out


def _local_first_per_time(rows: list[tuple]) -> list[tuple]:
    """One value per time: the local source's when there is one, else the first other (SPEC §2.2, S9).

    The same choice as the per-item reads' ORDER BY t, local first, then src.

    Args:
        rows: (time ms, value, src) rows ordered by time, then src.

    Returns:
        One (time, value) per time, ascending.
    """
    out: list[tuple] = []
    for t, value, src in rows:
        if not out or out[-1][0] != t:
            out.append((t, value))
        elif src == "local":
            out[-1] = (t, value)
    return out


def _volume_by_day(rows: list[tuple]) -> dict[int, dict[str, tuple[float, float, float]]]:
    """Stored volume rows as day -> source -> (sold, expired, listed), missing values as 0.

    Args:
        rows: (day, src, sold, expired, listed) rows.

    Returns:
        The nested mapping.
    """
    out: dict[int, dict[str, tuple[float, float, float]]] = {}
    for day, src, sold, expired, listed in rows:
        out.setdefault(day, {})[src] = (sold or 0.0, expired or 0.0, listed or 0.0)
    return out


def _newest_book(rows: list[tuple], rank: dict[str, tuple[int, int]]) -> tuple[int, list[tuple[int, int, int, int]]] | None:
    """The newest complete book among an item's book rows.

    Args:
        rows: (scan_id, price, stack, band, count) from the recent complete scans, ordered by price, stack, band.
        rank: scan_id -> (position, at ms), position 0 for the newest scan (ties: scan id).

    Returns:
        (at ms, [(price, stack, band, count), ...]) of the newest scan listing the item; None without rows.
    """
    if not rows:
        return None
    newest = min((row[0] for row in rows), key=lambda scan_id: rank[scan_id][0])
    return rank[newest][1], [(p, s, b, c) for sid, p, s, b, c in rows if sid == newest]
