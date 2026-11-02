"""`GET /api/app`'s payload from the running shell (SPEC §3.7.4, D97): the facts, then `engine/app_view` texts."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ahsync import version
from ahsync.app.wow_paths import WowCheck, check
from ahsync.engine.app_view import app_state, update_not_checked, wow_check
from ahsync_app import autostart

if TYPE_CHECKING:                                   # shell.py imports this module
    from ahsync_app.shell import ShellState, ShellWorld


def app_payload(world: ShellWorld, state: ShellState, setup: bool, wow_path: str, api_key_set: bool) -> dict:
    """Build `GET /api/app` now (it is asked on every page).

    Args:
        world: The platform and the executable.
        state: The shell's state (this start's Addon line).
        setup: True while the setup server answers.
        wow_path: The configured folder, or in setup the detected one ("" when none).
        api_key_set: Whether a key is found; never the key.

    Returns:
        The payload of SPEC §3.7.4. Its only path is wow_path.
    """
    found = check(wow_path) if wow_path else WowCheck(False, False, 0)
    folder = wow_check(wow_path, found.has_interface, found.has_addons, found.accounts, creatable=setup)
    autostart_state, translocated = autostart.facts(world.platform, world.exe)
    addon = state.addon.line if state.addon is not None else None
    return app_state(version.VERSION, version.BUILT, "stable", setup, wow_path, folder, api_key_set, autostart_state,
                     True, update_not_checked(version.VERSION), translocated, addon)
