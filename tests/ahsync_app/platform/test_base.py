"""Tests for ahsync_app/platform/base.py: the protocol's members not yet built say which milestone builds them."""
from __future__ import annotations

from pathlib import Path

import pytest

from ahsync_app.platform.base import Platform
from ahsync_app.platform.macos import MacPlatform
from ahsync_app.platform.windows import WindowsPlatform

TEN = ["app_dirs", "wow_candidates", "autostart_enable", "autostart_disable", "autostart_enabled", "restrict_file",
       "run_location_problem", "update_asset_name", "verify_update", "apply_update"]   # SPEC §3.7.2


@pytest.mark.parametrize("call, milestone", [
    (lambda p: p.verify_update(Path("x")), "M27"), (lambda p: p.apply_update(Path("x"), Path("y")), "M27"),
])
def test_members_built_later_raise_naming_their_milestone(call, milestone):
    for platform in (WindowsPlatform({}, Path("C:/Users/u")), MacPlatform(Path("/Users/u"))):
        with pytest.raises(NotImplementedError, match=milestone):
            call(platform)


def test_the_base_answers_nothing_itself():
    base = Platform()
    for call in (base.app_dirs, base.wow_candidates, lambda: base.restrict_file(Path("k")),
                 lambda: base.run_location_problem(Path("x")), lambda: base.autostart_enable(Path("x")),
                 base.autostart_disable, base.autostart_enabled, lambda: base.update_asset_name("0.4.0")):
        with pytest.raises(NotImplementedError):
            call()


def test_the_protocol_has_ten_members():
    members = [n for n, v in vars(Platform).items() if callable(v) and not n.startswith("_")]
    assert sorted(members) == sorted(TEN)
