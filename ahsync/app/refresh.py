"""The full refresh: analysis, then recommendations, then one atomic publish (SPEC §3.1.1, §5.0).

1. Analysis turns each market's history into versioned market intelligence
   (analysis.market, no character state).
2. Recommendations turn intelligence plus character state into dashboard pages
   (recommend.pages, no history).
3. Both are staged and swapped in with one transaction, so a failure halfway leaves
   the previous numbers live (SPEC §5.2). One item that fails is set aside with its
   error and the rest carry on (FM8).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ahsync.analysis.market import analyze_market, with_scan
from ahsync.config import Config, market_settings
from ahsync.db.sqlite import Database
from ahsync.recommend.inventory_page import inventory_page
from ahsync.recommend.on_ah_page import on_ah_page
from ahsync.recommend.pages import craft_page, disenchant_page
from ahsync.recommend.plan import plan_market, plan_rows, sell_records
from ahsync.recommend.plan_pages import plan_pages, view_actions

DAY_MS = 86_400_000

log = logging.getLogger(__name__)


@dataclass
class RefreshResult:
    """What a full refresh did."""

    markets: list[str]
    items: int = 0
    failed: dict[str, str] = field(default_factory=dict)    # "<market>:<key>" -> error
    error: str | None = None                                # why nothing was published, if so


def _stage_plan(db: Database, market: str, characters: list[tuple[str, int]], intel_items: dict, items: dict,
                now_ms: int, offset_ms: int, cfg: Config) -> None:
    """Plan the market's characters and stage the plan and its Today, Week, Flip and Inventory pages (SPEC §3.4C, §3.5).

    Args:
        db: Database (inside the refresh's transaction).
        market: Market id.
        characters: (character id, gold) on the market.
        intel_items: Item key -> intelligence.
        items: Item metadata.
        now_ms: Now.
        offset_ms: Local time minus UTC, for the pages' days and times.
        cfg: Config.
    """
    records = sell_records(db, [c for c, _ in characters], market, now_ms, cfg)      # SPEC §3.3.3
    actions = plan_market(db, characters, intel_items, items, now_ms, cfg, records)
    rows = plan_rows(actions, market, now_ms)
    db.stage_plan(rows)
    week_start = (now_ms + offset_ms) // DAY_MS * DAY_MS - offset_ms
    history = db.history_actions([c for c, _ in characters], week_start, week_start + 7 * DAY_MS)
    shown = view_actions(actions, rows, history)
    pages = plan_pages(shown, intel_items, items, sum(g for _, g in characters), now_ms, offset_ms, db.cost_basis(),
                       records)
    pages["inventory"] = inventory_page(db, [c for c, _ in characters], [a for a in shown if a.status == "pending"],
                                        intel_items, items, offset_ms)
    for page, body in pages.items():
        db.stage_view(page, market, body, now_ms)


def refresh_market(db: Database, market: str, now_ms: int, cfg: Config, result: RefreshResult,
                   offset_ms: int = 0) -> None:
    """Compute and stage one market's statistics, plan and pages.

    Args:
        db: Database.
        market: Market id.
        now_ms: Now.
        cfg: Config.
        result: Collects the item count and failures.
        offset_ms: Local time minus UTC (the clock's), for the plan pages.
    """
    intel, failed = analyze_market(db, market, now_ms, market_settings(cfg))
    for key, error in sorted(failed.items()):
        result.failed[f"{market}:{key}"] = error
        log.warning("refresh: item %s on %s failed: %s", key, market, error)
    with db.transaction():
        db.stage_intel(intel)
        _stage_recommendations(db, market, intel.items, now_ms, offset_ms, cfg)
    result.items += len(intel.items)


def _stage_recommendations(db: Database, market: str, stats: dict, now_ms: int, offset_ms: int, cfg: Config) -> None:
    """Stage a market's plan and every page built from its intelligence and character state (SPEC §3.5).

    Args:
        db: Database (inside a transaction).
        market: Market id.
        stats: Item key -> intelligence.
        now_ms: Now.
        offset_ms: Local time minus UTC.
        cfg: Config.
    """
    items = db.items()
    characters = db.characters_in(market)
    char_ids = [c for c, _ in characters]
    total_gold = sum(g for _, g in characters)
    db.stage_view("disenchant", market, disenchant_page(db, char_ids, stats, items, total_gold, now_ms, cfg), now_ms)
    db.stage_view("craft", market, craft_page(db, char_ids, stats, items, total_gold, now_ms, cfg), now_ms)
    db.stage_view("on_ah", market, on_ah_page(db, char_ids, stats, items), now_ms)          # D18
    _stage_plan(db, market, characters, stats, items, now_ms, offset_ms, cfg)


def replan_all(db: Database, now_ms: int, cfg: Config, offset_ms: int = 0) -> list[str]:
    """The incremental update: re-plan every market from its published intelligence (SPEC §3.1.1, IU13).

    It runs after a SavedVariables read, so what you did in game (bought, crafted, posted, moved
    stock, kept something) shows on the dashboard and in PortfolioPlan.lua without a full refresh.
    No history is read and the intelligence is not rebuilt: only the plan and the pages are, after your
    in-game scans made since the intelligence was built update the items they priced (IU6).

    Args:
        db: Database.
        now_ms: Now.
        cfg: Config.
        offset_ms: Local time minus UTC, for the plan pages.

    Returns:
        The markets re-planned (those with published intelligence); [] when it failed, in which
        case the previous plan and pages stay live and the log says why.
    """
    intels = {m: i for m in cfg.markets if (i := db.read_intel(m)) is not None}
    markets = list(intels)
    try:
        db.drop_staging()
        for market in markets:
            scanned = with_scan(intels[market], *db.scans_since(market, intels[market].built_ms))   # IU6
            with db.transaction():
                _stage_recommendations(db, market, scanned.items, now_ms, offset_ms, cfg)
        db.publish_refresh(markets, with_intel=False)       # the stored intelligence stays the analysis' own
    except Exception as error:                         # noqa: BLE001  keep the last good plan (SPEC §5.2 rule 4)
        log.error("re-plan failed: %s: %s", type(error).__name__, error)
        db.drop_staging()
        return []
    return markets


def refresh_all(db: Database, now_ms: int, cfg: Config, offset_ms: int = 0) -> RefreshResult:
    """The full refresh for every configured market (SPEC §3.1.1), published atomically.

    Args:
        db: Database.
        now_ms: Now.
        cfg: Config.
        offset_ms: Local time minus UTC (clock.local_offset_ms), for the plan pages; 0 shows UTC.

    Returns:
        What it did. Failed items are listed on the Status page. When writing fails
        (e.g. disk full, FM9) nothing is published, the previous numbers stay live, and
        `result.error` and a Status line say why.
    """
    result = RefreshResult(list(cfg.markets))
    try:
        db.drop_staging()
        for market in cfg.markets:
            refresh_market(db, market, now_ms, cfg, result, offset_ms)
        db.publish_refresh(list(cfg.markets))
    except Exception as error:                         # noqa: BLE001  keep the last good numbers (SPEC §5.2 rule 4)
        result.error = f"{type(error).__name__}: {error}"
        log.error("refresh failed: %s", result.error)
        db.drop_staging()
        with db.transaction():
            db.set_status("refresh", "error", "refresh_failed", {"error": result.error}, now_ms)
        return result
    with db.transaction():
        db.clear_status("refresh")
        if result.failed:
            db.set_status("refresh_items", "warn", "refresh_items_failed",
                          {"count": len(result.failed), "items": sorted(result.failed)[:10]}, now_ms)
        else:
            db.clear_status("refresh_items")
    return result
