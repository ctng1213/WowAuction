"""The AI assistant server's tools (SPEC §3.8.3): read-only views of what AHSync already published.

Each tool takes a ToolContext and the call's arguments and returns JSON-serializable data, or raises ToolError with a
plain sentence. Nothing here writes, refreshes or reads more history than a dashboard page does (SPEC §3.1.2).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from ahsync.app.status_page import status_page
from ahsync.clock import Clock
from ahsync.config import Config, market_sources
from ahsync.db.sqlite import Database
from ahsync.export.addon_files import coverage30
from ahsync.engine.gathering import catalog
from ahsync.mcp import query as data
from ahsync.mcp.protocol import PAGES, ToolError
from ahsync.mcp.stats import MarketStats
from ahsync.recommend.gathering_page import read_catalog
from ahsync.recommend.inventory_page import detail_of
from ahsync.recommend.plan_pages import item_looks

DEFAULT_LIMIT, MAX_LIMIT = 25, 200
DROPPED = ("details",)                     # per-item details are the `item` tool's; a page would be megabytes
NAME_FIELDS = ("item", "product", "name")  # where a row names its item, for `search`
NO_REFRESH = "No refresh has finished yet for {market}: AHSync fills its pages after its first full refresh."


@dataclass(frozen=True)
class ToolContext:
    """What every tool reads: the database on a read-only connection, the config and the clock; the data tools also
    the database file and the market table (SPEC §3.8.4)."""

    db: Database
    config: Config
    clock: Clock
    db_path: Path | None = None
    stats: MarketStats | None = None


def _utc_text(ms: int | None) -> str | None:
    """A UTC time as ISO 8601 text, for an assistant to read and convert.

    Args:
        ms: UTC Unix ms, or None.

    Returns:
        "2026-11-08T21:07:00Z", or None.
    """
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _market(ctx: ToolContext, args: Mapping) -> str:
    """The call's market: the one named, else the first configured.

    Args:
        ctx: The context.
        args: The call's arguments.

    Returns:
        A configured market id.

    Raises:
        ToolError: The market isn't configured.
    """
    market = args.get("market") or ctx.config.markets[0]
    if market not in ctx.config.markets:
        raise ToolError("market must be one of: " + ", ".join(ctx.config.markets))
    return market


def list_markets(ctx: ToolContext, args: Mapping) -> dict:
    """Every configured market with its refresh time, primary source and item count (MCP8).

    Args:
        ctx: The context.
        args: None used.

    Returns:
        {"markets": [{"market", "refreshed_ms", "refreshed_utc", "prices_until_ms", "prices_until_utc",
        "primary_source", "items"}], "now_utc"}; a market with no refresh yet has None times and 0 items.
    """
    out = []
    for market in ctx.config.markets:
        intel = ctx.db.read_intel(market)
        built = intel.built_ms if intel else None
        until = (intel.observations_until_ms or intel.built_ms) if intel else None
        out.append({"market": market, "refreshed_ms": built, "refreshed_utc": _utc_text(built),
                    "prices_until_ms": until, "prices_until_utc": _utc_text(until),
                    "primary_source": market_sources(ctx.config, market).primary,
                    "items": len(intel.items) if intel else 0})
    return {"markets": out, "now_utc": _utc_text(ctx.clock.now_ms())}


def _limit(args: Mapping) -> int:
    """The call's list limit.

    Args:
        args: The call's arguments.

    Returns:
        1-200; 25 when not given.

    Raises:
        ToolError: Not a whole number in range.
    """
    limit = args.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ToolError(f"limit must be a whole number from 1 to {MAX_LIMIT}")
    return limit


def _character_view(payload: dict, character: str | None) -> dict:
    """A page with its per-character view applied, as the dashboard's Character filter does (D75, SPEC §3.5 by_char).

    Args:
        payload: A stored page.
        character: A character id; None or "" for All characters.

    Returns:
        The page with that view's rows and fields, without by_char.

    Raises:
        ToolError: A character was asked of a page without views, or the page has no view for it.
    """
    views = payload.get("by_char")
    if not isinstance(views, dict):
        if character:
            raise ToolError("This page has no per-character view; only gathering and inventory do")
        return payload
    view = views.get(character or "")
    if view is None:
        known = ", ".join(c for c, _ in payload.get("characters", []) if c)
        raise ToolError(f"No character {character} on this page; its characters are: {known}")
    page = {k: v for k, v in payload.items() if k != "by_char"}
    if "keys" in view:                     # Gathering: each row once, a view's keys and the cells that differ
        shared = {str(r["key"]): r for r in payload.get("rows", [])}
        cells = view.get("cells", {})
        page["rows"] = [{**shared[str(k)], **cells.get(str(k), {})} for k in view["keys"] if str(k) in shared]
        page.update({k: v for k, v in view.items() if k not in ("keys", "cells")})
    else:                                  # Inventory: a complete view of rows and tiles
        page.update(view)
    return page


def _sorted_rows(rows: list, key: str, ascending: bool) -> list:
    """Rows by one of their engine sort keys, as the dashboard's sortRows: no value last, ties in served order.

    Args:
        rows: A page's rows.
        key: A sort key.
        ascending: Lowest first instead of highest first.

    Returns:
        The rows sorted.

    Raises:
        ToolError: No row has that sort key.
    """
    known = sorted({k for r in rows if isinstance(r.get("sort"), dict) for k in r["sort"]})
    if key not in known:
        raise ToolError("sort must be one of this page's sort keys: " + (", ".join(known) or "none"))
    present = [r for r in rows if (r.get("sort") or {}).get(key) is not None]
    missing = [r for r in rows if (r.get("sort") or {}).get(key) is None]
    try:                                   # list.sort is stable, reversed too: ties keep the served order
        present.sort(key=lambda r: r["sort"][key], reverse=not ascending)
    except TypeError:
        raise ToolError(f"The rows' {key} values can't be compared") from None
    return present + missing


def _trim(value: object, limit: int, path: str, cut: dict) -> object:
    """Every list of records (dicts) in a page cut to `limit` items, recording each cut list's full length by its
    path; lists of plain values (characters, filters, labels) stay whole.

    Args:
        value: A page or part of one.
        limit: Items kept per list.
        path: Where value sits ("rows", "pool.rows").
        cut: Path -> full length, filled in.

    Returns:
        The trimmed copy.
    """
    if isinstance(value, dict):
        return {k: _trim(v, limit, f"{path}.{k}" if path else k, cut) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) > limit and any(isinstance(v, dict) for v in value):
            cut[path or "."] = len(value)
            value = value[:limit]
        return [_trim(v, limit, path, cut) for v in value]
    return value


def _stored_page(ctx: ToolContext, page: str, market: str) -> tuple[dict, int]:
    """A page's payload: the stored view, or the Status page built now (it changes between refreshes).

    Args:
        ctx: The context.
        page: A name from PAGES.
        market: A configured market.

    Returns:
        (payload, built UTC ms).

    Raises:
        ToolError: No refresh has staged the page yet.
    """
    now = ctx.clock.now_ms()
    if page == "status":
        return status_page(ctx.db, ctx.config, now, ctx.clock.local_offset_ms(now), None), now
    stored = ctx.db.view(page, market)
    if stored is None:
        raise ToolError(NO_REFRESH.format(market=market))
    return stored


def get_page(ctx: ToolContext, args: Mapping) -> dict:
    """One dashboard page as the dashboard shows it, its lists cut to `limit` (MCP11).

    Args:
        ctx: The context.
        args: {"page", "market", "character", "sort", "ascending", "limit", "search"} (SPEC §3.8.3).

    Returns:
        {"page", "market", "built_utc", "data": the page without per-item details, "cut": {list path: full length}}.

    Raises:
        ToolError: An unknown page, market, character or sort key, a bad limit, or no refresh yet.
    """
    page = args.get("page")
    if page not in PAGES:
        raise ToolError("page must be one of: " + ", ".join(PAGES))
    market, limit = _market(ctx, args), _limit(args)
    payload, built = _stored_page(ctx, page, market)
    data = {k: v for k, v in _character_view(payload, args.get("character")).items() if k not in DROPPED}
    rows = data.get("rows")
    if isinstance(rows, list):
        search = str(args.get("search") or "").casefold()
        if search:
            rows = [r for r in rows if any(search in str(r.get(f, "")).casefold() for f in NAME_FIELDS)]
        if args.get("sort"):
            rows = _sorted_rows(rows, str(args["sort"]), bool(args.get("ascending")))
        data["rows"] = rows
    elif args.get("sort") or args.get("search"):
        raise ToolError("This page has no single list of rows to sort or search")
    cut: dict = {}
    trimmed = _trim(data, limit, "", cut)
    if isinstance(trimmed.get("rate_cards"), dict) and isinstance(trimmed.get("rows"), list):
        kept = {str(r.get("key")) for r in trimmed["rows"]}       # Gathering: the rate cards of the rows shown
        trimmed["rate_cards"] = {k: v for k, v in trimmed["rate_cards"].items() if k in kept}
    return {"page": page, "market": market, "built_utc": _utc_text(built), "data": trimmed, "cut": cut}


def _item_key(ctx: ToolContext, args: Mapping, catalog: Mapping) -> int:
    """The item a call names, by key or by exact name (case ignored).

    Args:
        ctx: The context.
        args: {"key"} or {"name"}.
        catalog: Item key -> ItemInfo.

    Returns:
        An item key.

    Raises:
        ToolError: Neither given, a key that isn't a whole number, or no item (or several) by that name.
    """
    if "key" in args:
        key = args["key"]
        if isinstance(key, bool) or not isinstance(key, int):
            raise ToolError("key must be a whole number (an item key)")
        return key
    name = str(args.get("name") or "").strip()
    if not name:
        raise ToolError("Give an item's key or its name")
    found = sorted(k for k, info in catalog.items() if (info.name or "").casefold() == name.casefold())
    if not found:
        raise ToolError(f"No item named {name}")
    if len(found) > 1:
        raise ToolError(f"Several items are named {name}; ask by key: " + ", ".join(map(str, found)))
    return found[0]


def item(ctx: ToolContext, args: Mapping) -> dict:
    """One item's detail as the dashboard's item detail shows it (MCP12).

    Args:
        ctx: The context.
        args: {"key"} or {"name"}, "market", "heatmaps".

    Returns:
        {"market", "key", "name", "built_utc", "detail"}: the detail without its heatmaps unless asked.

    Raises:
        ToolError: An unknown market or item, an item the market has no data for, or no refresh yet.
    """
    market = _market(ctx, args)
    intel = ctx.db.read_intel(market)
    if intel is None:
        raise ToolError(NO_REFRESH.format(market=market))
    catalog = ctx.db.items()
    key = _item_key(ctx, args, catalog)
    stats = intel.items
    if key not in stats:
        name = catalog[key].name if key in catalog else str(key)
        raise ToolError(f"{market} has no market data for {name} (key {key})")
    now = ctx.clock.now_ms()
    names = {k: info.name for k, info in catalog.items()}
    looks = item_looks({key}, stats, catalog)
    detail = detail_of(key, looks[key], stats, catalog, names, ctx.clock.local_offset_ms(now),
                       coverage30(ctx.db, intel, now))
    if not args.get("heatmaps"):
        detail = {k: v for k, v in detail.items() if k != "heat"}
    return {"market": market, "key": key, "name": names.get(key, str(key)), "built_utc": _utc_text(intel.built_ms),
            "detail": detail}


@lru_cache(maxsize=1)
def catalog_keys() -> dict[str, frozenset[int]]:
    """The gathering catalog's item keys per category (Fishing, Herbalism, Mining, Skinning, Monster).

    Returns:
        Category -> keys; empty when the catalog can't be read.
    """
    items, _ = catalog(read_catalog()[0])
    out: dict[str, set[int]] = {}
    for it in items:
        for category in it.categories:
            out.setdefault(category, set()).add(it.key)
    return {k: frozenset(v) for k, v in out.items()}


def _fresh_stats(ctx: ToolContext) -> MarketStats:
    """The market table, rebuilt first when a newer refresh has been published (SPEC §3.8.4).

    Args:
        ctx: The context.

    Returns:
        The table.

    Raises:
        ToolError: The data tools aren't available (no table: the server is stopping).
    """
    if ctx.stats is None or ctx.db_path is None:
        raise ToolError("The data tools aren't available; ask again in a moment.")
    ctx.stats.refresh(ctx.db, ctx.config.markets)
    return ctx.stats


def describe_data(ctx: ToolContext, args: Mapping) -> dict:
    """The tables the query tool may read, with units and examples (MCP9).

    Args:
        ctx: The context.
        args: None used.

    Returns:
        query.describe_data's answer.
    """
    conn = data.open_reader(ctx.db_path, _fresh_stats(ctx))
    try:
        return data.describe_data(conn)
    finally:
        conn.close()


def query(ctx: ToolContext, args: Mapping) -> dict:
    """One read-only SQL query (MCP10, SEC20).

    Args:
        ctx: The context.
        args: {"sql"}.

    Returns:
        query.run_query's answer.

    Raises:
        ToolError: The guard's refusals and the limits (SPEC §3.8.5).
    """
    conn = data.open_guarded(ctx.db_path, _fresh_stats(ctx))
    try:
        return data.run_query(conn, args.get("sql"))
    finally:
        conn.close()


def item_group_stats(ctx: ToolContext, args: Mapping) -> dict:
    """Every item of one group with its market_stats row (MCP17).

    Args:
        ctx: The context.
        args: {"group", "market", "sort", "ascending", "min_sold_per_day", "limit"}.

    Returns:
        query.item_group_stats's answer.
    """
    conn = data.open_reader(ctx.db_path, _fresh_stats(ctx))
    try:
        return data.item_group_stats(conn, args, catalog_keys(), ctx.config.markets)
    finally:
        conn.close()


# Tool name -> its function (SPEC §3.8.3).
HANDLERS: dict[str, Callable[[ToolContext, Mapping], dict]] = {
    "list_markets": list_markets, "describe_data": describe_data, "query": query,
    "item_group_stats": item_group_stats, "get_page": get_page, "item": item,
}
