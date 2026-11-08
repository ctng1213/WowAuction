"""The update check against GitHub Releases (at every start, then daily), and the update state kept in `meta` (SPEC §3.7.8; DR13–DR15, FM52).

One plain GET through the transport, never through `ApiClient`: GitHub must never see the AHF key (DR15), and a
failure is one log line and a wait until tomorrow (FM52), not the client's retries. What the check and the
updater learned lives in `meta` under `update.*`; `read_state` turns it into `engine.updates.UpdateState`.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from ahsync.clock import Clock
from ahsync.collect.client import USER_AGENT, HttpRequest, Transport, TransportError
from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine.app_view import is_dev
from ahsync.engine.updates import NOT_FOUND, OFFLINE, UNAVAILABLE, UpdateState, pick_release

log = logging.getLogger("ahsync.update")

REPO = "https://api.github.com/repos/ctng1213/WowAuction"
LATEST_URL = f"{REPO}/releases/latest"                 # never a pre-release or a draft: the stable channel
LIST_URL = f"{REPO}/releases?per_page=20"              # the beta channel takes the highest of these
DAY_MS = 86_400_000
KEYS = ("checked_ms", "etag", "tag", "url", "prerelease", "failure", "failed_since_ms", "downloaded",
        "downloaded_path", "download_failed", "download_tried_ms", "left_beta", "cant_replace", "updated_to",
        "updated_ms", "pending")


def read_meta(db: Database) -> dict[str, str]:
    """Every `update.*` value that is set.

    Args:
        db: The database.

    Returns:
        Key without the prefix -> value.
    """
    values = {key: db.get_meta(f"update.{key}") for key in KEYS}
    return {key: value for key, value in values.items() if value is not None}


def write_meta(db: Database, values: Mapping[str, object]) -> None:
    """Set or clear `update.*` values in one commit.

    Args:
        db: The database.
        values: Key without the prefix -> value (str() of it), None to clear.
    """
    with db.transaction():
        for key, value in values.items():
            assert key in KEYS, key
            db.set_meta(f"update.{key}", None if value is None else str(value))


def read_state(db: Database, version: str, config: Config) -> UpdateState:
    """The update state for the texts (SPEC §3.7.8–§3.7.10).

    Args:
        db: The database.
        version: The running version.
        config: The channel and `auto_update`.

    Returns:
        The state.
    """
    meta = read_meta(db)

    def number(key: str) -> int | None:
        """A stored ms value.

        Args:
            key: The key without the prefix.

        Returns:
            The number; None when unset.
        """
        return int(meta[key]) if meta.get(key, "").isdigit() else None

    return UpdateState(version, config.update_channel, config.auto_update, number("checked_ms"), meta.get("tag"),
                       meta.get("url"), meta.get("prerelease") == "1", meta.get("failure"), number("failed_since_ms"),
                       meta.get("downloaded"), meta.get("download_failed"), meta.get("cant_replace") == "1",
                       meta.get("updated_to"), number("updated_ms"), meta.get("left_beta") == "1")


def due(db: Database, version: str, config: Config, now_ms: int, at_start: bool = False) -> bool:
    """Whether a check should be sent now: never from source or with `update_check` off; else at every start,
    then once a day (DR15, DR33).

    Args:
        db: The database.
        version: The running version.
        config: `update_check`.
        now_ms: Now, UTC ms.
        at_start: True for the first check after AHSync started, which skips the day's wait.

    Returns:
        True when a request should go out.
    """
    if is_dev(version) or not config.update_check:
        return False
    if at_start:
        return True
    checked = read_state(db, version, config).checked_ms
    return checked is None or now_ms - checked >= DAY_MS


def check(db: Database, transport: Transport, clock: Clock, version: str, config: Config,
          at_start: bool = False) -> bool:
    """Ask GitHub for the release this channel offers, when one is due (SPEC §3.7.8; DR13–DR15, DR25, DR33, FM52).

    Args:
        db: The database.
        transport: Sends the one request.
        clock: Now.
        version: The running version.
        config: The channel and `update_check`.
        at_start: True for the first check after AHSync started: it asks even within a day of the last (DR33).

    Returns:
        True when a request was sent.

    Side effects:
        `update.*` in meta; one log line per check.
    """
    now = clock.now_ms()
    if not due(db, version, config, now, at_start):
        return False
    beta = config.update_channel == "beta"
    meta = read_meta(db)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if meta.get("etag"):
        headers["If-None-Match"] = meta["etag"]
    try:
        response = transport(HttpRequest(LIST_URL if beta else LATEST_URL, headers))
    except TransportError:
        return failed(db, meta, now, OFFLINE, "offline")
    if response.status == 304:
        write_meta(db, {"checked_ms": now, "failure": None, "failed_since_ms": None})
        log.info("update check: unchanged (304)")
        return True
    if response.status != 200:
        return failed(db, meta, now, NOT_FOUND if response.status == 404 else UNAVAILABLE, str(response.status))
    try:
        body = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return failed(db, meta, now, UNAVAILABLE, "unreadable body")
    if not beta and not (isinstance(body, dict) and "tag_name" in body):
        return failed(db, meta, now, UNAVAILABLE, "no tag_name")
    if beta and not isinstance(body, list):
        return failed(db, meta, now, UNAVAILABLE, "no release list")
    release = pick_release(None if beta else body, body if beta else None, config.update_channel)
    found = {"tag": None, "url": None, "prerelease": None} if release is None else \
        {"tag": release.tag, "url": release.url, "prerelease": "1" if release.prerelease else None}
    if found["tag"] != meta.get("tag"):
        found.update(download_failed=None, download_tried_ms=None)    # a new release gets its own tries
    write_meta(db, {"checked_ms": now, "etag": response.headers.get("etag"), "failure": None, "failed_since_ms": None,
                    **found})
    log.info("update check: %s", release.tag if release else "no release for this channel")
    return True


def failed(db: Database, meta: Mapping[str, str], now_ms: int, failure: str, what: str) -> bool:
    """Record a check that got no release: one log line, no alert, and nothing until tomorrow (FM52).

    Args:
        db: The database.
        meta: The stored `update.*` values before this check.
        now_ms: Now.
        failure: OFFLINE, NOT_FOUND or UNAVAILABLE.
        what: For the log line: "offline", "404", "403", "no tag_name".

    Returns:
        True (a request was attempted).
    """
    log.info("update check: %s", what)
    since = meta.get("failed_since_ms") or now_ms             # the first failure since the last answer
    write_meta(db, {"checked_ms": now_ms, "failure": failure, "failed_since_ms": since})
    return True
