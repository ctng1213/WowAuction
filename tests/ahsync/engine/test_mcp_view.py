"""Tests for ahsync/engine/mcp_view.py: the Status page's AI assistant card (SPEC §3.8.1)."""
from __future__ import annotations

import json
from dataclasses import replace

from ahsync.engine.mcp_view import (ClaudeConfig, McpFacts, code_snippet, desktop_setup, desktop_snippet, mcp_card,
                                    megabytes, member_snippet)

SUN_13_10 = 1_794_143_400_000          # Sun 2026-11-08 13:10 UTC
WINDOWS = ("C:\\Users\\you\\AppData\\Local\\Programs\\AHSync\\ahsync-mcp.exe",)
PATH = "C:\\Users\\you\\AppData\\Local\\Packages\\Claude_pzs8sxrjxfjjc\\LocalCache\\Roaming\\Claude\\claude_desktop_config.json"
OFF = McpFacts(running=False, error=None, started_ms=None, table_bytes=0, table_items=0, intel_items=10_236,
               market="forever.normal.horde.us", refreshed_ms=SUN_13_10 - 3 * 60_000, characters=4, command=WINDOWS,
               offset_ms=0)
RUNNING = replace(OFF, running=True, started_ms=SUN_13_10, table_bytes=1_740_800, table_items=10_236)


def test_MCP1_off_offers_enable_with_the_memory_it_will_use_and_no_setup():
    card = mcp_card(OFF)
    assert (card["title"], card["pill"], card["pill_class"]) == ("AI assistant (Claude)", "off", "muted")
    assert card["button"] == {"text": "Enable", "enabled": True, "value": True, "primary": True}
    assert card["note"] == "Starts the assistant server in AHSync and uses about 1.7 MB of memory while enabled."
    assert (card["error"], card["lines"], card["setup"], card["ask"]) == (None, [], None, [])
    assert card["privacy"].startswith("What Claude reads is sent to Anthropic")


def test_MCP1_running_shows_where_it_runs_its_memory_its_data_and_the_setup():
    card = mcp_card(RUNNING)
    assert (card["pill"], card["pill_class"], card["button"]["text"], card["button"]["value"]) == (
        "running", "ok", "Disable", False)
    assert card["note"] == "Stops the server and frees its memory at once."
    assert card["lines"] == [["Server", "running on this computer only · since Sun 13:10"],
                             ["Memory in use", "1.7 MB · market table of 10,236 items"],
                             ["Data Claude sees", "refresh of Sun 13:07 · forever.normal.horde.us"],
                             ["Characters", "4 · professions and skills included"]]
    assert card["setup"]["desktop"]["status"] == "missing"                  # no Claude found (the default)
    assert card["setup"]["code"]["steps"][0] == {"text": "Run this once in a terminal:",
                                                 "code": f"claude mcp add ahsync -- {WINDOWS[0]}"}
    assert len(card["ask"]) == 3
    found = replace(RUNNING, claude=ClaudeConfig(system="windows", state="no_file", path=PATH))
    assert mcp_card(found)["setup"]["desktop"]["steps"][0] == {"text": "Create this file:", "code": PATH}


def codes(setup: dict) -> list:
    """Each step's path or snippet."""
    return [s["code"] for s in setup["steps"]]


def test_MCP19_not_found_asks_to_install_claude_and_names_no_file():
    setup = desktop_setup(ClaudeConfig(system="windows", state="not_found"), WINDOWS)
    assert setup == {"status": "missing", "message": "Claude Desktop wasn't found on this computer. Install it from "
                     "claude.ai/download, open it once, then press Check again.", "steps": [], "note": None}


def test_MCP19_no_file_gives_the_path_and_the_whole_config():
    setup = desktop_setup(ClaudeConfig(system="windows", state="no_file", path=PATH), WINDOWS)
    assert setup["status"] == "todo" and codes(setup)[0] == PATH
    assert json.loads(codes(setup)[1]) == {"mcpServers": {"ahsync": {"command": WINDOWS[0]}}}
    assert "Save as type: All files" in setup["steps"][1]["text"]
    assert setup["steps"][2]["text"].startswith("Quit Claude Desktop completely (right-click its icon by the clock")
    assert setup["steps"][3]["text"].startswith("Turn on web search")
    mac = desktop_setup(ClaudeConfig(system="macos", state="no_file", path="/Users/u/c.json"), ("/A", "--mcp"))
    assert "Notepad" not in mac["steps"][1]["text"] and "Claude → Quit Claude" in mac["steps"][2]["text"]


def test_MCP19_a_file_without_mcpservers_gets_the_block_to_add():
    setup = desktop_setup(ClaudeConfig(system="windows", state="no_servers", path=PATH), WINDOWS)
    assert setup["steps"][1]["text"] == ("Add this just before its last }, and put a comma after the entry before "
                                         "it, then save:")
    assert json.loads("{" + codes(setup)[1] + "}") == {"mcpServers": {"ahsync": {"command": WINDOWS[0]}}}
    # followed literally on {"a": 1}: the block before the last }, a comma after "a"'s value
    assert json.loads('{"a": 1,\n' + codes(setup)[1] + "}")["mcpServers"]["ahsync"]["command"] == WINDOWS[0]


def test_MCP19_mcpservers_without_ahsync_gets_the_entry_to_add():
    setup = desktop_setup(ClaudeConfig(system="windows", state="no_entry", path=PATH), ("/A", "--mcp"))
    assert setup["steps"][1]["text"].startswith('Add this just before the } that closes "mcpServers", and put a comma')
    assert json.loads("{" + codes(setup)[1] + "}") == {"ahsync": {"command": "/A", "args": ["--mcp"]}}


def test_MCP19_empty_objects_ask_for_no_comma():
    blank = desktop_setup(ClaudeConfig(system="windows", state="no_file", path=PATH, empty=True), WINDOWS)
    assert (blank["message"], blank["steps"][0]["text"], blank["steps"][1]["text"]) == (
        "Claude Desktop's config file is empty. Fill it in:", "Open this file:", "Paste this into it and save:")
    braces = desktop_setup(ClaudeConfig(system="windows", state="no_servers", path=PATH, empty=True), WINDOWS)
    assert braces["steps"][1]["text"] == "Replace everything in it with this, then save:"
    assert json.loads(codes(braces)[1]) == {"mcpServers": {"ahsync": {"command": WINDOWS[0]}}}
    servers = desktop_setup(ClaudeConfig(system="windows", state="no_entry", path=PATH, empty=True), WINDOWS)
    assert servers["steps"][1]["text"] == 'Put this between the { and } after "mcpServers":, then save:'
    # followed literally on {"mcpServers": {}}: the entry between the braces, no comma anywhere
    assert json.loads('{"mcpServers": {' + codes(servers)[1] + "}}") == {"mcpServers": {"ahsync": {
        "command": WINDOWS[0]}}}


def test_MCP19_an_old_entry_is_replaced_and_the_right_one_is_set_up():
    old = desktop_setup(ClaudeConfig(system="windows", state="entry", path=PATH, entry=("C:\\Old\\ahsync-mcp.exe",)),
                        WINDOWS)
    assert old["message"] == "Claude Desktop's config starts a different program for AHSync: C:\\Old\\ahsync-mcp.exe"
    assert json.loads("{" + codes(old)[1] + "}") == {"ahsync": {"command": WINDOWS[0]}}
    assert old["steps"][1]["text"] == ('Replace its "ahsync" entry with this, keeping the comma after it if there '
                                       "was one, then save:")
    unreadable = desktop_setup(ClaudeConfig(system="windows", state="entry", path=PATH, entry=()), WINDOWS)
    assert unreadable["message"].endswith("an entry AHSync can't read")
    right = desktop_setup(ClaudeConfig(system="windows", state="entry", path=PATH, entry=(WINDOWS[0].upper(),)),
                          WINDOWS)                                          # Windows paths ignore case
    assert (right["status"], right["message"], codes(right)) == ("ok", "✓ Claude Desktop is set up for AHSync. If "
                                                                 "you just changed the file, quit Claude Desktop "
                                                                 "completely and reopen it.", [PATH, None])
    mac = ClaudeConfig(system="macos", state="entry", path="/c.json", entry=("/a", "--mcp"))
    assert desktop_setup(mac, ("/A", "--mcp"))["status"] == "todo"          # macOS paths keep their case


def test_MCP19_invalid_and_unreadable_files_say_why():
    cut = desktop_setup(ClaudeConfig(system="windows", state="invalid", path=PATH, error_line=1), WINDOWS)
    assert (cut["status"], cut["message"]) == ("warn", "This file isn't valid JSON near line 1, so Claude Desktop "
                                                       "can't read it either. Fix it, save, then press Check again.")
    wrong_kind = desktop_setup(ClaudeConfig(system="windows", state="invalid", path=PATH), WINDOWS)
    assert wrong_kind["message"].startswith("This file isn't valid JSON, so")
    big = desktop_setup(ClaudeConfig(system="windows", state="unreadable", path=PATH, reason="it's over 1 MB"),
                        WINDOWS)
    assert big["message"] == 'AHSync couldn\'t read this file (it\'s over 1 MB). It needs this entry inside "mcpServers":'
    assert codes(big)[:2] == [PATH, member_snippet("ahsync", {"command": WINDOWS[0]})]
    lost = desktop_setup(ClaudeConfig(system="other", state="unreadable",
                                      reason="something went wrong while AHSync looked for it"), WINDOWS)
    assert codes(lost)[0] == member_snippet("ahsync", {"command": WINDOWS[0]})     # no path step: none was found


def test_MCP18_the_other_folder_is_named_in_a_note():
    found = ClaudeConfig(system="windows", state="no_file", path=PATH, other_folder="C:\\Users\\you\\AppData\\Roaming\\Claude")
    assert desktop_setup(found, WINDOWS)["note"] == ("Claude also has a folder at C:\\Users\\you\\AppData\\Roaming\\Claude; "
                                                     "this uses the one Claude used last.")


def test_MCP4_a_failed_enable_says_why_and_offers_enable_again():
    card = mcp_card(replace(OFF, error="Port 8766 is in use by another program."))
    assert (card["pill"], card["pill_class"], card["error"], card["button"]["text"]) == (
        "couldn't start", "warn", "Port 8766 is in use by another program.", "Enable")
    assert mcp_card(replace(RUNNING, error="old"))["error"] is None          # running: an old reason is gone


def test_sizes_snippets_and_missing_times():
    assert [megabytes(b) for b in (0, 49_999, 50_000, 1_740_800, 12_345_678)] == [
        "under 0.1 MB", "under 0.1 MB", "0.1 MB", "1.7 MB", "12.3 MB"]
    mac = ("/Applications/AHSync.app/Contents/MacOS/AHSync", "--mcp")
    assert json.loads(desktop_snippet(mac)) == {"mcpServers": {"ahsync": {"command": mac[0], "args": ["--mcp"]}}}
    source = ("C:\\Program Files\\Python312\\python.exe", "-m", "ahsync.mcp", "--data-dir", "C:\\Dev\\data", "")
    assert code_snippet(source) == ('claude mcp add ahsync -- "C:\\Program Files\\Python312\\python.exe" -m ahsync.mcp '
                                    '--data-dir C:\\Dev\\data ""')
    early = mcp_card(replace(RUNNING, started_ms=None, refreshed_ms=None))
    assert early["lines"][0][1].endswith("since —") and early["lines"][2][1] == "no refresh yet · forever.normal.horde.us"
