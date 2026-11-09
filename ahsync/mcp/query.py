"""The assistant's data tools (SPEC §3.8.3–§3.8.5): read-only SQL over allowlisted tables and the market table.

Every query runs on a connection of its own: the database opened read-only, `market_stats` attached, then a sqlite3
authorizer that allows only SELECT, reads of allowlisted tables and allowlisted functions. A progress handler stops
it after 2 s, length limits cap any value, and the result is cut at 500 rows or 256 KB (SEC20).
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from ahsync.mcp.protocol import ToolError
from ahsync.mcp.stats import COLUMNS, SCHEMA, TABLE, MarketStats

MAX_ROWS, MAX_BYTES, MAX_SECONDS = 500, 256 * 1024, 2.0
MAX_SQL_CHARS = 10_000
MAX_VALUE_BYTES = 1_000_000            # SQLITE_LIMIT_LENGTH: no string or blob over 1 MB (a doubling CTE stops here)

# Table -> what one row is (SPEC §3.8.5). Every other table, and sqlite_master, is unreadable.
TABLES: dict[str, str] = {
    TABLE: "One item in one market with its current numbers (the published intelligence of the last refresh).",
    "items": "The item catalog: key, base item id, name, quality, item level, class, subclass, vendor prices.",
    "candles_h1": "One item's hourly price candle in one market from one source: open, high, low, close in copper.",
    "candles_d1": "One item's daily price candle in one market from one source, copper.",
    "volume_d1": "One item's estimated units sold, expired and newly listed on one day in one market.",
    "sales_h1": "One item's estimated units sold in one hour in one market.",
    "lifetimes_d1": "How long one item's auctions lived on one day, by how they ended.",
    "new_listings_d1": "Where one item's new listings were placed against the cheapest on one day.",
    "observed_hours": "One hour a source observed one market's auction house.",
    "current": "One item's newest price from one source in one market.",
    "posts": "One auction you posted: character, item, quantity, price, duration, and how it ended.",
    "sell_records": "Your posting record for one item in one market: posts, sales, expiries.",
    "payouts": "Gold the mailbox paid you for one sale.",
    "purchases": "One item you bought: character, quantity, price, when.",
    "crafts": "One craft one of your characters made.",
    "mail_sent": "One mail one of your characters sent: recipient, item, quantity.",
    "auction_mail": "One auction house mail: sold, expired, cancelled, won.",
    "disenchants": "One item you disenchanted, and what it gave.",
    "gather_events": "One loot event while gathering: character, source, loot.",
    "farming_sessions": "One confirmed farming session: character, start, end, units.",
    "farming_session_items": "Units of one item gathered in one session.",
    "characters": "One of your characters: name, realm, faction, market, class, level, gold (copper).",
    "professions": "One profession of one character, with its skill and maximum.",
    "recipes": "One recipe: profession, product, yield.",
    "reagents": "One reagent of a recipe, with its quantity.",
    "known_recipes": "One recipe one of your characters knows.",
    "inventory": "Units of one item one character holds, and where (bags, bank, mail).",
    "own_auctions": "One auction of yours on the AH now.",
    "cost_basis": "What you paid for the units you hold of one item, copper.",
}
ALLOWED_TABLES = frozenset(TABLES)
# Built-in functions a read needs; anything else (load_extension, zeroblob, randomblob, printf, readfile, ...) is denied.
ALLOWED_FUNCTIONS = frozenset((
    "abs", "avg", "count", "max", "min", "sum", "total", "round", "length", "lower", "upper", "substr", "substring",
    "trim", "ltrim", "rtrim", "replace", "instr", "coalesce", "ifnull", "nullif", "iif", "typeof", "like", "glob",
    "date", "time", "datetime", "julianday", "strftime", "unixepoch", "group_concat", "string_agg", "cast",
    "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "lag", "lead", "first_value",
    "last_value", "nth_value", "sign", "floor", "ceil", "ceiling", "sqrt", "exp", "ln", "log", "log10", "log2",
    "pow", "power", "mod", "median",
))
_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
_UNIT_HINTS = ((re.compile(r"^t$"), "UTC Unix ms: the hour's or day's start"),
               (re.compile(r"_ms$|^at$|^ts$|^time$|_at$"), "UTC Unix ms"),
               (re.compile(r"^(cur|med\d+|p\d+_\d+|price|buyout|bid|open|high|low|close|gold|vendor_\w+|paid|"
                           r"amount|copper|cost\w*|net\w*)$"), "copper"),
               (re.compile(r"_per_day$"), "units per day"), (re.compile(r"^hour$"), "UTC hour number (ms / 3600000)"),
               (re.compile(r"^day$"), "UTC day number (ms / 86400000)"))
EXAMPLES = (
    ("Herbs cheapest against their 30-day median, with at least 1 sale a day",
     f"SELECT name, cur, med30, round(100.0 * cur / med30 - 100, 1) AS pct_vs_med30, sold_per_day FROM {TABLE} "
     "WHERE class = 7 AND subclass = 9 AND cur IS NOT NULL AND med30 > 0 AND sold_per_day >= 1 "
     "ORDER BY pct_vs_med30 LIMIT 20"),
    ("Each character's professions", "SELECT c.name, p.profession, p.skill, p.max_skill FROM characters c "
                                       "JOIN professions p ON p.char_id = c.id ORDER BY c.name, p.profession"),
    ("Your last 20 purchases", "SELECT * FROM purchases ORDER BY 1 DESC LIMIT 20"),
)
# item_group_stats: group -> trade-goods subclass (class 7), or a gathering catalog category.
CLASS_GROUPS = {"parts": 1, "cloth": 5, "leather": 6, "metal_stone": 7, "cooking": 8, "herb": 9, "elemental": 10,
                "other": 11, "enchanting": 12}
CATALOG_GROUPS = {"fishing": "Fishing", "herbalism": "Herbalism", "mining": "Mining", "skinning": "Skinning"}
GROUP_LIMIT, GROUP_MAX = 50, 500


def authorizer(real_tables: frozenset[str]) -> Callable[[int, str | None, str | None, str | None, str | None], int]:
    """The query connection's authorizer (SPEC §3.8.5): SELECT, allowlisted reads and functions; nothing else.

    A read of a real table must name an allowlisted one. SQLite reports `count(*)` over a table and over the
    query's own CTE alike (no schema, no column), so a name that is no real table is the query's own and is allowed.

    Args:
        real_tables: Every table in the database and the market table, lower case.

    Returns:
        The callback for set_authorizer.
    """
    def authorize(action: int, arg1: str | None, arg2: str | None, db: str | None, _source: str | None) -> int:
        """Allow or deny one action of the query being prepared.

        Args:
            action: The sqlite3 action code.
            arg1: Table name (READ) or None.
            arg2: Column (READ) or function name (FUNCTION).
            db: Schema name, when SQLite gives one.
            _source: Trigger or view, unused.

        Returns:
            SQLITE_OK or SQLITE_DENY.
        """
        if action in (sqlite3.SQLITE_SELECT, _RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            name = (arg1 or "").lower()
            if name in ALLOWED_TABLES:
                return sqlite3.SQLITE_OK
            own = db is None and name not in real_tables and not name.startswith("sqlite_")
            return sqlite3.SQLITE_OK if own else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if (arg2 or "").lower() in ALLOWED_FUNCTIONS else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY
    return authorize


def open_reader(db_path: Path, stats: MarketStats) -> sqlite3.Connection:
    """A read-only connection with the market table attached, for AHSync's own queries (describe_data, groups).

    Args:
        db_path: data/ahsync.db.
        stats: The current market table.

    Returns:
        The connection; close it after use.

    Raises:
        ToolError: The market table is freed (the server is stopping).
    """
    if stats.uri is None:
        raise ToolError("The market table isn't available; ask again in a moment.")
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None,
                           check_same_thread=False)
    conn.execute("PRAGMA query_only = ON")
    conn.execute(f"ATTACH DATABASE ? AS {SCHEMA}", (stats.uri,))
    return conn


def open_guarded(db_path: Path, stats: MarketStats) -> sqlite3.Connection:
    """A connection for one assistant query: read-only, the market table attached, then the guard (SEC20).

    Args:
        db_path: data/ahsync.db.
        stats: The current market table.

    Returns:
        The guarded connection; close it after the query.
    """
    conn = open_reader(db_path, stats)
    real = frozenset(r[0].lower() for schema in ("main", SCHEMA)
                     for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type IN ('table', 'view')"))
    conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 1)
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
    conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_CHARS)
    conn.set_authorizer(authorizer(real))
    return conn


def _reason(error: sqlite3.Error, sql: str) -> str:
    """A refused or failed query's reason, in a sentence (SEC20).

    Args:
        error: What sqlite3 raised.
        sql: The query.

    Returns:
        The sentence.
    """
    text = str(error)
    if "interrupted" in text:
        return f"The query was stopped after {MAX_SECONDS:g} s; narrow it (a WHERE, a LIMIT, fewer joins)."
    if "not authorized" in text or "prohibited" in text:
        return ("Not allowed: only SELECT queries reading the tables describe_data lists, with plain functions, "
                "may run.")
    if "too big" in text:
        return "A value in the result is too large (over 1 MB)."
    if "one statement" in text:
        return "One statement per query."
    if "readonly" in text or "read-only" in text:
        return "Not allowed: the data is read-only."
    return f"The query failed: {text}"


def _cell(value: object) -> object:
    """One result value as JSON can carry it: a blob as hex, an infinite or NaN float as None.

    Args:
        value: A value SQLite returned.

    Returns:
        The value to send.
    """
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def run_query(conn: sqlite3.Connection, sql: object) -> dict:
    """Run one read-only query, cut to MAX_ROWS rows and MAX_BYTES bytes (MCP10, SEC20).

    Args:
        conn: A guarded connection (open_guarded).
        sql: The assistant's query text.

    Returns:
        {"columns", "rows", "row_count", "truncated", "seconds"}.

    Raises:
        ToolError: Not one SELECT, refused by the guard, stopped by the time limit, or failed.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ToolError("sql must be one SELECT (or WITH … SELECT) query")
    if len(sql) > MAX_SQL_CHARS:
        raise ToolError(f"The query is over {MAX_SQL_CHARS:,} characters")
    if not re.match(r"\s*(SELECT|WITH|VALUES)\b", sql, re.IGNORECASE):
        raise ToolError("Not allowed: only SELECT queries may run.")
    started = time.monotonic()
    conn.set_progress_handler(lambda: int(time.monotonic() - started > MAX_SECONDS), 5_000)
    rows, size, truncated = [], 0, False
    try:
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description or ()]
        for row in cursor:
            item = [_cell(v) for v in row]
            size += len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            if len(rows) >= MAX_ROWS or size > MAX_BYTES:
                truncated = True
                break
            rows.append(item)
    except (sqlite3.Error, sqlite3.Warning) as error:          # Warning: "You can only execute one statement…"
        raise ToolError(_reason(error, sql)) from None
    finally:
        conn.set_progress_handler(None, 0)
    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated,
            "seconds": round(time.monotonic() - started, 3)}


def _unit(column: str) -> str | None:
    """A column's unit, from its name.

    Args:
        column: A column name.

    Returns:
        The unit; None when the name says nothing.
    """
    for pattern, unit in _UNIT_HINTS:
        if pattern.search(column):
            return unit
    return None


def describe_data(conn: sqlite3.Connection) -> dict:
    """Every table the query tool may read: what a row is, its columns with units, and example queries (MCP9).

    Args:
        conn: A reader (open_reader).

    Returns:
        {"tables": {name: {"row", "columns": [{"name", "type", "unit"?, "about"?}]}}, "examples": [...], "notes"}.
    """
    tables = {}
    for name, row in TABLES.items():
        schema = SCHEMA if name == TABLE else "main"
        cols = []
        for _, col, kind, *_rest in conn.execute(f"PRAGMA {schema}.table_info({name})"):
            entry = {"name": col, "type": kind}
            about = COLUMNS[col][1] if name == TABLE and col in COLUMNS else None
            unit = _unit(col)
            if about:
                entry["about"] = about
            elif unit:
                entry["unit"] = unit
            cols.append(entry)
        tables[name] = {"row": row, "columns": cols}
    return {"tables": tables, "examples": [{"question": q, "sql": s} for q, s in EXAMPLES],
            "notes": [f"At most {MAX_ROWS} rows or {MAX_BYTES // 1024} KB come back, and a query stops after "
                      f"{MAX_SECONDS:g} s.", "Money is integer copper; times are UTC Unix ms.",
                      f"{TABLE} holds the last refresh's numbers, one row per market and item."]}


def item_group_stats(conn: sqlite3.Connection, args: Mapping, catalog_keys: Mapping[str, set[int]],
                     markets: tuple[str, ...]) -> dict:
    """Every item of one group with its market_stats row (MCP17; the user, 2026-11-09: the bulk read).

    Args:
        conn: A connection with the market table attached (no guard needed: the query is AHSync's).
        args: {"group", "market", "sort", "ascending", "min_sold_per_day", "limit"}.
        catalog_keys: Catalog category -> item keys (the gathering catalog's lists).
        markets: The configured markets.

    Returns:
        {"group", "market", "count" (the items that match, before the limit), "items": [row dicts], "truncated"}.

    Raises:
        ToolError: An unknown group, market or sort column, or a bad limit or minimum.
    """
    group = args.get("group")
    if group not in CLASS_GROUPS and group not in CATALOG_GROUPS:
        raise ToolError("group must be one of: " + ", ".join([*CLASS_GROUPS, *CATALOG_GROUPS]))
    market = args.get("market") or markets[0]
    if market not in markets:
        raise ToolError("market must be one of: " + ", ".join(markets))
    limit = args.get("limit", GROUP_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= GROUP_MAX:
        raise ToolError(f"limit must be a whole number from 1 to {GROUP_MAX}")
    sort = args.get("sort") or "name"
    if sort not in COLUMNS:
        raise ToolError("sort must be one of: " + ", ".join(COLUMNS))
    minimum = args.get("min_sold_per_day")
    if minimum is not None and (isinstance(minimum, bool) or not isinstance(minimum, (int, float))):
        raise ToolError("min_sold_per_day must be a number")
    where, params = ["market = ?"], [market]
    if group in CLASS_GROUPS:
        where.append("class = 7 AND subclass = ?")
        params.append(CLASS_GROUPS[group])
    else:
        keys = sorted(catalog_keys.get(CATALOG_GROUPS[group], set()))
        where.append(f"key IN ({', '.join('?' * len(keys))})" if keys else "0")
        params.extend(keys)
    if minimum is not None:
        where.append("sold_per_day >= ?")
        params.append(minimum)
    order = "ASC" if args.get("ascending") or (sort == "name" and "ascending" not in args) else "DESC"
    sql = (f'SELECT * FROM {SCHEMA}.{TABLE} WHERE {" AND ".join(where)} '
           f'ORDER BY "{sort}" IS NULL, "{sort}" {order}, key')
    cursor = conn.execute(sql, params)
    names = [d[0] for d in cursor.description]
    every = [dict(zip(names, row)) for row in cursor]
    return {"group": group, "market": market, "count": len(every), "items": every[:limit],
            "truncated": len(every) > limit}
