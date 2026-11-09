"""Tests for ahsync/engine/mcp_view.py: the Status page's AI assistant card (SPEC §3.8.1)."""
from __future__ import annotations

import json
from dataclasses import replace

from ahsync.engine.mcp_view import McpFacts, code_snippet, desktop_snippet, mcp_card, megabytes

SUN_13_10 = 1_794_143_400_000          # Sun 2026-11-08 13:10 UTC
WINDOWS = ("C:\\Users\\you\\AppData\\Local\\Programs\\AHSync\\ahsync-mcp.exe",)
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
    desktop = json.loads(card["setup"]["desktop"]["snippet"])
    assert desktop == {"mcpServers": {"ahsync": {"command": WINDOWS[0]}}}
    assert card["setup"]["code"]["snippet"] == f"claude mcp add ahsync -- {WINDOWS[0]}"
    assert len(card["setup"]["desktop"]["steps"]) == 4 and len(card["ask"]) == 3


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
