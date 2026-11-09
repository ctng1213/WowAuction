"""Tests for ahsync/mcp/tools.py: the assistant's read-only tools on WP1's fixture world (SPEC §3.8.3)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.mcp.protocol import ToolError
from ahsync.mcp.server import open_read_only
from ahsync.mcp.tools import HANDLERS, ToolContext, get_page, item, list_markets
from tests.ahsync.mcp.conftest import MARKET
from tests.support.fake_clock import FakeClock

NOW = 1_794_000_000_000                  # 2026-11-06 UTC


@pytest.fixture()
def ctx(world):
    """A tool context on a read-only connection to the refreshed world."""
    db = open_read_only(world.db_path)
    yield ToolContext(db, world.config, world.clock())
    db.conn.close()


def test_MCP8_list_markets_dates_the_refresh_and_counts_its_items(ctx):
    intel = ctx.db.read_intel(MARKET)
    out = list_markets(ctx, {})
    assert out["markets"] == [{
        "market": MARKET, "refreshed_ms": intel.built_ms, "refreshed_utc": out["markets"][0]["refreshed_utc"],
        "prices_until_ms": intel.observations_until_ms or intel.built_ms,
        "prices_until_utc": out["markets"][0]["prices_until_utc"], "primary_source": "ahledger",
        "items": len(intel.items)}]
    assert out["markets"][0]["refreshed_utc"].endswith("Z") and out["now_utc"].startswith("20")
    assert list_markets(ctx, {})["markets"][0]["items"] > 0


def test_MCP11_get_page_serves_the_page_as_the_dashboard_orders_it(ctx):
    stored, _ = ctx.db.view("gathering", MARKET)
    first = get_page(ctx, {"page": "gathering", "limit": 2})
    data = first["data"]
    assert (first["page"], first["market"], first["cut"]) == ("gathering", MARKET, {"rows": 150})
    assert [r["key"] for r in data["rows"]] == [r["key"] for r in stored["rows"][:2]]
    assert data["header"] == stored["header"] and data["help"] == stored["help"]
    assert data["foot_all"] == stored["by_char"][""]["foot_all"]               # the All characters view's footers
    assert set(data["rate_cards"]) == {str(r["key"]) for r in data["rows"]}     # only the rows shown
    assert "details" not in data and "by_char" not in data
    assert len(data["characters"]) == 3                                       # plain lists stay whole

    by_price = get_page(ctx, {"page": "gathering", "sort": "price", "limit": 200})["data"]["rows"]
    prices = [r["sort"]["price"] for r in by_price]
    priced = [p for p in prices if p is not None]
    assert priced == sorted(priced, reverse=True) and prices == priced + [None] * (len(prices) - len(priced))
    cheapest = get_page(ctx, {"page": "gathering", "sort": "price", "ascending": True, "limit": 200})["data"]["rows"]
    low = [r["sort"]["price"] for r in cheapest]
    assert low[:len(priced)] == sorted(priced) and low[len(priced):] == [None] * (len(low) - len(priced))

    brugan = get_page(ctx, {"page": "gathering", "character": "Brugan-Grommash", "limit": 200})["data"]
    view = stored["by_char"]["Brugan-Grommash"]
    assert [r["key"] for r in brugan["rows"]] == view["keys"]
    for key, cells in view["cells"].items():
        row = next(r for r in brugan["rows"] if str(r["key"]) == key)
        assert all(row[k] == v for k, v in cells.items())
    assert brugan["foot"] == view["foot"]

    ores = get_page(ctx, {"page": "gathering", "search": "ore", "limit": 200})["data"]["rows"]
    assert ores and all("ore" in r["item"].lower() for r in ores)


@pytest.mark.parametrize("args, message", [
    ({"page": "nope"}, "page must be one of: portfolio, today, week"),
    ({"page": "gathering", "sort": "nope"}, "sort must be one of this page's sort keys: "),
    ({"page": "flip", "character": "Brugan-Grommash"}, "This page has no per-character view; only gathering and "
                                                       "inventory do"),
    ({"page": "gathering", "character": "Cara-Grommash"}, "No character Cara-Grommash on this page; its characters "
                                                          "are: Amaya-Grommash, Brugan-Grommash"),
    ({"page": "portfolio", "sort": "profit"}, "This page has no single list of rows to sort or search"),
    ({"page": "flip", "limit": 0}, "limit must be a whole number from 1 to 200"),
    ({"page": "flip", "limit": True}, "limit must be a whole number from 1 to 200"),
    ({"page": "flip", "market": "forever.rp.alliance.us"}, "market must be one of: forever.pvp.horde.us"),
])
def test_MCP13_get_page_errors_are_plain_sentences(ctx, args, message):
    with pytest.raises(ToolError) as error:
        get_page(ctx, args)
    assert error.value.message.startswith(message)


def test_MCP11_every_page_answers_within_its_lists_limit(ctx):
    for page in ("portfolio", "today", "week", "on_ah", "disenchant", "craft", "flip", "gathering", "inventory",
                 "history", "rules", "characters", "status"):
        out = get_page(ctx, {"page": page, "limit": 5})
        assert out["page"] == page and isinstance(out["data"], dict)
        assert all(n > 5 for n in out["cut"].values())
        assert len(json.dumps(out)) < 256 * 1024
    inventory = get_page(ctx, {"page": "inventory", "character": "Amaya-Grommash"})["data"]
    assert all(r["character"] == "Amaya" for r in inventory["rows"]) and "details" not in inventory


def test_MCP12_item_is_the_dashboards_item_detail(ctx):
    inventory, _ = ctx.db.view("inventory", MARKET)
    linen = item(ctx, {"name": "linen cloth"})                               # case ignored
    assert (linen["market"], linen["key"], linen["name"]) == (MARKET, 2589, "Linen Cloth")
    shown = inventory["details"]["2589"]
    assert linen["detail"] == {k: v for k, v in shown.items() if k != "heat"}
    assert item(ctx, {"key": 2589, "heatmaps": True})["detail"] == shown
    assert linen["built_utc"].endswith("Z")


@pytest.mark.parametrize("args, message", [
    ({"name": "Nonexistent Thing"}, "No item named Nonexistent Thing"),
    ({}, "Give an item's key or its name"),
    ({"key": "2589"}, "key must be a whole number (an item key)"),
    ({"key": 999999}, "forever.pvp.horde.us has no market data for 999999 (key 999999)"),
    ({"key": 2589, "market": "elsewhere"}, "market must be one of: forever.pvp.horde.us"),
])
def test_MCP13_item_errors_are_plain_sentences(ctx, args, message):
    with pytest.raises(ToolError) as error:
        item(ctx, args)
    assert error.value.message == message


def test_MCP13_before_the_first_refresh_every_tool_says_so(tmp_path: Path):
    Database.open(tmp_path / "ahsync.db", NOW, None).conn.close()
    db = open_read_only(tmp_path / "ahsync.db")
    empty = ToolContext(db, Config(markets=(MARKET,)), FakeClock(NOW))
    assert list_markets(empty, {})["markets"][0] | {"primary_source": None} == {
        "market": MARKET, "refreshed_ms": None, "refreshed_utc": None, "prices_until_ms": None,
        "prices_until_utc": None, "primary_source": None, "items": 0}
    for name, args in (("get_page", {"page": "flip"}), ("item", {"key": 2589})):
        with pytest.raises(ToolError) as error:
            HANDLERS[name](empty, args)
        assert error.value.message == ("No refresh has finished yet for forever.pvp.horde.us: AHSync fills its pages "
                                       "after its first full refresh.")
    assert get_page(empty, {"page": "status"})["page"] == "status"          # built on request, like the dashboard's
    db.conn.close()


def test_a_search_that_matches_nothing_still_takes_a_valid_sort_and_nested_cuts_keep_their_paths(ctx):
    none = get_page(ctx, {"page": "gathering", "search": "zzz-no-such-item", "sort": "price"})
    assert none["data"]["rows"] == [] and "rows" not in none["cut"]
    portfolio = get_page(ctx, {"page": "portfolio", "limit": 1})
    assert all("[" in path or "." not in path or not path.startswith("rows") for path in portfolio["cut"])
    assert len(set(portfolio["cut"])) == len(portfolio["cut"])
