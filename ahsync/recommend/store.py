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
