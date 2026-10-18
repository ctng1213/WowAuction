"""The recommendation side's store (SPEC §3.5): dashboard pages and plan-action skips, mixed into db.sqlite.Database."""
from __future__ import annotations

import json
import sqlite3

class RecommendStore:
    """Staged dashboard pages, the published page read, and skipping an action; needs `self.conn`."""

    conn: sqlite3.Connection

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

    def stage_plan(self, rows: list[tuple]) -> None:
        """Write a market's new plan actions into the staging table (published by publish_refresh).

        Args:
            rows: From recommend.plan.plan_rows.
        """
        self.conn.executemany(
            "INSERT OR REPLACE INTO plan_new(action_id, run_at, slot_ms, char_id, kind, key, qty, price, expected_net, "
            "conf, status, done_qty, done_at, parent_id, window_start_ms, window_end_ms, anchor_net, anchor_window_ms, "
            "replaces_id, week_start_ms, peak_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows)

    def open_actions(self, char_ids: list[str]) -> list[tuple]:
        """The characters' pending and late actions, for the status pass (SPEC §3.4.1).

        Args:
            char_ids: Character ids.

        Returns:
            (action_id, kind, char_id, key, qty, slot_ms, window_end_ms, anchor_net, anchor_window_ms,
            week_start_ms, status), by time then id.
        """
        if not char_ids:
            return []
        marks = ",".join("?" * len(char_ids))
        return [tuple(r) for r in self.conn.execute(
            f"SELECT action_id, kind, char_id, key, qty, slot_ms, window_end_ms, anchor_net, anchor_window_ms, "
            f"week_start_ms, status FROM actions WHERE status IN ('pending', 'late') AND char_id IN ({marks}) "
            f"ORDER BY slot_ms, action_id", tuple(char_ids))]

    def addon_actions(self, char_ids: list[str]) -> list[tuple]:
        """The characters' open actions as PortfolioPlan.lua exports them (SPEC §3.5, EX3).

        Args:
            char_ids: Character ids.

        Returns:
            (action_id, char_id, kind, key, qty, price, slot_ms, expected_net, parent_id, window_end_ms, status,
            conf = the planned chance to sell, peak_ms), pending and late only, by time then id.
        """
        if not char_ids:
            return []
        marks = ",".join("?" * len(char_ids))
        return [tuple(r) for r in self.conn.execute(
            f"SELECT action_id, char_id, kind, key, qty, price, slot_ms, expected_net, parent_id, window_end_ms, status, "
            f"conf, peak_ms FROM actions WHERE status IN ('pending', 'late') AND char_id IN ({marks}) ORDER BY slot_ms, action_id",
            tuple(char_ids))]

    def set_action_status(self, action_id: str, status: str) -> None:
        """Change one action's status (the status pass: late or missed).

        Args:
            action_id: The action.
            status: Its new status.
        """
        self.conn.execute("UPDATE actions SET status = ? WHERE action_id = ?", (status, action_id))

    def add_action(self, row: tuple) -> None:
        """Add one action straight to the live plan (a missed post's replacement, SPEC §3.4.0).

        Args:
            row: An `actions` row, as recommend.plan.plan_rows makes them.
        """
        self.conn.execute(
            "INSERT OR REPLACE INTO actions(action_id, run_at, slot_ms, char_id, kind, key, qty, price, expected_net, "
            "conf, status, done_qty, done_at, parent_id, window_start_ms, window_end_ms, anchor_net, anchor_window_ms, "
            "replaces_id, week_start_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)

    def history_actions(self, char_ids: list[str], from_ms: int, to_ms: int) -> list[tuple]:
        """Kept plan actions (done, partial, missed, skipped) of some characters in a time range (SPEC §3.4.1).

        Args:
            char_ids: Character ids.
            from_ms: Start, inclusive.
            to_ms: End, exclusive.

        Returns:
            (action_id, parent_id, kind, char_id, key, qty, price, slot_ms, expected_net, status), by time then id.
        """
        if not char_ids:
            return []
        marks = ",".join("?" * len(char_ids))
        return [tuple(r) for r in self.conn.execute(
            f"SELECT action_id, parent_id, kind, char_id, key, qty, price, slot_ms, expected_net, status FROM actions "
            f"WHERE status NOT IN ('pending', 'late') AND char_id IN ({marks}) AND slot_ms >= ? AND slot_ms < ? "
            f"ORDER BY slot_ms, action_id", (*char_ids, from_ms, to_ms))]

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
