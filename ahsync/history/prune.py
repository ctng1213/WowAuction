"""Retention policy (SPEC §3.2): 90 days of history, 30 days of order books, rule tombstones 90 days.

`cost_basis` and live item rules are kept until they no longer apply, so they
are never pruned here. The DB primitives live in db.py; this module only
decides the cutoffs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ahsync.clock import DAY_MS, HOUR_MS
from ahsync.db.sqlite import Database

BOOK_RETENTION_DAYS = 30
MS_TABLES = ("candles_h1", "candles_d1", "posts", "purchases", "crafts", "auction_mail", "mail_sent", "skips",
             "actions")


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
    deleted["fetch_failures"] = db.delete_before("fetch_failures", cutoff_ms // HOUR_MS)
    deleted["scans"] = db.delete_scans_before(now_ms - book_days * DAY_MS)
    deleted["item_rules"] = db.delete_rule_tombstones_before(cutoff_ms)
    return PruneReport(deleted)
