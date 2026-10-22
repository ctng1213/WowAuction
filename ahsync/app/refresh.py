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

from ahsync.analysis.market import HISTORY_DAYS, analyze_market, with_scan
from ahsync.collect.own_mail import recompute_mail_volume
from ahsync.collect.local_scans import recompute_stale_volume
from ahsync.config import Config, market_settings, market_sources, portfolio_settings
from ahsync.db.sqlite import Database
from ahsync.engine.characters_view import CharacterSheet, characters_view
from ahsync.engine.plan_chains import Arriving
from ahsync.engine.positions import committed
from ahsync.engine.views import GOLD_PAGES, gold_pill
from ahsync.recommend.gathering_page import gathering_page
from ahsync.recommend.history_page import history_page
from ahsync.recommend.inventory_page import inventory_page
from ahsync.recommend.on_ah_page import on_ah_page
from ahsync.recommend.pages import craft_page, disenchant_page
from ahsync.recommend.plan import plan_rows, sell_records
from ahsync.recommend.portfolio import alternative_rows, plan_portfolio, portfolio_export
from ahsync.recommend.portfolio_page import portfolio_page
from ahsync.recommend.positions_pass import clock_of, current, plan_steps, views
from ahsync.recommend.rules_page import rules_page
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
                now_ms: int, offset_ms: int, cfg: Config, built_ms: int) -> tuple[dict, list, int | None]:
    """Plan the market's characters, stage the plan, its alternatives and what the addon files carry of it (M11), and
    build its Portfolio (M13), Today, Week, Flip and Inventory pages (SPEC §3.4C, §3.4D, §3.5).

    Args:
        db: Database (inside the refresh's transaction).
        market: Market id.
        characters: (character id, gold) on the market.
        intel_items: Item key -> intelligence.
        items: Item metadata.
        now_ms: Now.
        offset_ms: Local time minus UTC, for the pages' days and times.
        cfg: Config.
        built_ms: The intelligence's built_ms.

    Returns:
        (page name -> payload, for the caller to stage; the funded plan's actions (engine.plan.PlanAction), for the
        Craft page's chains (D56); the portfolio's remaining capacity in copper, for the Craft and Disenchant pages'
        limit (D45), None without characters).

    Raises:
        engine.selection.SelectionError: The allocation broke a D.5 rule: nothing of the run is published.
    """
    char_ids = [c for c, _ in characters]
    records = sell_records(db, char_ids, market, now_ms, cfg)                       # SPEC §3.3.3
    db.store_sell_records(market, records)                                           # for the status pass (M8)
    positions = current(db, now_ms)                                                  # SPEC §3.4D.9, M14
    plan = plan_portfolio(db, characters, intel_items, items, now_ms, cfg, records, built_ms,
                          committed([p for p in positions if p.char_id in char_ids]))     # D.1: open positions
    actions = plan.actions if plan else []
    rows = plan_rows(actions, market, now_ms, plan.action_cands if plan else ())
    db.stage_plan(rows)
    db.stage_alternatives(market, alternative_rows(plan))                           # SPEC §3.4D, M9
    held = views(db, positions, char_ids, plan_steps(actions), intel_items,
                 clock_of(now_ms, offset_ms, portfolio_settings(cfg)))                # section A and `positions`
    db.write_position_views(held)
    db.stage_export(market, *portfolio_export(plan, cfg, now_ms, held))             # SPEC §3.5, M11
    week_start = (now_ms + offset_ms) // DAY_MS * DAY_MS - offset_ms
    history = db.history_actions([c for c, _ in characters], week_start, week_start + 7 * DAY_MS)
    shown = view_actions(actions, rows, history, items)
    uses: dict[int, frozenset[int]] = {}
    for r in db.known_recipes(char_ids):
        if r.product is not None:
            uses[r.product] = uses.get(r.product, frozenset()) | {k for k, _ in r.reagents}
    pages = plan_pages(shown, intel_items, items, sum(g for _, g in characters), now_ms, offset_ms, db.cost_basis(),
                       records, Arriving(tuple(db.transit(char_ids)), uses))                      # C23
    pages["portfolio"] = portfolio_page(characters, plan, [r[0] for r in rows], intel_items, items, now_ms,
                                        offset_ms, cfg, built_ms, held)                            # SPEC §3.5, M13
    pages["inventory"] = inventory_page(db, [c for c, _ in characters], [a for a in shown if a.status == "pending"],
                                        intel_items, items, offset_ms)
    return pages, actions, plan.accounting.remaining_capacity if plan else None


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
    intel, failed = analyze_market(db, market, now_ms, market_settings(cfg),
                                   ("local", *market_sources(cfg, market).rank))                 # SRC3
    for key, error in sorted(failed.items()):
        result.failed[f"{market}:{key}"] = error
        log.warning("refresh: item %s on %s failed: %s", key, market, error)
    with db.transaction():
        db.stage_intel(intel)
        _stage_recommendations(db, market, intel.items, now_ms, offset_ms, cfg, intel.built_ms)
    result.items += len(intel.items)


def _stage_recommendations(db: Database, market: str, stats: dict, now_ms: int, offset_ms: int, cfg: Config,
                           built_ms: int) -> None:
    """Stage a market's plan and every page built from its intelligence and character state (SPEC §3.5).

    Args:
        db: Database (inside a transaction).
        market: Market id.
        stats: Item key -> intelligence.
        now_ms: Now.
        offset_ms: Local time minus UTC.
        cfg: Config.
        built_ms: The intelligence's built_ms (price ages, SPEC §3.4D.6).
    """
    items = db.items()
    characters = db.characters_in(market)
    char_ids = [c for c, _ in characters]
    total_gold = sum(g for _, g in characters)
    pages, planned, capacity = _stage_plan(db, market, characters, stats, items, now_ms, offset_ms, cfg, built_ms)
    pages["disenchant"] = disenchant_page(db, char_ids, stats, items, total_gold, now_ms, cfg, capacity)   # D71
    pages["craft"] = craft_page(db, char_ids, stats, items, total_gold, now_ms, cfg, planned, offset_ms,
                                capacity)                                                      # D45, D56
    pages["on_ah"] = on_ah_page(db, char_ids, stats, items, now_ms, cfg)                       # D18, D53
    pages["history"] = history_page(db, char_ids, stats, items, now_ms, offset_ms)            # D47
    sheets = [CharacterSheet(c, cls, level, faction, m, gold, cfg.budgets.get(c), recipes, updated, profs)
              for c, cls, level, faction, m, gold, recipes, updated, profs in db.character_sheets(char_ids)]
    pages["characters"] = characters_view(sheets, now_ms)                                      # D49
    pages["rules"] = rules_page(db, char_ids, market, stats, items, now_ms, offset_ms, cfg)    # D50
    # SPEC §3.4E: staged at the full refresh and at every re-plan after a SavedVariables read (new professions or a
    # scan); research only, so nothing of it touches the plan (E.9).
    pages["gathering"] = gathering_page(db, market, char_ids, stats, items, now_ms, offset_ms, cfg)   # D80
    pill = gold_pill([g for _, g in characters], now_ms, offset_ms)                            # D74
    for page, body in pages.items():
        db.stage_view(page, market, {**body, "gold": pill} if page in GOLD_PAGES else body, now_ms)


def rebuild_gathering(db: Database, now_ms: int, cfg: Config, offset_ms: int = 0) -> list[str]:
    """Rebuild only the Gathering page of every market, after a manual-rate write (SPEC §3.5 POST /api/manual_rate).

    Gathering is research, not part of the plan (§3.4E.9), so nothing is re-planned and no history is read: the
    page is built from the published intelligence plus the newer scans (IU6), like a re-plan's, and replaces the
    published one in a single row write.

    Args:
        db: Database.
        now_ms: Now.
        cfg: Config.
        offset_ms: Local time minus UTC.

    Returns:
        The markets whose page was rebuilt (those with published intelligence).
    """
    markets = []
    for market in cfg.markets:
        intel = db.read_intel(market)
        if intel is None:
            continue
        scanned = with_scan(intel, *db.scans_since(market, intel.built_ms))
        characters = db.characters_in(market)
        body = gathering_page(db, market, [c for c, _ in characters], scanned.items, db.items(), now_ms, offset_ms, cfg)
        with db.transaction():
            db.replace_view("gathering", market, {**body, "gold": gold_pill([g for _, g in characters], now_ms,
                                                                               offset_ms)}, now_ms)
        markets.append(market)
    return markets


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
                _stage_recommendations(db, market, scanned.items, now_ms, offset_ms, cfg, scanned.built_ms)
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
            with db.transaction():                     # estimates from an older method first (V24)
                old = recompute_stale_volume(db, market)
                if old:
                    db.set_status(f"volume_method:{market}", "info", "volume_method_old", {"market": market, "days": old},
                                  now_ms)
                else:
                    db.clear_status(f"volume_method:{market}")
                recompute_mail_volume(db, market, [c for c, _ in db.characters_in(market)],   # V35
                                      (now_ms - HISTORY_DAYS * DAY_MS) // DAY_MS, now_ms // DAY_MS)
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
