"""Tests for ahsync/web/server.py: the dashboard's endpoints and Security rules (SPEC §3.5, SEC1-SEC7)."""
from __future__ import annotations

import http.client
import json
import sqlite3
import logging
import re
from dataclasses import dataclass, field

import pytest

from ahsync.config import Config
from ahsync.engine.views import (COLUMN_HELP, MARKET_COLUMNS, CraftInput, DisenchantInput, craft_money, craft_row,
                                 disenchant_row, market_activity)
from ahsync.db.sqlite import Database
from ahsync.web.server import (INDEX_HTML, MAX_BODY_BYTES, PAGES, TOKEN_HEADER, TOKEN_PLACEHOLDER, DashboardApp,
                               new_token, open_server_db, start_dashboard, stop_dashboard)
from tests.support.fake_clock import FakeClock

M, OTHER = "forever.pvp.horde.us", "forever.pvp.alliance.us"
KEY = "ak_live_SECRET_9f8e7d6c5b4a"
BUILT = 1_792_150_000_000


@dataclass
class Refreshes:
    """Counts refresh requests (stands in for the scheduler's hook)."""

    calls: list[int] = field(default_factory=list)

    def __call__(self) -> None:
        """Record one request."""
        self.calls.append(1)


@dataclass
class Reply:
    """One HTTP reply as the tests see it."""

    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict:
        """Decode the body as JSON."""
        return json.loads(self.body.decode("utf-8"))


def de_row() -> dict:
    """One Disenchant row, built by the engine exactly as the full refresh will."""
    pair = market_activity(0.92, 86, 48, "ahf", 93)
    return disenchant_row(DisenchantInput(4567, "Bandit Cinch", 2, 18, 300, (("Strange Dust", 1.6),), 912.0,
                                          pair, 20 * 60_000), 314_000)


def craft_row_example() -> dict:
    """One Craft row, built by the engine."""
    pair = market_activity(0.65, 64, 5, "ahf", 6)
    cells = craft_money(1, 4200, {}, {})
    return craft_row(CraftInput(11479, "Transmute: Iron to Gold", 2, "Alchemy", "Brugan (Alchemy 225)", "AH", 1, cells, 4200,
                                9500.0, 1855.0, pair, "ready", "Buys 1 Iron Bar on the AH"), 314_000)


@pytest.fixture
def db(tmp_path):
    """A fresh database, opened as AHSync does."""
    database = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    yield database
    database.close()


def publish(db: Database, page: str, market: str, body: dict, built_ms: int = BUILT) -> None:
    """Publish one page's payload the way a full refresh does (stage, then swap)."""
    with db.transaction():
        db.stage_view(page, market, body, built_ms)
    db.publish_refresh([market])


def add_action(db: Database, action_id: str, status: str = "pending") -> None:
    """Insert a plan action."""
    with db.transaction():
        db.conn.execute("INSERT INTO actions(action_id, status) VALUES (?, ?)", (action_id, status))


def action_status(db: Database, action_id: str) -> str:
    """Read a plan action's status."""
    return db.conn.execute("SELECT status FROM actions WHERE action_id = ?", (action_id,)).fetchone()[0]


@pytest.fixture
def refreshes():
    """The injected refresh hook."""
    return Refreshes()


@pytest.fixture
def app(db, refreshes):
    """An app on port 8765, called directly (no socket)."""
    config = Config(markets=(M, OTHER), api_key=KEY)
    return DashboardApp(db, config, FakeClock(), refreshes, token="tok-123", port=8765)


GOOD = {"Host": "127.0.0.1:8765"}


def write_headers(token: str = "tok-123", origin: str = "http://127.0.0.1:8765", host: str = "127.0.0.1:8765") -> dict:
    """Headers of a well-formed write from the dashboard page."""
    return {"Host": host, "Origin": origin, TOKEN_HEADER: token, "Content-Type": "application/json"}


# ---------------------------------------------------------------- live server

@pytest.fixture
def live(tmp_path, refreshes):
    """A real server on an ephemeral 127.0.0.1 port, with its own thread-safe connection."""
    Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None).close()
    server_db = open_server_db(tmp_path / "ahsync.db")
    config = Config(markets=(M, OTHER), api_key=KEY)
    app = DashboardApp(server_db, config, FakeClock(), refreshes, token=new_token(), port=0)
    server = start_dashboard(app, 0)
    yield app
    stop_dashboard(server)
    server_db.close()


def send(app: DashboardApp, method: str, target: str, headers: dict | None = None, body: bytes = b"") -> Reply:
    """Send one real HTTP request to a live app (Host defaults to 127.0.0.1:<port>)."""
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=5)
    try:
        sent = {"Host": f"127.0.0.1:{app.port}", **(headers or {})}
        conn.request(method, target, body=body or None, headers=sent)
        response = conn.getresponse()
        return Reply(response.status, {k.lower(): v for k, v in response.getheaders()}, response.read())
    finally:
        conn.close()


def live_write(app: DashboardApp, path: str, payload: dict, **overrides: str) -> Reply:
    """A real POST from the dashboard page, with any header overridden."""
    origin = f"http://127.0.0.1:{app.port}"
    headers = {"Origin": origin, TOKEN_HEADER: app.token, "Content-Type": "application/json", **overrides}
    headers = {k: v for k, v in headers.items() if v is not None}
    return send(app, "POST", path, headers, json.dumps(payload).encode())


# ---------------------------------------------------------------- SEC scenarios

def test_SEC1_skip_with_the_right_token_and_origin_is_accepted(live):
    add_action(live.db, "a1")
    reply = live_write(live, "/api/skip", {"action_id": "a1"})
    assert reply.status == 200 and reply.json() == {"skipped": True}
    assert action_status(live.db, "a1") == "skipped"


def test_SEC1_the_localhost_origin_is_accepted_too(app, db):
    add_action(db, "a1")
    reply = app.handle("POST", "/api/skip", write_headers(host="localhost:8765", origin="http://localhost:8765"),
                       b'{"action_id": "a1"}')
    assert reply.status == 200 and action_status(db, "a1") == "skipped"


def test_SEC2_a_foreign_origin_gets_403_and_is_logged_without_the_token(live, caplog):
    add_action(live.db, "a1")
    with caplog.at_level(logging.WARNING, logger="ahsync.web"):
        reply = live_write(live, "/api/skip", {"action_id": "a1"}, Origin="https://evil.example")
    assert reply.status == 403 and reply.json()["error"] == "bad_origin"
    assert action_status(live.db, "a1") == "pending"
    assert "evil.example" in caplog.text and live.token not in caplog.text


def test_SEC2_origin_must_match_the_host_used(app, db, refreshes):
    for origin in ("", "null", "http://127.0.0.1:9999", "https://127.0.0.1:8765", "http://localhost:8765"):
        reply = app.handle("POST", "/api/refresh", write_headers(origin=origin))
        assert reply.status == 403, origin
    assert refreshes.calls == []


def test_SEC3_a_write_without_the_token_gets_403(live, caplog):
    add_action(live.db, "a1")
    with caplog.at_level(logging.WARNING, logger="ahsync.web"):
        reply = live_write(live, "/api/skip", {"action_id": "a1"}, **{TOKEN_HEADER: None})
        refresh = live_write(live, "/api/refresh", {}, **{TOKEN_HEADER: None})
    assert reply.status == 403 and refresh.status == 403
    assert action_status(live.db, "a1") == "pending" and live.request_refresh.calls == []
    assert "missing token" in caplog.text


def test_SEC3_a_wrong_token_gets_403_and_is_not_logged(app, db, caplog):
    add_action(db, "a1")
    with caplog.at_level(logging.WARNING, logger="ahsync.web"):
        reply = app.handle("POST", "/api/skip", write_headers(token="guess-456"), b'{"action_id": "a1"}')
    assert reply.status == 403 and action_status(db, "a1") == "pending"
    assert "wrong token" in caplog.text and "guess-456" not in caplog.text and "tok-123" not in caplog.text


def test_SEC4_a_rebinding_host_gets_403_with_no_data(live):
    publish(live.db, "craft", M, {"rows": [craft_row_example()], "hidden": 0})
    add_action(live.db, "a1")
    evil = {"Host": f"evil.example:{live.port}"}
    for method, target in (("GET", "/"), ("GET", "/api/markets"), ("GET", f"/api/views/craft?market={M}"),
                           ("GET", "/nope"), ("POST", "/api/skip")):
        reply = live_write(live, target, {"action_id": "a1"}, **evil) if method == "POST" else \
            send(live, method, target, evil)
        assert reply.status == 403, target
        assert reply.json() == {"error": "bad_host"}
        assert live.token.encode() not in reply.body and b"Transmute" not in reply.body and M.encode() not in reply.body
    assert action_status(live.db, "a1") == "pending"


def test_SEC4_only_the_two_loopback_names_with_this_port_pass(app):
    for host in ("127.0.0.1:8765", "localhost:8765"):
        assert app.handle("GET", "/api/markets", {"Host": host}).status == 200
    for host in ("", "127.0.0.1", "localhost", "127.0.0.1:8766", "LOCALHOST:8765", "[::1]:8765", "0.0.0.0:8765"):
        assert app.handle("GET", "/api/markets", {"Host": host}).status == 403, host


def test_SEC5_get_on_skip_is_405_and_changes_nothing(live, refreshes):
    add_action(live.db, "a1")
    skip = send(live, "GET", "/api/skip?id=a1")
    refresh = send(live, "GET", "/api/refresh")
    assert skip.status == 405 and refresh.status == 405 and skip.headers["allow"] == "POST"
    assert action_status(live.db, "a1") == "pending" and refreshes.calls == []


def test_SEC6_no_cors_header_and_no_secret_in_any_response(live, tmp_path):
    publish(live.db, "disenchant", M, {"rows": [de_row()], "hidden": 1})
    add_action(live.db, "a1")
    replies = [
        send(live, "GET", "/"), send(live, "GET", "/api/markets"), send(live, "GET", f"/api/views/disenchant?market={M}"),
        send(live, "GET", f"/api/views/craft?market={M}"), send(live, "GET", "/api/views/nope"),
        send(live, "GET", "/api/views/craft?market=forever.rp.alliance.us"), send(live, "GET", "/api/skip"),
        send(live, "OPTIONS", "/api/skip", {"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}),
        send(live, "PUT", "/api/skip"), send(live, "DELETE", "/"), send(live, "GET", "/", {"Host": "evil.example"}),
        live_write(live, "/api/skip", {"action_id": "a1"}), live_write(live, "/api/skip", {"bad": 1}),
        live_write(live, "/api/refresh", {}), live_write(live, "/api/refresh", {}, Origin="https://evil.example"),
        live_write(live, "/api/refresh", {}, **{TOKEN_HEADER: "old"}),
        send(live, "POST", "/api/skip", {"Content-Length": str(MAX_BODY_BYTES + 1)}),
    ]
    assert {r.status for r in replies} >= {200, 202, 400, 403, 404, 405, 413}
    for reply in replies:
        assert not any(name.startswith("access-control-") for name in reply.headers), reply.headers
        assert KEY.encode() not in reply.body
        assert str(tmp_path).encode() not in reply.body and str(tmp_path.as_posix()).encode() not in reply.body
        assert b"ahsync.db" not in reply.body


def test_SEC7_a_restart_makes_a_new_token_and_old_pages_are_told_to_reload(db, refreshes):
    config = Config(markets=(M,))
    first = DashboardApp(db, config, FakeClock(), refreshes, token=new_token(), port=8765)
    page = first.handle("GET", "/", GOOD).body.decode()
    old_token = first.token
    assert f'content="{old_token}"' in page

    restarted = DashboardApp(db, config, FakeClock(), refreshes, token=new_token(), port=8765)
    assert restarted.token != old_token
    reply = restarted.handle("POST", "/api/refresh", write_headers(token=old_token))
    assert reply.status == 403
    assert json.loads(reply.body) == {"error": "bad_token", "reload": True,
                                      "message": "AHSync was restarted: reload this page."}
    assert refreshes.calls == []
    assert "reload this page" in page                    # the page shows the reload prompt on that answer


# ---------------------------------------------------------------- endpoints

def test_index_serves_the_page_with_this_runs_token(app):
    reply = app.handle("GET", "/", GOOD)
    html = reply.body.decode()
    assert reply.status == 200 and reply.content_type.startswith("text/html")
    assert 'name="ahsync-token" content="tok-123"' in html and TOKEN_PLACEHOLDER not in html
    assert TOKEN_PLACEHOLDER in INDEX_HTML.read_text(encoding="utf-8")


def test_index_has_every_page_in_the_mockup_order_and_loads_nothing_external(app):
    html = app.handle("GET", "/", GOOD).body.decode()
    names = ["Today", "Week", "On the AH", "Disenchant", "Craft", "Flip", "Inventory", "History", "Rules",
             "Characters", "Status"]
    positions = [html.index(f"['{name}', ") for name in names]
    assert positions == sorted(positions)
    assert "<script src" not in html and "<link" not in html and "@import" not in html
    assert "Coming in a later milestone" in html


def test_every_live_table_has_the_market_columns_in_order_and_every_header_a_tooltip(app):
    html = app.handle("GET", "/", GOOD).body.decode()
    market = re.search(r"const MARKET_COLS = (\[.*?\]);\n", html).group(1)
    assert re.findall(r"\['([^']+)', 1, '(\w+)'\]", market) == [(h, c) for h, c in MARKET_COLUMNS]
    live = html[html.index("const LIVE = {"):html.index("// ---------- state")]
    tables = re.findall(r"cols: (\[.*?\]),\n\s*row:", live, re.S)
    assert len(tables) == 5 and all("...MARKET_COLS" in cols for cols in tables)     # today, flip, inventory, DE, craft
    headers = set(re.findall(r"\['([^']+)', [01], ", live + market))
    assert headers - set(COLUMN_HELP) == set()        # SPEC §3.5: every column header has a tooltip


def test_markets_come_from_the_config(app):
    reply = app.handle("GET", "/api/markets", GOOD)
    assert reply.status == 200 and json.loads(reply.body) == {"markets": [M, OTHER], "default": M}


def test_views_before_the_first_refresh_say_so(app):
    for page in ("disenchant", "craft"):
        reply = app.handle("GET", f"/api/views/{page}?market={M}", GOOD)
        assert reply.status == 404 and json.loads(reply.body) == {"error": "no_refresh_yet"}


def test_views_serve_the_stored_payload_as_is(app, db):
    rows = {"rows": [de_row()], "hidden": 2}
    publish(db, "disenchant", M, rows)
    reply = app.handle("GET", f"/api/views/disenchant?market={M}", GOOD)
    assert reply.status == 200
    assert json.loads(reply.body) == {"built": BUILT, "data": json.loads(json.dumps(rows))}
    row = json.loads(reply.body)["data"]["rows"][0]
    assert row["chance"] == "92%" and row["dot"] == "high" and row["sort"]["profit"] == 612


def test_views_pick_the_requested_market_or_the_default(app, db):
    publish(db, "craft", M, {"rows": [craft_row_example()], "hidden": 0}, built_ms=1)
    publish(db, "craft", OTHER, {"rows": [], "hidden": 0}, built_ms=2)
    assert json.loads(app.handle("GET", f"/api/views/craft?market={OTHER}", GOOD).body)["built"] == 2
    assert json.loads(app.handle("GET", "/api/views/craft", GOOD).body)["built"] == 1
    assert json.loads(app.handle("GET", "/api/views/craft?market=", GOOD).body)["built"] == 1


def test_views_of_an_unconfigured_market_are_404(app):
    reply = app.handle("GET", "/api/views/craft?market=forever.rp.alliance.us", GOOD)
    assert reply.status == 404 and json.loads(reply.body) == {"error": "unknown_market"}


def test_every_contract_page_is_routed_and_unknown_pages_are_404(app):
    assert set(PAGES) == {"disenchant", "craft", "today", "week", "on_ah", "flip", "inventory", "history", "rules",
                          "characters", "status"}
    for page in PAGES:
        if page != "status":                      # built on request, never "no refresh yet"
            assert json.loads(app.handle("GET", f"/api/views/{page}", GOOD).body) == {"error": "no_refresh_yet"}
    for target in ("/api/views/nope", "/api/views/", "/api/views", "/index.html", "/api", "/../data/ahsync.db"):
        reply = app.handle("GET", target, GOOD)
        assert reply.status == 404 and json.loads(reply.body) == {"error": "not_found"}, target


def test_status_is_built_on_request_and_never_shows_the_key(app, db):
    with db.transaction():
        db.set_status("realm:X", "warn", "unknown_realm", {"realm": "X", "faction": "Horde"}, BUILT)
    reply = app.handle("GET", "/api/views/status", GOOD)
    body = json.loads(reply.body)
    assert reply.status == 200 and body["built"] == FakeClock().now_ms()
    assert [a["title"] for a in body["data"]["alerts"]] == ["No AHF prices yet", "No AHF prices yet",
                                                            "Realm not mapped to a market"]
    assert [c["title"] for c in body["data"]["cards"]] == [M, OTHER, "API", "Characters", "Storage"]
    assert KEY not in reply.body.decode()


def test_status_says_busy_when_the_database_cant_be_read(app, db):
    db.conn.close()
    reply = app.handle("GET", "/api/views/status", GOOD)
    assert reply.status == 503 and json.loads(reply.body) == {"error": "database_busy"}


def test_post_on_a_read_endpoint_is_405(app):
    for target in ("/", "/api/markets", "/api/views/craft"):
        reply = app.handle("POST", target, write_headers())
        assert reply.status == 405 and dict(reply.headers)["Allow"] == "GET"


def test_refresh_now_calls_the_hook_and_returns_202(app, refreshes):
    reply = app.handle("POST", "/api/refresh", write_headers())
    assert reply.status == 202 and json.loads(reply.body) == {"queued": True}
    assert refreshes.calls == [1]


def test_skip_reports_whether_an_open_action_changed(app, db):
    add_action(db, "a1")
    add_action(db, "a2", "done")
    assert json.loads(app.handle("POST", "/api/skip", write_headers(), b'{"action_id": "a1"}').body) == {"skipped": True}
    assert json.loads(app.handle("POST", "/api/skip", write_headers(), b'{"action_id": "a2"}').body) == {"skipped": False}
    assert db.conn.execute("SELECT done_at FROM actions WHERE action_id = 'a1'").fetchone()[0] == FakeClock().now_ms()


def test_skip_with_a_bad_body_is_400(app, db):
    add_action(db, "a1")
    for body in (b"", b"not json", b"[1]", b'{"action_id": 7}', b'{"action_id": ""}', b"\xff"):
        assert app.handle("POST", "/api/skip", write_headers(), body).status == 400, body
    assert action_status(db, "a1") == "pending"


def test_a_database_error_degrades_to_503(app, db):
    db.close()
    view = app.handle("GET", "/api/views/craft", GOOD)
    skip = app.handle("POST", "/api/skip", write_headers(), b'{"action_id": "a1"}')
    assert view.status == 503 and skip.status == 503 and json.loads(view.body) == {"error": "database_busy"}


def test_live_server_binds_loopback_only_and_serves_the_page(live):
    reply = send(live, "GET", "/")
    assert reply.status == 200 and live.token.encode() in reply.body
    assert reply.headers["cache-control"] == "no-store" and reply.headers["x-content-type-options"] == "nosniff"


def test_live_server_reads_what_the_main_connection_published(live, tmp_path):
    main_db = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    try:
        assert send(live, "GET", f"/api/views/craft?market={M}").status == 404
        publish(main_db, "craft", M, {"rows": [craft_row_example()], "hidden": 0})
        reply = send(live, "GET", f"/api/views/craft?market={M}")
        assert reply.status == 200 and reply.json()["data"]["rows"][0]["product"] == "Transmute: Iron to Gold"
    finally:
        main_db.close()


def test_new_tokens_are_long_and_different():
    tokens = {new_token() for _ in range(5)}
    assert len(tokens) == 5 and all(len(t) >= 40 for t in tokens)


# ---------------------------------------------------------------- robustness (security review)

def test_FM28_a_stalled_client_blocks_neither_other_requests_nor_stopping(tmp_path, refreshes):
    import socket
    import time
    Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None).close()
    server_db = open_server_db(tmp_path / "ahsync.db")
    app = DashboardApp(server_db, Config(markets=(M,)), FakeClock(), refreshes, token=new_token(), port=0)
    server = start_dashboard(app, 0)
    stalled = socket.create_connection(("127.0.0.1", app.port))
    stalled.sendall(b"GET / HTTP/1.1\r\n")                # half a request, then silence
    started = time.monotonic()
    reply = send(app, "GET", "/api/markets", {"Host": f"127.0.0.1:{app.port}"})
    assert reply.status == 200 and time.monotonic() - started < 2
    started = time.monotonic()
    stop_dashboard(server)
    assert time.monotonic() - started < 2
    stalled.close()
    server_db.close()


def test_FM29_a_corrupt_stored_page_is_a_503_not_a_dropped_connection(app, db):
    with db.transaction():
        db.conn.execute("INSERT INTO views(page, market, body, built) VALUES ('craft', ?, '{not json', 1)", (M,))
    reply = app.handle("GET", "/api/views/craft", GOOD, b"")
    assert reply.status == 503 and json.loads(reply.body) == {"error": "bad_view"}


def test_preflight_and_head_get_no_cors_and_keep_the_safety_headers(live):
    import http.client
    for method in ("OPTIONS", "HEAD"):
        conn = http.client.HTTPConnection("127.0.0.1", live.port, timeout=5)
        conn.request(method, "/api/skip", headers={"Host": f"127.0.0.1:{live.port}",
                                                     "Origin": "https://evil.example",
                                                     "Access-Control-Request-Method": "POST"})
        reply = conn.getresponse()
        reply.read()
        headers = {k.lower(): v for k, v in reply.getheaders()}
        conn.close()
        assert reply.status in (405, 501)
        assert not any(k.startswith("access-control-") for k in headers)
        assert headers.get("x-content-type-options") == "nosniff" and headers.get("cache-control") == "no-store"
        assert headers.get("server") == "AHSync"


def test_an_oversized_body_is_refused(live):
    reply = live_write(live, "/api/skip", {"action_id": "x" * 5000})
    assert reply.status == 413


def test_SEC8_rule_is_stored_from_the_dashboard_and_checked_like_every_write(app, db, monkeypatch):
    from ahsync.web import server as server_module
    replans, exports = [], []
    monkeypatch.setattr(server_module, "replan_all", lambda *a: replans.append(a) or [])
    monkeypatch.setattr(server_module, "export_plan", lambda *a: exports.append(a) or True)
    reply = app.handle("POST", "/api/rule", write_headers(), b'{"key": 2318, "rule": "keep"}')
    assert reply.status == 200 and json.loads(reply.body) == {"set": True}
    assert db.conn.execute("SELECT rule, source FROM item_rules WHERE key = 2318").fetchall() == [("keep", "dashboard")]
    assert len(replans) == 1 and len(exports) == 1                                  # re-planned, plan file rewritten
    assert app.handle("POST", "/api/rule", write_headers(), b'{"key": 2318, "rule": "maybe"}').status == 400
    assert app.handle("POST", "/api/rule", write_headers(), b'{"rule": "keep"}').status == 400
    assert app.handle("POST", "/api/rule", write_headers(), b'not json').status == 400
    foreign = app.handle("POST", "/api/rule", write_headers(origin="https://evil.example"), b'{"key": 1, "rule": "ignore"}')
    assert foreign.status == 403 and not db.conn.execute("SELECT 1 FROM item_rules WHERE key = 1").fetchall()


def test_a_busy_database_answers_503_for_a_rule(app, monkeypatch):
    def busy(*_args):
        """Fail like a locked database."""
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(app.db, "upsert_item_rule", busy)
    assert app.handle("POST", "/api/rule", write_headers(), b'{"key": 2, "rule": "keep"}').status == 503
