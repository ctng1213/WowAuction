"""Retention policy (SPEC §3.2): 90 days of history, 30 days of order books, rule tombstones and closed positions 90 days,
the lifetimes' per-gap ledger 2 days (SPEC §2.4).

`cost_basis` and live item rules are kept until they no longer apply, so they
are never pruned here. The DB primitives live in db.py; this module only
decides the cutoffs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ahsync.clock import DAY_MS, HOUR_MS
from ahsync.db.sqlite import Database

BOOK_RETENTION_DAYS = 30
LEDGER_DAYS = 2               # the lifetimes' per-gap ledger: the open day and a late snapshot's replay (SPEC §2.4)
MS_TABLES = ("candles_h1", "candles_d1", "sales_h1", "posts", "purchases", "crafts", "disenchants", "auction_mail", "payouts",
             "untracked", "mail_sent",
             "skips", "actions", "gather_events")


@dataclass(frozen=True)
class PruneReport:
    """Rows deleted per table in one prune."""

    deleted: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        """All rows deleted.

        Returns:
            The sum over tables.
        """
        return sum(self.deleted.values())


def prune(db: Database, now_ms: int, retention_days: int = 90, book_days: int = BOOK_RETENTION_DAYS) -> PruneReport:
    """Delete everything older than the retention windows, one transaction per table.

    A row exactly `retention_days` old is kept; anything older goes (S6).

    Args:
        db: The database.
        now_ms: Current time, UTC ms.
        retention_days: History window in days (config `retention_days`).
        book_days: Order-book window in days.

    Returns:
        Rows deleted per table.
    """
    cutoff_ms = now_ms - retention_days * DAY_MS
    deleted = {table: db.delete_before(table, cutoff_ms) for table in MS_TABLES}
    deleted["volume_d1"] = db.delete_before("volume_d1", cutoff_ms // DAY_MS)
    deleted["volume_cover"] = db.delete_before("volume_cover", cutoff_ms // DAY_MS)
    deleted["lifetimes_d1"] = db.delete_before("lifetimes_d1", cutoff_ms // DAY_MS)
    deleted["new_listings_d1"] = db.delete_before("new_listings_d1", cutoff_ms // DAY_MS)
    deleted["sold_prices_d1"] = db.delete_before("sold_prices_d1", cutoff_ms // DAY_MS)       # BZ52
    for table in ("gap_flows", "gap_seen"):                # only Ingest writes them; 2 days cover every re-fold
        deleted[table] = db.delete_before(table, now_ms - LEDGER_DAYS * DAY_MS)
    deleted["fetch_failures"] = db.delete_before("fetch_failures", cutoff_ms // HOUR_MS)
    deleted["observed_hours"] = db.delete_before("observed_hours", cutoff_ms // HOUR_MS)   # SRC16
    deleted["ahledger_rows"] = db.delete_before("ahledger_rows", cutoff_ms // 1000)        # its `updated` is in s
    deleted["scans"] = db.delete_scans_before(now_ms - book_days * DAY_MS)
    deleted["item_rules"] = db.delete_rule_tombstones_before(cutoff_ms)
    deleted["positions"] = db.delete_closed_positions_before(cutoff_ms)   # 90 days after they close (SPEC §3.2)
    with db.transaction():                                     # farming sessions: 90 days, like their events
        deleted["farming_sessions"] = db.delete_farming_sessions_before(cutoff_ms)
    return PruneReport(deleted)
