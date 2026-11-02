"""The setup server's requests: first-run setup on the dashboard's port (SPEC §3.7.4; DR5–DR8, SEC13, FM50).

Served only while `config.toml` is missing, by the same server class and the same Security
checks as the dashboard (`web/server.py`): `GET /` redirects to `/setup`; `GET /setup` serves the
page with this run's token; `GET /api/app` answers; every `/api/views/*` is `404 no_refresh_yet`;
`POST /api/setup` checks a folder or saves everything. After a save the shell stops this server
and starts the normal run (the hand-over).
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ahsync.app.setup import KeyNotSaved, SetupError, SetupRequest, folder_check, parse_setup, read_body
from ahsync.web.server import (TOKEN_PLACEHOLDER, VIEWS_PREFIX, Response, host_allowed, json_response,
                               write_refusal)

log = logging.getLogger("ahsync.web")

SETUP_HTML = Path(__file__).parent / "static" / "setup.html"
SETUP_GET_PATHS = ("/", "/setup", "/api/app")
SETUP_POST = "/api/setup"


@dataclass
class SetupApp:
    """Setup mode's request handling, with every outside dependency injected."""

    token: str                                       # this run's write token
    port: int                                        # set by start_dashboard
    app_info: Callable[[], dict]                     # GET /api/app's payload (setup true)
    save: Callable[[SetupRequest], None]             # writes AddOns, config.toml and the key file (app/setup.py)
    after_save: Callable[[SetupRequest], bool]       # start at login and the addon install -> restart_wow
    key_not_saved: Callable[[str], None]             # records the Status line api_key_not_saved (FM50)
    redact: Callable[[str], None] = lambda _secret: None   # adds a key to the log's redactor before any write
    page_html: str = ""                              # setup.html with the token placeholder; read when empty
    done: threading.Event = field(default_factory=threading.Event)   # set after a save: the shell hands over
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        """Load the page once, so serving it touches no files."""
        if not self.page_html:
            self.page_html = SETUP_HTML.read_text(encoding="utf-8")

    def handle(self, method: str, target: str, headers: Mapping[str, str], body: bytes = b"") -> Response:
        """Answer one request in setup mode (SPEC §3.7.4, §3.5 Security).

        Args:
            method: HTTP method.
            target: Path and optional query.
            headers: Request headers; names matched case-insensitively.
            body: Request body bytes.

        Returns:
            403 for a bad Host, Origin or token; 405 for the wrong method; 404 for anything setup doesn't serve
            (every view is "no_refresh_yet"); else the endpoint's answer.
        """
        lowered = {k.lower(): v for k, v in headers.items()}
        host = lowered.get("host", "")
        if not host_allowed(host, self.port):
            log.warning("rejected %s request in setup: Host %r is not this dashboard", method, host[:100])
            return json_response(403, {"error": "bad_host"})
        path = urlsplit(target).path
        if path.startswith(VIEWS_PREFIX):
            return json_response(404, {"error": "no_refresh_yet"}) if method == "GET" else \
                json_response(405, {"error": "method_not_allowed"}, (("Allow", "GET"),))
        allowed = "GET" if path in SETUP_GET_PATHS else "POST" if path == SETUP_POST else None
        if allowed is None:
            return json_response(404, {"error": "not_found"})
        if method != allowed:
            return json_response(405, {"error": "method_not_allowed"}, (("Allow", allowed),))
        if method == "POST":
            return write_refusal(path, host, lowered, self.token) or self.post_setup(body)
        if path == "/":
            return Response(302, b"", "text/plain; charset=utf-8", (("Location", "/setup"),))
        if path == "/setup":
            return Response(200, self.page_html.replace(TOKEN_PLACEHOLDER, self.token).encode("utf-8"),
                            "text/html; charset=utf-8")
        return json_response(200, self.app_info())

    def post_setup(self, body: bytes) -> Response:
        """Check a folder, or save the setup and ask for the hand-over (DR6–DR8, FM50).

        Args:
            body: {"wow_path", "check_only": true} for the Check button; else the full save body.

        Returns:
            200 {"ok", "text"} for a check; 200 {"set": true, "restart_wow"} after a save; 400 {"error":
            "bad_request", "message"} with nothing written; 500 {"error": "key_not_saved", "message"} when the key
            file failed (config.toml kept, the server stays up for a retry); 500 {"error": "config_not_written"}.
        """
        try:
            request = read_body(body)
            if request.get("check_only") is True:
                return json_response(200, folder_check(request.get("wow_path")))
            checked = parse_setup(request)
        except SetupError as error:
            return json_response(400, {"error": "bad_request", "message": str(error)})
        if checked.api_key:
            self.redact(checked.api_key)
        with self._lock:                          # one save at a time: a double click can't interleave writes
            if self.done.is_set():
                return json_response(409, {"error": "already_set"})
            try:
                self.save(checked)
            except KeyNotSaved as error:
                self.key_not_saved(error.reason)
                return json_response(500, {"error": "key_not_saved",
                                           "message": f"the key could not be saved: {error.reason}"})
            except OSError as error:
                log.warning("setup: config.toml not written: %s", type(error).__name__)
                return json_response(500, {"error": "config_not_written",
                                           "message": "the settings could not be saved: check the folder can be written"})
            try:
                restart_wow = self.after_save(checked)
            except Exception as error:            # the settings are saved: hand over anyway; the start installs
                log.warning("setup: a step after the save failed: %s", type(error).__name__)
                restart_wow = False
            self.done.set()
        log.info("setup saved; handing over to the normal start")
        return json_response(200, {"set": True, "restart_wow": restart_wow})
