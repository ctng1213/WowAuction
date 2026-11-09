"""The Status page's "AI assistant (Claude)" card (SPEC §3.8.1; mockup docs/mockups/ai-assistant.html).

Pure: the server's facts in, every text, number and setup snippet of the card out. The page only draws it and posts
what its button names.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ahsync.engine.plan_chains import clock

TITLE = "AI assistant (Claude)"
INTRO = "Let Claude read your AHSync data to answer your questions."
INTRO_SUB = "Read-only: it can't change your plan, rules or settings. It runs on your computer only, and only while " \
            "enabled."
PRIVACY = "What Claude reads is sent to Anthropic to answer you: prices, your characters, their gold, professions and " \
          "trades."
STOP_NOTE = "Stops the server and frees its memory at once."
BYTES_PER_ITEM = 170                   # the market table's size per item, measured on 10,236 items (1.7 MB; MC2)
ASK = ("What fish are the most profitable to catch right now for my characters?",
       "Find recent price trends I can take advantage of.",
       "Where are all the places to farm Thorium Ore?")
DESKTOP_STEPS = ("In Claude Desktop: Settings → Developer → Edit Config. It opens claude_desktop_config.json.",
                 "Add this inside it (or merge it into an existing mcpServers), then save:",
                 "Quit and reopen Claude Desktop. AHSync shows under its connectors.",
                 "Turn on web search in Claude, so it can check Wowhead for fishing levels and farming spots.")
CODE_STEPS = ("Run this once in a terminal:", "Start Claude Code and ask away. /mcp lists AHSync.")


@dataclass(frozen=True)
class McpFacts:
    """What the card shows: the server's state and the data it serves."""

    running: bool
    error: str | None                  # why the last Enable failed; None when it didn't
    started_ms: int | None             # when the server started, UTC ms
    table_bytes: int                   # the market table's memory, bytes (0 when off)
    table_items: int                   # its rows
    intel_items: int                   # items the first market's published intelligence prices (the estimate's base)
    market: str                        # the first configured market
    refreshed_ms: int | None           # that market's refresh the server reads, UTC ms; None before the first
    characters: int                    # your characters
    command: tuple[str, ...]           # the bridge Claude launches: program, then its arguments
    offset_ms: int                     # local time minus UTC


def megabytes(size: int) -> str:
    """A size in megabytes to one decimal, half up (SPEC §3.3 rounding).

    Args:
        size: Bytes.

    Returns:
        "1.7 MB"; "under 0.1 MB" below 0.05 MB.
    """
    tenths = (size + 50_000) // 100_000
    if tenths == 0:
        return "under 0.1 MB"
    return f"{tenths // 10}.{tenths % 10} MB"


def desktop_snippet(command: tuple[str, ...]) -> str:
    """Claude Desktop's config entry for the bridge.

    Args:
        command: The bridge's program and arguments.

    Returns:
        The JSON to paste, indented by two.
    """
    entry: dict = {"command": command[0]}
    if len(command) > 1:
        entry["args"] = list(command[1:])
    return json.dumps({"mcpServers": {"ahsync": entry}}, indent=2, ensure_ascii=False)


def code_snippet(command: tuple[str, ...]) -> str:
    """Claude Code's one-line setup for the bridge.

    Args:
        command: The bridge's program and arguments.

    Returns:
        `claude mcp add ahsync -- <program> <args>`, each part quoted when it has a space.
    """
    parts = [f'"{p}"' if " " in p or not p else p for p in command]
    return "claude mcp add ahsync -- " + " ".join(parts)


def _lines(f: McpFacts) -> list[list[str]]:
    """The running card's lines: server, memory, data, characters.

    Args:
        f: The facts.

    Returns:
        [label, value] pairs.
    """
    since = " ".join(clock(f.started_ms, f.offset_ms)) if f.started_ms is not None else "—"
    data = (f"refresh of {' '.join(clock(f.refreshed_ms, f.offset_ms))} · {f.market}" if f.refreshed_ms is not None
            else f"no refresh yet · {f.market}")
    return [["Server", f"running on this computer only · since {since}"],
            ["Memory in use", f"{megabytes(f.table_bytes)} · market table of {f.table_items:,} items"],
            ["Data Claude sees", data],
            ["Characters", f"{f.characters} · professions and skills included"]]


def mcp_card(f: McpFacts) -> dict:
    """The card (SPEC §3.8.1, MCP1, MCP4).

    Args:
        f: The facts.

    Returns:
        {"title", "pill", "pill_class", "intro", "intro_sub", "button": {"text", "enabled", "value", "primary"},
        "note", "error", "lines", "setup" ({"desktop": {"steps", "snippet"}, "code": {"steps", "snippet"}} while
        running, else None), "privacy", "ask"}.
    """
    if f.running:
        pill, pill_class = "running", "ok"
        button = {"text": "Disable", "enabled": True, "value": False, "primary": False}
        note = STOP_NOTE
    else:
        pill, pill_class = ("couldn't start", "warn") if f.error else ("off", "muted")
        button = {"text": "Enable", "enabled": True, "value": True, "primary": True}
        note = (f"Starts the assistant server in AHSync and uses about {megabytes(f.intel_items * BYTES_PER_ITEM)} "
                "of memory while enabled.")
    setup = None
    if f.running:
        setup = {"desktop": {"steps": list(DESKTOP_STEPS), "snippet": desktop_snippet(f.command)},
                 "code": {"steps": list(CODE_STEPS), "snippet": code_snippet(f.command)}}
    return {"title": TITLE, "pill": pill, "pill_class": pill_class, "intro": INTRO, "intro_sub": INTRO_SUB,
            "button": button, "note": note, "error": None if f.running else f.error,
            "lines": _lines(f) if f.running else [], "setup": setup, "privacy": PRIVACY,
            "ask": list(ASK) if f.running else []}
