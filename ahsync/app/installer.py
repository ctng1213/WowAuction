"""Automatic updates, the install half: install a verified download at the next start or on Restart to update, and
settle the result (SPEC §3.7.9; DR22, DR23, FM54, FM55, FM78).

The download half (`app/updater.py`) leaves a verified asset in `<data>/updates/` and `update.downloaded` in meta.
At the next start, before `run()` (or when Restart to update stops the app), `install` marks it `update.pending` and
hands it to the platform, which starts the installer or the swap helper detached; the app then exits. A plain quit,
the stop file (the uninstaller, an installer run by hand, `tools/deploy.py --restart`) never installs (FM78). Once the
new version runs, the update job's `settle` compares `update.pending` with it: the same version means it worked, any
other that it failed, and either way `updates/` is emptied.
"""
from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from ahsync.app.update_check import read_meta, read_state, write_meta
from ahsync.app.updater import UPDATES_DIR
from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine.updates import INSTALL_FAILED, newer, ready

log = logging.getLogger("ahsync.update")


def waiting_install(db: Database, version: str, config: Config, updates_dir: Path,
                    asset_name: Callable[[str], str]) -> Path | None:
    """The verified asset to install, if one is waiting and may install itself (SPEC §3.7.9).

    Args:
        db: The database (`update.*` in meta).
        version: The running version.
        config: The channel and `auto_update` (the same test as the Update line's "installs at the next start").
        updates_dir: `<data>/updates/`, the only folder an install is taken from.
        asset_name: Version -> this OS's asset, as the download named it.

    Returns:
        The asset's path; None while the last install is unsettled (`update.pending`), when nothing newer than the
        running version waits, the channel no longer offers it (DR30), the app can't replace itself where it is
        (FM54), or the stored path is not that asset in `updates/` (the database is not trusted with a path).
    """
    meta = read_meta(db)
    if meta.get("pending") is not None:
        return None
    state = read_state(db, version, config)
    if not ready(state) or state.cant_replace or not newer(state.tag, version) or state.downloaded is None:
        return None
    expected = updates_dir / asset_name(state.downloaded)
    stored = meta.get("downloaded_path")
    if stored is None or Path(stored) != expected or not expected.exists():
        return None
    return expected


def install(db: Database, version: str, config: Config, updates_dir: Path, asset_name: Callable[[str], str],
            apply: Callable[[Path], None]) -> bool:
    """Start installing the waiting update; the caller exits right after (DR22, DR23).

    Args:
        db: The database.
        version: The running version.
        config: As `waiting_install`.
        updates_dir: As `waiting_install`.
        asset_name: As `waiting_install`.
        apply: The platform's `apply_update` for the running executable: starts the installer or the swap helper.

    Returns:
        True when the install was started (the caller exits); False when nothing waits or it couldn't start.

    Side effects:
        `update.pending` is set before `apply` and cleared again when `apply` fails in any way.
    """
    path = waiting_install(db, version, config, updates_dir, asset_name)
    if path is None:
        return False
    target = read_state(db, version, config).downloaded
    write_meta(db, {"pending": target})
    try:
        apply(path)
    except Exception as error:                      # never a traceback at start or quit: the app runs on (M1)
        log.warning("the update to %s could not start: %s: %s", target, type(error).__name__, error)
        write_meta(db, {"pending": None})
        return False
    log.info("installing the update to %s", target)
    return True


def settle(db: Database, data_dir: Path, version: str, now_ms: int, forget_previous: Callable[[], None]) -> str | None:
    """Record how the last install went and empty `updates/` (DR22, FM55).

    Run by the update job at start (and then every time, cheaply): with `update.pending` set, the running version
    says whether the install worked. A file still held by the finishing installer (Windows) is left and removed at a
    later start (DR22).

    Args:
        db: The database.
        data_dir: data/, whose `updates/` folder is emptied.
        version: The running version.
        now_ms: Now, UTC ms.
        forget_previous: The platform's cleanup after an update that worked (macOS: `AHSync.app.previous`).

    Returns:
        "updated" or "failed" when a pending install was settled; None otherwise.
    """
    meta = read_meta(db)
    pending = meta.get("pending")
    folder = data_dir / UPDATES_DIR
    if pending is None:
        if meta.get("downloaded") is None:
            empty(folder)                          # leftovers a finishing installer still held last time (DR22)
        return None
    cleared = {"pending": None, "downloaded": None, "downloaded_path": None, "cant_replace": None}
    if pending == version:
        write_meta(db, {**cleared, "updated_to": pending, "updated_ms": now_ms, "download_failed": None})
        log.info("updated to AHSync %s", version)
        forget_previous()
        outcome = "updated"
    else:
        write_meta(db, {**cleared, "download_failed": INSTALL_FAILED, "download_tried_ms": now_ms})
        log.warning("the update to %s failed; still on %s", pending, version)
        outcome = "failed"
    empty(folder)
    return outcome


def empty(folder: Path) -> None:
    """Remove every file and folder in `updates/`, skipping what is still in use.

    Args:
        folder: `<data>/updates/`.
    """
    if not folder.is_dir():
        return
    for item in sorted(folder.iterdir()):
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        except OSError:
            log.info("an update file is still in use: %s", item.name)
