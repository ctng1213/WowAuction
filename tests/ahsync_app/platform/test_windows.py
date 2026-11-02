"""Tests for ahsync_app/platform/windows.py: the Windows answers, on any OS (SPEC §3.7.2)."""
from __future__ import annotations

from pathlib import Path

from ahsync_app.platform.windows import WindowsPlatform

LOCAL = r"C:\Users\u\AppData\Local"


def test_DR1_the_per_user_folders_are_under_localappdata():
    dirs = WindowsPlatform({"LOCALAPPDATA": LOCAL}, Path("C:/Users/u")).app_dirs()
    assert str(dirs.data_dir) == r"C:\Users\u\AppData\Local\AHSync\data"
    assert str(dirs.config_file) == r"C:\Users\u\AppData\Local\AHSync\config.toml"


def test_without_localappdata_the_profile_s_appdata_local_is_used():
    dirs = WindowsPlatform({}, Path(r"C:\Users\u")).app_dirs()
    assert str(dirs.data_dir) == r"C:\Users\u\AppData\Local\AHSync\data"


def test_the_wow_candidates_put_forever_s_folder_first():
    candidates = [str(p) for p in WindowsPlatform({}, Path("C:/Users/u")).wow_candidates()]
    assert candidates[0] == r"C:\Program Files (x86)\World of Warcraft\_classic_beta_"
    assert all(c.startswith("C:\\Program Files (x86)\\World of Warcraft\\") for c in candidates)
    assert len(set(candidates)) == len(candidates)


def test_windows_needs_no_permission_change_and_has_no_translocation(tmp_path):
    key = tmp_path / "api_key"
    key.write_text("x", encoding="utf-8")
    platform = WindowsPlatform({}, tmp_path)
    platform.restrict_file(key)
    assert key.read_text(encoding="utf-8") == "x"
    assert platform.run_location_problem(Path(r"C:\Users\u\Downloads\AHSync\AHSync.exe")) is None
