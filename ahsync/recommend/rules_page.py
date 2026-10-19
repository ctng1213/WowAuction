"""The Rules page, built at refresh and re-plan time (SPEC §3.4.2, §3.5 Rules, D50).

This module only gathers inputs: your live rules, sell records, resolved posts and stock. engine.rules_view
makes every text.
"""
from __future__ import annotations

from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine.rules_view import rules_view
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel
from ahsync.recommend.plan import sell_records
from ahsync.recommend.plan_pages import item_looks

DAY_MS = 86_400_000
OUTCOME_DAYS = 90


def rules_page(db: Database, char_ids: list[str], market: str, stats: dict[int, ItemIntel], items: dict[int, ItemInfo],
               now_ms: int, offset_ms: int, cfg: Config) -> dict:
    """The Rules payload (SPEC §3.5 dashboard data).

    Args:
        db: Database.
        char_ids: The market's characters.
        market: Market id (sell records read its closes).
        stats: Item key -> intelligence.
        items: Item metadata (names).
        now_ms: Now.
        offset_ms: Local time minus UTC, ms.
        cfg: Config (sell record settings).

    Returns:
        {"decisions", "kept", "blacklist"}.
    """
    records = sell_records(db, char_ids, market, now_ms, cfg)
    rules = db.live_rule_rows()
    looks = item_looks(set(records) | {key for key, *_ in rules}, stats, items, records=records)
    return rules_view(rules, looks, db.post_outcomes(char_ids, now_ms - OUTCOME_DAYS * DAY_MS), db.owned_lots(char_ids),
                      now_ms, offset_ms)
