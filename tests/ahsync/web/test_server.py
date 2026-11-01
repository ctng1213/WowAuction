"""Tests for ahsync/web/server.py: the dashboard's endpoints and Security rules (SPEC §3.5, SEC1-SEC7)."""
from __future__ import annotations

import http.client
import json
import sqlite3
import logging
import re
from dataclasses import dataclass, field

import pytest

from ahsync.config import Config, MarketSources
from ahsync.engine.views import (COLUMN_HELP, MARKET_COLUMNS, CraftInput, DisenchantInput, craft_money, craft_row,
                                 disenchant_row, market_activity)
from ahsync.db.sqlite import Database
from ahsync.web.server import (INDEX_HTML, MAX_BODY_BYTES, PAGES, TOKEN_HEADER, TOKEN_PLACEHOLDER, DashboardApp,
                               new_token, open_server_db, start_dashboard, stop_dashboard)
from tests.support.fake_clock import FakeClock

M, OTHER = "forever.pvp.horde.us", "forever.pvp.alliance.us"
AHF_PRIMARY = {M: MarketSources(primary="ahf"), OTHER: MarketSources(primary="ahf")}   # these tests predate M19
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
    config = Config(markets=(M, OTHER), api_key=KEY, sources=AHF_PRIMARY)
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
    config = Config(markets=(M, OTHER), api_key=KEY, sources=AHF_PRIMARY)
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
    names = ["Portfolio", "Today", "Week", "On the AH", "Disenchant", "Craft", "Flip", "Gathering", "Inventory",
             "History", "Rules", "Characters", "Status"]
    order = html[html.index("const ORDER = "):]                     # column lists name 'Status' too
    positions = [order.index(f"['{name}', ") for name in names]
    assert positions == sorted(positions)
    assert "<script src" not in html and "<link" not in html and "@import" not in html
    assert "Coming in a later milestone" in html


def test_every_live_table_has_the_market_columns_in_order_and_every_header_a_tooltip(app):
    html = app.handle("GET", "/", GOOD).body.decode()
    market = re.search(r"const MARKET_COLS = (\[.*?\]);\n", html).group(1)
    assert re.findall(r"\['([^']+)', 1, '(\w+)'\]", market) == [(h, c) for h, c in MARKET_COLUMNS]
    live = html[html.index("const LIVE = {"):html.index("// ---------- state")]
    tables = re.findall(r"cols: (\[.*?\]),\n\s*row:", live, re.S)
    market_tables = [cols for cols in tables if "'When'" not in cols]                  # History lists events, not items
    assert len(market_tables) == 6 and all("...MARKET_COLS" in cols for cols in market_tables)   # today, flip, on the AH, inventory, DE, craft
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
    assert json.loads(reply.body) == {"built": BUILT, "prices_newer": False, "credit": None,
                                         "data": json.loads(json.dumps(rows))}
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
    assert set(PAGES) == {"portfolio", "disenchant", "craft", "today", "week", "on_ah", "flip", "gathering", "inventory",
                          "history", "rules", "characters", "status"}
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


def test_no_single_quoted_dashboard_string_holds_an_apostrophe():
    # A contraction inside a '...' JS string ends it early: the whole script fails and the page is blank.
    page = INDEX_HTML.read_text(encoding="utf-8")
    code = [line for line in page.splitlines() if not line.lstrip().startswith("//")]
    broken = [line.strip() for line in code if re.search(r"[(:,=]\s*'[^'\"`\n]*[A-Za-z]'[A-Za-z]", line)]
    assert broken == []


def test_every_table_row_is_its_own_tr():
    # rowHtml wraps a row in <tr> only for pages with selectKey; every other page's row must bring its own,
    # or the browser runs all the rows together into one line.
    live = INDEX_HTML.read_text(encoding="utf-8").split("const LIVE = {", 1)[1]
    specs = re.split(r"\n  (\w+): \{\n", live)[1:]
    for name, body in zip(specs[::2], specs[1::2]):
        row = re.search(r"row: r => `(.*?)`", body, re.S)
        if row and "selectKey" not in body:
            assert row.group(1).startswith("<tr") and "</tr>`" in body, name


def test_D65_week_cards_name_the_character_and_chain_rows_draw_their_lines():
    page = INDEX_HTML.read_text(encoding="utf-8")
    chip = page[page.index("const chip = r =>"):page.index("// Your own sell record under a row")]
    assert "${esc(r.character)}" in chip and "chainLines(r," in chip and re.search(r"\.chip \.who[ {]", page)
    today = page[page.index("  today: {"):page.index("  week: {")]
    assert "chainLines(r, '<span class=\"sub\">', '</span>')" in today          # D62: the lines under a chain's row


def test_IU2_an_ahf_price_newer_than_the_intelligence_flags_every_page(app, db):
    from ahsync.intel.contract import MarketIntelligence
    publish(db, "today", M, {"rows": []})
    assert json.loads(app.handle("GET", f"/api/views/today?market={M}", GOOD).body)["prices_newer"] is False
    with db.transaction():
        db.stage_intel(MarketIntelligence(M, BUILT, BUILT, 1, {}))
        db.stage_view("today", M, {"rows": []}, BUILT)
    db.publish_refresh([M])
    db.upsert_current(M, "ahf", [(2589, 45, 20, BUILT - 3_600_000)])                   # older than the numbers
    assert json.loads(app.handle("GET", f"/api/views/today?market={M}", GOOD).body)["prices_newer"] is False
    db.upsert_current(M, "ahf", [(2589, 44, 20, BUILT + 3_600_000)])                   # an hourly file after the refresh
    reply = json.loads(app.handle("GET", f"/api/views/today?market={M}", GOOD).body)
    assert reply["prices_newer"] is True and reply["data"] == {"rows": []}        # numbers unchanged


def test_every_css_class_the_pages_build_bars_and_profession_rows_with_is_styled():
    page = INDEX_HTML.read_text(encoding="utf-8")
    for cls in ("bar", "prof", "tiles", "tile", "grid2", "card", "kv"):
        if f'class="{cls}"' in page:
            assert re.search(rf"\.{cls}[ {{,:]", page), f".{cls} is used but has no style"


def test_SEC9_a_reserve_is_stored_from_the_dashboard_and_checked_like_every_write(app, db, monkeypatch):
    from ahsync.web import server as server_module
    replans, exports = [], []
    monkeypatch.setattr(server_module, "replan_all", lambda *a: replans.append(a) or [])
    monkeypatch.setattr(server_module, "export_plan", lambda *a: exports.append(a) or True)
    with db.transaction():
        db.conn.execute("INSERT INTO characters(id, market, gold) VALUES ('Amaya-Grommash', ?, 1)", (M,))
    reply = app.handle("POST", "/api/reserve", write_headers(), b'{"char": "Amaya-Grommash", "key": 2589, "qty": 5}')
    assert reply.status == 200 and json.loads(reply.body) == {"set": True}
    assert db.conn.execute("SELECT qty, source FROM reserves WHERE key = 2589").fetchall() == [(5, "dashboard")]
    assert len(replans) == 1 and len(exports) == 1
    assert app.handle("POST", "/api/reserve", write_headers(), b'{"char": "Amaya-Grommash", "key": 2589, "qty": -2}').status == 400
    assert app.handle("POST", "/api/reserve", write_headers(), b'{"char": "Nobody-X", "key": 2589, "qty": 1}').status == 400
    assert app.handle("POST", "/api/reserve", write_headers(), b'{"char": "Amaya-Grommash", "key": "x", "qty": 1}').status == 400
    foreign = app.handle("POST", "/api/reserve", write_headers(origin="https://evil.example"),
                         b'{"char": "Amaya-Grommash", "key": 4306, "qty": 1}')
    assert foreign.status == 403 and not db.conn.execute("SELECT 1 FROM reserves WHERE key = 4306").fetchall()


def test_a_busy_database_answers_503_for_a_reserve(app, db, monkeypatch):
    with db.transaction():
        db.conn.execute("INSERT INTO characters(id, market, gold) VALUES ('Amaya-Grommash', ?, 1)", (M,))

    def busy(*_args):
        """Fail like a locked database."""
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(app.db, "set_reserve", busy)
    assert app.handle("POST", "/api/reserve", write_headers(), b'{"char": "Amaya-Grommash", "key": 2, "qty": 1}').status == 503


def test_SEC10_the_item_search_only_reads_and_is_no_write_path(app, db):
    with db.transaction():
        db.conn.execute("INSERT INTO items(key, item_id, name, quality) VALUES (2589, 2589, 'Linen Cloth', 1)")
    before = db.conn.execute("SELECT COUNT(*) FROM item_rules").fetchone()
    reply = app.handle("GET", "/api/items?q=linen", GOOD)
    assert reply.status == 200 and json.loads(reply.body) == {"items": [{"key": "2589", "name": "Linen Cloth", "quality": 1}]}
    assert json.loads(app.handle("GET", "/api/items", GOOD).body) == {"items": []}
    assert app.handle("POST", "/api/items", write_headers(), b'{"q": "linen"}').status in (404, 405)
    assert db.conn.execute("SELECT COUNT(*) FROM item_rules").fetchone() == before


def test_a_busy_database_answers_503_for_an_item_search(app, monkeypatch):
    def busy(*_args, **_kwargs):
        """Fail like a locked database."""
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(app.db, "search_items", busy)
    assert app.handle("GET", "/api/items?q=linen", GOOD).status == 503


def test_D55_a_click_on_a_rows_control_does_not_select_the_row():
    page = INDEX_HTML.read_text(encoding="utf-8")
    handler = page[page.index("tr[data-select]').forEach"):][:400]
    assert "e.target.closest('select, button, input, a')" in handler and handler.index("closest") < handler.index("render(id)")


RISK_TOML = ('markets = ["forever.pvp.horde.us", "forever.pvp.alliance.us"]\n# my own note\n'
             'risk_tolerance = 0.30                         # share of the capital base (§3.4D.1)\n\n[realms]\n"R" = "pvp"\n')


def test_SEC11_the_risk_tolerance_rewrites_its_line_re_plans_and_is_checked_like_every_write(db, refreshes, tmp_path,
                                                                                             monkeypatch):
    from ahsync.web import server as server_module
    path = tmp_path / "config.toml"
    path.write_text(RISK_TOML, encoding="utf-8")
    replans, exports, told = [], [], []
    monkeypatch.setattr(server_module, "replan_all", lambda *a: replans.append(a) or [M])
    monkeypatch.setattr(server_module, "export_plan", lambda *a: exports.append(a) or True)
    app = DashboardApp(db, Config(markets=(M, OTHER)), FakeClock(), refreshes, token="tok-123", port=8765,
                       config_path=path, config_changed=told.append)
    reply = app.handle("POST", "/api/risk_tolerance", write_headers(), b'{"risk_tolerance": 0.4}')
    assert reply.status == 200 and json.loads(reply.body) == {"set": True}
    assert path.read_text(encoding="utf-8") == RISK_TOML.replace("= 0.30 ", "= 0.40 ")      # only that line
    assert not (tmp_path / "config.toml.tmp").exists()
    assert app.config.risk_tolerance == 0.4 and told == [app.config]                  # the running app follows
    assert len(replans) == 1 and replans[0][2].risk_tolerance == 0.4 and len(exports) == 1   # re-planned, no history
    for bad in (b'{"risk_tolerance": 0.37}', b'{"risk_tolerance": "high"}', b'{"risk_tolerance": true}', b"[]", b"nope"):
        refused = app.handle("POST", "/api/risk_tolerance", write_headers(), bad)
        assert refused.status == 400 and "one of 0.1, 0.2, 0.3, 0.4 or 0.5" in json.loads(refused.body)["message"], bad
    foreign = app.handle("POST", "/api/risk_tolerance", write_headers(origin="https://evil.example"),
                         b'{"risk_tolerance": 0.1}')
    assert foreign.status == 403 and path.read_text(encoding="utf-8") == RISK_TOML.replace("= 0.30 ", "= 0.40 ")
    assert app.handle("GET", "/api/risk_tolerance", GOOD).status == 405                 # GET never changes state
    assert len(replans) == 1 and app.config.risk_tolerance == 0.4


def test_SEC11_a_config_that_cannot_be_written_keeps_the_setting_and_says_so_on_status(app, db, monkeypatch):
    from ahsync.web import server as server_module
    replans = []
    monkeypatch.setattr(server_module, "replan_all", lambda *a: replans.append(a) or [])
    reply = app.handle("POST", "/api/risk_tolerance", write_headers(), b'{"risk_tolerance": 0.2}')   # no config.toml
    assert reply.status == 500 and json.loads(reply.body) == {"error": "config_not_written"}
    assert app.config.risk_tolerance == 0.3 and replans == []
    [line] = [s for s in db.statuses() if s.topic == "risk_tolerance"]
    assert (line.level, line.kind, line.data) == ("error", "risk_tolerance_not_saved", {"error": "OSError"})

    def busy(*_args, **_kwargs):
        """Fail like a locked database."""
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(app.db, "set_status", busy)
    assert app.handle("POST", "/api/risk_tolerance", write_headers(), b'{"risk_tolerance": 0.2}').status == 500


def test_portfolio_is_served_like_every_page(app, db):
    publish(db, "portfolio", M, {"tiles": [], "empty": None})
    reply = app.handle("GET", f"/api/views/portfolio?market={M}", GOOD)
    assert reply.status == 200 and json.loads(reply.body)["data"] == {"tiles": [], "empty": None}


def test_D72_D79_the_portfolio_page_is_first_draws_its_sections_in_order_and_posts_the_risk_dropdown():
    page = INDEX_HTML.read_text(encoding="utf-8")
    assert page.index("['Portfolio', 'portfolio']") < page.index("['Today', 'today']")
    assert "saved : 'portfolio'" in page and "state = { page: 'portfolio'" in page
    body = page[page.index("const pfPage = "):page.index("// ---------- live pages")]
    order = [body.index(s) for s in ("pfTiles(d)", "d.fresh", "'A. Active positions'", "pfLists(d, values, help)")]
    assert order == sorted(order)
    lists = page[page.index("const pfLists = "):page.index("const pfPage = ")]
    assert lists.index("if (d.empty)") < lists.index("'B. Top opportunities today'") < \
        lists.index("'C. Top opportunities, next 7 days'") < lists.index("'How the pool was funded'")
    assert "pfCut(PF_COLS_B.length, b.cut)" in lists and "pfCut(PF_COLS_C.length, w.cut)" in lists
    risk = page[page.index("async function setRisk"):page.index("async function setRisk") + 600]
    assert "post('/api/risk_tolerance', { risk_tolerance: Number(sel.value) })" in risk and "load(id)" in risk
    assert "select[data-risk]').forEach(sel => { sel.onchange = () => setRisk(id, sel); }" in page


def test_every_portfolio_column_has_its_tooltip_and_the_market_columns_in_order():
    page = INDEX_HTML.read_text(encoding="utf-8")
    block = page[page.index("const PF_COLS_A"):page.index("const STAGE_CLASSES")]
    headers = set(re.findall(r"\['([^']+)', [01]\]", block))
    assert headers and headers - set(COLUMN_HELP) == set()
    assert block.count("...MARKET_COLS") == 2                                   # sections B and C show a chance


def test_D76_the_page_bar_keeps_today_and_week_between_portfolio_and_on_the_ah():
    # The addon half (a Portfolio tab, no Today or Week) is MainWindow_spec's "the tab bar reads Portfolio, …".
    # Since M16 Gathering follows Flip (D80).
    page = INDEX_HTML.read_text(encoding="utf-8")
    order = page[page.index("const ORDER = "):page.index("const nav = ")]
    assert re.findall(r"\['([^']+)', '(\w+)'\]", order) == [
        ("Portfolio", "portfolio"), ("Today", "today"), ("Week", "week"), ("On the AH", "on_ah"),
        ("Disenchant", "disenchant"), ("Craft", "craft"), ("Flip", "flip"), ("Gathering", "gathering"),
        ("Inventory", "inventory"), ("History", "history"), ("Rules", "rules"), ("Characters", "characters"),
        ("Status", "status")]
    assert [p for _, p in re.findall(r"\['([^']+)', '(\w+)'\]", order)] == list(PAGES)


def test_D80_the_gathering_page_follows_flip_is_served_and_carries_the_gold_pill(app, db):
    from ahsync.engine.gathering_view import gathering_view
    from ahsync.engine.views import GOLD_PAGES
    from tests.support import gathering_world as gw
    assert PAGES.index("gathering") == PAGES.index("flip") + 1 and "gathering" in GOLD_PAGES
    body = {**gathering_view(gw.world()), "gold": "Gold 31g 40s · 2 characters · Mon 10:38"}
    publish(db, "gathering", M, body)
    reply = app.handle("GET", f"/api/views/gathering?market={M}", GOOD)
    data = json.loads(reply.body)["data"]
    assert reply.status == 200 and data["gold"] == "Gold 31g 40s · 2 characters · Mon 10:38"
    assert data == json.loads(json.dumps(body))                                   # served as stored
    page = INDEX_HTML.read_text(encoding="utf-8")
    gathering = page[page.index("  gathering: {"):page.index("  on_ah: {")]
    assert "render: (d, values, id) => gaPage(d, values, id)" in gathering and "bind: gaBind" in gathering


def gathering_js() -> str:
    """The Gathering page's drawing code in index.html."""
    page = INDEX_HTML.read_text(encoding="utf-8")
    return page[page.index("// ---------- Gathering (SPEC"):page.index("// ---------- live pages")]


def test_D81_the_gathering_page_under_all_characters():
    from ahsync.engine.gathering_view import gathering_view
    from tests.support import gathering_world as gw
    p = gathering_view(gw.world())
    assert p["header"] == "16 in the catalog · 7 with a rate · desktop only: the addon only records loot"
    js = gathering_js()
    cols = re.findall(r"\['([^']+)', [01], ", js[js.index("const GA_COLS"):js.index("const GA_CLASSES")])
    assert cols == ["Item", "Category", "Skill", "AH Price", "AH Qty", "Median 30d", "Deviation", "Items/hr",
                    "Character", "Net gold/hr", "Sell window", "Sell window price"]
    assert set(cols) <= set(p["help"])
    copper = next(r for r in gw.view_rows(p, "") if r["item"] == "Copper Ore")
    # M18: the historical cells (the mockup's "2s · +10% · … · Sat 17:00 · 2s 24c (+2% vs now)")
    assert [copper[k] for k in ("ah_price", "qty", "median", "deviation", "rate", "character", "net", "window",
                                "window_price", "window_delta")] == ["2s 20c", "64", "2s", "+10%", "38", "Brugan",
                                                                     "79s 38c", "Sat 17:00", "2s 24c", "+2% vs now"]
    sorted_by = dict(re.findall(r"\['([^']+)', [01], '([a-z_]+)'\]", js[js.index("const GA_COLS"):js.index("const GA_CLASSES")]))
    assert {sorted_by[c] for c in ("Median 30d", "Deviation", "Sell window", "Sell window price")} <= set(copper["sort"])
    assert copper["note"] == "Brugan: 3 sessions, 1 h 50 min" and p["selected"] == str(gw.COPPER)
    assert p["by_char"][""]["toggle"] == {"label": "Only what a character can gather", "disabled": True,
                                          "title": "Pick a character first"}
    assert p["by_char"][""]["foot"] == [
        ['16 materials · sorted by AH Price, highest first; rows with "—" sort last', ""],
        ["4 no character can gather (amber Skill)", "amber"],
        ["Character = whose rate: the highest among the characters who can gather it", "muted"]]
    assert p["help"]["Character"].startswith("Whose observed or manual rate this is")
    assert p["help"]["Sell window"].startswith("The top selling window's start")
    assert COLUMN_HELP["Character"] == "Which of your characters does it"           # the other pages keep theirs
    assert COLUMN_HELP["Sell window"] == "When it is planned to sell: the day and the peak hour"
    assert "state.selected[id] || d.selected" in js and "class=\"selected\"" in js
    assert "view.toggle.disabled ? ' disabled' : ''" in js and "esc(view.toggle.title)" in js
    # Each row is sent once: a view picks the shared rows by its keys and lays its differing cells over them.
    assert "(view.keys || []).map(k => ({ ...shared[String(k)], ...((view.cells || {})[String(k)] || {}) }))" in js
    assert ".blocks.filter(b => !char || b.char === char)" in js and "lines: row.card_lines || []" in js


def test_D83_the_detail_draws_the_heatmap_marks_legend_reading_line_windows_and_seasonality():
    from ahsync.engine.gathering_view import gathering_view
    from tests.support import gathering_world as gw
    d = gathering_view(gw.world())["details"][str(gw.COPPER)]
    js = gathering_js()
    page = INDEX_HTML.read_text(encoding="utf-8")
    # the marks are the engine's: the page maps each to its outline class (the mockup's three outlines)
    assert "const GA_MARKS = { now: 'ga-now', top: 'ga-top', next: 'ga-next' };" in js and "GA_MARKS[mark]" in js
    assert {c[2] for r in d["heat"]["rows"] for c in r["cells"]} == {"", "now", "top", "next"}
    assert 'title="${esc(r.day)} ${esc(hours[h] || \'\')} ${esc(heat.texts[i])}"' in js and "d.heat_hours || []" in js


    assert ".heat .ga-now { outline: 2px solid var(--accent)" in page and "outline: 1px dashed #fffb" in page
    assert "outline: 1px dotted #fff5" in page
    assert "gaSwatch('ga-now', l.now), gaSwatch('ga-top', l.top), gaSwatch('ga-next', l.next)" in js
    # the reading line, the windows table and the timing lines are drawn as served, in E.7's order
    assert "(x.reading || []).map(([t, c])" in js and "['Rank', 1, null], ['Window', 0, null], ['Median price', 1, null]" in js
    history = js[js.index("function gaHistory"):js.index("function gaDetail")]
    assert history.index("gaHeat(x.heat, hours)") < history.index("x.windows.map") < history.index("gaLines(x.timing)")
    detail = js[js.index("function gaDetail"):js.index("function gaViewRows")]
    assert detail.index("gaLines(x.price)") < detail.index("gaRateCard(card, key)") < detail.index("marketCells(x.market)")
    assert detail.index("marketCells(x.market)") < detail.index("gaHistory(x, d.help || {}, d.heat_hours || [])")
    assert "if (x.history) return" in history                    # day one: the one line in place of the history


def test_D86_the_characters_page_tags_gathering_professions():
    page = INDEX_HTML.read_text(encoding="utf-8")
    characters = page[page.index("  characters: {"):page.index("  rules: {")]
    assert "${p.gathering ? ' <span class=\"muted\" style=\"font-size:12px\">· gathering</span>' : ''}" in characters
    assert "crafting and gathering alike" in characters


def gathering_characters(db: Database) -> None:

    """Amaya (Herbalism 45) and Brugan (Mining 68), as a professions read leaves them."""
    with db.transaction():
        db.conn.executemany("INSERT INTO characters(id, name, market, gold) VALUES (?, ?, ?, 0)",
                            [("Amaya-Grommash", "Amaya", M), ("Brugan-Grommash", "Brugan", M)])
        db.conn.executemany("INSERT INTO professions(char_id, profession, skill, max_skill) VALUES (?, ?, ?, 75)",
                            [("Amaya-Grommash", "Herbalism", 45), ("Brugan-Grommash", "Mining", 68)])


def test_SEC12_a_manual_rate_is_checked_stored_and_rebuilds_only_the_gathering_page(db, refreshes, monkeypatch,
                                                                                     caplog):
    from ahsync.web import server as server_module
    rebuilt, replans = [], []
    monkeypatch.setattr(server_module, "rebuild_gathering", lambda *a: rebuilt.append(a) or [M])
    monkeypatch.setattr(server_module, "replan_all", lambda *a: replans.append(a) or [M])
    gathering_characters(db)
    clock = FakeClock()
    app = DashboardApp(db, Config(markets=(M,)), clock, refreshes, token="tok-123", port=8765)

    def post(payload, headers=None) -> Reply:
        """POST /api/manual_rate."""
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        reply = app.handle("POST", "/api/manual_rate", headers or write_headers(), body)
        return Reply(reply.status, dict(reply.headers), reply.body)

    ok = post({"char": "Amaya-Grommash", "key": 2449, "items_per_hour": 12})
    assert (ok.status, ok.json()) == (200, {"set": True})
    assert db.conn.execute("SELECT * FROM manual_rates").fetchall() == [("Amaya-Grommash", 2449, 12, clock.now_ms())]
    assert len(rebuilt) == 1 and replans == [] and refreshes.calls == []          # the page only, no re-plan
    for bad, reason in ((0, "whole number from 1 to 10,000"), (10001, "whole number from 1 to 10,000"),
                        (12.5, "whole number from 1 to 10,000"), ("fast", "whole number from 1 to 10,000"),
                        (True, "whole number from 1 to 10,000")):
        refused = post({"char": "Amaya-Grommash", "key": 2449, "items_per_hour": bad})
        assert refused.status == 400 and reason in refused.json()["message"], bad
    for payload, reason in (({"char": "Cara-Grommash", "key": 2449, "items_per_hour": 5}, "unknown character"),
                            ({"char": "Amaya-Grommash", "key": 99999, "items_per_hour": 5}, "not in the gathering catalog"),
                            ({"char": "Brugan-Grommash", "key": 2449, "items_per_hour": 5},
                             "Brugan can't gather Earthroot (no Herbalism)"),
                            ({"char": "Amaya-Grommash", "key": 2449}, "whole number"),
                            (b"[1]", "JSON object"), (b"nope", "JSON object")):
        refused = post(payload)
        assert (refused.status, refused.json()["error"]) == (400, "bad_request") and reason in refused.json()["message"]
    assert db.conn.execute("SELECT items_per_hour FROM manual_rates").fetchall() == [(12,)] and len(rebuilt) == 1
    cleared = post({"char": "Amaya-Grommash", "key": 2449, "items_per_hour": None})
    assert (cleared.status, cleared.json()) == (200, {"set": True}) and db.count("manual_rates") == 0
    assert post({"char": "Amaya-Grommash", "key": 2449, "clear": True}).json() == {"set": False}   # nothing to clear
    caplog.set_level(logging.WARNING, logger="ahsync.web")
    for headers in (write_headers(origin="https://evil.example"), write_headers(token=""),
                    write_headers(host="evil.example:8765")):
        refused = post({"char": "Amaya-Grommash", "key": 2449, "items_per_hour": 12}, headers)
        assert refused.status == 403
    assert db.count("manual_rates") == 0 and "tok-123" not in caplog.text
    assert app.handle("GET", "/api/manual_rate", GOOD).status == 405                 # GET never changes state

    def busy(*_args, **_kwargs):
        """Fail like a locked database."""
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(app.db, "set_manual_rate", busy)
    assert post({"char": "Amaya-Grommash", "key": 2449, "items_per_hour": 12}).status == 503


def test_D85_the_rate_card_posts_save_and_clear_and_reloads_the_rebuilt_payload():
    from ahsync.engine.gathering_view import gathering_view
    from tests.support import gathering_world as gw
    js = gathering_js()
    assert "post('/api/manual_rate', { char, key: Number(key), items_per_hour: value })" in js and "load(id)" in js
    assert "typed === null ? null" in js                                              # Clear posts null
    assert "button[data-ga-save]').forEach" in js and "button[data-ga-clear]').forEach" in js
    assert "gaRateCard(card, key)" in js and "b.override.placeholder" in js and "b.clear ?" in js
    assert "l.title ? ` title=" in js                                                # how a session ended, on hover
    # What the page redraws from: Brugan's 15 on Copper Ore, then cleared; Earthroot's active override.
    manual = gathering_view(gw.world(manual={**gw.MANUAL, (gw.BRUGAN, gw.COPPER): 15}))
    copper = next(r for r in gw.view_rows(manual, "") if r["item"] == "Copper Ore")
    assert (copper["rate"], copper["rate_sub"], copper["net"], copper["note"]) == ("15", "manual", "31s 33c",
                                                                                   "Brugan: manual rate")
    back = next(r for r in gw.view_rows(gathering_view(gw.world()), "") if r["item"] == "Copper Ore")
    assert (back["rate"], back["rate_sub"]) == ("38", "")
    earth = gw.view_card(gathering_view(gw.world()), "", gw.EARTHROOT)["blocks"][0]
    assert (earth["head"], earth["clear"], earth["override"]) == ("Amaya · 12/hr manual", True, None)


def test_D88_the_gathering_pages_empty_states_come_from_the_payload():
    js = gathering_js()
    assert "d.no_match.split('{q}').join(search)" in js and "d.no_match_filters" in js
    assert "gaOnly(values) && view.alert ?" in js and "esc(view.alert.title)" in js and "esc(view.alert.text)" in js
    assert "const last = d.no_rates ?" in js
    # The filters compare the engine's row fields only; categories OR, controls AND (GA10).
    assert "r.cats.some(c => values['cat_' + c] !== '0')" in js and "(!only || r.eligible)" in js


def test_D74_the_top_bar_draws_the_responses_gold_and_hides_the_pill_without_one(app, db):
    page = INDEX_HTML.read_text(encoding="utf-8")
    assert '<span class="pill gold" id="goldpill"' in page and "setGold(body.data && body.data.gold);" in page
    setter = page[page.index("function setGold"):page.index("function setGold") + 200]
    assert "pill.hidden = !text;" in setter and "pill.textContent = text || '';" in setter
    publish(db, "inventory", M, {"rows": [], "gold": "Gold 31g 40s · 2 characters · Mon 10:38"})
    reply = app.handle("GET", f"/api/views/inventory?market={M}", GOOD)
    assert json.loads(reply.body)["data"]["gold"] == "Gold 31g 40s · 2 characters · Mon 10:38"   # served as stored


def test_D75_D71_the_inventory_filter_picks_an_engine_view_and_the_disenchant_notice_comes_from_the_response():
    page = INDEX_HTML.read_text(encoding="utf-8")
    inventory = page[page.index("  inventory: {"):page.index("  status: {")]
    assert "pick: (d, values) => ({ ...d, ...((d.by_char || {})[values.character || ''] || {}) })" in inventory
    assert "{ id: 'character', label: 'Character', fromData: 'characters', test: () => true }" in inventory
    disenchant = page[page.index("  disenchant: {"):page.index("  craft: {")]
    assert "notice: d => d.notice ?" in disenchant and "esc(d.notice.title)" in disenchant
    assert "filterBar(id, rows) + (spec.notice ? spec.notice(data) : '') + body" in page


def test_the_top_bar_credit_follows_the_primary_source_and_the_page_only_draws_it(db, refreshes):
    from ahsync.intel.contract import MarketIntelligence
    with db.transaction():
        db.stage_intel(MarketIntelligence(M, BUILT, BUILT, 1, {}))
        db.stage_view("today", M, {"rows": []}, BUILT)
    db.publish_refresh([M])
    db.upsert_current(M, "ahledger", [(2589, 44, 20, BUILT + 3_600_000)])               # the table updated after the numbers

    def reply(sources: dict) -> dict:
        """The Today response of an app whose market has these sources."""
        config = Config(markets=(M,), sources=sources)
        app = DashboardApp(db, config, FakeClock(), refreshes, token="tok-123", port=8765)
        return json.loads(app.handle("GET", f"/api/views/today?market={M}", GOOD).body)

    default = reply({})                                                                 # the primary is AHledger
    assert default["prices_newer"] is True
    assert default["credit"] == {"text": "Prices from AHledger", "url": "https://ahledger.com"}
    ahf = reply({M: MarketSources(primary="ahf")})
    assert ahf["credit"] is None and ahf["prices_newer"] is False                       # no AHledger text with "ahf"
    page = INDEX_HTML.read_text(encoding="utf-8")
    draw = page[page.index("function setBuilt"):page.index("// ---------- table")]
    assert "credit.text" in draw and "credit.url" in draw and "Prices from" not in draw  # the text is the engine's
