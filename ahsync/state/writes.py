"""SavedVariables ingest writes (SPEC §2.2.1, §3.2), split off store/db.py by responsibility.

`Database` inherits `IngestWrites`. Each write reports whether it changed a row, so
reading the same file again changes nothing (FM12) and the caller knows which items
went dirty (§3.1.1). The merge rules make the result independent of the order files
are read in (FI2). Writes don't open transactions: ingest/apply.py wraps one file in one.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager


class IngestWrites:
    """Mixin with the SavedVariables upserts; needs `self.conn`, an open SQLite connection."""

    conn: sqlite3.Connection

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """Make the writes inside one row's unit: on an exception only they are undone (SPEC §5.2 rule 6).

        Yields:
            Nothing. Use inside transaction(); the exception is re-raised.
        """
        self.conn.execute("SAVEPOINT ingest_row")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK TO ingest_row")
            self.conn.execute("RELEASE ingest_row")
            raise
        self.conn.execute("RELEASE ingest_row")

    def _upsert(self, table: str, row: dict[str, object], key: tuple[str, ...], merge: dict[str, str],
                when: str = "1") -> bool:
        """Insert a row, or merge it into the row with the same key.

        Args:
            table: Table name (trusted, from code).
            row: Column -> value.
            key: The conflict columns.
            merge: Column -> SQL expression for its merged value (may use `excluded.`); empty keeps the old row.
            when: SQL condition under which the merge is applied.

        Returns:
            True when a row was inserted or changed.
        """
        columns = list(row)
        action = f"UPDATE SET {', '.join(f'{c} = {e}' for c, e in merge.items())} WHERE {when}" if merge else "NOTHING"
        sql = (f"INSERT INTO {table}({', '.join(columns)}) VALUES({', '.join('?' * len(columns))}) "
               f"ON CONFLICT({', '.join(key)}) DO {action}")
        return self.conn.execute(sql, [row[c] for c in columns]).rowcount > 0

    def upsert_character(self, row: dict[str, object]) -> bool:
        """Store a character: the newest `updated` wins, level only rises, market follows the config.

        Args:
            row: characters columns (id, name, realm, faction, market, class, level, gold, updated).

        Returns:
            True when the row was inserted or changed.
        """
        newer = "excluded.updated > characters.updated"
        merge = {c: f"CASE WHEN {newer} THEN excluded.{c} ELSE {c} END"
                 for c in ("name", "realm", "faction", "class", "gold", "updated")}
        merge |= {"level": "MAX(level, excluded.level)", "market": "excluded.market"}
        when = f"{newer} OR excluded.level > characters.level OR excluded.market IS NOT characters.market"
        return self._upsert("characters", row, ("id",), merge, when)

    def character_updated(self, char_id: str) -> int | None:
        """A stored character's `updated` time.

        Args:
            char_id: "Name-Realm".

        Returns:
            UTC ms, or None when the character isn't stored.
        """
        row = self.conn.execute("SELECT updated FROM characters WHERE id = ?", (char_id,)).fetchone()
        return row[0] if row else None

    def replace_professions(self, char_id: str, skills: dict[str, tuple[int, int]]) -> bool:
        """Replace a character's professions with a file's (SPEC §2.2.1 "follow the file").

        Args:
            char_id: "Name-Realm".
            skills: Profession name -> (skill, max skill).

        Returns:
            True when any profession was added, removed or changed.
        """
        rows = {(name, skill): max_skill for name, (skill, max_skill) in skills.items()}
        return bool(self._replace_rows("professions", {"char_id": char_id}, ("profession", "skill", "max_skill"),
                                       rows))

    def replace_reserves(self, char_id: str, items: dict[int, int]) -> set[int]:
        """Replace a character's reserves with a file's (SPEC §3.4.3, S14).

        Args:
            char_id: "Name-Realm".
            items: Item key -> units kept (-1 keeps all); empty clears them.

        Returns:
            Item keys whose reserve was added, removed or changed.
        """
        return self._replace_rows("reserves", {"char_id": char_id}, ("key", "qty"), {(k,): q for k, q in items.items()})

    def upsert_recipe(self, row: dict[str, object], reagents: dict[int, int]) -> bool:
        """Store a recipe definition and its reagents; a known skill or cooldown is never replaced by unknown.

        Args:
            row: recipes columns (profession, spell_id, product_key, yield_min, yield_max, skill_req, cooldown_s);
                skill_req and cooldown_s are None when unknown.
            reagents: Item key -> count, replacing the recipe's stored reagents.

        Returns:
            True when the recipe or its reagents were inserted or changed.
        """
        merge = {c: f"excluded.{c}" for c in ("product_key", "yield_min", "yield_max")}
        merge |= {c: f"COALESCE(excluded.{c}, {c})" for c in ("skill_req", "cooldown_s")}
        when = " OR ".join(f"{e} IS NOT recipes.{c}" for c, e in merge.items())
        changed = self._upsert("recipes", row, ("profession", "spell_id"), merge, when)
        recipe = {"profession": row["profession"], "spell_id": row["spell_id"]}
        return bool(self._replace_rows("reagents", recipe, ("key", "count"), reagents)) or changed

    def replace_known_recipes(self, char_id: str, spell_ids: set[int]) -> set[int]:
        """Replace a character's known recipes with a file's (SPEC §2.2.1 "follow the file").

        Args:
            char_id: "Name-Realm".
            spell_ids: The recipes' spell ids.

        Returns:
            Spell ids added or removed.
        """
        old = {r[0] for r in self.conn.execute("SELECT spell_id FROM known_recipes WHERE char_id = ?", (char_id,))}
        for spell_id in sorted(old - spell_ids):
            self.conn.execute("DELETE FROM known_recipes WHERE char_id = ? AND spell_id = ?", (char_id, spell_id))
        self.conn.executemany("INSERT INTO known_recipes(char_id, spell_id) VALUES(?, ?)",
                              [(char_id, spell_id) for spell_id in sorted(spell_ids - old)])
        return old ^ spell_ids

    def add_vendor_reagent(self, key: int) -> bool:
        """Record an item a merchant was seen selling; add-only.

        Args:
            key: AHF item key.

        Returns:
            True when it was new.
        """
        return self._upsert("vendor_reagents", {"key": key}, ("key",), {})

    def _replace_rows(self, table: str, scope: dict[str, object], columns: tuple[str, ...],
                      rows: dict[tuple, int], extra: dict[str, object] | None = None) -> set[int]:
        """Replace every row of one scope (a character's place, a recipe) with new rows.

        Args:
            table: Table name (trusted, from code).
            scope: Column -> value that selects the rows replaced.
            columns: The row columns; the first is the item key and the last the quantity.
            rows: Row key (every column but the last) -> quantity.
            extra: Column -> value written on every new row but not compared (e.g. `seen`).

        Returns:
            Item keys whose rows were added, removed or changed.
        """
        where, params = " AND ".join(f"{c} = ?" for c in scope), list(scope.values())
        old = {tuple(r[:-1]): r[-1]
               for r in self.conn.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE {where}", params)}
        if old == rows:
            return set()
        extra = extra or {}
        names = [*scope, *extra, *columns]
        self.conn.execute(f"DELETE FROM {table} WHERE {where}", params)
        self.conn.executemany(f"INSERT INTO {table}({', '.join(names)}) VALUES({', '.join('?' * len(names))})",
                              [(*params, *extra.values(), *k, n) for k, n in sorted(rows.items())])
        return {k[0] for k, _ in old.items() ^ rows.items()}

    def replace_snapshot(self, char_id: str, location: str, at_ms: int, rows: dict[tuple, int]) -> set[int] | None:
        """Replace one place's snapshot, unless the stored one is as new or newer (SPEC §2.2.1).

        Args:
            char_id: "Name-Realm".
            location: 'bags', 'bank' or 'mail' (table inventory; row key (key, bound 0/1)) or 'auctions'
                (table own_auctions; row key (key, price, stack, band)).
            at_ms: When the snapshot was captured.
            rows: Row key -> units (inventory qty, or auction count).

        Returns:
            Item keys whose rows changed (possibly none), or None when the snapshot was older and not applied.
        """
        if not self._upsert("snapshots", {"char_id": char_id, "location": location, "at": at_ms},
                            ("char_id", "location"), {"at": "excluded.at"}, "excluded.at > snapshots.at"):
            return None
        if location == "auctions":
            changed = self._replace_rows("own_auctions", {"char_id": char_id},
                                         ("key", "price", "stack", "band", "count"), rows, {"seen": at_ms})
            # An unchanged newer snapshot still proves the auctions are up: refresh when they were last seen.
            self.conn.execute("UPDATE own_auctions SET seen = ? WHERE char_id = ?", (at_ms, char_id))
            return changed
        return self._replace_rows("inventory", {"char_id": char_id, "location": location},
                                  ("key", "bound", "qty"), rows)

    def upsert_event(self, table: str, row: dict[str, object]) -> bool:
        """Store a captured event by its id (SPEC §5.2 rule 2).

        Posts and purchases carry `confirmed`: an attempt is stored with 0, and a later copy with 1 upgrades it
        with that copy's fields; a confirmed row is never downgraded. Other events never change once stored.

        Args:
            table: 'posts', 'purchases', 'crafts', 'auction_mail', 'payouts' or 'mail_sent'.
            row: The table's columns, including event_id (and post_id for posts).

        Returns:
            True when the row was inserted or changed.

        Raises:
            ValueError: Not an event table.
        """
        if table not in ("posts", "purchases", "crafts", "auction_mail", "payouts", "mail_sent"):
            raise ValueError(f"not an event table: {table}")
        key = ("post_id",) if table == "posts" else ("event_id",)
        merge = {c: f"excluded.{c}" for c in row if c not in key} if "confirmed" in row else {}
        return self._upsert(table, row, key, merge, f"excluded.confirmed > {table}.confirmed")

    def record_skip(self, event_id: str, char_id: str, action_id: str, at_ms: int) -> int | None:
        """Keep a skip event, and mark its action skipped if it exists and is still open (SPEC §3.4.1).

        Args:
            event_id: The skip event's id.
            char_id: "Name-Realm".
            action_id: The action skipped, from PortfolioPlan.lua.
            at_ms: When it was skipped.

        Returns:
            The action's item key when an action changed to skipped now, else None.
        """
        self._upsert("skips", {"event_id": event_id, "char_id": char_id, "action_id": action_id, "at": at_ms},
                     ("event_id",), {})
        row = self.conn.execute("SELECT key FROM actions WHERE action_id = ? AND status IN "
                                "('pending', 'late', 'partial', 'missed')", (action_id,)).fetchone()
        if row is None:
            return None
        self.conn.execute("UPDATE actions SET status = 'skipped' WHERE action_id = ?", (action_id,))
        return row[0]

    def add_item_names(self, rows: Iterable[tuple[int, int, str, int]]) -> int:
        """Name items AHF's item list doesn't have, e.g. soulbound ones (SPEC §2.2.1 itemNames, S13).

        An addon-named row has no `updated` time: AHF's /items rows always replace it, and it
        never replaces theirs.

        Args:
            rows: (item key, base item id, name, quality).

        Returns:
            Rows inserted or renamed.
        """
        count = 0
        for key, item_id, name, quality in rows:
            count += self.conn.execute(
                "INSERT INTO items(key, item_id, name, quality) VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "name = excluded.name, quality = excluded.quality WHERE items.updated IS NULL "
                "AND (items.name IS NOT excluded.name OR items.quality IS NOT excluded.quality)",
                (key, item_id, name, quality)).rowcount
        return count

    def upsert_item_rule(self, key: int, rule: str, set_at_ms: int, source: str) -> bool:
        """Store an item rule if it is newer than the stored one; 'none' is a clear (SPEC §3.4.2, O10, O18).

        Args:
            key: AHF item key.
            rule: 'no_buy', 'ignore', 'keep' or 'none'.
            set_at_ms: When it was set.
            source: 'addon' or 'dashboard'.

        Returns:
            True when the row was inserted or changed. Equal times are settled by the rule name, so the
            result doesn't depend on which side was read first.
        """
        row = {"key": key, "rule": rule, "set_at": set_at_ms, "source": source}
        return self._upsert("item_rules", row, ("key",), {c: f"excluded.{c}" for c in ("rule", "set_at", "source")},
                            "excluded.set_at > item_rules.set_at OR "
                            "(excluded.set_at = item_rules.set_at AND excluded.rule > item_rules.rule)")

    def character_market(self, char_id: str) -> str | None:
        """A stored character's market.

        Args:
            char_id: "Name-Realm".

        Returns:
            The AHF market id, or None when the character or its market is unknown.
        """
        row = self.conn.execute("SELECT market FROM characters WHERE id = ?", (char_id,)).fetchone()
        return row[0] if row else None

    def recipe_item_keys(self, spell_id: int) -> set[int]:
        """Every item a recipe makes or uses, in any profession.

        Args:
            spell_id: The recipe's spell id.

        Returns:
            Product and reagent keys (empty when the recipe is unknown).
        """
        rows = self.conn.execute("SELECT product_key FROM recipes WHERE spell_id = ? AND product_key IS NOT NULL "
                                 "UNION SELECT key FROM reagents WHERE spell_id = ?", (spell_id, spell_id))
        return {r[0] for r in rows}

    def meta_with_prefix(self, prefix: str) -> dict[str, str]:
        """Every meta value whose name starts with a prefix.

        Args:
            prefix: Name prefix, e.g. "savedvars_ingested_until:" (no SQL wildcards needed).

        Returns:
            Name without the prefix -> value.
        """
        rows = self.conn.execute("SELECT name, value FROM meta WHERE substr(name, 1, ?) = ? ORDER BY name",
                                 (len(prefix), prefix))
        return {name[len(prefix):]: value for name, value in rows}
