"""Tests for ahsync/mcp/query.py: read-only SQL, the data map and item groups (SPEC §3.8.3–§3.8.5)."""
from __future__ import annotations

import re
import sqlite3

import pytest

from ahsync.mcp import query as data
from ahsync.mcp.protocol import ToolError
from ahsync.mcp.server import open_read_only
from ahsync.mcp.stats import COLUMNS, TABLE, MarketStats
from ahsync.mcp.tools import HANDLERS, ToolContext, catalog_keys
from tests.ahsync.mcp.conftest import MARKET


@pytest.fixture()
def ctx(world):
    """A tool context with the market table built, on the refreshed world."""
    db = open_read_only(world.db_path)
    stats = MarketStats()
    stats.build(db, world.config.markets)
    yield ToolContext(db, world.config, world.clock(), world.db_path, stats)
    stats.close()
    db.conn.close()


def _call(ctx, name: str, args: dict) -> dict:
    """Call one tool.

    Args:
        ctx: The context.
        name: A tool.
        args: Its arguments.

    Returns:
        Its answer.
    """
    return HANDLERS[name](ctx, args)


def test_MCP9_describe_data_names_every_allowed_table_with_units_and_examples_that_run(ctx):
    out = _call(ctx, "describe_data", {})
    assert set(out["tables"]) == data.ALLOWED_TABLES
    assert {"meta", "sync_state", "fetch_failures", "status", "views", "intel", "plan_new"}.isdisjoint(out["tables"])
    stats_cols = {c["name"]: c for c in out["tables"][TABLE]["columns"]}
    assert list(stats_cols) == list(COLUMNS) and "copper" in stats_cols["med30"]["about"]
    candles = {c["name"]: c for c in out["tables"]["candles_h1"]["columns"]}
    assert candles["close"]["unit"] == "copper" and candles["t"]["unit"].startswith("UTC Unix ms")
    assert all(row["row"] for row in out["tables"].values())
    for example in out["examples"]:
        assert _call(ctx, "query", {"sql": example["sql"]})["columns"]          # each runs as given


def test_MCP10_a_query_answers_columns_and_rows_cut_at_500_rows_or_256_kb(ctx):
    count = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 600) "
    rows = _call(ctx, "query", {"sql": count + "SELECT i FROM n"})
    assert (rows["columns"], rows["row_count"], rows["truncated"], rows["rows"][-1]) == (["i"], 500, True, [500])
    assert _call(ctx, "query", {"sql": count + "SELECT count(*) FROM n"})["rows"] == [[600]]   # a CTE may be counted
    wide = _call(ctx, "query", {"sql": count + f"SELECT i, '{'x' * 1000}' AS pad FROM n"})
    assert wide["truncated"] and wide["row_count"] < 500
    assert sum(len(str(r)) for r in wide["rows"]) <= 256 * 1024
    linen = _call(ctx, "query", {"sql": f"SELECT name, cur FROM {TABLE} WHERE key = 2589"})
    assert linen["rows"] == [["Linen Cloth", ctx.db.read_intel(MARKET).items[2589].cur]] and not linen["truncated"]


SEC20 = [
    ("INSERT INTO items(key, item_id) VALUES (1, 1)", "Not allowed: only SELECT queries may run."),
    ("DELETE FROM posts", "Not allowed: only SELECT queries may run."),
    ("ATTACH 'x.db' AS x", "Not allowed: only SELECT queries may run."),
    ("PRAGMA writable_schema = 1", "Not allowed: only SELECT queries may run."),
    ("WITH x AS (SELECT 1) INSERT INTO items(key, item_id) SELECT 1, 1 FROM x", "Not allowed"),
    ("SELECT load_extension('x')", "Not allowed: only SELECT queries reading the tables"),
    ("SELECT * FROM meta", "Not allowed: only SELECT queries reading the tables"),
    ("SELECT * FROM sqlite_master", "Not allowed: only SELECT queries reading the tables"),
    ("SELECT * FROM views", "Not allowed: only SELECT queries reading the tables"),
    ("SELECT (SELECT count(*) FROM meta)", "Not allowed: only SELECT queries reading the tables"),   # counts too
    ("WITH meta AS (SELECT 1) SELECT count(*) FROM meta", "Not allowed: only SELECT queries reading the tables"),
    ("SELECT * FROM pragma_table_info('meta')", "Not allowed: only SELECT queries reading the tables"),
    ("SELECT name FROM sqlite_schema", "Not allowed: only SELECT queries reading the tables"),
    ("SELECT * FROM json_each('[1]')", "Not allowed: only SELECT queries reading the tables"),
    ("SELECT 1; SELECT 2", "One statement per query."),
    ("SELECT zeroblob(1000000000)", "Not allowed: only SELECT queries reading the tables"),
    ("SELECT printf('%.*c', 1000000000, 'x')", "Not allowed: only SELECT queries reading the tables"),
    ("WITH RECURSIVE r(s) AS (SELECT 'x' UNION ALL SELECT s || s FROM r) SELECT length(s) FROM r",
     "A value in the result is too large (over 1 MB)."),
    ("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n",
     "The query was stopped after 2 s"),
]


def test_SEC20_writes_other_tables_risky_functions_and_runaways_are_refused_and_change_nothing(ctx):
    before = (ctx.db_path.read_bytes(), ctx.db_path.stat().st_mtime_ns)
    for sql, reason in SEC20:
        with pytest.raises(ToolError) as error:
            _call(ctx, "query", {"sql": sql})
        assert error.value.message.startswith(reason), (sql, error.value.message)
    for bad, reason in ((None, "sql must be one SELECT"), ("   ", "sql must be one SELECT"),
                        ("SELECT " + "1," * 6000 + "1", "The query is over 10,000 characters")):
        with pytest.raises(ToolError) as error:
            _call(ctx, "query", {"sql": bad})
        assert error.value.message.startswith(reason)
    assert (ctx.db_path.read_bytes(), ctx.db_path.stat().st_mtime_ns) == before


def test_MCP15_no_allowed_column_can_hold_a_credential(ctx):
    credential = re.compile(r"key|token|secret|passw|credential|auth", re.IGNORECASE)
    named = [(t, c["name"]) for t, d in _call(ctx, "describe_data", {})["tables"].items() for c in d["columns"]
             if credential.search(c["name"])]
    assert all(c == "key" or c.endswith("_key") and c != "api_key" for _, c in named), named
    assert all(c in ("key", "product_key") or c.endswith("_key") for _, c in named)   # item keys only
    assert not {"api_key", "blizzard", "client_secret"} & {c for _, c in named}


def _group_conn(rows: list[dict]) -> sqlite3.Connection:
    """A connection whose attached `stats` schema holds a hand-made market table.

    Args:
        rows: Rows by column name; missing columns are NULL.

    Returns:
        The connection.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS stats")
    conn.execute(f"CREATE TABLE stats.{TABLE} ({', '.join(f'{c} {t}' for c, (t, _) in COLUMNS.items())})")
    for row in rows:
        conn.execute(f"INSERT INTO stats.{TABLE} VALUES ({', '.join('?' * len(COLUMNS))})",
                     [row.get(c) for c in COLUMNS])
    return conn


def test_MCP17_item_group_stats_reads_a_whole_group_in_one_call():
    base = {"market": MARKET, "class": 7}
    rows = [
        {**base, "key": 2447, "name": "Peacebloom", "subclass": 9, "cur": 18, "sold_per_day": 40.0},
        {**base, "key": 785, "name": "Mageroyal", "subclass": 9, "cur": 240, "sold_per_day": 3.0},
        {**base, "key": 3820, "name": "Stranglekelp", "subclass": 9, "cur": 120, "sold_per_day": 0.5},
        {**base, "key": 2449, "name": "Earthroot", "subclass": 9, "cur": None, "sold_per_day": 2.0},
        {**base, "key": 6291, "name": "Raw Brilliant Smallfish", "subclass": 8, "cur": 8, "sold_per_day": 9.0},
        {**base, "key": 6522, "name": "Deviate Fish", "class": 0, "subclass": 5, "cur": 150, "sold_per_day": 4.0},
        {**base, "key": 769, "name": "Chunk of Boar Meat", "subclass": 8, "cur": 5, "sold_per_day": 20.0},
        {**base, "market": "forever.normal.horde.us", "key": 2447, "name": "Peacebloom", "subclass": 9, "cur": 99},
    ]
    conn = _group_conn(rows)
    keys = catalog_keys()
    markets = (MARKET,)
    herbs = data.item_group_stats(conn, {"group": "herb"}, keys, markets)
    assert ([i["name"] for i in herbs["items"]], herbs["count"], herbs["truncated"]) == (
        ["Earthroot", "Mageroyal", "Peacebloom", "Stranglekelp"], 4, False)            # by name; this market only
    assert herbs["items"][2]["cur"] == 18 and set(herbs["items"][0]) == set(COLUMNS)
    fish = data.item_group_stats(conn, {"group": "fishing"}, keys, markets)            # the catalog's list
    assert [i["name"] for i in fish["items"]] == ["Deviate Fish", "Raw Brilliant Smallfish"]
    cooking = data.item_group_stats(conn, {"group": "cooking"}, keys, markets)        # fish and meat together
    assert [i["name"] for i in cooking["items"]] == ["Chunk of Boar Meat", "Raw Brilliant Smallfish"]
    dear = data.item_group_stats(conn, {"group": "herb", "sort": "cur", "min_sold_per_day": 1, "limit": 2}, keys,
                                 markets)
    assert ([i["name"] for i in dear["items"]], dear["count"], dear["truncated"]) == (
        ["Mageroyal", "Peacebloom"], 3, True)                                         # Earthroot: no price, last
    cheap = data.item_group_stats(conn, {"group": "herb", "sort": "cur", "ascending": True}, keys, markets)
    assert [i["name"] for i in cheap["items"]] == ["Peacebloom", "Stranglekelp", "Mageroyal", "Earthroot"]
    for args, message in (({"group": "nope"}, "group must be one of: parts, cloth"),
                          ({"group": "herb", "sort": "drop table"}, "sort must be one of: market, key"),
                          ({"group": "herb", "limit": 501}, "limit must be a whole number from 1 to 500"),
                          ({"group": "herb", "min_sold_per_day": "lots"}, "min_sold_per_day must be a number"),
                          ({"group": "herb", "market": "elsewhere"}, "market must be one of: forever.pvp.horde.us")):
        with pytest.raises(ToolError) as error:
            data.item_group_stats(conn, args, keys, markets)
        assert error.value.message.startswith(message)


def test_the_data_tools_need_the_market_table(world):
    db = open_read_only(world.db_path)
    bare = ToolContext(db, world.config, world.clock())
    for name, args in (("query", {"sql": "SELECT 1"}), ("describe_data", {}), ("item_group_stats", {"group": "herb"})):
        with pytest.raises(ToolError) as error:
            HANDLERS[name](bare, args)
        assert error.value.message == "The data tools aren't available; ask again in a moment."
    db.conn.close()
