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
WEB_SEARCH = "Turn on web search in Claude, so it can check Wowhead for fishing levels and farming spots."
CODE_STEPS = ("Run this once in a terminal:", "Start Claude Code and ask away. /mcp lists AHSync.")
NOT_FOUND = ("Claude Desktop wasn't found on this computer. Install it from claude.ai/download, open it once, then "
             "press Check again.")
SET_UP = ("✓ Claude Desktop is set up for AHSync. If you just changed the file, quit Claude Desktop completely and "
          "reopen it.")
QUIT_HOW = {"windows": "right-click its icon by the clock, then Quit", "macos": "Claude → Quit Claude"}
NOTEPAD = " In Notepad, choose Save as type: All files, so it isn't saved as .txt."


@dataclass(frozen=True)
class ClaudeConfig:
    """What AHSync found of Claude Desktop's config (SPEC §3.8.1; made by mcp/claude_config.py, read-only).

    Only the ahsync entry's program and arguments are ever kept: the rest of the file stays unread (MCP20).
    """

    system: str                        # "windows", "macos" or "other"
    state: str                         # not_found, no_file, no_servers, no_entry, entry, invalid, unreadable
    path: str | None = None            # claude_desktop_config.json's full path; None when not_found
    entry: tuple[str, ...] = ()        # state "entry": the ahsync entry's command and args; () when malformed
    empty: bool = False                # no_file: the file exists but is blank; no_servers: it is {}; no_entry:
                                       # mcpServers is {} (so there is no entry before ours to put a comma after)
    error_line: int | None = None      # state "invalid": the line the JSON breaks on; None for a wrong-kind section
    reason: str | None = None          # state "unreadable": why, e.g. "it's over 1 MB"
    other_folder: str | None = None    # another Claude folder that exists but isn't the one used


NO_CLAUDE = ClaudeConfig(system="other", state="not_found")


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
    claude: ClaudeConfig = NO_CLAUDE   # Claude Desktop's config as found while the page was built (MC3b)


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
    return json.dumps({"mcpServers": {"ahsync": bridge_entry(command)}}, indent=2, ensure_ascii=False)


def bridge_entry(command: tuple[str, ...]) -> dict:
    """The `ahsync` entry of Claude Desktop's mcpServers.

    Args:
        command: The bridge's program and arguments.

    Returns:
        {"command": program} plus "args" when there are any.
    """
    entry: dict = {"command": command[0]}
    if len(command) > 1:
        entry["args"] = list(command[1:])
    return entry


def member_snippet(name: str, value: dict) -> str:
    """One JSON member, `"name": {...}`, to paste inside an object (SPEC §3.8.1).

    Args:
        name: The member's name.
        value: Its value.

    Returns:
        The member indented by two, without the braces around it.
    """
    lines = json.dumps({name: value}, indent=2, ensure_ascii=False).splitlines()[1:-1]
    return "\n".join(line[2:] for line in lines)


def _same_bridge(found: tuple[str, ...], command: tuple[str, ...], system: str) -> bool:
    """Whether the ahsync entry starts this install's bridge; Windows paths match ignoring case.

    Args:
        found: The entry's command and args.
        command: This install's bridge.
        system: "windows", "macos" or "other".

    Returns:
        True when they are the same program and arguments.
    """
    if system == "windows":
        return tuple(p.casefold() for p in found) == tuple(p.casefold() for p in command)
    return found == command


def _step(text: str, code: str | None = None) -> dict:
    """One numbered step of a setup tab.

    Args:
        text: What to do.
        code: A path or snippet to show with a Copy button; None for none.

    Returns:
        {"text", "code"}.
    """
    return {"text": text, "code": code}


def desktop_setup(found: ClaudeConfig, command: tuple[str, ...]) -> dict:
    """The Claude Desktop tab: which file to change and exactly how (SPEC §3.8.1 table, MCP19).

    Args:
        found: What AHSync found of Claude's config.
        command: This install's bridge.

    Returns:
        {"status": "ok" | "todo" | "warn" | "missing", "message", "steps": [{"text", "code"}], "note"}; every state
        but not_found has the file's full path as a step. Nothing of the file but the ahsync entry's command is used.
    """
    if found.state == "not_found":
        return {"status": "missing", "message": NOT_FOUND, "steps": [], "note": None}
    entry = bridge_entry(command)
    path, status, steps = found.path, "todo", []
    quit_step = _step(f"Quit Claude Desktop completely ({QUIT_HOW.get(found.system, 'quit it from its menu')}), then "
                      "reopen it. In a new chat, AHSync is listed among Claude's connectors.")
    if found.state == "no_file" and found.empty:
        message = "Claude Desktop's config file is empty. Fill it in:"
        steps = [_step("Open this file:", path),
                 _step("Paste this into it and save:", desktop_snippet(command)), quit_step]
    elif found.state == "no_file":
        message = "Claude Desktop has no config file yet. Create it:"
        notepad = NOTEPAD if found.system == "windows" else ""
        steps = [_step("Create this file:", path), _step("Paste this into it and save." + notepad,
                                                          desktop_snippet(command)), quit_step]
    elif found.state == "no_servers" and found.empty:
        message = "Add AHSync to Claude Desktop's config file:"
        steps = [_step("Open this file:", path),
                 _step("Replace everything in it with this, then save:", desktop_snippet(command)), quit_step]
    elif found.state == "no_servers":
        message = "Add AHSync to Claude Desktop's config file:"
        steps = [_step("Open this file:", path),
                 _step("Add this just before its last }, and put a comma after the entry before it, then save:",
                       member_snippet("mcpServers", {"ahsync": entry})), quit_step]
    elif found.state == "no_entry" and found.empty:
        message = "Add AHSync to Claude Desktop's config file:"
        steps = [_step("Open this file:", path),
                 _step('Put this between the { and } after "mcpServers":, then save:',
                       member_snippet("ahsync", entry)), quit_step]
    elif found.state == "no_entry":
        message = "Add AHSync to Claude Desktop's config file:"
        steps = [_step("Open this file:", path),
                 _step('Add this just before the } that closes "mcpServers", and put a comma after the entry '
                       "before it, then save:", member_snippet("ahsync", entry)), quit_step]
    elif found.state == "entry" and _same_bridge(found.entry, command, found.system):
        status, message, steps = "ok", SET_UP, [_step("The file:", path)]
    elif found.state == "entry":
        started = " ".join(found.entry) if found.entry else "an entry AHSync can't read"
        message = f"Claude Desktop's config starts a different program for AHSync: {started}"
        steps = [_step("Open this file:", path),
                 _step('Replace its "ahsync" entry with this, keeping the comma after it if there was one, then '
                       "save:", member_snippet("ahsync", entry)), quit_step]
    elif found.state == "invalid":
        near = f" near line {found.error_line}" if found.error_line is not None else ""
        status = "warn"
        message = (f"This file isn't valid JSON{near}, so Claude Desktop can't read it either. Fix it, save, then "
                   "press Check again.")
        steps = [_step("The file:", path)]
    else:
        status = "warn"
        message = f'AHSync couldn\'t read this file ({found.reason}). It needs this entry inside "mcpServers":'
        steps = [_step("The file:", path)] if path else []      # no path: the search itself failed
        steps += [_step("The entry:", member_snippet("ahsync", entry)), quit_step]
    note = (f"Claude also has a folder at {found.other_folder}; this uses the one Claude used last."
            if found.other_folder else None)
    return {"status": status, "message": message, "steps": steps + [_step(WEB_SEARCH)], "note": note}


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
        "note", "error", "lines", "setup" ({"desktop": desktop_setup's tab, "code": {"steps": [{"text", "code"}]}}
        while running, else None), "privacy", "ask"}.
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
        setup = {"desktop": desktop_setup(f.claude, f.command),
                 "code": {"steps": [_step(CODE_STEPS[0], code_snippet(f.command)), _step(CODE_STEPS[1])]}}
    return {"title": TITLE, "pill": pill, "pill_class": pill_class, "intro": INTRO, "intro_sub": INTRO_SUB,
            "button": button, "note": note, "error": None if f.running else f.error,
            "lines": _lines(f) if f.running else [], "setup": setup, "privacy": PRIVACY,
            "ask": list(ASK) if f.running else []}
