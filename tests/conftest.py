"""Shared test guards (M27): no test may start a real process through the platform layer.

The app's install path starts installers and helpers detached (`ahsync_app/platform/base.py`); a test that forgot to
inject its recording `Processes` would start a real one (an empty `install.log` once appeared in the repo root that
way). Every test gets a guard that makes the real runner and spawner fail loudly instead.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def no_real_processes(monkeypatch):
    """Make the platform's real command runner and detached spawn raise in every test."""
    from ahsync_app.platform import base

    def forbidden(*args, **kwargs):
        """A real process was about to start."""
        raise AssertionError(f"a test reached a real process start: {args!r}; inject a recording Processes")

    monkeypatch.setattr(base, "run_command", forbidden)
    monkeypatch.setattr(base, "spawn_detached", forbidden)


@pytest.fixture(autouse=True)
def no_real_wow_folder(monkeypatch):
    """Make every write into the real default WoW folder fail loudly (2026-11-20).

    `Config()` defaults `wow_path` to the real `_forever_` install, so a test that runs an export or an addon install
    with a default config wrote its fixture market into the user's game: BZ43 did on every full run from 2026-11-08,
    and the addon showed "data 777 d old". Tests point `wow_path` at `tmp_path` instead.
    """
    from pathlib import Path

    from ahsync.config import Config
    from ahsync.export import addon_files, addon_install

    real = Path(Config().wow_path).resolve()

    def checked(name, fn):
        """Wrap a writer so a path under the real WoW folder raises."""
        def wrapper(path, *args, **kwargs):
            """Refuse the real WoW folder; otherwise write as usual."""
            target = Path(path).resolve()
            if target == real or real in target.parents:
                raise AssertionError(f"a test reached the real WoW folder through {name}: {target}; "
                                     "set Config.wow_path to tmp_path")
            return fn(path, *args, **kwargs)
        return wrapper

    monkeypatch.setattr(addon_files, "write_atomic", checked("write_atomic", addon_files.write_atomic))
    monkeypatch.setattr(addon_install, "can_write", checked("can_write", addon_install.can_write))
    original_copy = addon_install.copy_atomically
    monkeypatch.setattr(addon_install, "copy_atomically",
                        lambda src, dst, *a, **k: checked("copy_atomically", lambda d, *b, **c: original_copy(src, d, *b, **c))(dst, *a, **k))
