"""The texts of the AHSync app itself: its version, the tray's hover and the Addon line (SPEC §3.7).

Every text the app card, the tray and `GET /api/app` show is made here, so none is
built in JavaScript or in the shell (SPEC §8.5).
"""
from __future__ import annotations

import time

DEV_VERSION = "0.0.0"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def is_dev(version: str) -> bool:
    """Whether this is a source run, which never checks for or installs updates (SPEC §3.7.11).

    Args:
        version: `ahsync.version.VERSION`.

    Returns:
        True for "0.0.0".
    """
    return version == DEV_VERSION


def version_text(version: str, built: str, channel: str = "stable") -> str:
    """The version pill, e.g. "0.3.1 · built 2 Nov" (DR2; SPEC §3.7.10, §3.7.11).

    Args:
        version: "MAJOR.MINOR.PATCH".
        built: The build date "YYYY-MM-DD", or "" when unknown.
        channel: "stable" or "beta"; the beta channel adds "· beta".

    Returns:
        "dev build" for a source run; else the version, "beta" on the beta channel and the build day.
    """
    if is_dev(version):
        return "dev build"
    parts = [version] + (["beta"] if channel == "beta" else [])
    if built:
        _year, month, day = (int(p) for p in built.split("-"))
        parts.append(f"built {day} {MONTHS[month - 1]}")
    return " · ".join(parts)


def hover_text(version: str, synced_ms: int | None, offset_ms: int) -> str:
    """The tray icon's hover text, e.g. "AHSync 0.3.1 · synced 14:06" (DR2; SPEC §3.7.1).

    Args:
        version: "MAJOR.MINOR.PATCH".
        synced_ms: When the last hour was synced, UTC ms; None before the first sync.
        offset_ms: Local time minus UTC, ms.

    Returns:
        "AHSync dev build" for a source run; else the version and, once synced, the local sync time.
    """
    if is_dev(version):
        return "AHSync dev build"
    if synced_ms is None:
        return f"AHSync {version}"
    return f"AHSync {version} · synced {time.strftime('%H:%M', time.gmtime((synced_ms + offset_ms) // 1000))}"


def addon_text(outcome: str, wow_path: str = "", added_or_removed: int = 0, changed: int = 0) -> dict:
    """The Addon line of the app card and `GET /api/app`'s `addon` (SPEC §3.7.7; DR3, DR4, FM49).

    Args:
        outcome: "installed" (the install ran), "no_addons" (no Interface/AddOns under wow_path)
            or "not_writable" (the write probe failed).
        wow_path: The configured WoW folder, shown when it has no Interface/AddOns.
        added_or_removed: Files the install added or removed (WoW loads only files present at client start).
        changed: Existing files it replaced.

    Returns:
        {"text", "class"}: class "amber" when nothing could be installed, else "".
    """
    if outcome == "no_addons":
        return {"text": f"not installed: no Interface/AddOns under {wow_path}", "class": "amber"}
    if outcome == "not_writable":
        return {"text": "not installed: Interface/AddOns not writable", "class": "amber"}
    if added_or_removed:
        return {"text": "AHProfit updated · restart WoW to load it", "class": ""}
    if changed:
        return {"text": "AHProfit updated · /reload in game", "class": ""}
    return {"text": "AHProfit installed · up to date", "class": ""}
