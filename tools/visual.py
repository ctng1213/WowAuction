"""UI layout snapshots against their golden files (SPEC §8.4 layer 2).

Usage: python tools/visual.py [--update] [--html]

- Runs the Lua visual specs (tests/visual/*_spec.lua). Each draws a scenario with the
  stub frames, runs the automatic layout checks and compares the frame-tree snapshot
  with tests/visual/golden/<scenario>.txt.
- --update rewrites the golden files from the current UI. Review their diff like code.
- --html turns every snapshot in tests/visual/out/ into an HTML page of boxes and text,
  for a quick look next to docs/mockups/addon.html.
"""
from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "visual" / "out"
LINE = re.compile(r'^(?P<indent> *)(?P<kind>\w+) (?P<name>\S+) (?P<x>-?\d+),(?P<y>-?\d+) (?P<w>\d+)x(?P<h>\d+)'
                  r'(?: "(?P<text>.*)")?(?P<hidden> \(hidden\))?$')


def snapshot_html(snapshot: str, title: str) -> str:
    """Draw one snapshot as absolutely positioned boxes.

    Args:
        snapshot: The frame-tree text from layout.lua.
        title: The scenario name.

    Returns:
        A standalone HTML page; hidden regions are left out.
    """
    boxes = []
    for line in snapshot.splitlines():
        m = LINE.match(line)
        if not m or m["hidden"]:
            continue
        x, y, w, h = (int(m[k]) for k in ("x", "y", "w", "h"))
        cls = "text" if m["kind"] == "FontString" else m["kind"].lower()
        text = html.escape(m["text"] or "")
        boxes.append(f'<div class="{cls}" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px" '
                     f'title="{html.escape(m["kind"])} {html.escape(m["name"])}">{text}</div>')
    return ("<!doctype html><meta charset='utf-8'><title>" + html.escape(title) + "</title><style>"
            "body{background:#1b1d22;margin:0;font:11px/1 Georgia,serif;color:#e8e2d0}"
            "div{position:absolute;box-sizing:border-box;white-space:nowrap;overflow:hidden}"
            ".frame,.scrollframe{border:1px solid #6b5a32}.button{border:1px solid #a33;background:#4a0b0b55}"
            ".text{color:#ffd100}.texture{background:#ffffff10}</style>" + "".join(boxes))


def main() -> int:
    """Run the visual specs, optionally updating the golden files or writing HTML views.

    Returns:
        The Lua runner's exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", action="store_true", help="rewrite tests/visual/golden from the current UI")
    parser.add_argument("--html", action="store_true", help="write tests/visual/out/<scenario>.html")
    args = parser.parse_args()
    env = dict(os.environ, AHP_UPDATE_VISUAL="1" if args.update else "")
    subprocess.run([sys.executable, str(ROOT / "tools" / "gen_lua_fixtures.py")], cwd=ROOT, check=True)
    lua = shutil.which("lua5.1")              # Windows won't add ".exe" to a name that already has a dot
    if lua is None:
        print("visual: lua5.1 not found on PATH (CLAUDE.md)")
        return 2
    code = subprocess.run([lua, "tests/support/lua/runner.lua", "tests/visual"], cwd=ROOT, env=env).returncode
    if args.html:
        for snapshot in sorted(OUT.glob("*.txt")):
            page = snapshot.with_suffix(".html")
            page.write_text(snapshot_html(snapshot.read_text(encoding="utf-8"), snapshot.stem), encoding="utf-8")
            print(f"visual: {page.relative_to(ROOT).as_posix()}")
    return code


if __name__ == "__main__":
    sys.exit(main())
