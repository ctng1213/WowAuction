"""Convert the shared engine scenarios and LP1 property cases to Lua (SPEC §8.1, §8.7).

Writes tests/addon/generated/<name>.lua for each JSON file. Each Lua file
returns the same data as a table, so the Lua engine port is checked against the
same expected numbers as Python. JSON null becomes the runner's JSON_NULL
sentinel, because nil can't sit in a Lua table. Output is deterministic: object
keys are sorted. Run from anywhere: python tools/gen_lua_fixtures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "tests" / "scenarios"
PROPERTIES = ROOT / "tests" / "properties"   # LP1 cases from tools/gen_properties.py
OUT = ROOT / "tests" / "addon" / "generated"


def lua_string(text: str) -> str:
    """Quote a string as a Lua 5.1 literal.

    Args:
        text: Any string.

    Returns:
        A double-quoted literal; control characters use decimal escapes, and
        non-ASCII stays as UTF-8 bytes, which Lua 5.1 strings hold as-is.
    """
    out = []
    for ch in text:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ord(ch) < 32:
            out.append(f"\\{ord(ch)}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def to_lua(value: object, indent: int = 0) -> str:
    """Render a JSON value as a Lua expression.

    Args:
        value: A value from json.loads.
        indent: Current nesting depth, for readable output.

    Returns:
        Lua source for the value.

    Raises:
        TypeError: If value is not a JSON type.
    """
    pad = "  " * (indent + 1)
    if value is None:
        return "JSON_NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)  # shortest round-trip form; Lua 5.1 parses it to the same double
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, list):
        if not value:
            return "{}"
        items = ",\n".join(pad + to_lua(v, indent + 1) for v in value)
        return "{\n" + items + "\n" + "  " * indent + "}"
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = ",\n".join(f"{pad}[{lua_string(k)}] = {to_lua(value[k], indent + 1)}" for k in sorted(value))
        return "{\n" + items + "\n" + "  " * indent + "}"
    raise TypeError(f"not a JSON value: {type(value).__name__}")


def generate(scenarios_dir: Path = SCENARIOS, out_dir: Path = OUT) -> list[Path]:
    """Regenerate the Lua form of every scenario file.

    Args:
        scenarios_dir: Folder of *.json scenario files.
        out_dir: Folder for the generated *.lua files; stale ones are removed.

    Returns:
        Paths written, sorted.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.lua"):
        stale.unlink()
    written = []
    for source in sorted(scenarios_dir.glob("*.json")):
        data = json.loads(source.read_text(encoding="utf-8"))
        target = out_dir / f"{source.stem}.lua"
        header = f"-- Generated from {source.relative_to(ROOT).as_posix()} by tools/gen_lua_fixtures.py. Do not edit.\n"
        target.write_text(header + "return " + to_lua(data) + "\n", encoding="utf-8", newline="\n")
        written.append(target)
    return written


def generate_market(out_dir: Path = OUT / "market") -> list[Path]:
    """Write real MarketIntelligence.lua files with the Python exporter, for the addon's loader specs.

    - ex2.lua: the EX2 market (Linen with a window and a 24 × 3 curve, a shortlisted item without a price).
    - big.lua: 20,000 priced items, every 10th on the shortlist (A3: Lua 5.1's constant limits).

    Args:
        out_dir: Folder for the files.

    Returns:
        Paths written.
    """
    sys.path.insert(0, str(ROOT))
    from dataclasses import replace

    from ahsync.engine.sellprob import encode_curve
    from ahsync.export.market_lua import market_lua
    from ahsync.intel.contract import IntelWindow, ItemIntel, MarketIntelligence

    hour, market, built = 3_600_000, "forever.pvp.horde.us", 497_861 * 3_600_000
    curve = encode_curve([[((r + c) % 50) / 50 for c in range(8)] for r in range(24)])
    linen = ItemIntel(2589, 45, 20, hour, 49, 49, 40, 60, 0.4, 48.0, 0.0, 48.0, "ahf", {"ahf": 7}, 45, 20, 0.9, False,
                      42.4, 86, 93, None, (), seasonality=72,
                      sell_windows=(IntelWindow(5 * 24 + 18, 4, 5 * 24 + 19, 1.12, 7),), curve=curve,
                      curve_hour=built // hour)
    empty = ItemIntel(6948, None, None, None, None, None, None, None, 0.0, None, None, None, None, {}, None, 1, 0.25,
                      True, None, 6, None, None, ())
    small = MarketIntelligence(market, built, built - hour, 1, {2589: linen, 6948: empty})
    big = MarketIntelligence(market, built, built - hour, 1,
                             {k: replace(linen, key=k, cur=k % 997 + 1) for k in range(1, 20_001)})
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {"ex2.lua": market_lua([small], {market: {6948}}, {"Amaya-Grommash": market}, built),
             "big.lua": market_lua([big], {market: set(range(10, 20_001, 10))}, {}, built)}
    written = []
    for name, text in files.items():
        (out_dir / name).write_text(text, encoding="utf-8", newline=chr(10))
        written.append(out_dir / name)
    return written


def generate_plan(out_dir: Path = OUT / "plan") -> list[Path]:
    """Write a real PortfolioPlan.lua with the Python exporter (EX3), for the addon's plan specs.

    Amaya has a pending post of 20 Linen and a late hold; Linen is `no_buy` and Silk cleared;
    Amaya is ingested until T + 2 h, Brugan never.

    Args:
        out_dir: Folder for the file.

    Returns:
        Paths written.
    """
    sys.path.insert(0, str(ROOT))
    from ahsync.export.plan_lua import plan_lua

    hour, t = 3_600_000, 1_760_000_000_000
    amaya = "Amaya-Grommash"
    actions = [("m:1:0", amaya, "post", 2589, 20, 55, t + 5 * hour, 1045.5, None, t + 10 * hour, "pending"),
               ("m:1:1", amaya, "hold", 6948, 1, None, t + 6 * hour, None, "m:1:0", None, "late")]
    text = plan_lua(actions, [(2589, "no_buy", t), (4306, "none", t + hour)], {amaya: t + 2 * hour}, t + 3 * hour)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ex3.lua").write_text(text, encoding="utf-8", newline=chr(10))
    return [out_dir / "ex3.lua"]


if __name__ == "__main__":
    paths = generate() + generate(PROPERTIES, OUT / "properties") + generate_market() + generate_plan()
    print(f"gen_lua_fixtures: {len(paths)} file(s) -> {OUT.relative_to(ROOT).as_posix()}")
    sys.exit(0)
