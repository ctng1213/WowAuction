"""`python -m ahsync.mcp [--data-dir DIR]`: the bridge Claude launches (SPEC §3.8.2)."""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from ahsync.mcp.bridge import run
from ahsync.version import VERSION


def default_data_dir() -> Path:
    """The packaged app's data folder, where its server writes mcp.json (SPEC §3.7.3).

    Returns:
        The per-user data folder (%LOCALAPPDATA%\\AHSync\\data, ~/Library/Application Support/AHSync/data).
    """
    from ahsync_app.platform import current
    return current().app_dirs().data_dir


def main(argv: list[str] | None = None) -> int:
    """Run the bridge on stdin and stdout until Claude closes it.

    Args:
        argv: Command-line arguments; None reads sys.argv.

    Returns:
        0.
    """
    parser = argparse.ArgumentParser(prog="ahsync-mcp", description="AHSync's AI assistant bridge (MCP over stdio)")
    parser.add_argument("--data-dir", type=Path, help="AHSync's data folder (default: the installed app's)")
    args = parser.parse_args(argv)
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
    stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")
    run(stdin, stdout, args.data_dir or default_data_dir(), VERSION)
    return 0


if __name__ == "__main__":
    sys.exit(main())
