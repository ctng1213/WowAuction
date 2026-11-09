"""The AI assistant server inside AHSync (SPEC §3.8.2): 127.0.0.1 only, only while enabled, read-only.

`McpServer.start` opens its own read-only connection, makes a new key, binds `127.0.0.1:<mcp_port>` and writes
`mcp.json` for the bridge; `stop` undoes all of it. `McpApp.handle` checks every request (the key, `Host`, no `Origin`;
SEC19) and answers JSON-RPC: initialize, ping and tools/list locally, tools/call through the tools.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ahsync.clock import Clock
from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.intel.store import IntelCache
from ahsync.mcp import protocol
from ahsync.mcp.protocol import ToolError
from ahsync.mcp.stats import MarketStats
from ahsync.mcp.tools import HANDLERS, ToolContext
from ahsync.web.server import BIND_ADDRESS, BUSY_TIMEOUT_MS, DashboardServer, Response, json_response

log = logging.getLogger("ahsync.mcp")

KEY_FILE = "mcp.json"                  # in AHSync's data folder: {"port", "key"} while enabled (SPEC §3.8.2)
ENDPOINT = "/mcp"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESULT_BYTES = 256 * 1024          # SPEC §3.8.3: every tool result
REQUEST_TIMEOUT_S = 30
STOPPED = "AHSync's AI assistant access was just turned off. Press Enable on its Status page to turn it back on."
NOT_STARTED = "AHSync couldn't start the assistant server ({why}). Try Enable again."
PORT_IN_USE = ("Port {port} is in use by another program. Set mcp_port in AHSync's settings to a free port, "
               "then Enable again.")


class McpStartError(Exception):
    """The server couldn't start; its message is the card's reason (MCP4)."""


def _empty(status: int) -> Response:
    """A response with no body (a notification's 202, a refusal).

    Args:
        status: HTTP status.

    Returns:
        The response.
    """
    return Response(status, b"")


@dataclass
class McpApp:
    """The request handling: the checks of SEC19, then JSON-RPC (SPEC §3.8.2)."""

    key: str
    port: int
    context: Callable[[], ToolContext]
    lock: threading.Lock = field(default_factory=threading.Lock)
    stopped: bool = False              # set by McpServer.stop under the lock: calls still in flight answer "off"

    def allowed(self, headers: Mapping[str, str]) -> bool:
        """Whether a request may be answered: the key, a local `Host`, and no browser `Origin` (SEC19).

        Args:
            headers: The request's headers.

        Returns:
            True when every check passes.
        """
        lower = {k.lower(): v for k, v in headers.items()}
        if "origin" in lower:
            return False
        if lower.get("host") not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            return False
        given = lower.get("authorization", "")
        return hmac.compare_digest(given.encode(), f"Bearer {self.key}".encode())

    def handle(self, method: str, path: str, headers: Mapping[str, str], body: bytes) -> Response:
        """Answer one HTTP request.

        Args:
            method: HTTP method.
            path: Request path.
            headers: Request headers.
            body: Request body.

        Returns:
            403 without the checks (no data); 404 off the endpoint; 405 for anything but POST; 202 for a
            notification; 200 with the JSON-RPC response otherwise.
        """
        if not self.allowed(headers):
            return _empty(403)
        if path != ENDPOINT:
            return _empty(404)
        if method != "POST":
            return Response(405, b"", headers=(("Allow", "POST"),))
        msg, bad = protocol.parse_message(body.decode("utf-8", errors="replace"))
        if bad is not None:
            return json_response(200, bad)
        if protocol.is_notification(msg):
            return _empty(202)
        return json_response(200, self.answer(msg))

    def answer(self, msg: dict) -> dict:
        """The JSON-RPC response to one request.

        Args:
            msg: A parsed request with an id.

        Returns:
            The response message.
        """
        local = protocol.local_answer(msg, _version())
        if local is not None:
            return local
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in HANDLERS or not isinstance(args, dict):
            return protocol.error(msg.get("id"), protocol.INVALID_PARAMS, f"Unknown tool: {name}")
        return protocol.result(msg.get("id"), self.call(name, args))

    def call(self, name: str, args: dict) -> dict:
        """Run one tool, as a tools/call result: its data, or an error the assistant reads (MCP13).

        Args:
            name: A tool in HANDLERS.
            args: Its arguments.

        Returns:
            The result object, never over MAX_RESULT_BYTES.
        """
        started = time.monotonic()
        try:
            with self.lock:
                if self.stopped:
                    return protocol.tool_error(STOPPED)
                value = HANDLERS[name](self.context(), args)
        except ToolError as error:
            return protocol.tool_error(error.message)
        except sqlite3.Error as error:
            log.warning("assistant tool %s failed reading the database: %s", name, error)
            return protocol.tool_error("AHSync's database is busy; ask again in a moment.")
        out = protocol.tool_result(value)
        size = len(out["content"][0]["text"].encode("utf-8"))   # bytes, as run_query counts them
        log.info("assistant tool %s answered in %d ms (%d bytes)", name, (time.monotonic() - started) * 1000, size)
        if size > MAX_RESULT_BYTES:
            return protocol.tool_error(f"The answer is {size // 1024} KB, over the {MAX_RESULT_BYTES // 1024} KB "
                                       "limit: ask for fewer rows (a smaller limit, a search or a sort).")
        return out


def _version() -> str:
    """AHSync's version, for serverInfo.

    Returns:
        "0.1.12", or "0.0.0" from source.
    """
    from ahsync.version import VERSION
    return VERSION


class _Handler(BaseHTTPRequestHandler):
    """One connection to the assistant server: read, ask McpApp, write (no CORS headers ever, SEC19)."""

    timeout = REQUEST_TIMEOUT_S

    def _respond(self) -> None:
        """Read the request, ask the app, and write its response."""
        app: McpApp = self.server.app       # type: ignore[attr-defined]
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > MAX_REQUEST_BYTES:
            response = _empty(413)
        else:
            body = self.rfile.read(max(length, 0)) if length else b""
            response = app.handle(self.command, self.path, dict(self.headers.items()), body)
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in response.headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(response.body)

    def do_POST(self) -> None:
        """Handle POST."""
        self._respond()

    def do_GET(self) -> None:
        """Handle GET (refused)."""
        self._respond()

    def do_OPTIONS(self) -> None:
        """Handle OPTIONS: no CORS preflight is ever granted (SEC19)."""
        self._respond()

    def log_message(self, format: str, *args: object) -> None:
        """Send the access log to the "ahsync.mcp" logger at debug level, never the headers (they hold the key).

        Args:
            format: printf-style format from http.server.
            *args: Its values.
        """
        log.debug(format, *args)


class _HttpServer(ThreadingHTTPServer):
    """The assistant server's listener: 127.0.0.1 only, the port held exclusively like the dashboard's (FM57)."""

    daemon_threads = True
    block_on_close = False
    server_bind = DashboardServer.server_bind
    handle_error = DashboardServer.handle_error

    def __init__(self, app: McpApp, port: int) -> None:
        """Bind to 127.0.0.1 only (SPEC §3.8.2).

        Args:
            app: The request handling.
            port: TCP port; 0 picks a free one (tests).

        Raises:
            OSError: The port is in use or can't be bound.
        """
        super().__init__((BIND_ADDRESS, port), _Handler)
        self.app = app


def write_key_file(data_dir: Path, port: int, key: str) -> Path:
    """Write mcp.json for the bridge, atomically, readable by the user only where the OS allows (SPEC §3.8.2).

    Args:
        data_dir: AHSync's data folder.
        port: The bound port.
        key: The key.

    Returns:
        The file's path.
    """
    path = data_dir / KEY_FILE
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"port": port, "key": key}), encoding="utf-8")
    os.chmod(temp, 0o600)                  # the user only on macOS; on Windows it only keeps the file writable
    os.replace(temp, path)
    return path


def remove_key_file(data_dir: Path) -> None:
    """Delete mcp.json (Disable, a stop, or a stale one from a crash; MCP2, MCP3).

    Args:
        data_dir: AHSync's data folder.
    """
    try:
        (data_dir / KEY_FILE).unlink()
    except FileNotFoundError:
        pass


def open_read_only(path: Path, intel_cache: IntelCache | None = None) -> Database:
    """A Database on a read-only connection of its own, for another thread (SPEC §3.8.2).

    Args:
        path: data/ahsync.db.
        intel_cache: The app's parsed intelligence, shared so nothing is parsed twice (BZ42); None: a new one.

    Returns:
        The database; every write through it fails.
    """
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None,
                           check_same_thread=False)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA query_only = ON")
    return Database(conn, path, intel_cache)


class McpServer:
    """The server's lifecycle: start makes the key, connection, listener and mcp.json; stop removes them."""

    def __init__(self, db_path: Path, data_dir: Path, config: Config, clock: Clock,
                 intel_cache: IntelCache | None = None) -> None:
        """Hold what start needs; nothing runs yet.

        Args:
            db_path: data/ahsync.db.
            data_dir: AHSync's data folder (where mcp.json goes).
            config: The app's config (mcp_port, markets).
            clock: The app's clock.
            intel_cache: The app's parsed intelligence, shared.
        """
        self.db_path, self.data_dir, self.config, self.clock = db_path, data_dir, config, clock
        self.intel_cache = intel_cache
        self._http: _HttpServer | None = None
        self._app: McpApp | None = None
        self._db: Database | None = None
        self.stats = MarketStats()
        self.started_ms: int | None = None

    @property
    def running(self) -> bool:
        """Whether the server is listening.

        Returns:
            True between start and stop.
        """
        return self._http is not None

    @property
    def port(self) -> int | None:
        """The bound port.

        Returns:
            The port while running; None otherwise.
        """
        return self._http.server_address[1] if self._http else None

    def start(self, port: int | None = None) -> None:
        """Open the read-only connection, bind the port, write mcp.json and serve on a daemon thread.

        Args:
            port: Overrides config.mcp_port (0 picks a free one, for tests).

        Raises:
            McpStartError: The port is in use or can't be bound, or the table or mcp.json can't be made; nothing is
                left behind (a failed start stops what it began).
        """
        if self.running:
            return
        port = self.config.mcp_port if port is None else port
        self._db = open_read_only(self.db_path, self.intel_cache)
        self.stats = MarketStats()                                  # a fresh table for this run
        db, config, clock, stats, path = self._db, self.config, self.clock, self.stats, self.db_path
        app = McpApp(secrets.token_hex(32), port, lambda: ToolContext(db, config, clock, path, stats))
        try:
            self._http = _HttpServer(app, port)
        except OSError as error:
            self._db.conn.close()
            self._db = None
            log.warning("the assistant server couldn't bind port %d: %s", port, error)
            raise McpStartError(PORT_IN_USE.format(port=port)) from None
        app.port = self._http.server_address[1]
        self._app = app
        try:
            self.stats.build(self._db, self.config.markets)        # Enable's memory (SPEC §3.8.1, §3.8.4)
            write_key_file(self.data_dir, app.port, app.key)
        except (sqlite3.Error, OSError, ValueError) as error:
            log.warning("the assistant server couldn't start: %s", error)
            self.stop()
            raise McpStartError(NOT_STARTED.format(why=type(error).__name__)) from None
        self.started_ms = self.clock.now_ms()
        threading.Thread(target=self._http.serve_forever, name="ahsync-mcp", daemon=True).start()
        log.info("assistant server listening on 127.0.0.1:%d", app.port)

    def stop(self) -> None:
        """Stop serving, close the port, free the table, close the connection, and delete mcp.json (MCP2).

        The table and the connection are released under the app's lock, after marking it stopped: a call still in
        flight then answers "off" instead of rebuilding the table after Disable.
        """
        remove_key_file(self.data_dir)
        if self._http is not None:
            if self.started_ms is not None:        # serving: shutdown() waits for a loop that never ran otherwise
                self._http.shutdown()
            self._http.server_close()
            self._http = None
            log.info("assistant server stopped")
        lock = self._app.lock if self._app is not None else threading.Lock()
        with lock:
            if self._app is not None:
                self._app.stopped = True
            self.stats.close()                                      # Disable frees it (MCP2)
            if self._db is not None:
                self._db.conn.close()
                self._db = None
        self._app = None
        self.started_ms = None
