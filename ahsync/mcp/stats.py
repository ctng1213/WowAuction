"""The in-memory market table, `market_stats` (SPEC §3.8.4): the published intelligence as rows SQL can search.

Each market's intelligence is one JSON document per market in the database (the `intel` table). While the assistant
server runs, this flattens it into an in-memory SQLite database, one row per market and item, with the item's
catalog fields. It is rebuilt when a newer refresh has been published, swapped in whole, and freed when the server
stops. Nothing is written to disk. The fields are the intelligence as built: no new formula.
"""

from __future__ import annotations

import itertools
import sqlite3
import threading
from collections.abc import Sequence

from ahsync.db.sqlite import Database

TABLE = "market_stats"
SCHEMA = "stats"                       # the name it is attached under on a query connection
# Column -> (SQL type, description with its unit). The order is the table's.
COLUMNS: dict[str, tuple[str, str]] = {
    "market": ("TEXT", "The market id, e.g. forever.pvp.horde.us."),
    "key": ("INTEGER", "The item key: the item id, or item id + 2147483648 x suffix id for a random-suffix item."),
    "item_id": ("INTEGER", "The base item id (Classic's)."),
    "name": ("TEXT", "The item's name."),
    "quality": ("INTEGER", "0 poor, 1 common, 2 uncommon, 3 rare, 4 epic, 5 legendary."),
    "ilvl": ("INTEGER", "Item level."),
    "class": ("INTEGER", "Item class id (Classic's): 0 consumable, 2 weapon, 4 armor, 7 trade goods, 9 recipe, ..."),
    "subclass": ("INTEGER", "Item subclass id; for trade goods (class 7): 1 parts, 5 cloth, 6 leather, "
                            "7 metal and stone, 8 cooking (raw fish and meat), 9 herb, 10 elemental, 11 other, "
                            "12 enchanting."),
    "cur": ("INTEGER", "The lowest buyout now, copper per unit; NULL when nothing is listed."),
    "cur_age_ms": ("INTEGER", "How old cur is, ms."),
    "med7": ("INTEGER", "Median of the hourly lowest buyout over 7 days, copper."),
    "med30": ("INTEGER", "The same over 30 days, copper."),
    "p10_30": ("INTEGER", "10th percentile of the hourly lowest buyout over 30 days, copper."),
    "p90_30": ("INTEGER", "90th percentile over 30 days, copper."),
    "trend7": ("REAL", "Price trend over 7 days, % per day."),
    "sold_per_day": ("REAL", "Estimated units sold per day (7 days); NULL when unknown."),
    "expired_per_day": ("REAL", "Estimated units expired unsold per day (7 days)."),
    "listed_per_day": ("REAL", "Estimated units newly listed per day (7 days)."),
    "volume_source": ("TEXT", "The source behind the sales estimate."),
    "units_on_ah": ("INTEGER", "Units listed now."),
    "chance": ("REAL", "Chance (0-1) that one reference stack posted now at the market price sells."),
    "conf": ("INTEGER", "AHSync's confidence in these numbers, %."),
    "vendor_sell": ("INTEGER", "What a vendor pays for one, copper; NULL when unknown."),
}
_INTEL_FIELDS = ("cur", "cur_age_ms", "med7", "med30", "p10_30", "p90_30", "trend7", "sold_per_day",
                 "expired_per_day", "listed_per_day", "volume_source", "units_on_ah", "chance", "conf")
_names = itertools.count(1)


class MarketStats:
    """The current market table: an in-memory database kept alive by one holding connection."""

    def __init__(self) -> None:
        """Start empty; build fills it."""
        self._holder: sqlite3.Connection | None = None
        self.uri: str | None = None
        self.built: dict[str, int | None] = {}     # market -> the intel built_ms the table was made from
        self._lock = threading.Lock()

    def _rows(self, db: Database, market: str) -> list[tuple]:
        """One market's rows: its intelligence joined with each item's catalog fields.

        Args:
            db: The database (read-only).
            market: A market id.

        Returns:
            Rows in COLUMNS order, by key; none before the first refresh.
        """
        intel = db.read_intel(market)
        if intel is None:
            return []
        meta = {r[0]: r[1:] for r in db.conn.execute(
            "SELECT key, item_id, name, quality, ilvl, class, subclass, vendor_sell FROM items")}
        out = []
        for key in sorted(intel.items):
            item = intel.items[key]
            item_id, name, quality, ilvl, cls, sub, vendor = meta.get(key, (None,) * 7)
            values = tuple(getattr(item, f) for f in _INTEL_FIELDS)
            out.append((market, key, item_id, name, quality, ilvl, cls, sub, *values, vendor))
        return out

    def build(self, db: Database, markets: Sequence[str]) -> None:
        """Build a new table from the published intelligence and swap it in; the old one is freed.

        Args:
            db: The database (read-only).
            markets: The configured markets.
        """
        uri = f"file:ahsync_market_stats_{next(_names)}?mode=memory&cache=shared"
        holder = sqlite3.connect(uri, uri=True, check_same_thread=False)
        cols = ", ".join(f'"{c}" {t}' for c, (t, _) in COLUMNS.items())
        holder.execute(f"CREATE TABLE {TABLE} ({cols}, PRIMARY KEY (market, key))")
        built: dict[str, int | None] = {}
        for market in markets:
            rows = self._rows(db, market)
            holder.executemany(f"INSERT INTO {TABLE} VALUES ({', '.join('?' * len(COLUMNS))})", rows)
            facts = db.intel_facts(market)
            built[market] = facts.built_ms if facts else None
        holder.execute(f"CREATE INDEX {TABLE}_group ON {TABLE}(market, class, subclass)")
        holder.commit()
        with self._lock:
            old, self._holder, self.uri, self.built = self._holder, holder, uri, built
            if old is not None:
                old.close()

    def stale(self, db: Database, markets: Sequence[str]) -> bool:
        """Whether a newer refresh has been published since the table was built (or it was never built).

        Args:
            db: The database (read-only).
            markets: The configured markets.

        Returns:
            True when build should run.
        """
        if self._holder is None:
            return True
        for market in markets:
            facts = db.intel_facts(market)
            if (facts.built_ms if facts else None) != self.built.get(market):
                return True
        return False

    def refresh(self, db: Database, markets: Sequence[str]) -> None:
        """Rebuild the table when a newer refresh has been published (SPEC §3.8.4).

        Args:
            db: The database (read-only).
            markets: The configured markets.
        """
        if self.stale(db, markets):
            self.build(db, markets)

    def size_bytes(self) -> int:
        """The table's memory: its pages × page size (the Status card's line, SPEC §3.8.4).

        Returns:
            Bytes; 0 when freed.
        """
        with self._lock:                   # the Status card asks from another thread
            if self._holder is None:
                return 0
            pages = self._holder.execute("PRAGMA page_count").fetchone()[0]
            return pages * self._holder.execute("PRAGMA page_size").fetchone()[0]

    def count(self) -> int:
        """The table's rows.

        Returns:
            Rows; 0 when freed.
        """
        with self._lock:
            if self._holder is None:
                return 0
            return self._holder.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]

    def connect(self) -> sqlite3.Connection:
        """A new connection to the current table, for one call.

        Returns:
            A connection whose `market_stats` is the table built last.

        Raises:
            RuntimeError: The table is freed (the server stopped).
        """
        with self._lock:
            if self.uri is None:
                raise RuntimeError("the market table is freed")
            return sqlite3.connect(self.uri, uri=True, check_same_thread=False)

    def close(self) -> None:
        """Free the table (Disable, a stop; MCP2)."""
        with self._lock:
            holder, self._holder, self.uri, self.built = self._holder, None, None, {}
            if holder is not None:
                holder.close()
