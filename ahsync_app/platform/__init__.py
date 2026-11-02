"""The thin OS layer (SPEC §3.7.2): `current()` is the one place that reads `sys.platform`."""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from ahsync_app.platform.base import Platform
from ahsync_app.platform.macos import MacPlatform
from ahsync_app.platform.windows import WindowsPlatform


def current(sys_platform: str = sys.platform, env: Mapping[str, str] = os.environ,
            home: Path | None = None) -> Platform:
    """The platform AHSync runs on.

    Args:
        sys_platform: `sys.platform` ("win32", "darwin").
        env: Environment variables.
        home: The user's home folder; None reads Path.home().

    Returns:
        The Windows or macOS platform.

    Raises:
        RuntimeError: On any other OS (Linux is a v1 non-goal, SPEC §3.7.14).
    """
    home = Path.home() if home is None else home
    if sys_platform == "win32":
        return WindowsPlatform(env, home)
    if sys_platform == "darwin":
        return MacPlatform(home)
    raise RuntimeError(f"the AHSync app runs on Windows and macOS, not {sys_platform}; use python -m ahsync")
