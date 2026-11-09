"""The dashboard's settings endpoints (SPEC §3.5, §3.7, §3.8.1): start at login, automatic updates, the update
channel, Restart to update, and the AI assistant's Enable / Disable. A mixin DashboardApp inherits; split off
web/server.py for size. Each rewrites one config.toml line through `_set_setting`."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import replace

from ahsync.app.update_check import write_meta
from ahsync.config import CHANNELS, ConfigError, write_setting
from ahsync.engine.app_view import BAD_LOCATION_TEXT
from ahsync.engine.mcp_view import McpFacts, mcp_card
from ahsync.web.responses import Response, body_field, json_response

log = logging.getLogger("ahsync.web")
AUTOSTART_SET, AUTOSTART_BAD_LOCATION, AUTOSTART_NOT_SET = "set", "bad_location", "not_set"   # set_autostart's outcomes


class SettingsEndpoints:
    """The settings writes and the AI assistant card; mixed into DashboardApp, whose fields they use."""

    def autostart(self, body: bytes) -> Response:
        """Turn start at login on or off from the Status card (SPEC §3.7.5; SEC14, DR11, FM51).

        The app shell does the write and keeps the Status line `autostart_not_set`; this only reads the body
        and maps the outcome.

        Args:
            body: JSON {"enabled": true | false}.

        Returns:
            200 {"set": true, "enabled": <the real state>}; 400 bad_request for a non-bool, 400 bad_location for a
            translocated app; 500 autostart_not_set when the entry can't be written; 404 without the app shell.
        """
        if self.app_hooks is None:
            return json_response(404, {"error": "not_found"})
        try:
            enabled = json.loads(body.decode("utf-8")).get("enabled")
        except (ValueError, AttributeError):
            enabled = None
        if not isinstance(enabled, bool):
            return json_response(400, {"error": "bad_request", "message": "enabled must be true or false"})
        outcome, now_enabled = self.app_hooks.set_autostart(enabled)
        if outcome == AUTOSTART_BAD_LOCATION:
            return json_response(400, {"error": "bad_location", "message": BAD_LOCATION_TEXT})
        if outcome != AUTOSTART_SET:
            return json_response(500, {"error": "autostart_not_set"})
        return json_response(200, {"set": True, "enabled": now_enabled})

    def update_auto(self, body: bytes) -> Response:
        """Turn automatic updates on or off from the Status card (SPEC §3.7.9; SEC15).

        Args:
            body: JSON {"enabled": true | false}.

        Returns:
            set_auto_update()'s answer; 400 for a non-bool; 404 without the app shell.
        """
        if self.app_hooks is None:
            return json_response(404, {"error": "not_found"})
        enabled = body_field(body, "enabled")
        if not isinstance(enabled, bool):
            return json_response(400, {"error": "bad_request", "message": "enabled must be true or false"})
        return self.set_auto_update(enabled)

    def set_auto_update(self, enabled: bool) -> Response:
        """Rewrite `auto_update` (the endpoint's and the tray's way in).

        Args:
            enabled: The new value.

        Returns:
            200 {"set": true, "enabled": enabled}; 500 {"error": "config_not_written"} when config.toml can't be
            rewritten (the setting unchanged).
        """
        return self._set_setting("auto_update", enabled, "true" if enabled else "false", {"enabled": enabled})

    def update_restart(self) -> Response:
        """Restart to update from the Status card (SPEC §3.7.9; SEC15): stop, and the shell installs on the way out.

        Returns:
            200 {"restarting": true} once the stop is asked; 409 {"error": "no_update"} when no verified update
            waits that may install itself; 404 without the app shell.
        """
        if self.app_hooks is None:
            return json_response(404, {"error": "not_found"})
        if not self.app_hooks.restart(self.db, self.config):
            return json_response(409, {"error": "no_update"})
        log.info("restart to update asked from the dashboard")
        return json_response(200, {"restarting": True})

    def update_channel(self, body: bytes) -> Response:
        """Choose the update channel from the Status card (SPEC §3.7.10; SEC15).

        Args:
            body: JSON {"channel": "stable" | "beta"}.

        Returns:
            set_update_channel()'s answer; 400 for any other value; 404 without the app shell.
        """
        if self.app_hooks is None:
            return json_response(404, {"error": "not_found"})
        channel = body_field(body, "channel")
        if channel not in CHANNELS or not isinstance(channel, str):
            return json_response(400, {"error": "bad_request", "message": "channel must be stable or beta"})
        return self.set_update_channel(channel)

    def set_update_channel(self, channel: str) -> Response:
        """Rewrite `update_channel`, and ask GitHub again at the next update job (DR26).

        Leaving the beta is remembered, so an install ahead of the latest stable release says it stays on its
        version (never a downgrade); joining it forgets that.

        Args:
            channel: "stable" or "beta".

        Returns:
            200 {"set": true, "channel": channel}; 500 {"error": "config_not_written"}.
        """
        before = self.config.update_channel
        response = self._set_setting("update_channel", channel, f'"{channel}"', {"channel": channel})
        if response.status == 200 and channel != before:
            try:
                with self._lock:
                    write_meta(self.db, {"checked_ms": None, "etag": None, "tag": None, "url": None,
                                         "prerelease": None, "left_beta": "1" if channel == "stable" else None})
            except sqlite3.Error:
                log.warning("the update state was not reset after the channel change")
        return response

    def _mcp_facts(self, now_ms: int) -> McpFacts:
        """What the AI assistant card shows (SPEC §3.8.1), read under the dashboard's lock.

        Args:
            now_ms: Now.

        Returns:
            The card's facts: the server's state, its table, the first market's refresh and the characters.
        """
        market = self.config.markets[0]
        intel = self.db.read_intel(market)
        stats = self.mcp.stats
        return McpFacts(running=self.mcp.running, error=self.mcp_error, started_ms=self.mcp.started_ms,
                        table_bytes=stats.size_bytes(), table_items=stats.count(),
                        intel_items=len(intel.items) if intel else 0, market=market,
                        refreshed_ms=intel.built_ms if intel else None, characters=len(self.db.characters()),
                        command=self.mcp_command, offset_ms=self.clock.local_offset_ms(now_ms))

    def set_mcp(self, body: bytes) -> Response:
        """Enable or Disable the AI assistant server from the Status card (SPEC §3.8.1; MCP1, MCP2, MCP4, SEC18).

        Enable starts the server, then saves `mcp_enabled = true`; a start that fails answers why and saves nothing.
        Disable stops it (freeing its table at once), then saves `false`.

        Args:
            body: JSON {"enabled": true | false}.

        Returns:
            200 {"set": true, "enabled": ...}; 200 {"enabled": false, "error": why} when it couldn't start; 400 for a
            non-bool; 404 without a server; 500 when config.toml can't be rewritten (an Enable is undone).
        """
        from ahsync.mcp.server import McpStartError        # here: mcp.server imports this module
        if self.mcp is None:
            return json_response(404, {"error": "not_found"})
        enabled = body_field(body, "enabled")
        if not isinstance(enabled, bool):
            return json_response(400, {"error": "bad_request", "message": "enabled must be true or false"})
        if enabled:
            try:
                self.mcp.start()
            except McpStartError as error:
                self.mcp_error = str(error)
                log.warning("the AI assistant server couldn't start")
                return json_response(200, {"enabled": False, "error": self.mcp_error})
        else:
            self.mcp.stop()
        self.mcp_error = None
        response = self._set_setting("mcp_enabled", enabled, "true" if enabled else "false", {"enabled": enabled})
        if response.status != 200 and enabled:
            self.mcp.stop()                                    # not remembered: don't leave it running unasked
        return response

    def _set_setting(self, name: str, value: object, formatted: str, answer: dict) -> Response:
        """Rewrite one config.toml line and tell the running settings (SPEC §3.5; SEC15).

        Args:
            name: The key.
            value: Its new value.
            formatted: The value as TOML.
            answer: Added to {"set": true} on success.

        Returns:
            200 {"set": true, **answer}; 500 {"error": "config_not_written"}.
        """
        try:
            if self.config_path is None:
                raise OSError("this run has no config.toml to write")
            with self._lock:                      # one writer: the endpoint and the tray can both get here
                write_setting(self.config_path, name, value, formatted)
                self.config = replace(self.config, **{name: value})
                self.config_changed(self.config)
        except (OSError, ConfigError) as error:
            log.warning("%s not saved: %s", name, type(error).__name__)
            return json_response(500, {"error": "config_not_written"})
        log.info("%s set to %s from the dashboard", name, formatted)
        return json_response(200, {"set": True, **answer})

    def mcp_card(self, now_ms: int) -> dict:
        """The Status page's AI assistant card (SPEC §3.8.1), under the dashboard's lock.

        Args:
            now_ms: Now.

        Returns:
            engine.mcp_view.mcp_card's card.
        """
        return mcp_card(self._mcp_facts(now_ms))
