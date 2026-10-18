"""Reads of imported character state (SPEC §5.0): the `StateReader` contract and its SQLite implementation."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Protocol

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


class StateReader(Protocol):
    """What recommendations may read about your characters (SPEC §5.0 contracts)."""

    def characters_in(self, market: str) -> list[tuple[str, int]]:
        """Your characters on a market and their gold.

        Args:
            market: Market id.

        Returns:
            (character id, gold copper), sorted by id.
        """

    def best_skill(self, char_ids: list[str], profession: str) -> int | None:
        """The highest skill any of the characters has in a profession.

        Args:
            char_ids: Character ids.
            profession: enUS profession name.

        Returns:
            The skill, or None.
        """

    def skill_of(self, char_id: str, profession: str) -> int | None:
        """A character's skill in a profession.

        Args:
            char_id: Character id.
            profession: enUS profession name.

        Returns:
            The skill, or None.
        """

    def known_recipes(self, char_ids: list[str]) -> list[RecipeRow]:
        """Recipes the characters know.

        Args:
            char_ids: Character ids.

        Returns:
            Recipes with reagents and crafters.
        """

    def owned_units(self, char_ids: list[str]) -> dict[int, int]:
        """Unbound units owned across the characters.

        Args:
            char_ids: Character ids.

        Returns:
            Item key -> units.
        """

    def owned_lots(self, char_ids: list[str]) -> list[tuple[str, int, int]]:
        """Unbound units per character and item.

        Args:
            char_ids: Character ids.

        Returns:
            (character id, item key, units), sorted by character then key.
        """

    def holdings(self, char_ids: list[str]) -> list[tuple[str, str, int, int, bool]]:
        """Every inventory row of the characters.

        Args:
            char_ids: Character ids.

        Returns:
            (character id, place, item key, units, bound), sorted by character, place, key, bound.
        """

    def cost_basis(self) -> dict[int, tuple[int, int]]:
        """What you paid for items, account-wide.

        Returns:
            Item key -> (units bought, total cost copper).
        """

    def item_rules(self) -> dict[int, str]:
        """Your live item rules.

        Returns:
            Item key -> "no_buy" | "ignore" | "keep".
        """

    def vendor_reagent_keys(self) -> set[int]:
        """Items a merchant was seen selling in unlimited supply.

        Returns:
            Item keys.
        """


class StateReads:
    """SQLite implementation of the state reads recommendations use (mixed into db.sqlite.Database)."""

    conn: sqlite3.Connection

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

    def skill_of(self, char_id: str, profession: str) -> int | None:
        """A character's skill in a profession.

        Args:
            char_id: Character id.
            profession: enUS profession name.

        Returns:
            The skill, or None when the character doesn't have it.
        """
        row = self.conn.execute("SELECT skill FROM professions WHERE char_id = ? AND profession = ?",
                                (char_id, profession)).fetchone()
        return row[0] if row else None

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

    def item_rules(self) -> dict[int, str]:
        """Your live item rules, set on the dashboard or in game; cleared rules ("none") are left out (SPEC §3.4.2).

        Returns:
            Item key -> "no_buy" | "ignore" | "keep".
        """
        return dict(self.conn.execute("SELECT key, rule FROM item_rules WHERE rule != 'none' ORDER BY key").fetchall())

    def owned_lots(self, char_ids: list[str]) -> list[tuple[str, int, int]]:
        """Unbound units per character and item, bags, bank and mail together (SPEC §3.4C.1, R10).

        Bound items are never lots: they can't be sold or mailed.

        Args:
            char_ids: Character ids.

        Returns:
            (character id, item key, units), sorted by character then key.
        """
        if not char_ids:
            return []
        marks = ",".join("?" * len(char_ids))
        return [tuple(r) for r in self.conn.execute(
            f"SELECT char_id, key, SUM(qty) FROM inventory WHERE bound = 0 AND char_id IN ({marks}) "
            f"GROUP BY char_id, key ORDER BY char_id, key", tuple(char_ids))]

    def holdings(self, char_ids: list[str]) -> list[tuple[str, str, int, int, bool]]:
        """Every inventory row of the characters, bound ones too (SPEC §3.5 Inventory).

        Args:
            char_ids: Character ids.

        Returns:
            (character id, place "bags" | "bank" | "mail", item key, units, bound), sorted by
            character, place, key, then bound.
        """
        if not char_ids:
            return []
        marks = ",".join("?" * len(char_ids))
        return [(c, loc, key, qty, bool(bound)) for c, loc, key, qty, bound in self.conn.execute(
            f"SELECT char_id, location, key, qty, bound FROM inventory WHERE char_id IN ({marks}) "
            f"ORDER BY char_id, location, key, bound", tuple(char_ids))]

    def cost_basis(self) -> dict[int, tuple[int, int]]:
        """What you paid for items, account-wide (SPEC §3.2 cost_basis).

        Returns:
            Item key -> (units bought, total cost copper), for items with any units bought.
        """
        return {key: (qty, total) for key, qty, total in self.conn.execute(
            "SELECT key, qty, total_cost FROM cost_basis WHERE qty > 0 ORDER BY key")}

    def characters(self) -> list[tuple[str, str, str, str | None, int | None]]:
        """Every character, on any market or none (SPEC §3.5 Status).

        Returns:
            (character id, realm, faction, market or None, updated ms or None), by id.
        """
        return [tuple(r) for r in self.conn.execute(
            "SELECT id, realm, faction, market, updated FROM characters ORDER BY id")]

    def partial_scan_keys(self, market: str, since_ms: int) -> set[int]:
        """Items in your targeted (partial) AH scans since a time (SPEC §3.5 tier 2, EX1).

        Full scans are left out: they cover the whole AH, so they would shortlist everything.

        Args:
            market: Market id.
            since_ms: Oldest scan time, ms.

        Returns:
            Item keys.
        """
        return {key for (key,) in self.conn.execute(
            "SELECT DISTINCT e.key FROM book_entries e JOIN scans s ON s.scan_id = e.scan_id "
            "WHERE s.market = ? AND s.src = 'local' AND s.kind = 'partial' AND s.at >= ?", (market, since_ms))}
