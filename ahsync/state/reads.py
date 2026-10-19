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

    def reserves(self, char_ids: list[str]) -> dict[tuple[str, int], int]:
        """Items the characters keep for themselves.

        Args:
            char_ids: Character ids.

        Returns:
            (character id, item key) -> units kept; -1 keeps all.
        """

    def owned_units(self, char_ids: list[str]) -> dict[int, int]:
        """Unbound units owned across the characters, less their reserves.

        Args:
            char_ids: Character ids.

        Returns:
            Item key -> units.
        """

    def owned_lots(self, char_ids: list[str]) -> list[tuple[str, int, int]]:
        """Unbound units per character and item, less their reserves.

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

    def snapshot_times(self, char_ids: list[str]) -> dict[tuple[str, str], int]:
        """When each place of each character was last captured (SPEC §2.2.1 per-location times, D24).

        Args:
            char_ids: Character ids.

        Returns:
            (character id, place) -> capture time, UTC ms.
        """

    def character_sheets(self, char_ids: list[str]) -> list[tuple]:
        """What the addon captured about each character, for the Characters page (SPEC §3.5, D49).

        Args:
            char_ids: Character ids.

        Returns:
            (id, class, level, faction, market, gold copper, known recipes, updated ms, ((profession, skill, max), ...)
            by name) per character, by id.
        """

    def own_auctions(self, char_ids: list[str]) -> list[tuple[str, int, int, int, int, int, int | None]]:
        """Your auctions as the addon last saw them, with the deposit of the post behind each (SPEC §3.5 On the AH).

        Args:
            char_ids: Character ids.

        Returns:
            (char_id, key, price each copper, stack, band 1-4, auctions, deposit per auction copper or None when
            its post wasn't captured), by character, key, price, stack, band.
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

    def post_outcomes(self, char_ids: list[str], since_ms: int) -> list[tuple[int, str, int, int, int, str]]:
        """The characters' posts with an outcome, posted since a time (SPEC §3.3.3).

        Args:
            char_ids: Character ids.
            since_ms: Earliest post time.

        Returns:
            (item key, post id, price, posted at, resolved at, outcome), by key then resolution time.
        """

    def keep_times(self) -> dict[int, int]:
        """When you last chose Keep for items (SPEC §3.4.2).

        Returns:
            Item key -> set_at ms, for items whose live rule is "keep".
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
        """Unbound units you own in bags, bank and mail across the characters, less what each keeps
        for itself (SPEC §3.4B owned, §3.4.3, R20).

        Args:
            char_ids: Character ids.

        Returns:
            Item key -> free units; items with none free are left out.
        """
        units: dict[int, int] = {}
        for _, key, qty in self.owned_lots(char_ids):
            units[key] = units.get(key, 0) + qty
        return dict(sorted(units.items()))

    def reserves(self, char_ids: list[str]) -> dict[tuple[str, int], int]:
        """Items the characters keep for themselves (SPEC §3.4.3).

        Args:
            char_ids: Character ids.

        Returns:
            (character id, item key) -> units kept; -1 keeps all.
        """
        if not char_ids:
            return {}
        marks = ",".join("?" * len(char_ids))
        return {(c, k): q for c, k, q in self.conn.execute(
            f"SELECT char_id, key, qty FROM reserves WHERE char_id IN ({marks}) ORDER BY char_id, key",
            tuple(char_ids))}

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

    def post_outcomes(self, char_ids: list[str], since_ms: int) -> list[tuple[int, str, int, int, int, str]]:
        """The characters' posts with an outcome, posted since a time (SPEC §3.3.3).

        Args:
            char_ids: Character ids.
            since_ms: Earliest post time.

        Returns:
            (item key, post id, price, posted at, resolved at, outcome), by key then resolution time.
        """
        if not char_ids:
            return []
        marks = ",".join("?" * len(char_ids))
        return [tuple(r) for r in self.conn.execute(
            f"SELECT key, post_id, price, at, resolved_at, outcome FROM posts WHERE char_id IN ({marks}) AND at >= ? "
            f"AND outcome IS NOT NULL AND resolved_at IS NOT NULL ORDER BY key, resolved_at, post_id",
            (*char_ids, since_ms))]

    def keep_times(self) -> dict[int, int]:
        """When you last chose Keep for items (SPEC §3.4.2).

        Returns:
            Item key -> set_at ms, for items whose live rule is "keep".
        """
        return dict(self.conn.execute("SELECT key, set_at FROM item_rules WHERE rule = 'keep' ORDER BY key").fetchall())

    def owned_lots(self, char_ids: list[str]) -> list[tuple[str, int, int]]:
        """Unbound units per character and item, bags, bank and mail together, less what the character
        keeps for itself (SPEC §3.4C.1, §3.4.3, R10, R20).

        Bound items are never lots: they can't be sold or mailed. Neither are kept units.

        Args:
            char_ids: Character ids.

        Returns:
            (character id, item key, free units), sorted by character then key; none free is left out.
        """
        if not char_ids:
            return []
        marks = ",".join("?" * len(char_ids))
        rows = self.conn.execute(
            f"SELECT i.char_id, i.key, SUM(i.qty) - COALESCE(MAX(r.qty), 0), MAX(r.qty) = -1 FROM inventory i "
            f"LEFT JOIN reserves r ON r.char_id = i.char_id AND r.key = i.key "
            f"WHERE i.bound = 0 AND i.char_id IN ({marks}) GROUP BY i.char_id, i.key ORDER BY i.char_id, i.key",
            tuple(char_ids))
        return [(c, k, free) for c, k, free, keep_all in rows if free > 0 and not keep_all]

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

    def snapshot_times(self, char_ids: list[str]) -> dict[tuple[str, str], int]:
        """When each place of each character was last captured (SPEC §2.2.1 per-location times, D24).

        Args:
            char_ids: Character ids.

        Returns:
            (character id, place) -> capture time, UTC ms.
        """
        if not char_ids:
            return {}
        marks = ",".join("?" * len(char_ids))
        return {(c, loc): at for c, loc, at in self.conn.execute(
            f"SELECT char_id, location, at FROM snapshots WHERE char_id IN ({marks}) AND location != 'auctions'",
            tuple(char_ids))}

    def character_sheets(self, char_ids: list[str]) -> list[tuple]:
        """What the addon captured about each character, for the Characters page (SPEC §3.5, D49).

        Args:
            char_ids: Character ids.

        Returns:
            (id, class, level, faction, market, gold copper, known recipes, updated ms, ((profession, skill, max), ...)
            by name) per character, by id.
        """
        if not char_ids:
            return []
        marks = ",".join("?" * len(char_ids))
        profs: dict[str, list[tuple]] = {}
        for c, name, skill, top in self.conn.execute(
                f"SELECT char_id, profession, skill, max_skill FROM professions WHERE char_id IN ({marks}) "
                f"ORDER BY char_id, profession", tuple(char_ids)):
            profs.setdefault(c, []).append((name, skill, top))
        return [(c, cls, level, faction, market, gold, recipes, updated, tuple(profs.get(c, ())))
                for c, cls, level, faction, market, gold, updated, recipes in self.conn.execute(
                    f"SELECT id, class, level, faction, market, gold, updated, "
                    f"(SELECT COUNT(*) FROM known_recipes k WHERE k.char_id = c.id) FROM characters c "
                    f"WHERE id IN ({marks}) ORDER BY id", tuple(char_ids))]

    def cost_basis(self) -> dict[int, tuple[int, int]]:
        """What you paid for items, account-wide (SPEC §3.2 cost_basis).

        Returns:
            Item key -> (units bought, total cost copper), for items with any units bought.
        """
        return {key: (qty, total) for key, qty, total in self.conn.execute(
            "SELECT key, qty, total_cost FROM cost_basis WHERE qty > 0 ORDER BY key")}

    def own_auctions(self, char_ids: list[str]) -> list[tuple[str, int, int, int, int, int, int | None]]:
        """Your auctions as the addon last saw them, with the deposit of the post behind each (SPEC §3.5 On the AH).

        Args:
            char_ids: Character ids.

        Returns:
            (char_id, key, price each copper, stack, band 1-4, auctions, deposit per auction copper or None when
            its post wasn't captured), by character, key, price, stack, band.
        """
        if not char_ids:
            return []
        marks = ",".join("?" * len(char_ids))
        return [tuple(r) for r in self.conn.execute(
            f"SELECT a.char_id, a.key, a.price, a.stack, a.band, COALESCE(a.count, 1), "
            f"(SELECT p.deposit FROM posts p WHERE p.char_id = a.char_id AND p.key = a.key AND p.stack = a.stack "
            f" AND p.price = a.price AND p.outcome IS NULL ORDER BY p.at DESC LIMIT 1) "
            f"FROM own_auctions a WHERE a.char_id IN ({marks}) ORDER BY a.char_id, a.key, a.price, a.stack, a.band",
            tuple(char_ids))]

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

    def live_rule_rows(self) -> list[tuple[int, str, int, str]]:
        """Your live item rules with when and where each was set, for the Rules page (SPEC §3.4.2, D50).

        Returns:
            (item key, rule, set_at ms, source "addon" | "dashboard"), cleared ones left out, by key.
        """
        return [tuple(r) for r in self.conn.execute(
            "SELECT key, rule, set_at, source FROM item_rules WHERE rule != 'none' ORDER BY key")]

    def rule_rows(self) -> list[tuple[int, str, int]]:
        """Every item rule with its time, cleared ones ("none") included, for PortfolioPlan.lua (SPEC §3.4.2).

        Returns:
            (item key, rule, set_at ms), by key.
        """
        return [tuple(r) for r in self.conn.execute("SELECT key, rule, set_at FROM item_rules ORDER BY key")]
