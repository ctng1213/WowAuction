"""Tests for ahsync_app/platform/macos.py: the macOS answers, on any OS (SPEC §3.7.2)."""
from __future__ import annotations

from pathlib import Path

from ahsync_app.platform.base import TRANSLOCATED
from ahsync_app.platform.macos import MacPlatform


def test_DR1_the_per_user_folders_are_under_application_support():
    dirs = MacPlatform(Path("/Users/u")).app_dirs()
    assert dirs.data_dir.as_posix() == "/Users/u/Library/Application Support/AHSync/data"
    assert dirs.config_file.as_posix() == "/Users/u/Library/Application Support/AHSync/config.toml"


def test_the_wow_candidates_put_forever_s_folder_first():
    candidates = [p.as_posix() for p in MacPlatform(Path("/Users/u")).wow_candidates()]
    assert candidates[0] == "/Applications/World of Warcraft/_classic_beta_"
    assert all(c.startswith("/Applications/World of Warcraft/") for c in candidates)


def test_the_key_file_is_made_readable_by_the_user_only():
    calls = []
    MacPlatform(Path("/Users/u"), chmod=lambda path, mode: calls.append((path, mode))).restrict_file(Path("/k"))
    assert calls == [(Path("/k"), 0o600)]


def test_a_translocated_app_is_a_location_problem_and_applications_is_not():
    platform = MacPlatform(Path("/Users/u"))
    exe = "/private/var/folders/x1/abc/T/AppTranslocation/0F2E/d/AHSync.app/Contents/MacOS/AHSync"
    assert platform.run_location_problem(Path(exe)) == TRANSLOCATED
    assert platform.run_location_problem(Path("/Applications/AHSync.app/Contents/MacOS/AHSync")) is None
    assert platform.run_location_problem(Path("/Users/u/Applications/AHSync.app/Contents/MacOS/AHSync")) is None
