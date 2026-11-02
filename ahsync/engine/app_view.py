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


RULESET_CHOICES = (("normal", "Normal"), ("pvp", "PvP"), ("rp", "RP"))       # config.RULESETS, with labels
FACTION_CHOICES = (("alliance", "Alliance"), ("horde", "Horde"))
NOT_DETECTED = "not found: enter the folder that holds Interface and WTF"
TRANSLOCATED_TEXT = ("AHSync is running from where it was downloaded. Drag AHSync to Applications and open it "
                     "from there, then Start at login can be turned on.")
BAD_LOCATION_TEXT = "Move AHSync to Applications, then turn it on again"     # POST /api/autostart's 400 (DR11)


def autostart_line(enabled: bool, translocated: bool) -> dict:
    """`GET /api/app`'s `autostart`: the toggle's state and why it can't be used (SPEC §3.7.5; DR11, D97).

    Args:
        enabled: Whether the login entry exists (the real state, read from the OS).
        translocated: Whether the app runs translocated (macOS), where Start at login is refused.

    Returns:
        {"enabled", "available", "note"}; the note is "not set: Move AHSync to Applications, then turn it on
        again" for a translocated app, else "".
    """
    note = f"not set: {BAD_LOCATION_TEXT}" if translocated else ""
    return {"enabled": enabled, "available": not translocated, "note": note}


def wow_check(wow_path: str, has_interface: bool, has_addons: bool, accounts: int, creatable: bool) -> dict:
    """The WoW folder's check pill and line (SPEC §3.7.4; DR5, DR6).

    Args:
        wow_path: The folder checked; "" when nothing was detected.
        has_interface: Whether `<wow_path>/Interface` exists.
        has_addons: Whether `<wow_path>/Interface/AddOns` exists.
        accounts: The `WTF/Account/*` folders.
        creatable: Setup's check: a folder with Interface but no AddOns passes, as the save creates AddOns.
            False for a configured folder, which the addon install needs complete.

    Returns:
        {"ok", "pill", "text"}: "found Interface/AddOns · 2 accounts", "not found: …" or
        "no Interface/AddOns under <wow_path>".
    """
    if not wow_path:
        return {"ok": False, "pill": "not found", "text": NOT_DETECTED}
    if not has_addons and not (creatable and has_interface):
        return {"ok": False, "pill": "not found", "text": f"no Interface/AddOns under {wow_path}"}
    counted = "no accounts yet" if accounts == 0 else f"{accounts} account{'s' if accounts > 1 else ''}"
    return {"ok": True, "pill": "found", "text": f"found Interface/AddOns · {counted}"}


def update_not_checked(version: str) -> dict:
    """The Update line before any check (SPEC §3.7.4, §3.7.8; DR2); M25's check replaces it.

    Args:
        version: The running version.

    Returns:
        {"text", "url", "pill", "restart", "class"}: "not checked for a dev build" or "not checked yet", muted.
    """
    text = "not checked for a dev build" if is_dev(version) else "not checked yet"
    return {"text": text, "url": None, "pill": None, "restart": False, "class": "muted"}


def app_state(version: str, built: str, channel: str, setup: bool, wow_path: str, check: dict,
              api_key_set: bool, autostart: dict, auto_update: bool, update: dict,
              location_problem: bool, addon: dict | None) -> dict:
    """`GET /api/app`: the app's own state for the setup page, the Status card and the top bar (SPEC §3.7.4, D97).

    Args:
        version: "MAJOR.MINOR.PATCH" ("0.0.0" from source).
        built: The build date "YYYY-MM-DD", or "".
        channel: "stable" or "beta".
        setup: True while the setup server answers.
        wow_path: The configured folder, or in setup the detected one ("" when none): the one path a response
            may carry (SPEC §3.5 Security).
        check: From wow_check.
        api_key_set: Whether a key is found (env var, key file or config); never the key itself.
        autostart: {"enabled", "available", "note"}.
        auto_update: The `auto_update` setting.
        update: The Update line, e.g. from update_not_checked.
        location_problem: Whether the app runs translocated (macOS).
        addon: This start's Addon line {"text", "class"}; None before any install (setup).

    Returns:
        The payload of SPEC §3.7.4, every text made here.
    """
    return {"version": version, "version_text": version_text(version, built, channel), "dev": is_dev(version),
            "channel": channel, "setup": setup, "wow_path": wow_path, "wow_check": check,
            "markets": {"rulesets": [list(c) for c in RULESET_CHOICES], "factions": [list(c) for c in FACTION_CHOICES]},
            "api_key_set": api_key_set, "autostart": autostart, "auto_update": auto_update, "update": update,
            "location_problem": TRANSLOCATED_TEXT if location_problem else None,
            "addon": addon if addon is not None else {"text": "not installed yet", "class": "muted"}}
