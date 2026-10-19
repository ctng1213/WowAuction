"""Your auction mail as a daily volume source: units sold and expired per item and day (SPEC §3.3.1 source 3).

Your mail sees only your own auctions, so it is a lower bound on the market's sales: the lowest kind ("own"),
used for a day no other source observed, and a floor under the estimates (V34, V35). Each day is recomputed
whole from the stored mail, so reading it again changes nothing.
"""
from __future__ import annotations

from ahsync.db.sqlite import Database

DAY_MS = 86_400_000


def recompute_mail_volume(db: Database, market: str, char_ids: list[str], from_day: int, to_day: int) -> int:
    """Write the `mail` volume rows of a market's days from your characters' auction mail (V35).

    Args:
        db: Database, inside the caller's transaction.
        market: Market id.
        char_ids: Your characters on the market.
        from_day: First day number rewritten.
        to_day: Last day number rewritten, inclusive.

    Returns:
        Days written with any row.
    """
    marks = ",".join("?" * len(char_ids))
    rows: dict[int, dict[int, tuple[float, float, float]]] = {}
    if char_ids:
        for day, key, sold, expired in db.conn.execute(
                f"SELECT at / {DAY_MS}, key, SUM(CASE outcome WHEN 'sold' THEN COALESCE(qty, 1) ELSE 0 END), "
                f"SUM(CASE outcome WHEN 'expired' THEN COALESCE(qty, 1) ELSE 0 END) FROM auction_mail "
                f"WHERE char_id IN ({marks}) AND outcome IN ('sold', 'expired') AND at BETWEEN ? AND ? "
                f"GROUP BY 1, 2 ORDER BY 1, 2", (*char_ids, from_day * DAY_MS, (to_day + 1) * DAY_MS - 1)):
            rows.setdefault(day, {})[key] = (float(sold), float(expired), 0.0)
    for day in range(from_day, to_day + 1):
        db.replace_volume_day(market, day, "mail", rows.get(day, {}), None, ("own", None) if day in rows else None)
    return len(rows)
