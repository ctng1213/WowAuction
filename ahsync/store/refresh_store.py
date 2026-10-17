"""Reads and staged writes for the full refresh, and the published views (SPEC §3.1.1, §3.5, §5.2).

`Database` inherits `RefreshStore`. A full refresh writes `item_stats_new` and
`views_new`, then `publish_refresh` swaps them in with one transaction, so the
dashboard never sees half a refresh and a failure leaves the previous numbers live.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


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
class RecipeRow:
    """A recipe with its reagents and the characters who know it."""

    profession: str
    spell: int
    product: int | None
    yield_min: int
    yield_max: int
    cooldown_s: int | None
    reagents: tuple[tuple[int, int], ...]
    crafters: tuple[str, ...]


class RefreshStore:
    """Mixin with the full refresh's reads and staged writes; needs `self.conn` and `self.transaction()`."""

    conn: sqlite3.Connection

    # ------------------------------------------------------------ reads

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

    def characters_in(self, market: str) -> list[tuple[str, int]]:
        """Your characters on a market and their gold.

        Args:
            market: Market id.

        Returns:
            (character id, gold copper), sorted by id.
        """
        return [(r[0], r[1] or 0) for r in self.conn.execute(
            "SELECT id, gold FROM characters WHERE market = ? ORDER BY id", (market,))]

    def best_skill(self, char_ids: list[str], profession: str) -> int | None:
        """The highest skill any of the characters has in a profession.

        Args:
            char_ids: Character ids.
            profession: enUS profession name.

        Returns:
            The skill, or None when none of them has it.
        """
        if not char_ids:
            return None
        marks = ",".join("?" * len(char_ids))
        row = self.conn.execute(f"SELECT MAX(skill) FROM professions WHERE profession = ? AND char_id IN ({marks})",
                                (profession, *char_ids)).fetchone()
        return row[0]

    def known_recipes(self, char_ids: list[str]) -> list[RecipeRow]:
        """Recipes known by any of the characters, with reagents and crafters.

        Args:
            char_ids: Character ids.

        Returns:
            Recipes sorted by spell id; crafters sorted.
        """
        if not char_ids:
            return []
        marks = ",".join("?" * len(char_ids))
        rows = self.conn.execute(
            f"SELECT r.profession, r.spell_id, r.product_key, r.yield_min, r.yield_max, r.cooldown_s, "
            f"group_concat(DISTINCT k.char_id) FROM recipes r JOIN known_recipes k ON k.spell_id = r.spell_id "
            f"WHERE k.char_id IN ({marks}) GROUP BY r.profession, r.spell_id ORDER BY r.spell_id, r.profession",
            tuple(char_ids)).fetchall()
        out = []
        for profession, spell, product, ymin, ymax, cooldown, crafters in rows:
            reagents = tuple(tuple(x) for x in self.conn.execute(
                "SELECT key, count FROM reagents WHERE profession = ? AND spell_id = ? ORDER BY key",
                (profession, spell)))
            out.append(RecipeRow(profession, spell, product, ymin or 1, ymax or ymin or 1, cooldown, reagents,
                                 tuple(sorted(crafters.split(",")))))
        return out

    def owned_units(self, char_ids: list[str]) -> dict[int, int]:
        """Unbound units you own in bags, bank and mail across the characters (SPEC §3.4B owned).

        Args:
            char_ids: Character ids.

        Returns:
            Item key -> units.
        """
        if not char_ids:
            return {}
        marks = ",".join("?" * len(char_ids))
        return dict(self.conn.execute(
            f"SELECT key, SUM(qty) FROM inventory WHERE bound = 0 AND char_id IN ({marks}) "
            f"GROUP BY key ORDER BY key", tuple(char_ids)).fetchall())

    def vendor_reagent_keys(self) -> set[int]:
        """Items a merchant was seen selling in unlimited supply (SPEC §3.4B vendor).

        Returns:
            Item keys.
        """
        return {r[0] for r in self.conn.execute("SELECT key FROM vendor_reagents")}

    # ------------------------------------------------------------ staged writes and publish

    def stage_item_stats(self, market: str, key: int, stats: dict, updated_ms: int) -> None:
        """Write one item's statistics into the staging table.

        Args:
            market: Market id.
            key: Item key.
            stats: JSON-serializable statistics.
            updated_ms: Refresh time.
        """
        self.conn.execute("INSERT OR REPLACE INTO item_stats_new(market, key, stats, updated) VALUES (?, ?, ?, ?)",
                          (market, key, json.dumps(stats, sort_keys=True), updated_ms))

    def stage_view(self, page: str, market: str, body: dict, built_ms: int) -> None:
        """Write one page's precomputed JSON into the staging table.

        Args:
            page: Page name, e.g. "disenchant".
            market: Market id.
            body: JSON-serializable payload (SPEC §3.5 dashboard data).
            built_ms: Refresh time.
        """
        self.conn.execute("INSERT OR REPLACE INTO views_new(page, market, body, built) VALUES (?, ?, ?, ?)",
                          (page, market, json.dumps(body, sort_keys=True), built_ms))

    def publish_refresh(self, markets: list[str]) -> None:
        """Swap the staged statistics and views in for these markets, in one transaction (SPEC §5.2 rule 3).

        Args:
            markets: The markets this refresh rebuilt.
        """
        with self.transaction():
            for market in markets:
                for live, staged, cols in (("item_stats", "item_stats_new", "market, key, stats, updated"),
                                           ("views", "views_new", "page, market, body, built")):
                    self.conn.execute(f"DELETE FROM {live} WHERE market = ?", (market,))
                    self.conn.execute(f"INSERT INTO {live}({cols}) SELECT {cols} FROM {staged} WHERE market = ?",
                                      (market,))
                    self.conn.execute(f"DELETE FROM {staged} WHERE market = ?", (market,))

    def skip_action(self, action_id: str, at_ms: int) -> bool:
        """Mark a plan action skipped from the dashboard (SPEC §3.4.1).

        Args:
            action_id: The action's id.
            at_ms: When it was skipped.

        Returns:
            True when an open action (pending, late, partial or missed) was changed.
        """
        with self.transaction():
            cursor = self.conn.execute(
                "UPDATE actions SET status = 'skipped', done_at = ? WHERE action_id = ? "
                "AND status IN ('pending', 'late', 'partial', 'missed')", (at_ms, action_id))
        return cursor.rowcount > 0

    def view(self, page: str, market: str) -> tuple[dict, int] | None:
        """A published page payload, as the dashboard serves it.

        Args:
            page: Page name.
            market: Market id.

        Returns:
            (payload, built ms), or None before the first refresh.
        """
        row = self.conn.execute("SELECT body, built FROM views WHERE page = ? AND market = ?",
                                (page, market)).fetchone()
        return (json.loads(row[0]), row[1]) if row else None
