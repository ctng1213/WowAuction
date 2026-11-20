"""The Cooldowns page's edge: the curated catalog file and its Status line (SPEC §3.4F.1, §3.5).

This module reads the catalog data file (`ahsync/data/cooldown_catalog.json`), and engine.cooldowns
validates it. A row the catalog can't use is skipped with a Status line (CD2); a catalog that can't be
read leaves the page empty with a Status line, and the rest of the refresh carries on. Built CD1: the
catalog's reading and its Status line. Built CD3: the staged page (cooldowns_page), from each character's
stored skills, known recipes, bags, bank and ready times (never recalculated, D1) and each catalog item's
newest price; engine.cooldowns_view.render applies the clock at each request (CD20).
"""
from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Protocol

from ahsync.engine.cooldowns import CooldownRecipe, Holder, catalog, catalog_keys, catalog_text
from ahsync.engine.cooldowns_view import PriceFact, stage
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel
from ahsync.recommend.gathering_page import newest_price

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "cooldown_catalog.json"
STATUS_TOPIC = "cooldown_catalog"


class StatusWrites(Protocol):
    """Where the catalog's Status line goes (db.sqlite.Database satisfies it)."""

    def set_status(self, topic: str, level: str, kind: str, data: dict, at: int) -> None:
        """Set a topic's Status line (db.sqlite).

        Args:
            topic: Stable id.
            level: 'info', 'warn' or 'error'.
            kind: Message kind.
            data: Structured fields.
            at: Time, ms.
        """


def read_catalog(path: Path = CATALOG_PATH) -> tuple[list[Any], int | None, str]:
    """Read the catalog data file's rows and version (SPEC §3.4F.1).

    Args:
        path: The JSON file: {"_comment", "version", "recipes": [...]}.

    Returns:
        (its "recipes" rows, its version, "") or ([], None, why it couldn't be read). A version that
        isn't a positive int is None.
    """
    unread = "cooldown catalog not read ({}): the Cooldowns page is empty"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        rows = document["recipes"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [], None, unread.format(type(error).__name__)
    if not isinstance(rows, list):
        return [], None, unread.format("no recipes list")
    version = document.get("version")
    return rows, version if isinstance(version, int) and not isinstance(version, bool) and version > 0 else None, ""


def load_catalog(db: StatusWrites, now_ms: int, path: Path = CATALOG_PATH) -> tuple[list[CooldownRecipe], int | None]:
    """Read and validate the catalog, and keep its Status line current (CD1, CD2).

    Args:
        db: Where the Status line goes.
        now_ms: Now, for the Status line.
        path: The catalog file.

    Returns:
        (the valid rows by spell id, the file's version). Side effect: sets the `cooldown_catalog` Status line:
        "cooldown catalog: 14 recipes" (info), or the skipped rows or why the file couldn't be read (warn).
    """
    rows, version, unread = read_catalog(path)
    kept, skipped = catalog(rows)
    text = unread or catalog_text(kept, skipped)
    db.set_status(STATUS_TOPIC, "warn" if unread or skipped else "info", "cooldown_catalog", {"text": text}, now_ms)
    return kept, version


def holders(db: Any, char_ids: Sequence[str]) -> list[Holder]:
    """The characters as the Cooldowns page sees them: what the addon stored, never recalculated (SPEC §3.4F.2, D1).

    Args:
        db: The database (character sheets, known spells, holdings, ready times).
        char_ids: The market's characters.

    Returns:
        One Holder per character, by id.
    """
    ids = list(char_ids)
    known, ready = db.known_spells(ids), db.recipe_cooldowns(ids)
    places: dict[tuple[str, str], dict[int, int]] = {}
    for c, place, key, qty, _bound in db.holdings(ids):
        bucket = places.setdefault((c, place), {})
        bucket[key] = bucket.get(key, 0) + qty
    out = []
    for c, _cls, _level, _faction, _market, _gold, _recipes, updated, profs in db.character_sheets(ids):
        out.append(Holder(c, c.split("-")[0], {name: skill for name, skill, _ in profs}, known.get(c, frozenset()),
                          places.get((c, "bags"), {}), places.get((c, "bank"), {}),
                          {s: (r[0], r[2]) for (cc, s), r in ready.items() if cc == c}, updated))
    return out


def cooldowns_page(db: Any, market: str, char_ids: Sequence[str], stats: dict[int, ItemIntel],
                   items: dict[int, ItemInfo], now_ms: int, path: Path = CATALOG_PATH,
                   prices_ms: int | None = None) -> dict:
    """The staged Cooldowns payload (SPEC §3.4F.6, §3.5 `cooldowns`); the server renders it at each request.

    Args:
        db: Current prices, the characters' stored data and the Status line.
        market: Market id.
        char_ids: The market's characters.
        stats: Item key -> intelligence (sales per day).
        items: Item metadata (names).
        now_ms: Now.
        path: The catalog file.
        prices_ms: A cloud document's prices time, UTC ms; None for a desktop-built one (as Gathering, GA35).

    Returns:
        engine.cooldowns_view.stage's payload. Side effect: the catalog's Status line (load_catalog).
    """
    recipes, version = load_catalog(db, now_ms, path)
    keys = catalog_keys(recipes)
    prices = {}
    for key in sorted(keys):
        cur = newest_price(db.current_price(market, key), stats.get(key), prices_ms)
        sold = stats[key].sold_per_day if key in stats else None
        prices[key] = PriceFact(cur.price if cur else None, cur.at if cur else None, sold)
    names = {key: items[key].name if key in items else f"item {key}" for key in sorted(keys)}
    return stage(recipes, version, holders(db, char_ids), prices, names, now_ms)
