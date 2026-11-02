"""Tests for ahsync_app/platform/__init__.py: current() picks the OS's platform."""
from __future__ import annotations

from pathlib import Path

import pytest

from ahsync_app import platform
from ahsync_app.platform.macos import MacPlatform
from ahsync_app.platform.windows import WindowsPlatform


def test_current_picks_the_platform_from_sys_platform():
    assert isinstance(platform.current("win32", {}, Path("C:/Users/u")), WindowsPlatform)
    assert isinstance(platform.current("darwin", {}, Path("/Users/u")), MacPlatform)
    assert platform.current().name in ("windows", "macos")


def test_any_other_os_is_refused_with_the_source_run_named():
    with pytest.raises(RuntimeError, match="python -m ahsync"):
        platform.current("linux", {}, Path("/home/u"))
