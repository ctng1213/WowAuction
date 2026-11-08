"""Tests for ahsync_app/platform/base.py: the protocol's members, and what the base answers itself."""
from __future__ import annotations

from pathlib import Path

import pytest

from ahsync_app.platform.base import Platform

MEMBERS = ["app_dirs", "wow_candidates", "autostart_enable", "autostart_disable", "autostart_enabled",
           "restrict_file", "run_location_problem", "update_asset_name", "verify_update", "can_replace", "apply_update",
           "forget_previous", "ca_bundle"]   # SPEC §3.7.2


def test_the_base_answers_nothing_itself():
    base = Platform()
    for call in (base.app_dirs, base.wow_candidates, lambda: base.restrict_file(Path("k")),
                 lambda: base.run_location_problem(Path("x")), lambda: base.autostart_enable(Path("x")),
                 base.autostart_disable, base.autostart_enabled, lambda: base.update_asset_name("0.4.0"), base.ca_bundle,
                 lambda: base.verify_update(Path("u"), Path("x")), lambda: base.apply_update(Path("u"), Path("x"))):
        with pytest.raises(NotImplementedError):
            call()
    assert base.can_replace(Path("x")) is True and base.forget_previous(Path("x")) is None   # Windows' answers too


def test_the_protocol_has_its_thirteen_members():
    members = [n for n, v in vars(Platform).items() if callable(v) and not n.startswith("_")]
    assert sorted(members) == sorted(MEMBERS)
