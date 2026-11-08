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
