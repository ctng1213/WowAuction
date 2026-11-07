"""Reads of the Historical Store (SPEC §5.0): the `HistoryReader` contract and its SQLite implementation.

Analysis depends only on `HistoryReader` (AR1, AR4), so it runs on any implementation: this SQLite
one locally, or a cloud store later.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ahsync.collect.provider import source_precedence

DEFAULT_RANK = {"local": 0, "ahf": 1}   # the per-item reads' rank, HistoryWindow's default (SRC3)
SUFFIX_TABLE = Path(__file__).resolve().parents[1] / "data" / "bonus_suffixes.json"   # tools/gen_bonus_suffixes.py


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
    source_rank: tuple[str, ...] = ("local", "ahf")   # price sources, most trusted first within a kind (SRC3, SRC9)


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
    # day -> source -> (kind, seconds observed per UTC hour or None for the whole day); market-wide, shared
    volume_cover: dict[int, dict[str, tuple[str, tuple[int, ...] | None]]] = field(default_factory=dict)
    # day -> (ended, expired, matched) listings from auction lifetimes, summed over id sources (SPEC §2.4)
    lifetimes: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    # day -> position (thousandths of the lowest other price) -> new units, summed over id sources (SPEC §3.3.2, BZ29)
    new_listings: dict[int, dict[int, float]] = field(default_factory=dict)


class ItemCatalog(dict):
    """Item key -> ItemInfo, plus AHF's random suffix names, so pages can name a variant `items` lacks (D60)."""

    def __init__(self, rows: Iterable[tuple[int, ItemInfo]] = (), suffix_names: Mapping[int, str] | None = None):
        """Build the catalog.

        Args:
            rows: (item key, ItemInfo) pairs.
            suffix_names: Random suffix id -> name ("of Defense"); None for none.
        """
        super().__init__(rows)
        self.suffix_names: Mapping[int, str] = dict(suffix_names or {})


def suffix_names_of(items: Mapping[int, ItemInfo]) -> Mapping[int, str]:
    """The suffix names that come with item metadata (D60).

    Args:
        items: An ItemCatalog, or a plain mapping (tests), which has none.

    Returns:
        Suffix id -> name; empty for a plain mapping.
    """
    return items.suffix_names if isinstance(items, ItemCatalog) else {}


def load_suffix_names(path: Path = SUFFIX_TABLE) -> dict[int, str]:
    """AHF's random suffix id -> name table, generated with the bonus table (SPEC §2.1, §2.2.1).

    Args:
        path: The generated JSON (tools/gen_bonus_suffixes.py).

    Returns:
        Suffix id -> name; empty when the file or its `names` table is missing (variants then read "(variant)").
    """
    try:
        table = json.loads(path.read_text(encoding="utf-8")).get("names") or {}
    except (OSError, ValueError):
        return {}
    return {int(suffix): name for suffix, name in table.items()}


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

    def items(self) -> ItemCatalog:
        """All item metadata, with the suffix names that name variants missing from it (D60).

        Returns:
            Item key -> ItemInfo, as an ItemCatalog.
        """
        rows = self.conn.execute("SELECT key, name, quality, ilvl, class, vendor_buy, vendor_sell, stack FROM items")
        return ItemCatalog(((r[0], ItemInfo(r[0], r[1] or str(r[0]), *r[2:])) for r in rows), load_suffix_names())

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
        rank = " ".join(f"WHEN ? THEN {i}" for i in range(len(window.source_rank)))
        cur = _Groups(self.conn.execute(
            "SELECT key, key, price, qty, at, src FROM current WHERE market = ? "
            f"ORDER BY key, at DESC, CASE src {rank} ELSE {len(window.source_rank)} END, src",
            (market, *window.source_rank)))
        vol = _Groups(self.conn.execute(
            "SELECT key, day, src, sold, expired, listed FROM volume_d1 WHERE market = ? AND day >= ? "
            "ORDER BY key, day, src", (market, window.volume_from_day)))
        cover = self.volume_cover(market, window.volume_from_day)
        ends = _Groups(self.conn.execute(
            "SELECT key, day, ended, expired, matched FROM lifetimes_d1 WHERE market = ? AND day >= ? "
            "ORDER BY key, day, src", (market, window.volume_from_day)))
        news = _Groups(self.conn.execute(
            "SELECT key, day, positions FROM new_listings_d1 WHERE market = ? AND day >= ? ORDER BY key, day, src",
            (market, window.volume_from_day)))
        # The recent complete scans first (newest first, ties: scan id), then only their entries:
        # a join over every stored book would read and sort all of them.
        scans = self.conn.execute(
            "SELECT sid, at FROM scans WHERE market = ? AND at >= ? AND kind = 'full' AND coverage >= 1 "
            "ORDER BY at DESC, scan_id", (market, window.book_since_ms)).fetchall()
        rank = {sid: (i, at) for i, (sid, at) in enumerate(scans)}
        books = _Groups(self.conn.execute(
            f"SELECT key, sid, price, stack, band, count FROM book_entries WHERE sid IN "
            f"({','.join('?' * len(scans))}) ORDER BY key, price, stack, band", [s for s, _ in scans]))
        ranked = {src: i for i, src in enumerate(window.source_rank)}
        for key in keys:
            candles = h1.take(key)
            current = cur.take(key)
            yield ItemHistory(
                key, _best_per_time([(t, c, src) for t, c, _, src in candles if c is not None], ranked),
                _best_per_time([(t, q or 0, src) for t, _, q, src in candles], ranked),
                [tuple(r) for r in sales.take(key)], _best_per_time(d1.take(key), ranked),
                CurrentPrice(*current[0]) if current else None, _volume_by_day(vol.take(key)),
                _newest_book(books.take(key), rank), cover, _lifetimes_by_day(ends.take(key)),
                _positions_by_day(news.take(key)))

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
        """One close per item-hour for statistics, by source kind then rank (SPEC §2.2, S9, SRC9).

        Args:
            market: AHF market id.
            key: AHF item key.
            from_ms: First hour start to include, ms.
            to_ms: Last hour start to include, ms.

        Returns:
            (hour start ms, close copper), ascending; hours with no listed close are left out.
        """
        rows = self.conn.execute(
            "SELECT t, close, src FROM candles_h1 WHERE market = ? AND key = ? AND t BETWEEN ? AND ? "
            "AND close IS NOT NULL ORDER BY t, src", (market, key, from_ms, to_ms)).fetchall()
        return _best_per_time(rows, DEFAULT_RANK)

    def hourly_supply(self, market: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """Units listed per item-hour for `supply_how`, by source kind then rank (SPEC §3.3.6, SRC9).

        Args:
            market: Market id.
            key: Item key.
            from_ms: First hour start to include, ms.
            to_ms: Last hour start to include, ms.

        Returns:
            (hour start ms, units listed), ascending; an hour with nothing listed is 0, an unobserved one absent.
        """
        rows = self.conn.execute(
            "SELECT t, COALESCE(qty, 0), src FROM candles_h1 WHERE market = ? AND key = ? AND t BETWEEN ? AND ? "
            "ORDER BY t, src", (market, key, from_ms, to_ms)).fetchall()
        return _best_per_time(rows, DEFAULT_RANK)

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
        """Daily closes, one per day, by source kind then rank (SPEC §2.2).

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
            "AND close IS NOT NULL ORDER BY t, src", (market, key, from_ms, to_ms)).fetchall()
        return _best_per_time(rows, DEFAULT_RANK)

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

    def volume_cover(self, market: str, from_day: int) -> dict[int, dict[str, tuple[str, tuple[int, ...] | None]]]:
        """How each volume source observed each day of a market (SPEC §3.3.1).

        Args:
            market: Market id.
            from_day: Oldest day number to read.

        Returns:
            day -> source -> (kind, seconds observed per UTC hour, or None for the whole day).
        """
        out: dict[int, dict[str, tuple[str, tuple[int, ...] | None]]] = {}
        for day, src, kind, covered in self.conn.execute(
                "SELECT day, src, kind, covered FROM volume_cover WHERE market = ? AND day >= ? ORDER BY day, src",
                (market, from_day)):
            out.setdefault(day, {})[src] = (kind, tuple(int(s) for s in covered.split(",")) if covered else None)
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
            "SELECT s.sid, s.at FROM scans s JOIN book_entries b ON b.sid = s.sid "
            "WHERE s.market = ? AND b.key = ? AND s.at >= ? AND s.kind = 'full' AND s.coverage >= 1 "
            "ORDER BY s.at DESC, s.scan_id LIMIT 1", (market, key, since_ms)).fetchone()
        if row is None:
            return None
        entries = self.conn.execute(
            "SELECT price, stack, band, count FROM book_entries WHERE sid = ? AND key = ? "
            "ORDER BY price, stack, band", (row[0], key)).fetchall()
        return row[1], [tuple(e) for e in entries]

    def scans_since(self, market: str, since_ms: int
                    ) -> tuple[dict[int, tuple[int, int, int]], dict[int, tuple[int, list[tuple[int, int, int, int]]]]]:
        """Your in-game scans' prices and newest complete book after a time (SPEC §3.1.1, IU6).

        Args:
            market: Market id.
            since_ms: Only later scans count (the intelligence's build time).

        Returns:
            (item key -> (lowest buyout, units at it, seen ms) from `current` src local, item key -> (scan ms,
            [(price, stack, band, count)] by price)) from the newest complete local scan after since_ms.
        """
        prices = {key: (price, qty, at) for key, price, qty, at in self.conn.execute(
            "SELECT key, price, qty, at FROM current WHERE market = ? AND src = 'local' AND at > ? "
            "AND price IS NOT NULL ORDER BY key", (market, since_ms))}
        newest = self.conn.execute(
            "SELECT sid, at FROM scans WHERE market = ? AND src = 'local' AND kind = 'full' AND coverage >= 1 "
            "AND at > ? ORDER BY at DESC, scan_id LIMIT 1", (market, since_ms)).fetchone()
        books: dict[int, tuple[int, list[tuple[int, int, int, int]]]] = {}
        if newest:
            for key, price, stack, band, count in self.conn.execute(
                    "SELECT key, price, stack, band, count FROM book_entries WHERE sid = ? "
                    "ORDER BY key, price, stack, band", (newest[0],)):
                books.setdefault(key, (newest[1], []))[1].append((price, stack, band, count))
        return prices, books

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

    def observed_hours(self, market: str, from_hour: int, to_hour: int) -> dict[str, list[int]]:
        """Hours with a complete observation per source, for the Data coverage card (SPEC §3.5).

        Args:
            market: Market id.
            from_hour: First UTC hour number (floor(ms / 3600000)), inclusive.
            to_hour: Last UTC hour number, inclusive.

        Returns:
            Source -> its hours in the range, sorted; sources without a row are absent. One query on the
            observed_hours table, no candle reads.
        """
        hours: dict[str, list[int]] = {}
        for src, hour in self.conn.execute(
                "SELECT src, hour FROM observed_hours WHERE market = ? AND hour BETWEEN ? AND ? ORDER BY src, hour",
                (market, from_hour, to_hour)):
            hours.setdefault(src, []).append(hour)
        return hours

    def newest_partial_scan(self, market: str, from_ms: int, to_ms: int) -> tuple[int, float] | None:
        """The newest partial local scan in a time range, for the coverage card's rule text (SPEC §3.5).

        Args:
            market: Market id.
            from_ms: Start, inclusive.
            to_ms: End, inclusive.

        Returns:
            (time ms, coverage 0..1) of the newest local scan that is kind 'partial' or under full coverage
            (ties: scan id), or None.
        """
        row = self.conn.execute(
            "SELECT at, coverage FROM scans WHERE market = ? AND src = 'local' AND (kind = 'partial' OR coverage < 1) "
            "AND at BETWEEN ? AND ? ORDER BY at DESC, scan_id DESC LIMIT 1", (market, from_ms, to_ms)).fetchone()
        return None if row is None else (row[0], row[1])

    def complete_books(self, market: str, from_ms: int, to_ms: int) -> list[tuple[str, int, str]]:
        """Complete books of a market from every source in a time range, with each one's source (V39).

        Args:
            market: Market id.
            from_ms: Start, inclusive.
            to_ms: End, inclusive.

        Returns:
            (scan id, time ms, source: "local", "ahf" or a provider's name), oldest first (ties: scan id).
        """
        return [tuple(r) for r in self.conn.execute(
            "SELECT scan_id, at, src FROM scans WHERE market = ? AND kind = 'full' AND coverage >= 1 "
            "AND at BETWEEN ? AND ? ORDER BY at, scan_id", (market, from_ms, to_ms))]

    def scan_entries(self, scan_id: str) -> list[tuple[int, int, int, int, int]]:
        """One stored book's rows.

        Args:
            scan_id: The scan.

        Returns:
            (key, unit price, stack, band, count), sorted.
        """
        return [tuple(r) for r in self.conn.execute(
            "SELECT b.key, b.price, b.stack, b.band, b.count FROM book_entries b JOIN scans s ON s.sid = b.sid "
            "WHERE s.scan_id = ? ORDER BY b.key, b.price, b.stack, b.band", (scan_id,))]


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


def _best_per_time(rows: list[tuple], ranked: dict[str, int]) -> list[tuple]:
    """One value per time: the most granular source's, then the most trusted (SPEC §2.2, S9, SRC3, SRC9).

    Args:
        rows: (time ms, value, src) rows ordered by time.
        ranked: src -> rank, 0 most trusted ("local" first by default); orders sources of the same kind only, an
            unranked source after every ranked one, by name (collect.provider.source_precedence).

    Returns:
        One (time, value) per time, ascending.
    """
    best: list[tuple] = []
    for t, value, src in rows:
        order = source_precedence(src, ranked)
        if not best or best[-1][0] != t:
            best.append((t, value, order))
        elif order < best[-1][2]:
            best[-1] = (t, value, order)
    return [(t, value) for t, value, _ in best]


def _positions_by_day(rows: list[tuple]) -> dict[int, dict[int, float]]:
    """Stored new-listing positions, unpacked and summed per day over their sources (SPEC §3.3.2, BZ29).

    Args:
        rows: (day, "<position>:<units>,...") rows, by day.

    Returns:
        day -> position -> units.
    """
    out: dict[int, dict[int, float]] = {}
    for day, packed in rows:
        by_position = out.setdefault(day, {})
        for pair in filter(None, packed.split(",")):
            position, units = pair.split(":")
            by_position[int(position)] = by_position.get(int(position), 0.0) + float(units)
    return out


def _lifetimes_by_day(rows: list[tuple]) -> dict[int, tuple[float, float, float]]:
    """Stored ended-listing rows summed per day over their sources (SPEC §2.4).

    Args:
        rows: (day, ended, expired, matched) rows, by day.

    Returns:
        day -> (ended, expired, matched).
    """
    out: dict[int, tuple[float, float, float]] = {}
    for day, *values in rows:
        out[day] = tuple(a + b for a, b in zip(out.get(day, (0.0, 0.0, 0.0)), values))
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
        rows: (sid, price, stack, band, count) from the recent complete scans, ordered by price, stack, band.
        rank: sid -> (position, at ms), position 0 for the newest scan (ties: scan id).

    Returns:
        (at ms, [(price, stack, band, count), ...]) of the newest scan listing the item; None without rows.
    """
    if not rows:
        return None
    newest = min((row[0] for row in rows), key=lambda sid: rank[sid][0])
    return rank[newest][1], [(p, s, b, c) for sid, p, s, b, c in rows if sid == newest]
