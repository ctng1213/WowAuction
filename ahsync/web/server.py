"""The dashboard's HTTP server, bound to 127.0.0.1 only (SPEC §3.5).

It serves the static page and the page payloads the full refresh stored in
`views`, exactly as stored: no arithmetic here (SPEC §8.5). Every request
passes the Security rules of SPEC §3.5 (SEC1-SEC7):

- `Host` must be `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding), else 403;
- GET never changes state; a write endpoint answers GET with 405;
- every write needs `Origin` = the dashboard's own origin and `X-AHSync-Token` =
  the per-run token embedded in the served page, else 403 and a log line
  without the token;
- no CORS headers, and no secrets or file paths in any response.

`DashboardApp.handle` is plain request-in, response-out, so tests can call it
directly; `start_dashboard` wraps it in a stdlib HTTP server on a thread.
"""
from __future__ import annotations

import hmac
import json
import logging
import secrets
import socket
import socketserver
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ahsync.app.refresh import rebuild_gathering, replan_all
from ahsync.app.status_page import status_page
from ahsync.clock import Clock
from ahsync import version
from ahsync.app.update_check import ManualCheck, read_state, write_meta
from ahsync.engine.updates import UpdateState
from ahsync.config import (CHANNELS, Config, ConfigError, is_cloud_market, market_sources, write_risk_tolerance,
                           write_setting)
from ahsync.db.sqlite import Database
from ahsync.intel.store import IntelCache
from ahsync.engine.app_view import BAD_LOCATION_TEXT
from ahsync.engine.gathering import catalog, eligible, why_not_eligible
from ahsync.engine.status_view import cloud_freshness
from ahsync.engine.views import source_credit
from ahsync.export.addon_files import export_plan
from ahsync.recommend.gathering_page import CATALOG_PATH, read_catalog
from ahsync.web.responses import Response, body_field, json_response   # noqa: F401  re-exported
from ahsync.web.settings import (AUTOSTART_BAD_LOCATION, AUTOSTART_NOT_SET, AUTOSTART_SET,   # noqa: F401  re-exported
                                  SettingsEndpoints)

log = logging.getLogger("ahsync.web")

BIND_ADDRESS = "127.0.0.1"
TOKEN_HEADER = "X-AHSync-Token"
TOKEN_PLACEHOLDER = "__AHSYNC_TOKEN__"
INDEX_HTML = Path(__file__).parent / "static" / "index.html"
REQUEST_TIMEOUT_S = 10            # seconds a connection may stay silent (FM28)
MAX_BODY_BYTES = 4096
BUSY_TIMEOUT_MS = 5000

# SPEC §3.5 dashboard data. Every page is staged by the full refresh except status, built on request.
PAGES = ("portfolio", "today", "week", "on_ah", "disenchant", "craft", "flip", "gathering", "inventory", "history",
         "rules", "characters", "status")
GET_PATHS = ("/", "/api/markets", "/api/items", "/api/app")
POST_PATHS = ("/api/refresh", "/api/skip", "/api/rule", "/api/reserve", "/api/risk_tolerance", "/api/manual_rate",
              "/api/autostart", "/api/update/auto", "/api/update/channel", "/api/update/restart", "/api/mcp")
MANUAL_RATE_MAX = 10_000                           # SPEC §3.4E.5: a whole number 1-10,000 items per hour
RISK_CHOICES = (0.1, 0.2, 0.3, 0.4, 0.5)          # SPEC §3.5: the Portfolio page's Risk tolerance dropdown
RULES = ("no_buy", "ignore", "keep", "none")       # SPEC §3.4.2: what the dashboard may set
VIEWS_PREFIX = "/api/views/"




@dataclass(frozen=True)
class AppHooks:
    """What the app shell adds to the dashboard and to run() (SPEC §3.7); `python -m ahsync` has none, and its paths
    answer 404."""

    app_info: Callable[[Database | None, Config | None], dict]   # GET /api/app (D97): the dashboard's db and settings
    set_autostart: Callable[[bool], tuple[str, bool]]    # wanted state -> (an AUTOSTART_* outcome, the real state)
    asset_name: Callable[[str], str] = lambda version: ""          # version -> this OS's release asset (§3.7.9)
    verify_update: Callable[[Path], str | None] = lambda path: None   # None: may install; else why not (§3.7.9)
    dashboard_ready: Callable[[object], None] = lambda app: None   # gets the DashboardApp, for the tray's toggles
    can_replace: Callable[[], bool] = lambda: True                 # whether the app may replace itself (FM54, M27)
    forget_previous: Callable[[], None] = lambda: None             # cleanup after an update that worked (M27)
    restart: Callable[[Database, Config], bool] = lambda db, config: False   # Restart to update: True once stopping
    update_checks: ManualCheck = field(default_factory=ManualCheck)  # Check for updates in the tray (DR34)
    mcp_command: Callable[[Path], tuple[str, ...]] | None = None    # data dir -> the bridge Claude launches (§3.8.1)


def new_token() -> str:
    """Make the per-run write token (SPEC §3.5 Security), random at each start.

    Returns:
        43 URL-safe characters (256 random bits), safe to embed in HTML as is.
    """
    return secrets.token_urlsafe(32)


def host_allowed(host: str, port: int) -> bool:
    """Whether a request's Host names this dashboard (SPEC §3.5 Security, DNS rebinding; SEC4).

    Args:
        host: The Host header.
        port: The port the server is bound to.

    Returns:
        True for "127.0.0.1:<port>" and "localhost:<port>".
    """
    return host in (f"127.0.0.1:{port}", f"localhost:{port}")


def write_refusal(path: str, host: str, headers: Mapping[str, str], token: str) -> Response | None:
    """Check a write's Origin and token (SPEC §3.5 Security; SEC1-SEC3, SEC7, SEC13).

    Args:
        path: The write endpoint.
        host: The request's Host, already allowed.
        headers: Request headers with lower-case names.
        token: This run's write token.

    Returns:
        None when both checks pass; otherwise a 403 response, after logging the reason (never the token's
        value). A bad token says to reload the page, since a page from before a restart holds the old token (SEC7).
    """
    origin = headers.get("origin", "")
    if origin != f"http://{host}":
        log.warning("rejected POST %s: Origin %r is not this dashboard", path, origin[:100])
        return json_response(403, {"error": "bad_origin"})
    sent = headers.get(TOKEN_HEADER.lower(), "")
    if not sent or not hmac.compare_digest(sent.encode("utf-8"), token.encode("utf-8")):
        log.warning("rejected POST %s: %s token", path, "wrong" if sent else "missing")
        return json_response(403, {"error": "bad_token", "reload": True,
                                   "message": "AHSync was restarted: reload this page."})
    return None


def open_server_db(path: Path, intel_cache: IntelCache | None = None) -> Database:
    """Open a second connection to data/ahsync.db for the server's thread.

    The main thread's connection can't be shared across threads; WAL mode (set by
    Database.open) lets this one read while the main thread writes. It runs no
    migration or staging cleanup: the main connection already did that.

    Args:
        path: data/ahsync.db, already opened (and migrated) by Database.open.
        intel_cache: The main connection's IntelCache, so both share one parsed summary (BZ42); None: its own.

    Returns:
        A Database usable from the server thread; writes wait up to 5 s for a lock.
    """
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return Database(conn, path, intel_cache)


@dataclass
class DashboardApp(SettingsEndpoints):
    """The dashboard's request handling, with every outside dependency injected."""

    db: Database
    config: Config
    clock: Clock
    request_refresh: Callable[[], None]   # queues a full refresh (merged with one already queued; IU4)
    token: str                            # this run's write token (new_token())
    port: int                             # the port clients must name in Host; set by start_dashboard
    page_html: str = ""                   # index.html with the token placeholder; read from disk when empty
    config_path: Path | None = None       # config.toml, rewritten by the Risk tolerance dropdown (SEC11)
    config_changed: Callable[[Config], None] = lambda _config: None   # tells the scheduler the new settings
    catalog_path: Path = CATALOG_PATH     # the gathering catalog, for POST /api/manual_rate's checks (SEC12)
    app_hooks: AppHooks | None = None     # the app shell's paths (/api/app, /api/autostart); None: they answer 404
    mcp: Any = None                       # the AI assistant server (mcp.server.McpServer); None: no card, /api/mcp 404
    mcp_command: tuple[str, ...] = ()     # the bridge Claude launches, for the card's setup snippets (SPEC §3.8.1)
    mcp_error: str | None = None          # why the last Enable failed (MCP4); None after a start or a Disable
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        """Load the static page once, so serving it touches no files."""
        if not self.page_html:
            self.page_html = INDEX_HTML.read_text(encoding="utf-8")

    # ------------------------------------------------------------ entry point

    def handle(self, method: str, target: str, headers: Mapping[str, str], body: bytes = b"") -> Response:
        """Answer one request (SPEC §3.5 endpoints and Security).

        Args:
            method: HTTP method, e.g. "GET".
            target: Request target: path and optional query, e.g. "/api/views/craft?market=x".
            headers: Request headers; names are matched case-insensitively.
            body: Request body bytes (POST only).

        Returns:
            The response: 403 for a bad Host (SEC4) or a failed write check (SEC2, SEC3,
            SEC7), 404 for unknown paths, 405 for the wrong method (SEC5), else the endpoint's.
        """
        lowered = {k.lower(): v for k, v in headers.items()}
        host = lowered.get("host", "")
        if not host_allowed(host, self.port):
            log.warning("rejected %s request: Host %r is not this dashboard", method, host[:100])
            return json_response(403, {"error": "bad_host"})
        parts = urlsplit(target)
        path, query = parts.path, parse_qs(parts.query)
        allowed = self.allowed_method(path)
        if allowed is None:
            return json_response(404, {"error": "not_found"})
        if method != allowed:
            return json_response(405, {"error": "method_not_allowed"}, (("Allow", allowed),))
        if method == "POST":
            refused = self.check_write(path, host, lowered)
            return refused or self.do_post(path, body)
        return self.do_get(path, query)

    # ------------------------------------------------------------ security

    @staticmethod
    def allowed_method(path: str) -> str | None:
        """The one method a path accepts.

        Args:
            path: Request path without the query.

        Returns:
            "GET" or "POST", or None for an unknown path.
        """
        if path in GET_PATHS or (path.startswith(VIEWS_PREFIX) and path[len(VIEWS_PREFIX):] in PAGES):
            return "GET"
        return "POST" if path in POST_PATHS else None

    def check_write(self, path: str, host: str, headers: Mapping[str, str]) -> Response | None:
        """Check a write's Origin and token (SPEC §3.5 Security; SEC1-SEC3, SEC7).

        Args:
            path: The write endpoint.
            host: The request's Host, already allowed.
            headers: Request headers with lower-case names.

        Returns:
            None when both checks pass; otherwise write_refusal's 403.
        """
        return write_refusal(path, host, headers, self.token)

    # ------------------------------------------------------------ GET

    def do_get(self, path: str, query: dict[str, list[str]]) -> Response:
        """Serve a GET endpoint.

        Args:
            path: "/", "/api/markets", "/api/items", "/api/app" or "/api/views/<page>".
            query: Parsed query string.

        Returns:
            The page, the markets, an item search (D54), the app's state (D97), or a stored view payload.
        """
        if path == "/":
            html = self.page_html.replace(TOKEN_PLACEHOLDER, self.token).encode("utf-8")
            return Response(200, html, "text/html; charset=utf-8")
        if path == "/api/markets":
            markets = list(self.config.markets)
            return json_response(200, {"markets": markets, "default": markets[0]})
        if path == "/api/items":
            return self.search_items(query.get("q", [""])[0])
        if path == "/api/app":
            if self.app_hooks is None:             # `python -m ahsync`: no app shell around it
                return json_response(404, {"error": "not_found"})
            with self._lock:
                return json_response(200, self.app_hooks.app_info(self.db, self.config))
        return self.get_view(path[len(VIEWS_PREFIX):], query.get("market", [""])[0])

    def search_items(self, text: str) -> Response:
        """Search items by name for the Rules page's Blacklist (SPEC §3.5 Rules, D54, SEC10). Reads only.

        Args:
            text: What was typed.

        Returns:
            200 {"items": [{"key", "name", "quality"}]}; 503 when the database is busy.
        """
        try:
            with self._lock:
                found = self.db.search_items(text)
        except sqlite3.Error as error:
            log.warning("item search failed: %s", error)
            return json_response(503, {"error": "database_busy"})
        return json_response(200, {"items": [{"key": str(k), "name": n, "quality": q} for k, n, q in found]})

    def get_view(self, page: str, market: str) -> Response:
        """Serve one page's stored payload (SPEC §3.5 dashboard data).

        Args:
            page: A name from PAGES.
            market: Market id; "" means the first configured market.

        Returns:
            200 {"built": ms, "prices_newer": bool (IU2), "credit": {"text", "url"} or None (S19), "freshness": the cloud
            market's pill {"text", "stale"} or None (D100; the key only for a cloud market), "data": payload};
            404 {"error": "no_refresh_yet"} before the
            first refresh; 404 {"error": "unknown_market"} for a market not in the config;
            503 {"error": "database_busy"} when the database can't be read right now;
            503 {"error": "bad_view"} when the stored payload can't be decoded.
        """
        market = market or self.config.markets[0]
        if market not in self.config.markets:
            return json_response(404, {"error": "unknown_market"})
        if page == "status":
            return self.get_status()
        try:
            with self._lock:
                stored = self.db.view(page, market)
                primary = market_sources(self.config, market).primary
                newer = self.db.prices_newer_than_intel(market, primary)
                freshness = self._cloud_freshness(market) if is_cloud_market(self.config, market) else False
        except sqlite3.Error as error:
            log.warning("reading the %s view failed: %s", page, error)
            return json_response(503, {"error": "database_busy"})
        except ValueError as error:                 # a corrupt stored payload (FM29)
            log.warning("the stored %s view can't be decoded: %s", page, error)
            return json_response(503, {"error": "bad_view"})
        if stored is None:
            return json_response(404, {"error": "no_refresh_yet"})
        payload, built_ms = stored
        body = {"built": built_ms, "prices_newer": newer, "credit": source_credit(primary), "data": payload}
        if freshness is not False:
            body["freshness"] = freshness
        return json_response(200, body)

    def _cloud_freshness(self, market: str) -> dict | None:
        """A cloud market's freshness pill from its published summary (D100).

        Args:
            market: A cloud market.

        Returns:
            engine.status_view.cloud_freshness's pill; None before the first summary.
        """
        intel = self.db.intel_facts(market)       # the facts only: no parse during a fetch (BZ43)
        now = self.clock.now_ms()
        if intel is None:
            return None
        return cloud_freshness(intel.observations_until_ms or intel.built_ms, intel.built_ms, now,
                               self.clock.local_offset_ms(now))

    def get_status(self) -> Response:
        """Build and serve the Status page now (SPEC §3.5 Status): it changes between refreshes.

        Returns:
            200 {"built": now ms, "data": payload}; 503 {"error": "database_busy"} when the
            database can't be read right now.
        """
        now = self.clock.now_ms()
        try:
            with self._lock:
                app = self.app_hooks.app_info(self.db, self.config) if self.app_hooks is not None else None
                payload = status_page(self.db, self.config, now, self.clock.local_offset_ms(now), app)
                if self.mcp is not None:
                    payload["mcp_card"] = self.mcp_card(now)
        except sqlite3.Error as error:
            log.warning("building the status page failed: %s", error)
            return json_response(503, {"error": "database_busy"})
        return json_response(200, {"built": now, "data": payload})

    # ------------------------------------------------------------ POST

    def do_post(self, path: str, body: bytes) -> Response:
        """Serve a write endpoint whose checks passed.

        Args:
            path: "/api/refresh", "/api/skip" or "/api/rule".
            body: The request body.

        Returns:
            202 for a queued refresh, or skip's or rule's response.
        """
        if path == "/api/refresh":
            self.request_refresh()
            log.info("full refresh requested from the dashboard")
            return json_response(202, {"queued": True})
        if path == "/api/rule":
            return self.rule(body)
        if path == "/api/reserve":
            return self.reserve(body)
        if path == "/api/risk_tolerance":
            return self.risk_tolerance(body)
        if path == "/api/manual_rate":
            return self.manual_rate(body)
        if path == "/api/autostart":
            return self.autostart(body)
        if path == "/api/update/auto":
            return self.update_auto(body)
        if path == "/api/update/channel":
            return self.update_channel(body)
        if path == "/api/update/restart":
            return self.update_restart()
        if path == "/api/mcp":
            return self.set_mcp(body)
        return self.skip(body)

    def _manual_rate_request(self, body: bytes) -> tuple[str, int, int | None] | str:
        """Read and check a manual-rate write (SPEC §3.5 POST /api/manual_rate, SEC12).

        Args:
            body: JSON {"char", "key", "items_per_hour"}; "items_per_hour": null or "clear": true clears.

        Returns:
            (character id, item key, items per hour or None to clear), or why it is refused.
        """
        try:
            request = json.loads(body.decode("utf-8"))
        except ValueError:
            request = None
        if not isinstance(request, dict):
            return "the body must be a JSON object"
        char_id, key, value = request.get("char"), request.get("key"), request.get("items_per_hour")
        if char_id not in {c for c, *_ in self.db.characters()}:
            return "unknown character"
        found = {it.key: it for it in catalog(read_catalog(self.catalog_path)[0])[0]}
        if isinstance(key, bool) or not isinstance(key, int) or key not in found:
            return "the item is not in the gathering catalog"
        if request.get("clear") is True or ("items_per_hour" in request and value is None):
            return char_id, key, None
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MANUAL_RATE_MAX:
            return "items_per_hour must be a whole number from 1 to 10,000"
        skills = {c: {p: s for p, s, _ in profs} for c, *_, profs in self.db.character_sheets([char_id])}
        if not eligible(found[key], skills.get(char_id, {})):
            why = why_not_eligible(found[key], skills.get(char_id, {}))
            return f"{char_id.split('-')[0]} can't gather {found[key].name} ({why})"
        return char_id, key, value

    def manual_rate(self, body: bytes) -> Response:
        """Set or clear a character's manual farming rate from the Gathering page (SPEC §3.4E.5, §3.5, SEC12, D85).

        Stores the row (set_at = now) or deletes it, then rebuilds only the Gathering page: no history read and
        nothing re-planned, since Gathering is not part of the plan.

        Args:
            body: JSON {"char": "<Name-Realm>", "key": <catalog key>, "items_per_hour": 1-10,000 | null}.

        Returns:
            200 {"set": true|false} (false when a clear finds nothing); 400 {"error": "bad_request", "message"} for
            a bad body, an unknown character or key, a rate outside 1-10,000 or not whole, or a character not
            eligible for the item; 503 when the database is busy.
        """
        now = self.clock.now_ms()
        try:
            with self._lock:
                checked = self._manual_rate_request(body)
                if isinstance(checked, str):
                    return json_response(400, {"error": "bad_request", "message": checked})
                char_id, key, value = checked
                with self.db.transaction():
                    changed = (self.db.clear_manual_rate(char_id, key) if value is None
                               else self.db.set_manual_rate(char_id, key, value, now))
                rebuild_gathering(self.db, now, self.config, self.clock.local_offset_ms(now))
        except sqlite3.Error as error:
            log.warning("setting a manual rate failed: %s", error)
            return json_response(503, {"error": "database_busy"})
        log.info("manual rate of item %s for %s %s from the dashboard", key, char_id,
                 "cleared" if value is None else f"set to {value}/hr")
        return json_response(200, {"set": changed})

    def rule(self, body: bytes) -> Response:
        """Set or clear an item rule from the dashboard, then re-plan and rewrite the plan file (SPEC §3.4.2, D43, SEC8).

        Args:
            body: JSON {"key": <item key>, "rule": "no_buy" | "ignore" | "keep" | "none"}.

        Returns:
            200 {"set": true|false} (false when an equally new rule already won); 400 for a bad body;
            503 when the database is busy.
        """
        try:
            request = json.loads(body.decode("utf-8"))
            key, rule = int(request.get("key")), request.get("rule")
        except (ValueError, AttributeError, TypeError):
            key, rule = None, None
        if key is None or rule not in RULES:
            return json_response(400, {"error": "bad_request"})
        response = self._store_and_replan(lambda now: self.db.upsert_item_rule(key, rule, now, "dashboard"), "a rule")
        log.info("rule %s set on item %s from the dashboard", rule, key)
        return response

    def reserve(self, body: bytes) -> Response:
        """Set or clear a character's reserve from the dashboard, then re-plan and rewrite the plan file (SPEC §3.4.3, SEC9).

        Args:
            body: JSON {"char": "<Name-Realm>", "key": <item key>, "qty": <units kept; -1 all; 0 sells normally>}.

        Returns:
            200 {"set": true|false}; 400 for a bad body or a character AHSync doesn't know; 503 when the database
            is busy.
        """
        try:
            request = json.loads(body.decode("utf-8"))
            char_id, key, qty = request.get("char"), int(request.get("key")), int(request.get("qty"))
        except (ValueError, AttributeError, TypeError):
            char_id, key, qty = None, None, None
        known = {c for c, *_ in self.db.characters()}
        if char_id not in known or key is None or qty is None or qty < -1:
            return json_response(400, {"error": "bad_request"})
        response = self._store_and_replan(lambda now: self.db.set_reserve(char_id, key, qty, now, "dashboard"), "a reserve")
        log.info("reserve of item %s for %s set to %s from the dashboard", key, char_id, qty)
        return response

    def risk_tolerance(self, body: bytes) -> Response:
        """Set the risk tolerance from the Portfolio page, then re-plan and rewrite the plan file (SPEC §3.5, SEC11, D79).

        Only config.toml's `risk_tolerance` line changes (temp file + rename); the running settings follow, and the
        allocation re-runs from the published intelligence without reading history (§3.1.1).

        Args:
            body: JSON {"risk_tolerance": one of 0.1, 0.2, 0.3, 0.4, 0.5}.

        Returns:
            200 {"set": true}; 400 with the reason for any other value or a bad body; 500 {"error":
            "config_not_written"} (and a Status line) when config.toml can't be rewritten, the setting unchanged;
            503 when the database is busy.
        """
        try:
            value = json.loads(body.decode("utf-8")).get("risk_tolerance")
        except (ValueError, AttributeError):
            value = None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value not in RISK_CHOICES:
            return json_response(400, {"error": "bad_request",
                                       "message": "risk_tolerance must be one of 0.1, 0.2, 0.3, 0.4 or 0.5"})
        try:
            if self.config_path is None:
                raise OSError("this run has no config.toml to write")
            with self._lock:                      # one writer of config.toml and its temp file at a time
                write_risk_tolerance(self.config_path, float(value))
        except (OSError, ConfigError) as error:
            return self._config_not_written(error)
        self.config = replace(self.config, risk_tolerance=float(value))
        self.config_changed(self.config)
        log.info("risk tolerance set to %s from the dashboard", value)
        return self._store_and_replan(lambda _now: self.db.clear_status("risk_tolerance") or True, "the risk tolerance")

    def tray_facts(self) -> tuple[UpdateState, int | None] | None:
        """The update state and the last sync, without waiting: the tray asks every second and must not stall on a
        re-plan (DR12, DR2).

        Returns:
            (the update state, the newest market's last sync run in UTC ms or None); None when the dashboard is busy.
        """
        if not self._lock.acquire(blocking=False):
            return None
        try:
            runs = [self.db.get_sync_state(m).last_run for m in self.config.markets]
            synced = max((r for r in runs if r is not None), default=None)
            return read_state(self.db, version.VERSION, self.config), synced
        except sqlite3.Error:
            return None
        finally:
            self._lock.release()

    def _config_not_written(self, error: Exception) -> Response:
        """Report a config.toml that couldn't be rewritten: a log line and a Status line, never its path (SEC11).

        Args:
            error: What failed.

        Returns:
            500 {"error": "config_not_written"}.
        """
        log.warning("risk tolerance not saved: %s", type(error).__name__)
        try:
            with self._lock:
                with self.db.transaction():
                    self.db.set_status("risk_tolerance", "error", "risk_tolerance_not_saved",
                                       {"error": type(error).__name__}, self.clock.now_ms())
        except sqlite3.Error:
            log.warning("could not record the failure on Status")
        return json_response(500, {"error": "config_not_written"})

    def _store_and_replan(self, write: Callable[[int], bool], what: str) -> Response:
        """Store one dashboard change, re-plan and rewrite the plan file (SPEC §3.4.2, §3.4.3).

        Args:
            write: now ms -> whether the change was stored (called inside a transaction).
            what: What it is, for the log.

        Returns:
            200 {"set": stored}; 503 {"error": "database_busy"} when the database is busy.
        """
        now = self.clock.now_ms()
        try:
            with self._lock:
                with self.db.transaction():
                    changed = write(now)
                replan_all(self.db, now, self.config, self.clock.local_offset_ms(now))
                export_plan(self.db, self.config, now, self.clock.sleep)
        except sqlite3.Error as error:
            log.warning("setting %s failed: %s", what, error)
            return json_response(503, {"error": "database_busy"})
        return json_response(200, {"set": changed})

    def skip(self, body: bytes) -> Response:
        """Mark a plan action skipped (SPEC §3.4.1, §3.5).

        Args:
            body: JSON {"action_id": "<id>"}.

        Returns:
            200 {"skipped": true|false} (false when the action isn't open); 400 for a bad
            body; 503 when the database is busy.
        """
        try:
            action_id = json.loads(body.decode("utf-8")).get("action_id")
        except (ValueError, AttributeError):
            action_id = None
        if not isinstance(action_id, str) or not action_id:
            return json_response(400, {"error": "bad_request"})
        try:
            with self._lock:
                changed = self.db.skip_action(action_id, self.clock.now_ms())
        except sqlite3.Error as error:
            log.warning("skipping an action failed: %s", error)
            return json_response(503, {"error": "database_busy"})
        return json_response(200, {"skipped": changed})


class DashboardHandler(BaseHTTPRequestHandler):
    """Adapts http.server requests to DashboardApp.handle; `server.app` holds the app."""

    server_version = "AHSync"
    sys_version = ""
    timeout = REQUEST_TIMEOUT_S      # a stalled connection is dropped instead of holding a thread (FM28)

    def version_string(self) -> str:
        """The Server header.

        Returns:
            "AHSync", without http.server's trailing Python version.
        """
        return self.server_version

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Answer malformed or unsupported requests as JSON with the same safety headers (SPEC §3.5).

        Args:
            code: HTTP status from http.server (400, 414, 501, ...).
            message: Short reason (not echoed: it can quote the request).
            explain: Long reason (not echoed).
        """
        body = json.dumps({"error": "bad_request"}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD" and code >= 200 and code not in (204, 304):
            self.wfile.write(body)

    def _respond(self) -> None:
        """Read the request, ask the app, and write its response with the safety headers."""
        app: DashboardApp = self.server.app       # type: ignore[attr-defined]
        length = self._content_length()
        if length is None:
            response = json_response(413, {"error": "body_too_large"})
        else:
            body = self.rfile.read(length) if length else b""
            response = app.handle(self.command, self.path, dict(self.headers.items()), body)
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for name, value in response.headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(response.body)

    def _content_length(self) -> int | None:
        """The declared body size.

        Returns:
            Bytes to read (0 when absent or malformed); None when over MAX_BODY_BYTES.
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return 0
        return None if length > MAX_BODY_BYTES else max(length, 0)

    def do_GET(self) -> None:
        """Handle GET."""
        self._respond()

    def do_POST(self) -> None:
        """Handle POST."""
        self._respond()

    def do_PUT(self) -> None:
        """Handle PUT (always 405 or 404)."""
        self._respond()

    def do_DELETE(self) -> None:
        """Handle DELETE (always 405 or 404)."""
        self._respond()

    def do_OPTIONS(self) -> None:
        """Handle OPTIONS: no CORS preflight is ever granted (SEC6)."""
        self._respond()

    def log_message(self, format: str, *args: object) -> None:
        """Send the access log to the "ahsync.web" logger at debug level instead of stderr.

        Args:
            format: printf-style format from http.server.
            *args: Its values.
        """
        log.debug(format, *args)


class DashboardServer(ThreadingHTTPServer):
    """An HTTP server (one thread per request) that carries the app for its handler.

    Threads are daemons and closing doesn't wait for them, so a client that stalls
    can neither block other requests nor AHSync's exit (FM28). Database access is
    serialised by the app's lock.
    """

    daemon_threads = True
    block_on_close = False

    def handle_error(self, request: object, client_address: tuple) -> None:
        """Log a connection that failed mid-request (timeout, reset) instead of printing a traceback to stderr.

        Args:
            request: The socket.
            client_address: (host, port) of the client.
        """
        log.debug("dashboard connection from %s ended with an error", client_address[0], exc_info=True)

    def __init__(self, app: DashboardApp, port: int) -> None:
        """Bind to 127.0.0.1 only (SPEC §3.5).

        Args:
            app: The request handling.
            port: TCP port; 0 picks a free one (tests).

        Raises:
            OSError: The port is in use or can't be bound.
        """
        super().__init__((BIND_ADDRESS, port), DashboardHandler)
        self.app = app

    def server_bind(self) -> None:
        """Bind the port exclusively where the OS offers it (FM57), without looking up a name (FM59).

        On Windows `SO_REUSEADDR` lets a second server listen on a port already in use and take its requests, so
        `SO_EXCLUSIVEADDRUSE` replaces it: a taken port fails here, and nothing can share ours later. Elsewhere
        `SO_REUSEADDR` stays (it only allows rebinding past TIME_WAIT there). `HTTPServer.server_bind` is skipped:
        its `socket.getfqdn` is a reverse-DNS query, which macOS treats as local-network access (a permission
        prompt) and which can take seconds; the name it finds is never used.

        Raises:
            OSError: The port is in use or can't be bound.
        """
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def start_dashboard(app: DashboardApp, port: int) -> DashboardServer:
    """Bind the dashboard and serve it on a daemon thread.

    Args:
        app: The request handling; its `port` is set to the bound port.
        port: TCP port (config dashboard_port); 0 picks a free one.

    Returns:
        The running server; call `stop_dashboard` to stop it.

    Raises:
        OSError: The port can't be bound.
    """
    server = DashboardServer(app, port)
    app.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, name="ahsync-dashboard", daemon=True)
    thread.start()
    log.info("dashboard at http://localhost:%d", app.port)
    return server


def stop_dashboard(server: DashboardServer) -> None:
    """Stop serving and release the port.

    Args:
        server: From start_dashboard.
    """
    server.shutdown()
    server.server_close()
