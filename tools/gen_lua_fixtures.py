"""Convert the shared engine scenarios (tests/scenarios/*.json) to Lua (SPEC §8.1).

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
        header = f"-- Generated from tests/scenarios/{source.name} by tools/gen_lua_fixtures.py. Do not edit.\n"
        target.write_text(header + "return " + to_lua(data) + "\n", encoding="utf-8", newline="\n")
        written.append(target)
    return written


if __name__ == "__main__":
    paths = generate()
    print(f"gen_lua_fixtures: {len(paths)} file(s) -> {OUT.relative_to(ROOT).as_posix()}")
    sys.exit(0)
