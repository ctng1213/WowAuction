"""Tests for ahsync/mcp/bridge.py: the stdio bridge Claude launches (SPEC §3.8.2; MCP6)."""
from __future__ import annotations

import io
import json
import socket

from ahsync.mcp import bridge
from ahsync.mcp.bridge import NOT_RUNNING, OFF, TOO_SLOW, forward, read_key_file, respond, run
from ahsync.mcp.server import McpServer, write_key_file

CALL = {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "list_markets", "arguments": {}}}


def _never(*_args):
    """A post that must not happen."""
    raise AssertionError("the bridge posted without a key file")


def _free_port() -> int:
    """A port nothing listens on.

    Returns:
        The port number.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_MCP6_without_the_server_the_bridge_says_why_and_still_connects(tmp_path):
    off = respond(json.dumps(CALL), tmp_path, "0.1.12", _never)
    assert off == {"jsonrpc": "2.0", "id": 9, "result": {"content": [{"type": "text", "text": OFF}], "isError": True}}
    assert OFF == ("AHSync's AI assistant access is off, or AHSync isn't running. Start AHSync and press Enable on "
                   "its Status page (AI assistant), then ask again.")
    write_key_file(tmp_path, _free_port(), "ab" * 32)                      # a file left behind, nothing listening
    stale = respond(json.dumps(CALL), tmp_path, "0.1.12", bridge.http_post)
    assert stale["result"]["content"][0]["text"] == NOT_RUNNING == "AHSync isn't running. Start it and ask again."
    hello = respond(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                "params": {"protocolVersion": "2025-06-18"}}), tmp_path, "0.1.12", _never)
    assert hello["result"]["serverInfo"] == {"name": "AHSync", "version": "0.1.12"}


def test_MCP5_the_bridge_answers_the_handshake_itself_and_forwards_calls_with_the_key(tmp_path):
    sent = []
    answer = {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "{\"rows\":[[42]]}"}],
                                                   "isError": False}}

    def post(url, body, headers, timeout_s):
        sent.append((url, json.loads(body), headers))
        return 200, json.dumps(answer).encode()

    out = io.StringIO()
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "query", "arguments": {"sql": "SELECT count(*) FROM items"}}}
    write_key_file(tmp_path, 8766, "ab" * 32)
    run([json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
         json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}), json.dumps(call)], out, tmp_path, "0.1.12",
        post)
    hello, listing, result = (json.loads(line) for line in out.getvalue().splitlines())
    assert hello["result"]["protocolVersion"] == "2025-06-18" and hello["result"]["serverInfo"]["name"] == "AHSync"
    tools = listing["result"]["tools"]
    assert [t["name"] for t in tools] == ["list_markets", "describe_data", "query", "item_group_stats", "get_page",
                                          "item"]
    assert all(t["inputSchema"]["type"] == "object" for t in tools)
    assert len(sent) == 1                                                 # only the call left the bridge
    assert sent[0] == ("http://127.0.0.1:8766/mcp", call, {"Authorization": "Bearer " + "ab" * 32,
                                                            "Content-Type": "application/json"})
    assert result == answer                                               # unchanged


def test_a_call_carries_the_key_read_at_that_call_and_passes_the_answer_back(tmp_path):
    seen = []

    def post(url, body, headers, timeout_s):
        seen.append((url, json.loads(body), headers, timeout_s))
        return 200, json.dumps({"jsonrpc": "2.0", "id": 9, "result": {"isError": False, "content": []}}).encode()

    write_key_file(tmp_path, 8766, "11" * 32)
    assert forward(CALL, tmp_path, post)["result"] == {"isError": False, "content": []}
    write_key_file(tmp_path, 8767, "22" * 32)                              # Disable, Enable: a new key and port
    forward(CALL, tmp_path, post)
    assert [(s[0], s[2]["Authorization"]) for s in seen] == [
        ("http://127.0.0.1:8766/mcp", "Bearer " + "11" * 32), ("http://127.0.0.1:8767/mcp", "Bearer " + "22" * 32)]
    assert seen[0][1] == CALL and seen[0][3] == bridge.CALL_TIMEOUT_S


def test_a_refusal_a_timeout_or_garbage_reads_as_a_reason(tmp_path):
    write_key_file(tmp_path, 8766, "11" * 32)

    def timeout(*_args):
        raise TimeoutError("timed out")

    assert forward(CALL, tmp_path, timeout)["result"]["content"][0]["text"] == TOO_SLOW
    assert forward(CALL, tmp_path, lambda *a: (403, b""))["result"]["content"][0]["text"] == NOT_RUNNING
    assert forward(CALL, tmp_path, lambda *a: (200, b"<html>"))["result"]["content"][0]["text"] == NOT_RUNNING
    for bad in ('{"port": "8766", "key": "x"}', '{"port": 8766}', "not json", '{"port": true, "key": "k"}'):
        (tmp_path / "mcp.json").write_text(bad, encoding="utf-8")
        assert read_key_file(tmp_path) is None


def test_run_answers_each_request_once_in_order_and_skips_notifications_and_blank_lines(tmp_path):
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}), "",
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}), json.dumps(CALL), "{bad"]
    out = io.StringIO()
    run(lines, out, tmp_path, "0.0.0", _never)
    answers = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [a.get("id") for a in answers] == [1, 2, 9, None]
    assert answers[2]["result"]["content"][0]["text"] == OFF and answers[3]["error"]["code"] == -32700


def test_the_bridge_and_the_server_together(world, tmp_path):
    srv = McpServer(world.db_path, tmp_path, world.config, world.clock())
    srv.start(port=0)
    try:
        out = io.StringIO()
        run([json.dumps(CALL), json.dumps({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                                           "params": {"name": "item", "arguments": {"name": "Linen Cloth"}}})],
            out, tmp_path, "0.0.0")
        first, second = (json.loads(line) for line in out.getvalue().splitlines())
        assert json.loads(first["result"]["content"][0]["text"])["markets"][0]["market"] == "forever.pvp.horde.us"
        assert json.loads(second["result"]["content"][0]["text"])["key"] == 2589
    finally:
        srv.stop()
    out = io.StringIO()
    run([json.dumps(CALL)], out, tmp_path, "0.0.0")                       # disabled: no mcp.json any more
    assert json.loads(out.getvalue())["result"]["content"][0]["text"] == OFF
