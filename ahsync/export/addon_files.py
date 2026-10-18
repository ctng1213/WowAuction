"""Writing the addon's generated files into Interface/AddOns/AHProfit_Data (SPEC §3.5, §5.2).

Every write is atomic: the text goes to a temp file, is read back and checked, then
renamed over the old file. A blocked rename (file locked, antivirus) is retried 3 times
over 10 s; if it still fails the old file stays and Status warns (FM14). AHSync only
ever replaces these files and never deletes them, because WoW sees only files that
existed when the client started (SPEC §3.5).
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.export.market_lua import market_lua
from ahsync.export.plan_lua import plan_lua
from ahsync.state.importer import ingested_until

DATA_FOLDER = ("Interface", "AddOns", "AHProfit_Data")
MARKET_FILE = "MarketIntelligence.lua"
PLAN_FILE = "PortfolioPlan.lua"
ATTEMPTS = 3
RETRY_WAIT_S = 5               # 3 attempts, 5 s apart: 10 s in all (SPEC §5.2)
SCAN_DAYS_MS = 7 * 86_400_000  # targeted scans this recent put an item on the shortlist (EX1)
MAX_MARKET_BYTES = 2 * 1024 * 1024
MAX_PLAN_BYTES = 200 * 1024

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
    return _write(db, folder / MARKET_FILE, market_lua(intels, shortlists, char_markets, now_ms), MAX_MARKET_BYTES,
                  now_ms, sleep, replace)


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
    text = plan_lua(db.addon_actions(char_ids), db.rule_rows(), ingested_until(db), now_ms)
    return _write(db, folder / PLAN_FILE, text, MAX_PLAN_BYTES, now_ms, sleep, replace)
