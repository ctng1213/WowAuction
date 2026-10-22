"""The Gathering page, built at the full refresh and at every re-plan after a SavedVariables read (SPEC §3.4E, §3.5).

This module only gathers inputs at the edge: it reads the catalog data file
(`ahsync/data/gathering_catalog.json`), each catalog item's newest `current` price and its
intelligence (no history read, §3.1.1), the characters' professions, their farming sessions and
manual rates (M17). engine.gathering
validates the catalog and engine.gathering_view makes every cell and text. A row the catalog
can't use is skipped with a Status line (GA2); a catalog that can't be read leaves the page empty
with a Status line, and the rest of the refresh carries on.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from ahsync.config import Config, session_settings
from ahsync.engine.gathering import CatalogItem, catalog, skipped_text
from ahsync.engine.gathering_view import GatheringWorld, PriceFacts, gathering_view
from ahsync.engine.sellprob import cheapest_other
from ahsync.engine.sessions import Candidate, GatherEvent, Session, SessionSettings, sessions
from ahsync.history.reads import CurrentPrice, ItemInfo
from ahsync.intel.contract import ItemIntel

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "gathering_catalog.json"
STATUS_TOPIC = "gathering_catalog"


class GatheringReads(Protocol):
    """What the page reads from the database (db.sqlite.Database satisfies it)."""

    def current_price(self, market: str, key: int) -> CurrentPrice | None:
        """The newest price of an item across sources (history.reads).

        Args:
            market: Market id.
            key: Item key.

        Returns:
            The newest current row, or None when never seen.
        """

    def character_sheets(self, char_ids: list[str]) -> list[tuple]:
        """The characters' captured sheets with their professions (state.reads).

        Args:
            char_ids: Character ids.

        Returns:
            (id, class, level, faction, market, gold, recipes, updated ms, ((profession, skill, max), ...)) by id.
        """

    def farming_sessions(self, char_ids: Sequence[str]) -> list[Session]:
        """The stored confirmed farming sessions (state.gathering).

        Args:
            char_ids: The characters.

        Returns:
            Sessions with their items.
        """

    def events_after_sessions(self, char_ids: Sequence[str]) -> list[GatherEvent]:
        """Each character's gather events after her last stored session (state.gathering).

        Args:
            char_ids: The characters.

        Returns:
            The events.
        """

    def manual_rates(self, char_ids: Sequence[str]) -> dict[tuple[str, int], int]:
        """The manual rate overrides (state.gathering).

        Args:
            char_ids: The characters.

        Returns:
            (character id, item key) -> items per hour.
        """

    def set_status(self, topic: str, level: str, kind: str, data: dict, at: int) -> None:
        """Set a Status line.

        Args:
            topic: Stable id.
            level: 'info', 'warn' or 'error'.
            kind: Message kind (engine.status_view).
            data: Its fields.
            at: Time, ms.
        """

    def clear_status(self, topic: str) -> None:
        """Clear a Status line.

        Args:
            topic: The topic to clear.
        """


def read_catalog(path: Path = CATALOG_PATH) -> tuple[list[Any], str]:
    """Read the catalog data file's rows (SPEC §3.4E.1).

    Args:
        path: The JSON file: {"_comment", "version", "items": [...]}.

    Returns:
        (its "items" rows, "") or ([], why it couldn't be read).
    """
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))["items"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [], f"gathering catalog not read ({type(error).__name__}): the Gathering page is empty"
    return (rows, "") if isinstance(rows, list) else ([], "gathering catalog not read (no items list): the Gathering page is empty")


def load_catalog(db: GatheringReads, now_ms: int, path: Path = CATALOG_PATH) -> list[CatalogItem]:
    """Read and validate the catalog, and keep its Status line current (GA2).

    Args:
        db: Where the Status line goes.
        now_ms: Now, for the Status line.
        path: The catalog file.

    Returns:
        The valid catalog items, by key. Side effect: sets the `gathering_catalog` Status line ("gathering catalog:
        2 rows skipped (…)", or why the file couldn't be read), or clears it when every row is good.
    """
    rows, unread = read_catalog(path)
    items, skipped = catalog(rows)
    text = unread or skipped_text(skipped)
    if text:
        db.set_status(STATUS_TOPIC, "warn", "gathering_catalog", {"text": text}, now_ms)
    else:
        db.clear_status(STATUS_TOPIC)
    return items


def price_facts(key: int, cur: CurrentPrice | None, s: ItemIntel | None, info: ItemInfo | None) -> PriceFacts:
    """One catalog item's market facts from the newest `current` row and its intelligence.

    Args:
        key: The item key.
        cur: Its newest current price across sources, or None when never seen.
        s: Its intelligence, or None.
        info: Its item metadata (vendor price, quality), or None.

    Returns:
        The PriceFacts; the listed supply is the intelligence's, else the units at the newest price.
    """
    price = cur.price if cur else None
    supply = s.units_on_ah if s and s.units_on_ah is not None else (cur.qty if cur and price is not None else None)
    known = s is not None and not s.volume_unknown
    return PriceFacts(price, cur.at if cur else None, cur.src if cur else None, supply,
                      info.vendor_sell if info else None, (info.quality or 1) if info else 1,
                      s.chance if known else None, s.sold_per_day if s else None,
                      s.volume_source if s else None, s.volume_kind if s else None, s.conf if s else None,
                      cheapest_other(s.book or None, price) if s else price, s.med30 if s else None,
                      s.closes_30d if s else None)


def gathering_page(db: GatheringReads, market: str, char_ids: Sequence[str], stats: dict[int, ItemIntel],
                   items: dict[int, ItemInfo], now_ms: int, offset_ms: int, cfg: Config,
                   path: Path = CATALOG_PATH) -> dict:
    """The Gathering payload, without the gold pill (SPEC §3.5 `gathering`).

    Args:
        db: Current prices, character sheets and the Status line.
        market: Market id.
        char_ids: The market's characters.
        stats: Item key -> intelligence (with newer scans applied on a re-plan, IU6).
        items: Item metadata.
        now_ms: Now.
        offset_ms: Local time minus UTC.
        cfg: Config (the default chances).
        path: The catalog file.

    Returns:
        engine.gathering_view's payload, its rates from the stored sessions, the sessions since the last stored one
        and the manual overrides (farming_state).
    """
    catalog_items = load_catalog(db, now_ms, path)
    facts = {it.key: price_facts(it.key, db.current_price(market, it.key), stats.get(it.key), items.get(it.key))
             for it in catalog_items}
    sheets = db.character_sheets(list(char_ids))
    skills = {sheet[0]: {name: skill for name, skill, _ in sheet[8]} for sheet in sheets}
    synced = max((sheet[7] for sheet in sheets if sheet[7] is not None), default=None)
    found, open_ = farming_state(db, char_ids, now_ms, session_settings(cfg))
    world = GatheringWorld(catalog_items, facts, skills, list(char_ids), now_ms, offset_ms,
                           manual=db.manual_rates(list(char_ids)), sessions=found, open=open_, professions_ms=synced,
                           default_sell_prob=cfg.default_sell_prob,
                           default_sell_prob_cheapest=cfg.default_sell_prob_cheapest,
                           confirm_units=cfg.session_confirm_units, gap_minutes=cfg.session_gap_minutes)
    return gathering_view(world)


def farming_state(db: GatheringReads, char_ids: Sequence[str], now_ms: int,
                  settings: SessionSettings) -> tuple[list[Session], dict[str, Candidate]]:
    """The characters' confirmed sessions and open candidates as of now (SPEC §3.4E.4).

    The stored sessions, plus what the events after each character's last stored session give now: a candidate
    that ended since the last SavedVariables read (time passed) and the open candidate, neither stored.

    Args:
        db: The stored sessions and events.
        char_ids: The market's characters.
        now_ms: Now, ms.
        settings: session_confirm_units and session_gap_minutes.

    Returns:
        (confirmed sessions, character id -> open candidate).
    """
    tail, open_ = sessions(db.events_after_sessions(list(char_ids)), now_ms, settings.gap_ms, settings.confirm_units)
    return [*db.farming_sessions(list(char_ids)), *tail], open_
