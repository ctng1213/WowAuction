"""Tests for ahsync/mcp/protocol.py: the MCP subset shared by the server and the bridge (SPEC §3.8.2, §3.8.3)."""
from __future__ import annotations

import json

from ahsync.mcp import protocol
from ahsync.web.server import PAGES as DASHBOARD_PAGES


def test_MCP7_initialize_gives_context_not_instructions():
    answer = protocol.local_answer({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                               "clientInfo": {"name": "claude-ai", "version": "1"}}}, "0.1.12")
    got = answer["result"]
    assert got["protocolVersion"] == "2025-03-26"                          # the client's, when AHSync speaks it
    assert got["serverInfo"] == {"name": "AHSync", "version": "0.1.12"}
    assert got["capabilities"] == {"tools": {"listChanged": False}}
    text = got["instructions"]
    for fact in ("World of Warcraft: Forever", "Classic-era", "Item ids are Classic's", "2147483648", "copper",
                 "UTC Unix milliseconds", "last refresh", "list_markets"):
        assert fact in text
    for procedure in ("you should", "always", "first call", "step"):            # context, never a procedure
        assert procedure not in text.lower()
    newest = protocol.local_answer({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                                    "params": {"protocolVersion": "2099-01-01"}}, "0.0.0")
    assert newest["result"]["protocolVersion"] == protocol.PROTOCOL_VERSIONS[0]


def test_ping_tools_list_unknown_methods_and_notifications():
    assert protocol.local_answer({"jsonrpc": "2.0", "id": 3, "method": "ping"}, "0") == \
        {"jsonrpc": "2.0", "id": 3, "result": {}}
    listed = protocol.local_answer({"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, "0")["result"]["tools"]
    assert [t["name"] for t in listed] == ["list_markets", "get_page", "item"] == list(protocol.tool_names())
    assert all(t["inputSchema"]["type"] == "object" and t["description"] for t in listed)
    assert protocol.local_answer({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {}}, "0") is None
    assert protocol.local_answer({"jsonrpc": "2.0", "method": "notifications/initialized"}, "0") is None
    unknown = protocol.local_answer({"jsonrpc": "2.0", "id": 6, "method": "resources/list"}, "0")
    assert unknown["error"]["code"] == protocol.METHOD_NOT_FOUND


def test_bad_messages_get_json_rpc_errors():
    assert protocol.parse_message("{not json")[1]["error"]["code"] == protocol.PARSE_ERROR
    assert protocol.parse_message('{"jsonrpc": "1.0", "id": 7, "method": "ping"}')[1] == \
        {"jsonrpc": "2.0", "id": 7, "error": {"code": protocol.INVALID_REQUEST, "message": "Invalid request"}}
    assert protocol.parse_message("[1, 2]")[1]["id"] is None
    msg, bad = protocol.parse_message('{"jsonrpc": "2.0", "id": 8, "method": "ping"}')
    assert bad is None and msg["id"] == 8 and not protocol.is_notification(msg)


def test_tool_results_carry_json_text_and_errors_carry_a_sentence():
    ok = protocol.tool_result({"a": 1, "b": "é"})
    assert ok["isError"] is False and json.loads(ok["content"][0]["text"]) == {"a": 1, "b": "é"}
    assert ok["structuredContent"] == {"a": 1, "b": "é"}
    assert protocol.tool_result([1])["structuredContent"] == {"value": [1]}
    bad = protocol.tool_error("market must be one of: forever.pvp.horde.us")
    assert bad == {"content": [{"type": "text", "text": "market must be one of: forever.pvp.horde.us"}],
                   "isError": True}
    assert str(protocol.ToolError("no")) == "no" and protocol.ToolError("no").message == "no"


def test_the_tools_pages_are_the_dashboards():
    assert protocol.PAGES == DASHBOARD_PAGES
    page_tool = next(t for t in protocol.TOOLS if t["name"] == "get_page")
    assert page_tool["inputSchema"]["properties"]["page"]["enum"] == list(DASHBOARD_PAGES)
