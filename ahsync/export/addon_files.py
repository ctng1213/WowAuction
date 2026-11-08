"""Writing the addon's generated files into Interface/AddOns/AHProfit_Data (SPEC §3.5, §5.2).

Every write is atomic: the text goes to a temp file, is read back and checked, then
renamed over the old file. A blocked rename (file locked, antivirus) is retried 3 times
over 10 s; if it still fails the old file stays and Status warns (FM14). AHSync only
ever replaces these files and never deletes them, because WoW sees only files that
existed when the client started (SPEC §3.5). Each successful write is logged at INFO with
its size, generatedAt and what it holds (EX15). Both files carry EXPORT_VERSION (EX14).
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine import disenchant
from ahsync.engine.coverage import ranges_to_hours
from ahsync.engine.de_table import DE_TABLE
from ahsync.engine.graph import Graph, build, graph_recipe
from ahsync.engine.plan_views import record_line
from ahsync.engine.portfolio import candidate_from_json
from ahsync.export.market_lua import EXPORT_VERSION, item_count, market_lua, wanted
from ahsync.export.plan_lua import PortfolioFields, plan_lua
from ahsync.export.portfolio_lua import candidate_text, portfolio_text, position_text, trim_candidates
from ahsync.intel.contract import MarketIntelligence
from ahsync.recommend.plan import flip_offers, sell_records
from ahsync.state.importer import ingested_until

DATA_FOLDER = ("Interface", "AddOns", "AHProfit_Data")
MARKET_FILE = "MarketIntelligence.lua"
PLAN_FILE = "PortfolioPlan.lua"
ATTEMPTS = 3
RETRY_WAIT_S = 5               # 3 attempts, 5 s apart: 10 s in all (SPEC §5.2)
SCAN_DAYS_MS = 7 * 86_400_000  # targeted scans this recent put an item on the shortlist (EX1)
MAX_MARKET_BYTES = 2 * 1024 * 1024
MAX_PLAN_BYTES = 200 * 1024
HOUR_MS = 3_600_000
COVERAGE_HOURS = 720           # the tooltip header's coverage: the last 30 days (TT40)

log = logging.getLogger(__name__)


def data_folder(cfg: Config) -> Path:
    """Where AHProfit_Data lives in the WoW install.

    Args:
        cfg: Config (wow_path).

    Returns:
        <wow_path>/Interface/AddOns/AHProfit_Data.
    """
    return Path(cfg.wow_path).joinpath(*DATA_FOLDER)


def write_atomic(path: Path, text: str, sleep: Callable[[float], None],
                 replace: Callable[[Path, Path], None] = os.replace) -> bool:
    """Write a file atomically: temp file, check, rename; retried when the rename is blocked (FM14).

    Args:
        path: The file to replace.
        text: Its new contents (UTF-8).
        sleep: Waits this many seconds between attempts (the clock's, so tests don't wait).
        replace: Renames the temp file over the target (os.replace; injected to fail in tests).

    Returns:
        True when the new file is in place; False when every attempt failed, with the old
        file unchanged and the temp file removed.
    """
    data = text.encode("utf-8")
    temp = path.with_name(path.name + ".tmp")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            temp.write_bytes(data)
            if temp.read_bytes() != data:
                raise OSError("the temp file reads back different")
            replace(temp, path)
            return True
        except OSError as error:
            log.warning("writing %s failed (attempt %d of %d): %s", path.name, attempt, ATTEMPTS, error)
            if attempt < ATTEMPTS:
                sleep(RETRY_WAIT_S)
    try:
        temp.unlink()
    except OSError:
        pass
    return False


def shortlist(db: Database, market: str, now_ms: int) -> set[int]:
    """The items whose full curve the addon needs (SPEC §3.5 tier 2, EX1).

    Args:
        db: Database.
        market: Market id.
        now_ms: Now; targeted scans of the last 7 days count.

    Returns:
        Item keys: what you hold, your recipes' products and reagents, open plan actions,
        desktop list rows and items in your partial scans of the last 7 days.
    """
    char_ids = [c for c, _ in db.characters_in(market)]
    keys = {key for _, _, key, _, _ in db.holdings(char_ids)}
    for recipe in db.known_recipes(char_ids):
        keys |= {key for key, _ in recipe.reagents} | ({recipe.product} if recipe.product is not None else set())
    keys |= {row[3] for row in db.open_actions(char_ids)}
    keys |= db.partial_scan_keys(market, now_ms - SCAN_DAYS_MS)
    stored = db.view("disenchant", market)
    if stored is not None:
        keys |= {row["key"] for row in stored[0].get("rows", []) if "key" in row}
    return keys


DE_KINDS = {2: ("weapon", "w"), 4: ("armor", "a")}     # WoW item class -> DE table kind, export code


def disenchantable(db: Database) -> dict[int, str]:
    """Items one could disenchant, with the skill it needs (A23): for the addon's Disenchant tab.

    Args:
        db: Database (item metadata).

    Returns:
        Item key -> "skill,quality,ilvl,kind"; unknown-suffix keys are left out.
    """
    out = {}
    for key, info in db.items().items():
        kind = DE_KINDS.get(info.item_class)
        if key < 0 or not kind or not info.quality or not info.ilvl:
            continue
        row = disenchant.outcomes_for(DE_TABLE, info.quality, info.ilvl, kind[0])
        if row is not None:
            out[key] = f"{row[0]},{info.quality},{info.ilvl},{kind[1]}"
    return out


def vendor_prices(db: Database) -> dict[int, int]:
    """What a merchant charges for the items seen sold in unlimited supply (EX6): for the addon's crafts.

    Args:
        db: Database (vendor reagents and item metadata).

    Returns:
        Item key -> vendor buy price in copper; items without a known price are left out.
    """
    items = db.items()
    return {key: items[key].vendor_buy for key in sorted(db.vendor_reagent_keys())
            if key in items and items[key].vendor_buy}


def export_market(db: Database, cfg: Config, now_ms: int, sleep: Callable[[float], None],
                  replace: Callable[[Path, Path], None] = os.replace) -> bool:
    """Write MarketIntelligence.lua from the published intelligence (SPEC §3.5).

    Args:
        db: Database.
        cfg: Config: markets and wow_path.
        now_ms: Now (generatedAt).
        sleep: Waits between write attempts.
        replace: As write_atomic.

    Returns:
        True when written; False when there is no intelligence yet, the AddOns folder
        is missing, or the write failed (then Status says so).
    """
    intels = [i for i in (db.read_intel(m) for m in cfg.markets) if i is not None]
    folder = data_folder(cfg)
    if not intels or not folder.is_dir():
        if intels:
            log.warning("no %s folder; run tools/deploy.py, then restart WoW", folder)
        return False
    shortlists = {i.market: shortlist(db, i.market, now_ms) for i in intels}
    char_markets = {c: m for c, _, _, m, _ in db.characters() if m is not None}
    vendor = vendor_prices(db)
    items = db.items()
    flips = {i.market: flip_offers(i.items, items, set(vendor), now_ms, cfg) for i in intels}       # EX11
    de = disenchantable(db)
    graphs = {i.market: market_graph(db, i, shortlists[i.market], de) for i in intels}             # EX18
    rows = {i.market: candidate_rows(db, i.market) for i in intels}                                # EX17
    covered = {i.market: coverage30(db, i, now_ms) for i in intels}                                # TT40

    def build_text(kept: dict[str, list[str]]) -> str:
        """The whole file with these candidates.

        Args:
            kept: Market -> the K strings to write.

        Returns:
            MarketIntelligence.lua's text.
        """
        return market_lua(intels, shortlists, char_markets, now_ms, de, vendor, flips, cfg.sell_window_hours, kept,
                          graphs, covered)
    text, kept = fit_market(build_text, rows, MAX_MARKET_BYTES)
    _trim_status(db, rows, kept, MAX_MARKET_BYTES, now_ms)
    ok = _write(db, folder / MARKET_FILE, text, MAX_MARKET_BYTES, now_ms, sleep, replace)
    if ok:
        _log_written(MARKET_FILE, text, now_ms, f"{item_count(text)} items")
    return ok


def coverage30(db: Database, intel: MarketIntelligence, now_ms: int) -> int:
    """The hours a market was observed in the last 30 days, for the tooltip header's coverage (SPEC §4.3, TT40).

    Args:
        db: Database.
        intel: The market's published intelligence.
        now_ms: Now (generatedAt); the window is the 720 whole hours before its hour.

    Returns:
        A cloud summary's observed hours in the window (its `coverage`), else the hours any local source observed
        completely (`observed_hours`, as the Data coverage card counts them).
    """
    end = now_ms // HOUR_MS
    if intel.coverage:
        return sum(1 for h in ranges_to_hours(intel.coverage) if end - COVERAGE_HOURS <= h < end)
    by_src = db.observed_hours(intel.market, end - COVERAGE_HOURS, end - 1)
    return len(set().union(*by_src.values())) if by_src else 0


def candidate_rows(db: Database, market: str) -> list[tuple[str, str, float]]:
    """A market's exported candidates as K strings (SPEC §3.5 `K`, EX17).

    Args:
        db: Database (the published export_candidates).
        market: Market id.

    Returns:
        (K string, status "" funded or the D.6 status, adjusted profit float copper) in export order.
    """
    return [(candidate_text(candidate_from_json(data), status), status, adjusted)
            for _cand_id, status, adjusted, data in db.export_candidates(market)]


def market_graph(db: Database, intel: MarketIntelligence, short: set[int], de: dict[int, str]) -> Graph:
    """A market's dependency graph: its characters' known recipes and its exported disenchantable items (EX18).

    A recipe loop is cut (C13) and logged at INFO, once per cut edge (FM39).

    Args:
        db: Database (characters and known recipes).
        intel: The market's intelligence (which items are exported).
        short: The market's shortlist.
        de: Item key -> disenchant string, for every item one could disenchant.

    Returns:
        The Graph.
    """
    char_ids = [c for c, _ in db.characters_in(intel.market)]
    recipes = [graph_recipe(r.spell, r.product, r.yield_min, r.yield_max, r.reagents)
               for r in db.known_recipes(char_ids) if r.product is not None]
    graph = build(recipes, [k for k in sorted(de) if k in intel.items and wanted(k, intel.items[k], short)])
    for reagent, spell in graph.cut:
        log.info("recipe loop on %s: item %d is not followed back into spell %d (C13)", intel.market, reagent, spell)
    return graph


def fit_market(build_text: Callable[[dict[str, list[str]]], str], rows: dict[str, list[tuple[str, str, float]]],
               limit: int) -> tuple[str, dict[str, list[str]]]:
    """The market file within its budget, leaving out the lowest alternatives first (SPEC §3.5, FM40).

    Args:
        build_text: Builds the whole file from market -> K strings.
        rows: Market -> its exported candidates (candidate_rows).
        limit: The file's budget, bytes; the whole file counts (items, curves, K and G).

    Returns:
        (the file, market -> the K strings kept). When even the funded rows alone don't fit, every alternative is
        left out and the file is over budget (the size warning says so, as for any file).
    """
    kept = {m: [text for text, _s, _a in r] for m, r in rows.items()}
    text = build_text(kept)
    over = len(text.encode("utf-8")) - limit
    while over > 0:
        still = {m: set(k) for m, k in kept.items()}
        left = {m: [row for row in r if row[0] in still[m]] for m, r in rows.items()}
        trimmed = trim_candidates(left, over)
        if trimmed == kept:
            break
        kept = trimmed
        text = build_text(kept)
        over = len(text.encode("utf-8")) - limit
    return text, kept


def _trim_status(db: Database, rows: dict[str, list], kept: dict[str, list[str]], limit: int, now_ms: int) -> None:
    """Keep the "candidate export trimmed" Status line current (FM40).

    Args:
        db: Database (Status lines).
        rows: Market -> its exported candidates.
        kept: Market -> the K strings written.
        limit: The market file's budget, bytes.
        now_ms: Now.
    """
    total, written = sum(len(r) for r in rows.values()), sum(len(k) for k in kept.values())
    with db.transaction():
        if written < total:
            db.set_status("candidate_export", "warn", "candidate_export_trimmed",
                          {"kept": written, "total": total, "limit": limit}, now_ms)
        else:
            db.clear_status("candidate_export")


def _log_written(name: str, text: str, now_ms: int, holds: str) -> None:
    """One INFO line for a written addon file, so the data flow can be followed in the log (EX15).

    Args:
        name: The file name.
        text: What was written.
        now_ms: Its generatedAt, UTC ms.
        holds: What it holds, e.g. "312 items" or "4 open actions".
    """
    log.info("wrote %s: %d bytes, generatedAt %d, exportVersion %d, %s", name, len(text.encode("utf-8")), now_ms,
             EXPORT_VERSION, holds)


def _write(db: Database, path: Path, text: str, limit: int, now_ms: int, sleep: Callable[[float], None],
           replace: Callable[[Path, Path], None]) -> bool:
    """Write one addon file and keep its Status lines current (FM14, size budget).

    Args:
        db: Database (Status lines).
        path: The file.
        text: Its contents.
        limit: Its size budget, bytes.
        now_ms: Now.
        sleep: Waits between write attempts.
        replace: As write_atomic.

    Returns:
        True when written.
    """
    ok = write_atomic(path, text, sleep, replace)
    size = len(text.encode("utf-8"))
    with db.transaction():
        if ok:
            db.clear_status(f"addon_file:{path.name}")
        else:
            db.set_status(f"addon_file:{path.name}", "warn", "addon_file_blocked", {"file": path.name}, now_ms)
        if size > limit:
            db.set_status(f"addon_file_size:{path.name}", "warn", "addon_file_large",
                          {"file": path.name, "bytes": size, "limit": limit}, now_ms)
        else:
            db.clear_status(f"addon_file_size:{path.name}")
    return ok


def portfolio_fields(db: Database, cfg: Config) -> PortfolioFields:
    """The portfolio snapshot, gold, reservations and positions PortfolioPlan.lua carries (SPEC §3.5, EX18).

    Args:
        db: Database (each market's published snapshot, the characters' gold).
        cfg: Config (markets).

    Returns:
        PortfolioFields: market -> portfolio string, character -> "copper,atMs" for every character on a configured
        market, character -> reserved copper, and character -> position strings, from the last published plan.
    """
    updated = {c: at for c, _realm, _faction, _m, at in db.characters()}
    out = PortfolioFields({}, {}, {}, {})
    for market in cfg.markets:
        for char_id, gold in db.characters_in(market):
            out.gold[char_id] = f"{gold},{'' if updated.get(char_id) is None else updated[char_id]}"
        snap = db.portfolio_snapshot(market)
        if snap is None:
            continue
        out.portfolio[market] = portfolio_text(snap)
        out.reserved.update(snap["reserved"])
        for row in snap["positions"]:
            out.positions.setdefault(row[1], []).append(position_text(row))
    return out


def export_plan(db: Database, cfg: Config, now_ms: int, sleep: Callable[[float], None],
                replace: Callable[[Path, Path], None] = os.replace) -> bool:
    """Write PortfolioPlan.lua: open actions, item rules and ingestedUntil (SPEC §3.5, §2.2, EX3).

    Called only after the database commit that changed them (a refresh, a SavedVariables
    read, the status pass), so the addon never prunes a row AHSync hasn't stored.

    Args:
        db: Database.
        cfg: Config: markets and wow_path.
        now_ms: Now (generatedAt).
        sleep: Waits between write attempts.
        replace: As write_atomic.

    Returns:
        True when written; False when the AddOns folder is missing or the write failed.
    """
    folder = data_folder(cfg)
    if not folder.is_dir():
        return False
    char_ids = sorted(c for m in cfg.markets for c, _ in db.characters_in(m))
    records = {}
    for market in cfg.markets:
        for key, r in sell_records(db, [c for c, _ in db.characters_in(market)], market, now_ms, cfg).items():
            records[key] = f"{1 if r.not_selling else 0};{record_line(r)}"             # EX8
    actions = db.addon_actions(char_ids)
    text = plan_lua(actions, db.rule_rows(), ingested_until(db), now_ms, records, db.timed_reserves(char_ids),   # EX12
                    portfolio_fields(db, cfg))                                                                   # EX18
    ok = _write(db, folder / PLAN_FILE, text, MAX_PLAN_BYTES, now_ms, sleep, replace)
    if ok:
        _log_written(PLAN_FILE, text, now_ms, f"{len(actions)} open actions")
    return ok
