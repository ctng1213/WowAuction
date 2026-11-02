"""Start at login's shared decisions: every control calls these (SPEC §3.7.5; DR9-DR11, FM51).

The setup checkbox, the tray checkbox and the Status card's toggle (`POST /api/autostart`) all end here;
the platform only writes its entry. A translocated macOS app is refused before anything is written.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ahsync.engine.app_view import autostart_line
from ahsync.web.server import AUTOSTART_BAD_LOCATION, AUTOSTART_NOT_SET, AUTOSTART_SET
from ahsync_app.platform.base import Platform

log = logging.getLogger("ahsync.app")

SET, BAD_LOCATION, NOT_SET = AUTOSTART_SET, AUTOSTART_BAD_LOCATION, AUTOSTART_NOT_SET   # the server maps these


class BadLocation(Exception):
    """The app runs from a place a login entry can't point at (macOS App Translocation, DR11)."""


def set_enabled(platform: Platform, exe: Path, enabled: bool, args: tuple[str, ...] = ()) -> None:
    """Turn start at login on or off (SPEC §3.7.5).

    Args:
        platform: The OS layer.
        exe: The running executable, which the entry starts.
        enabled: The wanted state.
        args: Arguments before --autostart (from source: the script).

    Raises:
        BadLocation: When turning it on from a translocated app; nothing is written.
        OSError: When the entry can't be written or removed.
    """
    if not enabled:
        platform.autostart_disable()
        return
    if platform.run_location_problem(exe) is not None:
        raise BadLocation(exe)
    platform.autostart_enable(exe, args)


def is_enabled(platform: Platform) -> bool:
    """The real state: whether the entry exists; False when it can't be read or isn't built.

    Args:
        platform: The OS layer.

    Returns:
        True when AHSync starts at login.
    """
    try:
        return platform.autostart_enabled()
    except (NotImplementedError, OSError):
        return False


def request(platform: Platform, exe: Path, enabled: bool, args: tuple[str, ...] = ()) -> tuple[str, bool, str]:
    """A control's request, as an outcome and the real state afterwards (SPEC §3.7.5; SEC14, DR11, FM51).

    Args:
        platform: The OS layer.
        exe: The running executable.
        enabled: The wanted state.
        args: Arguments before --autostart (from source: the script).

    Returns:
        (SET, BAD_LOCATION or NOT_SET; whether the entry exists now; the failure's type name, else "").
        A failure is logged by its type only.
    """
    try:
        set_enabled(platform, exe, enabled, args)
    except BadLocation:
        log.info("start at login refused: AHSync runs translocated")
        return BAD_LOCATION, is_enabled(platform), ""
    except (OSError, NotImplementedError) as error:
        log.warning("start at login not %s: %s", "set" if enabled else "removed", type(error).__name__)
        return NOT_SET, is_enabled(platform), type(error).__name__
    log.info("start at login %s", "on" if enabled else "off")
    return SET, is_enabled(platform), ""


def self_heal(platform: Platform, exe: Path, args: tuple[str, ...] = ()) -> None:
    """While start at login is on, point it at this executable again: a moved app keeps starting (SPEC §3.7.5).

    Args:
        platform: The OS layer.
        exe: The running executable.
        args: Arguments before --autostart (from source: the script).

    Side effects:
        Rewrites the entry when it exists and the app isn't translocated; a failure is logged, never raised.
    """
    if not is_enabled(platform) or platform.run_location_problem(exe) is not None:
        return
    try:
        platform.autostart_enable(exe, args)
    except OSError as error:
        log.warning("start at login not refreshed: %s", type(error).__name__)


def facts(platform: Platform, exe: Path) -> tuple[dict, bool]:
    """`GET /api/app`'s `autostart` and whether the app runs translocated (SPEC §3.7.5; DR11, D97).

    Args:
        platform: The OS layer.
        exe: The running executable.

    Returns:
        (autostart_line's {"enabled", "available", "note"}, translocated).
    """
    translocated = platform.run_location_problem(exe) is not None
    return autostart_line(is_enabled(platform), translocated), translocated
