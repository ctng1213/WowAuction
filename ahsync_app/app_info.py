"""`GET /api/app`'s payload from the running shell (SPEC §3.7.4, D97): the facts, then `engine/app_view` texts."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ahsync import version
from ahsync.app.update_check import read_state
from ahsync.app.wow_paths import WowCheck, check
from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine.app_view import app_state, update_not_checked, wow_check
from ahsync.engine.updates import update_text
from ahsync_app import autostart

if TYPE_CHECKING:                                   # shell.py imports this module
    from ahsync_app.shell import ShellState, ShellWorld


def app_payload(world: ShellWorld, state: ShellState, setup: bool, wow_path: str, api_key_set: bool,
                db: Database | None = None, config: Config | None = None) -> dict:
    """Build `GET /api/app` now (it is asked on every page).

    Args:
        world: The platform, the executable and the clock.
        state: The shell's state (this start's Addon line).
        setup: True while the setup server answers.
        wow_path: The configured folder, or in setup the detected one ("" when none).
        api_key_set: Whether a key is found; never the key.
        db: The dashboard's database, for the update state; None in setup mode ("not checked yet").
        config: The dashboard's running settings (the channel and `auto_update` the toggles set); None: defaults.

    Returns:
        The payload of SPEC §3.7.4. Its only path is wow_path.
    """
    settings = config or Config()
    found = check(wow_path) if wow_path else WowCheck(False, False, 0)
    folder = wow_check(wow_path, found.has_interface, found.has_addons, found.accounts, creatable=setup)
    autostart_state, translocated = autostart.facts(world.platform, world.exe)
    addon = state.addon.line if state.addon is not None else None
    if db is None:
        update = update_not_checked(version.VERSION)
    else:
        now = world.clock.now_ms()
        update = update_text(read_state(db, version.VERSION, settings), now, world.clock.local_offset_ms(now))
    dirs = world.platform.app_dirs()
    return app_state(version.VERSION, version.BUILT, settings.update_channel, setup, wow_path, folder, api_key_set,
                     autostart_state, settings.auto_update, update, translocated, addon,
                     dirs.shown or dirs.data_dir.parent.name)
