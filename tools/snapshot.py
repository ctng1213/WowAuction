"""The whole-plan snapshot (SPEC §8.6, WP1): run a full refresh on the fixture world and collect every output.

Loads tests/fixtures/worlds/w1.json into a fresh database, runs `refresh_all` at the world's now_ms,
writes both addon files into a temporary AddOns folder, and gathers every page payload (the `views`
table), the plan's actions and the two addon files' text into one JSON document with sorted keys.
The test compares it byte for byte with tests/fixtures/worlds/w1.expected.json. To stay small, the
market file keeps its header and first lines (trimmed) and heat-map cells are digested (compact_cells).

Run:  python tools/snapshot.py            print a summary and whether it matches
      python tools/snapshot.py --update   rewrite w1.expected.json and print a diff summary
"""
from __future__ import annotations

import difflib
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ahsync.app.refresh import refresh_all                       # noqa: E402  after the path fix-up
from ahsync.config import Config                                 # noqa: E402
from ahsync.db.sqlite import Database                            # noqa: E402
from ahsync.export.addon_files import (DATA_FOLDER, MARKET_FILE, PLAN_FILE, export_market,   # noqa: E402
                                       export_plan)

WORLD = ROOT / "tests" / "fixtures" / "worlds" / "w1.json"
EXPECTED = ROOT / "tests" / "fixtures" / "worlds" / "w1.expected.json"
MARKET_ITEM_LINES = 40     # the market file is trimmed to its header and the first lines of its item table
PORTFOLIO_LINES = ("K[", "R[", "U[", "G.")     # ...but keeps every candidate and graph line (EX17, EX18)


def load_world(db: Database, world: dict) -> None:
    """Insert the world's rows into an empty database.

    Args:
        db: A freshly opened database.
        world: The w1.json document.
    """
    with db.transaction():
        for name, table in sorted(world["tables"].items()):
            columns = table["columns"]
            marks = ", ".join("?" for _ in columns)
            db.conn.executemany(f"INSERT INTO {name}({', '.join(columns)}) VALUES ({marks})",
                                [tuple(row) for row in table["rows"]])


def trimmed(text: str, keep: int = MARKET_ITEM_LINES) -> str:
    """The market file cut to its first `keep` lines, its candidate and graph lines (K, G) and its last line.

    Args:
        text: MarketIntelligence.lua.
        keep: Lines kept from the top (the header and the first items).

    Returns:
        The kept lines, then a marker with how many were left out, then every later K[, R[, U[ and G. line (the
        portfolio export, EX17, EX18), then the header table from `AHProfitMarket = {` on (else the last line).
    """
    lines = text.splitlines()
    if len(lines) <= keep + 1:
        return text
    header = next((i for i in range(keep, len(lines)) if lines[i].startswith("AHProfitMarket = {")), len(lines) - 1)
    portfolio = [line for line in lines[keep:header] if line.startswith(PORTFOLIO_LINES)]
    left_out = header - keep - len(portfolio)
    return "\n".join(lines[:keep] + [f"-- ({left_out} lines trimmed by tools/snapshot.py)", *portfolio, *lines[header:]])


def compact_cells(value: object) -> object:
    """Heat-map cell lists ({level, title} per hour) as one digest each, to keep the snapshot small.

    Args:
        value: A JSON value from a page payload.

    Returns:
        The same value, with every list of {level, title} cells replaced by {"levels": the levels as one
        string of digits, "first": the first title, "titles_sha256": a hash of every title joined by newlines};
        any change to a cell still changes the snapshot.
    """
    if isinstance(value, dict):
        return {k: compact_cells(v) for k, v in value.items()}
    if isinstance(value, list):
        if value and all(isinstance(c, dict) and set(c) == {"level", "title"} for c in value):
            titles = "\n".join(c["title"] for c in value)
            return {"levels": "".join(str(c["level"]) for c in value), "first": value[0]["title"],
                    "titles_sha256": hashlib.sha256(titles.encode("utf-8")).hexdigest()}
        return [compact_cells(v) for v in value]
    return value


def _digest(value: object) -> str:
    """A SHA-256 of a JSON value in canonical form.

    Args:
        value: A JSON value.

    Returns:
        The hex digest of its sorted-key JSON text.
    """
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def compact_gathering(body: dict) -> dict:
    """The Gathering page with its unpriced catalog rows digested, to keep the snapshot small (SPEC §3.4E).

    The shipped catalog has ~150 items and the fixture world prices a handful: every row, rate card and detail of
    an item with an AH price or a rate under All characters is kept in full; the others become one count and one
    hash per view (and for the details), so any change to them still changes the snapshot.

    Args:
        body: The gathering payload.

    Returns:
        A copy whose shared rows, rate cards and details, and each by_char view's keys and cells, are split into
        the kept items and {"count", "sha256"} for the rest ("other_rows", "other_details", a view's "other").
    """
    kept = {str(r["key"]) for r in body["rows"] if r["price"] is not None or r["rate"] != "—"}
    out = dict(body)
    rest_rows = [r for r in body["rows"] if str(r["key"]) not in kept]
    rest_cards = {k: v for k, v in body["rate_cards"].items() if k not in kept}
    out["rows"] = [r for r in body["rows"] if str(r["key"]) in kept]
    out["rate_cards"] = {k: v for k, v in body["rate_cards"].items() if k in kept}
    out["other_rows"] = {"count": len(rest_rows), "sha256": _digest([rest_rows, rest_cards])}
    out["by_char"] = {}
    for char, view in body["by_char"].items():
        rest = [view["keys"], {k: v for k, v in view["cells"].items() if k not in kept}]
        out["by_char"][char] = {**view, "keys": [k for k in view["keys"] if str(k) in kept],
                                "cells": {k: v for k, v in view["cells"].items() if k in kept},
                                "other": {"count": len(view["keys"]) - len(kept), "sha256": _digest(rest)}}
    rest_details = {k: v for k, v in body["details"].items() if k not in kept}
    out["details"] = {k: v for k, v in body["details"].items() if k in kept}
    out["other_details"] = {"count": len(rest_details), "sha256": _digest(rest_details)}
    return out


def outputs(db: Database, cfg: Config, now_ms: int, folder: Path) -> dict:
    """Every output of the refresh: pages, actions and the addon files.

    Args:
        db: The refreshed database.
        cfg: Config (wow_path points at folder's install).
        now_ms: The world's now.
        folder: The AHProfit_Data folder the files were written to.

    Returns:
        {"views": {page: {market: body}}, "actions": [row dicts in plan order], "addon_files": {name: text}}.
    """
    views: dict = {}
    for page, market, body in db.conn.execute("SELECT page, market, body FROM views ORDER BY page, market"):
        payload = json.loads(body)
        views.setdefault(page, {})[market] = compact_cells(compact_gathering(payload) if page == "gathering" else payload)
    cursor = db.conn.execute("SELECT * FROM actions")
    names = [d[0] for d in cursor.description]
    actions = sorted((dict(zip(names, row)) for row in cursor),
                     key=lambda a: (a["action_id"].rsplit(":", 1)[0], int(a["action_id"].rsplit(":", 1)[1])))
    market_text = (folder / MARKET_FILE).read_text(encoding="utf-8")
    return {"now_ms": now_ms, "views": views, "actions": actions,
            "addon_files": {MARKET_FILE: trimmed(market_text), PLAN_FILE: (folder / PLAN_FILE).read_text(encoding="utf-8")}}


def render(value: object, depth: int = 0) -> str:
    """JSON with sorted keys, one key per line, and lists of plain values on one line (a compact, diffable form).

    Args:
        value: A JSON value.
        depth: Nesting depth, for the indent.

    Returns:
        The JSON text, without a final newline.
    """
    pad, inner = " " * depth, " " * (depth + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{inner}{json.dumps(k, ensure_ascii=False)}: {render(v, depth + 1)}" for k, v in sorted(value.items())]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value):
        return "[\n" + ",\n".join(inner + render(v, depth + 1) for v in value) + "\n" + pad + "]"
    return json.dumps(value, ensure_ascii=False)


def snapshot(world_path: Path = WORLD) -> str:
    """Run the whole engine on a fixture world and render its outputs (SPEC §8.6).

    Args:
        world_path: The world JSON.

    Returns:
        The snapshot as JSON text (render): sorted keys, a final newline.

    Raises:
        RuntimeError: When the refresh fails or an addon file isn't written.
    """
    world = json.loads(world_path.read_text(encoding="utf-8"))
    now_ms = world["now_ms"]
    with tempfile.TemporaryDirectory() as tmp:
        install = Path(tmp) / "wow"
        folder = install.joinpath(*DATA_FOLDER)
        folder.mkdir(parents=True)
        cfg = Config(markets=tuple(world["config"]["markets"]), wow_path=str(install))
        db = Database.open(Path(tmp) / "ahsync.db", now_ms, None)
        try:
            load_world(db, world)
            result = refresh_all(db, now_ms, cfg)
            if result.error or result.failed:
                raise RuntimeError(f"refresh failed: {result.error or result.failed}")
            if not (export_market(db, cfg, now_ms, lambda _s: None) and export_plan(db, cfg, now_ms, lambda _s: None)):
                raise RuntimeError("an addon file was not written")
            out = outputs(db, cfg, now_ms, folder)
        finally:
            db.close()
    return render(out) + "\n"


def summary(text: str) -> str:
    """Rows per page and actions per kind, for a quick look.

    Args:
        text: A snapshot.

    Returns:
        One line per page plus one for the actions.
    """
    doc = json.loads(text)
    lines = []
    for page, markets in sorted(doc["views"].items()):
        for market, body in sorted(markets.items()):
            rows = body.get("rows") if isinstance(body, dict) else None
            lines.append(f"  {page:<11} {market}: {len(rows) if isinstance(rows, list) else '-'} rows")
    kinds: dict[str, int] = {}
    for a in doc["actions"]:
        kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
    lines.append("  actions: " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())))
    return "\n".join(lines)


def utf8_console(*streams: object) -> None:
    """Make text streams write UTF-8, replacing what they can't encode, so "≥" or "—" never crash a cp1252 console.

    Args:
        streams: Text streams (sys.stdout, sys.stderr); one without reconfigure (a replaced stream) is left as it is.

    Side effects:
        Reconfigures each stream that supports it.
    """
    for stream in streams:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str]) -> int:
    """Print the snapshot's summary; with --update rewrite w1.expected.json and print a diff summary.

    Args:
        argv: Command-line arguments.

    Returns:
        0 when it matches (or was updated); 1 when it differs.
    """
    utf8_console(sys.stdout, sys.stderr)
    text = snapshot()
    old = EXPECTED.read_text(encoding="utf-8") if EXPECTED.exists() else ""
    print(summary(text))
    print(f"  size: {len(text.encode('utf-8')) // 1024} KB")
    diff = list(difflib.unified_diff(old.splitlines(), text.splitlines(), lineterm="", n=0))
    changed = sum(1 for line in diff if line[:1] in "+-" and line[:3] not in ("+++", "---"))
    if "--update" in argv:
        EXPECTED.write_text(text, encoding="utf-8", newline="\n")
        print(f"snapshot: wrote {EXPECTED.relative_to(ROOT)} ({changed} lines changed)")
        for line in diff[:60]:
            print(line)
        return 0
    print("snapshot: matches" if not changed else f"snapshot: differs ({changed} lines); run with --update")
    return 0 if not changed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
