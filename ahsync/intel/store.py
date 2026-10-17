"""The Market Intelligence store (SPEC §5.0): staged writes of analysis results, mixed into db.sqlite.Database."""
from __future__ import annotations

import json
import sqlite3

class IntelStore:
    """Staged writes of market intelligence; publish_refresh (db.sqlite) swaps them in. Needs `self.conn`."""

    conn: sqlite3.Connection

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
