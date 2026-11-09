"""Tests for ahsync/mcp/server.py: the assistant server inside AHSync (SPEC §3.8.2; SEC19)."""
from __future__ import annotations

import http.client
import json
import logging
import socket

import pytest

from ahsync.mcp import server as mcp_server
from ahsync.mcp.server import KEY_FILE, McpServer, McpStartError, PORT_IN_USE


@pytest.fixture()
def running(world, tmp_path):
    """A started server on a free port, over the refreshed world."""
    srv = McpServer(world.db_path, tmp_path, world.config, world.clock())
    srv.start(port=0)
    yield srv
    srv.stop()


def _key(srv: McpServer) -> str:
    """The key the server wrote to mcp.json.

    Args:
        srv: A running server.

    Returns:
        The key.
    """
    return json.loads((srv.data_dir / KEY_FILE).read_text(encoding="utf-8"))["key"]


def _send(srv: McpServer, method: str = "POST", body: dict | str | None = None, headers: dict | None = None,
          path: str = "/mcp") -> tuple[int, dict, bytes]:
    """One HTTP request to the server, as a client on this computer would make it.

    Args:
        srv: A running server.
        method: HTTP method.
        body: A JSON-RPC message (dict) or raw text.
        headers: Headers to send; None sends the key and the local Host.
        path: Request path.

    Returns:
        (status, headers, body).
    """
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    raw = body if isinstance(body, str) else json.dumps(body) if body is not None else ""
    if headers is None:
        headers = {"Authorization": f"Bearer {_key(srv)}", "Host": f"127.0.0.1:{srv.port}"}
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for name, value in {**headers, "Content-Length": str(len(raw.encode()))}.items():
        conn.putheader(name, value)
    conn.endheaders(raw.encode())
    response = conn.getresponse()
    out = (response.status, dict(response.getheaders()), response.read())
    conn.close()
    return out


CALL = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_markets", "arguments": {}}}


def test_SEC19_only_the_key_a_local_host_and_no_origin_get_an_answer(running, caplog):
    caplog.set_level(logging.DEBUG, logger="ahsync.mcp")
    key, port = _key(running), running.port
    status, headers, body = _send(running, body=CALL)
    assert status == 200 and json.loads(body)["result"]["isError"] is False
    assert json.loads(json.loads(body)["result"]["content"][0]["text"])["markets"][0]["items"] > 0
    local = {"Authorization": f"Bearer {key}", "Host": f"localhost:{port}"}
    assert _send(running, body=CALL, headers=local)[0] == 200                       # localhost:<port> too
    refused = [
        {"Host": f"127.0.0.1:{port}"},                                               # no key
        {"Authorization": f"Bearer {'0' * 64}", "Host": f"127.0.0.1:{port}"},       # a wrong key
        {"Authorization": f"Bearer {key}", "Host": f"evil.example:{port}"},         # DNS rebinding
        {"Authorization": f"Bearer {key}", "Host": f"127.0.0.1:{port}", "Origin": "https://evil.example"},
        {"Authorization": f"Bearer {key}", "Host": f"127.0.0.1:{port}", "Origin": "null"},
    ]
    for h in refused:
        status, headers, body = _send(running, body=CALL, headers=h)
        assert (status, body) == (403, b"")
        assert not any(name.lower().startswith("access-control") for name in headers)
    assert _send(running, "GET")[0] == 405 and _send(running, "OPTIONS")[0] == 405
    assert _send(running, "GET", headers={"Host": f"127.0.0.1:{port}"})[0] == 403    # no key: no hint either
    assert _send(running, path="/other", body=CALL)[0] == 404
    assert key not in caplog.text


def test_start_writes_the_key_file_and_stop_removes_it_and_closes_the_port(world, tmp_path):
    srv = McpServer(world.db_path, tmp_path, world.config, world.clock())
    srv.start(port=0)
    found = json.loads((tmp_path / KEY_FILE).read_text(encoding="utf-8"))
    assert found["port"] == srv.port and len(found["key"]) == 64 and int(found["key"], 16) >= 0
    assert srv.running and srv.started_ms == world.now_ms
    port = srv.port
    srv.start(port=0)                                                     # a second start changes nothing
    assert srv.port == port
    srv.stop()
    assert not (tmp_path / KEY_FILE).exists() and not srv.running and srv.port is None
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)
    srv.start(port=0)                                                     # a new start, a new key
    assert json.loads((tmp_path / KEY_FILE).read_text(encoding="utf-8"))["key"] != found["key"]
    srv.stop()
    srv.stop()                                                            # stopping twice is harmless


def test_a_port_in_use_is_a_reason_and_leaves_nothing_behind(world, tmp_path):
    holder = McpServer(world.db_path, tmp_path / "a", world.config, world.clock())
    (tmp_path / "a").mkdir()
    holder.start(port=0)
    try:
        second = McpServer(world.db_path, tmp_path, world.config, world.clock())
        with pytest.raises(McpStartError) as error:
            second.start(port=holder.port)
        assert str(error.value) == PORT_IN_USE.format(port=holder.port)
        assert not second.running and not (tmp_path / KEY_FILE).exists()
    finally:
        holder.stop()


def test_protocol_answers_notifications_bad_json_and_unknown_tools(running):
    assert _send(running, body={"jsonrpc": "2.0", "method": "notifications/initialized"})[:1] == (202,)
    status, _, body = _send(running, body="{oops")
    assert status == 200 and json.loads(body)["error"]["code"] == -32700
    status, _, body = _send(running, body={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                           "params": {"name": "drop_table", "arguments": {}}})
    assert json.loads(body)["error"] == {"code": -32602, "message": "Unknown tool: drop_table"}
    status, _, body = _send(running, body={"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert [t["name"] for t in json.loads(body)["result"]["tools"]] == ["list_markets", "describe_data", "query", "item_group_stats", "get_page", "item"]
    big = "x" * (64 * 1024 + 1)
    assert _send(running, body=big)[0] == 413


def test_a_tool_error_or_an_oversized_answer_is_a_result_the_assistant_reads(running, monkeypatch):
    status, _, body = _send(running, body={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                           "params": {"name": "get_page", "arguments": {"page": "nope"}}})
    result = json.loads(body)["result"]
    assert result["isError"] is True and result["content"][0]["text"].startswith("page must be one of")
    monkeypatch.setitem(mcp_server.HANDLERS, "list_markets", lambda ctx, args: {"blob": "x" * (300 * 1024)})
    result = json.loads(_send(running, body=CALL)[2])["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"].startswith("The answer is 300 KB, over the 256 KB limit")


def test_a_call_in_flight_at_stop_answers_off_and_never_rebuilds_the_table(world, tmp_path):
    srv = McpServer(world.db_path, tmp_path, world.config, world.clock())
    srv.start(port=0)
    app, stats = srv._app, srv.stats
    assert stats.size_bytes() > 0
    srv.stop()                                                            # Disable while a request waits on the lock
    answer = app.call("query", {"sql": "SELECT 1"})
    assert answer["isError"] and answer["content"][0]["text"] == mcp_server.STOPPED
    assert stats.size_bytes() == 0 and stats.uri is None                  # still freed (MCP2)


def test_a_start_that_fails_after_binding_releases_everything(world, tmp_path, monkeypatch):
    import sqlite3
    srv = McpServer(world.db_path, tmp_path, world.config, world.clock())

    def broken(self, db, markets):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(mcp_server.MarketStats, "build", broken)
    with pytest.raises(McpStartError) as error:
        srv.start(port=0)
    assert str(error.value) == "AHSync couldn't start the assistant server (OperationalError). Try Enable again."
    assert not srv.running and not (tmp_path / KEY_FILE).exists()
    monkeypatch.undo()
    srv.start(port=0)                                                     # nothing left to block the next Enable
    assert srv.running
    srv.stop()


def test_MCP16_a_refresh_with_the_server_enabled_publishes_exactly_what_it_does_without(world, tmp_path):
    import shutil
    import sqlite3
    from ahsync.app.refresh import refresh_all
    from ahsync.db.sqlite import Database
    outputs = []
    for name, enabled in (("on", True), ("off", False)):
        folder = tmp_path / name
        folder.mkdir()
        shutil.copy(world.db_path, folder / "ahsync.db")
        srv = McpServer(folder / "ahsync.db", folder, world.config, world.clock())
        if enabled:
            srv.start(port=0)
            _send(srv, body={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "query", "arguments": {"sql": "SELECT count(*) FROM market_stats"}}})
        db = Database.open(folder / "ahsync.db", world.now_ms, None)
        assert not refresh_all(db, world.now_ms, world.config).error
        db.close()
        srv.stop()
        conn = sqlite3.connect(folder / "ahsync.db")
        outputs.append((conn.execute("SELECT page, market, body FROM views ORDER BY page, market").fetchall(),
                        conn.execute("SELECT * FROM actions ORDER BY action_id").fetchall()))
        conn.close()
    assert outputs[0] == outputs[1] and outputs[0][0]
