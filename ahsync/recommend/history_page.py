"""The History page, built at refresh and re-plan time (SPEC §3.5 History, D47, D48).

This module only gathers inputs: the journal of the last 30 days and the items' names. engine.history_view
makes every text.
"""
from __future__ import annotations

from ahsync.engine.history_view import history_view
from ahsync.engine.views import COLUMN_HELP
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel
from ahsync.recommend.plan_pages import item_looks
from ahsync.state.journal import JournalReads

DAY_MS = 86_400_000
HISTORY_DAYS = 30


def history_page(db: JournalReads, char_ids: list[str], stats: dict[int, ItemIntel], items: dict[int, ItemInfo],
                 now_ms: int, offset_ms: int) -> dict:
    """The History payload (SPEC §3.5 dashboard data).

    Args:
        db: Database (journal reads).
        char_ids: The market's characters.
        stats: Item key -> intelligence.
        items: Item metadata (names).
        now_ms: Now.
        offset_ms: Local time minus UTC, ms.

    Returns:
        {"rows", "tiles", "open", "help"}: the last 30 days, newest first, with the column help texts.
    """
    rows = db.journal(char_ids, now_ms - HISTORY_DAYS * DAY_MS)
    keys = {r[3] for r in rows}
    for r in rows:
        if r[2] == "disenchanted":
            keys |= {int(part.split(",")[0]) for part in str(r[7]).split(";") if part}
    looks = item_looks(keys, stats, items)
    view = history_view(rows, looks, {k: look.name for k, look in looks.items()}, now_ms, offset_ms)
    return {**view, "help": COLUMN_HELP}
