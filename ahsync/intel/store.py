"""The Market Intelligence store (SPEC §5.0): versioned analysis results, mixed into db.sqlite.Database.

One JSON document per market (intel.contract). A full refresh stages it in
`intel_new`; db.sqlite.Database.publish_refresh swaps it in with the pages.
"""
from __future__ import annotations

import sqlite3

from ahsync.intel.contract import MarketIntelligence, from_json, to_json


class IntelStore:
    """Staged writes and reads of market intelligence; needs `self.conn`."""

    conn: sqlite3.Connection

    def stage_intel(self, intel: MarketIntelligence) -> None:
        """Stage one market's intelligence (published by publish_refresh).

        Args:
            intel: The intelligence.
        """
        self.conn.execute("INSERT OR REPLACE INTO intel_new(market, schema_version, body, built) VALUES (?, ?, ?, ?)",
                          (intel.market, intel.schema_version, to_json(intel), intel.built_ms))

    def prices_newer_than_intel(self, market: str, src: str = "ahf") -> bool:
        """Whether the primary source's prices arrived after the published intelligence was built (IU2, SRC2).

        Two indexed lookups, so every page request can ask.

        Args:
            market: Market id.
            src: The market's primary remote source (config [sources]).

        Returns:
            True when its newest stored price is newer than the intelligence; False without either.
        """
        row = self.conn.execute(
            "SELECT (SELECT MAX(at) FROM current WHERE market = ? AND src = ?), "
            "(SELECT built FROM intel WHERE market = ?)", (market, src, market)).fetchone()
        return row[0] is not None and row[1] is not None and row[0] > row[1]

    def read_intel(self, market: str) -> MarketIntelligence | None:
        """The published intelligence of a market.

        Args:
            market: Market id.

        Returns:
            The intelligence, or None before the first refresh.

        Raises:
            intel.contract.IntelVersionError: When it was written by a newer AHSync.
        """
        row = self.conn.execute("SELECT body FROM intel WHERE market = ?", (market,)).fetchone()
        return from_json(row[0]) if row else None
