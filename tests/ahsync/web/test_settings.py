"""Tests for ahsync/web/settings.py: the settings endpoints; here the AI assistant's Enable / Disable and its Status
card (SPEC §3.8.1; MCP1, MCP2, MCP4, SEC18). Start at login and updates are tested in test_server.py."""
from __future__ import annotations

import json
import socket
import tomllib

import pytest

from ahsync.config import Config
from ahsync.mcp.server import KEY_FILE, McpServer
from ahsync.web.server import TOKEN_HEADER, DashboardApp
from tests.ahsync.mcp.conftest import MARKET, world  # noqa: F401  the refreshed fixture world
from tests.support.fake_clock import FakeClock


def _headers(token: str = "tok-123", origin: str | None = "http://127.0.0.1:8765") -> dict:
    """A dashboard write's headers.

    Args:
        token: The write token.
        origin: Origin; None leaves it out.

    Returns:
        Headers.
    """
    h = {"Host": "127.0.0.1:8765", TOKEN_HEADER: token, "Content-Type": "application/json"}
    if origin:
        h["Origin"] = origin
    return h


@pytest.fixture()
def dash(world, tmp_path):
    """A dashboard over the refreshed world with an AI assistant server on a free port, and its config.toml."""
    from ahsync.db.sqlite import Database
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'markets = ["{MARKET}"]\n', encoding="utf-8")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = Config(markets=(MARKET,), mcp_port=port)
    db = Database.open(world.db_path, world.now_ms, None)
    assistant = McpServer(world.db_path, tmp_path, config, world.clock())
    app = DashboardApp(db, config, FakeClock(world.now_ms), lambda: None, token="tok-123", port=8765,
                       config_path=config_path, mcp=assistant, mcp_command=("C:\\AHSync\\ahsync-mcp.exe",))
    yield app, assistant, config_path, tmp_path
    assistant.stop()
    db.close()


def _post(app, body: dict | str, headers: dict | None = None) -> tuple[int, dict]:
    """POST /api/mcp.

    Args:
        app: The dashboard.
        body: JSON body (dict) or raw text.
        headers: Headers; None: a good write.

    Returns:
        (status, JSON body).
    """
    raw = body if isinstance(body, str) else json.dumps(body)
    reply = app.handle("POST", "/api/mcp", headers or _headers(), raw.encode())
    return reply.status, json.loads(reply.body or b"{}")


def _card(app) -> dict:
    """The Status page's AI assistant card.

    Args:
        app: The dashboard.

    Returns:
        The card.
    """
    reply = app.handle("GET", "/api/views/status", {"Host": "127.0.0.1:8765"}, b"")
    return json.loads(reply.body)["data"]["mcp_card"]


def test_MCP1_enable_starts_the_server_builds_the_table_and_remembers_it(dash):
    app, assistant, config_path, data = dash
    off = _card(app)
    assert (off["pill"], off["button"]["text"], off["setup"]) == ("off", "Enable", None)
    assert not (data / KEY_FILE).exists() and not assistant.running and assistant.stats.size_bytes() == 0
    assert _post(app, {"enabled": True}) == (200, {"set": True, "enabled": True})
    key = json.loads((data / KEY_FILE).read_text(encoding="utf-8"))
    assert key["port"] == assistant.port and len(key["key"]) == 64
    assert assistant.stats.count() == len(app.db.read_intel(MARKET).items) > 0
    assert tomllib.loads(config_path.read_text(encoding="utf-8"))["mcp_enabled"] is True
    on = _card(app)
    assert (on["pill"], on["button"]["text"], on["lines"][1][0]) == ("running", "Disable", "Memory in use")
    assert on["lines"][1][1].endswith(f"market table of {assistant.stats.count():,} items")
    assert on["setup"]["desktop"]["status"] == "missing"                  # the test's dashboard finds no Claude
    assert on["setup"]["code"]["steps"][0]["code"] == "claude mcp add ahsync -- C:\\AHSync\\ahsync-mcp.exe"


def test_MCP2_disable_stops_the_server_frees_the_table_and_remembers_it(dash):
    app, assistant, config_path, data = dash
    _post(app, {"enabled": True})
    port = assistant.port
    assert _post(app, {"enabled": False}) == (200, {"set": True, "enabled": False})
    assert not assistant.running and not (data / KEY_FILE).exists() and assistant.stats.size_bytes() == 0
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)
    assert tomllib.loads(config_path.read_text(encoding="utf-8"))["mcp_enabled"] is False
    assert _card(app)["pill"] == "off"


def test_MCP4_a_port_in_use_is_shown_and_nothing_is_saved(dash):
    app, assistant, config_path, data = dash
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", app.config.mcp_port))
        busy.listen()
        status, body = _post(app, {"enabled": True})
    port = app.config.mcp_port
    reason = (f"Port {port} is in use by another program. Set mcp_port in AHSync's settings to a free port, "
              "then Enable again.")
    assert (status, body) == (200, {"enabled": False, "error": reason})
    assert not assistant.running and not (data / KEY_FILE).exists()
    assert "mcp_enabled" not in config_path.read_text(encoding="utf-8")
    card = _card(app)
    assert (card["pill"], card["error"], card["button"]["text"]) == ("couldn't start", reason, "Enable")
    assert _post(app, {"enabled": True})[1] == {"set": True, "enabled": True}      # the port is free again
    assert _card(app)["error"] is None


def test_SEC18_post_api_mcp_is_checked_like_every_write(dash):
    app, assistant, _config, _data = dash
    assert _post(app, {"enabled": True}, _headers(origin="https://evil.example"))[0] == 403
    assert _post(app, {"enabled": True}, _headers(token="wrong"))[0] == 403
    assert not assistant.running                                          # neither changed anything
    assert _post(app, {"enabled": "yes"}) == (400, {"error": "bad_request",
                                                    "message": "enabled must be true or false"})
    assert app.handle("GET", "/api/mcp", {"Host": "127.0.0.1:8765"}, b"").status == 405
    assert _post(app, {"enabled": True})[0] == 200 and assistant.running


def test_without_a_server_there_is_no_card_and_the_endpoint_is_404(dash):
    app, *_ = dash
    app.mcp = None
    reply = app.handle("GET", "/api/views/status", {"Host": "127.0.0.1:8765"}, b"")
    assert "mcp_card" not in json.loads(reply.body)["data"]
    assert _post(app, {"enabled": True}) == (404, {"error": "not_found"})


def test_MCP20_the_status_page_shows_nothing_of_claudes_file_but_the_ahsync_command(dash, caplog):
    from ahsync.mcp.claude_config import find_claude_config
    app, _assistant, _config, data = dash
    claude = data / "Roaming" / "Claude"
    claude.mkdir(parents=True)
    (claude / "claude_desktop_config.json").write_text(json.dumps({"mcpServers": {
        "github": {"command": "gh-mcp", "env": {"TOKEN": "ghp_secret123"}},
        "ahsync": {"command": r"C:\Old\ahsync-mcp.exe", "env": {"X": "hidden"}}}}), encoding="utf-8")
    app.claude_config = lambda: find_claude_config({"APPDATA": str(data / "Roaming")}, data, "win32")
    assert _post(app, {"enabled": True})[0] == 200
    with caplog.at_level("DEBUG"):
        reply = app.handle("GET", "/api/views/status", {"Host": "127.0.0.1:8765"}, b"")
    page = reply.body.decode("utf-8") + caplog.text
    assert r"C:\\Old\\ahsync-mcp.exe" in page                      # the ahsync entry's command, JSON-escaped
    assert not any(secret in page for secret in ("github", "gh-mcp", "TOKEN", "ghp_secret123", "hidden"))


def test_MCP21_each_status_build_looks_again_and_changes_nothing(dash):
    from ahsync.mcp.claude_config import find_claude_config
    app, _assistant, _config, data = dash
    claude = data / "Roaming" / "Claude"
    claude.mkdir(parents=True)
    config = claude / "claude_desktop_config.json"
    app.claude_config = lambda: find_claude_config({"APPDATA": str(data / "Roaming")}, data, "win32")
    assert _post(app, {"enabled": True})[0] == 200
    assert _card(app)["setup"]["desktop"]["steps"][1] == {"text": "Create this file:", "code": str(config)}
    assert not config.exists() and sorted(p.name for p in claude.iterdir()) == []     # never created
    config.write_text(json.dumps({"mcpServers": {"ahsync": {"command": r"C:\AHSync\ahsync-mcp.exe"}}}),
                      encoding="utf-8")
    before = (config.read_bytes(), config.stat().st_mtime_ns)
    for _ in range(2):
        assert _card(app)["setup"]["desktop"]["status"] == "ok"                       # the change shows at once
    assert (config.read_bytes(), config.stat().st_mtime_ns) == before
    assert sorted(p.name for p in claude.iterdir()) == ["claude_desktop_config.json"]
    for text, status in (("", "todo"), ("{}", "todo"), ('{"a": 1}', "todo"), ('{"mcpServers": {}}', "todo"),
                         (json.dumps({"mcpServers": {"ahsync": {"command": r"C:\Old.exe"}}}), "todo"),
                         ('{"preferences": {"mcpServers": {}}}', "warn"),
                         ('{"mcpServers": [', "warn"), ('{"mcpServers": []}', "warn"), (" " * 1_000_001, "warn")):
        config.write_text(text, encoding="utf-8")
        before = (config.read_bytes(), config.stat().st_mtime_ns)
        for _ in range(2):
            assert _card(app)["setup"]["desktop"]["status"] == status, text[:40]
        assert (config.read_bytes(), config.stat().st_mtime_ns) == before             # every state: read-only
        assert sorted(p.name for p in claude.iterdir()) == ["claude_desktop_config.json"]


def test_MCP21_a_finder_that_fails_still_lets_the_status_page_answer(dash, caplog):
    app, _assistant, _config, _data = dash

    def broken():
        raise RuntimeError("ghp_secret123 in a message")
    app.claude_config = broken
    assert _post(app, {"enabled": True})[0] == 200
    reply = app.handle("GET", "/api/views/status", {"Host": "127.0.0.1:8765"}, b"")
    setup = json.loads(reply.body)["data"]["mcp_card"]["setup"]["desktop"]
    assert (reply.status, setup["status"]) == (200, "warn")
    assert setup["message"].startswith("AHSync couldn't read this file (something went wrong while AHSync looked")
    assert "RuntimeError" in caplog.text and "ghp_secret123" not in caplog.text + reply.body.decode("utf-8")
