"""Tests for ahsync_app/app_info.py: GET /api/app's payload from the shell's facts (SPEC §3.7.4, D97)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ahsync_app import app_info
from ahsync_app.platform.base import TRANSLOCATED, Platform
from ahsync_app.shell import ShellState


class Facts(Platform):
    """A platform answering only what the payload asks."""

    def __init__(self, translocated: bool = False, enabled: bool | None = None) -> None:
        self.translocated = translocated
        self.enabled = enabled

    def run_location_problem(self, exe: Path) -> str | None:
        return TRANSLOCATED if self.translocated else None

    def autostart_enabled(self) -> bool:
        if self.enabled is None:
            return super().autostart_enabled()      # the base: no answer, read as off
        return self.enabled


@dataclass
class World:
    """The two fields app_info reads."""

    platform: Platform
    exe: Path = Path("/Applications/AHSync.app/Contents/MacOS/AHSync")


def test_D97_in_setup_mode_with_a_detected_folder(tmp_path):
    (tmp_path / "Interface" / "AddOns").mkdir(parents=True)
    for name in ("A1", "A2"):
        (tmp_path / "WTF" / "Account" / name).mkdir(parents=True)
    payload = app_info.app_payload(World(Facts()), ShellState(False), True, str(tmp_path), False)
    assert payload["setup"] is True and payload["wow_path"] == str(tmp_path) and payload["api_key_set"] is False
    assert payload["wow_check"]["text"] == "found Interface/AddOns · 2 accounts"
    assert payload["version_text"] == "dev build" and payload["update"]["text"] == "not checked for a dev build"
    assert payload["autostart"] == {"enabled": False, "available": True, "note": ""}
    assert payload["location_problem"] is None and payload["addon"]["text"] == "not installed yet"
    paths = [v for v in json.dumps(payload).split('"') if "/" in v or "\\" in v]
    assert all(str(tmp_path).replace("\\", "\\\\") in v or v.startswith("found Interface/AddOns") for v in paths)


def test_D97_nothing_detected_translocated_and_start_at_login_on():
    payload = app_info.app_payload(World(Facts(translocated=True, enabled=True)), ShellState(False), True, "", False)
    assert payload["wow_path"] == "" and payload["wow_check"]["pill"] == "not found"
    assert payload["autostart"] == {"enabled": True, "available": False,
                                    "note": "not set: Move AHSync to Applications, then turn it on again"}
    assert payload["location_problem"].startswith("AHSync is running from where it was downloaded")
