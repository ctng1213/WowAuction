"""Tests for ahsync_app/platform/windows.py: the Windows answers, on any OS (SPEC §3.7.2)."""
from __future__ import annotations

from pathlib import Path

import pytest

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
    assert candidates == [r"C:\Program Files (x86)\World of Warcraft\_forever_", r"C:\Program Files\World of Warcraft\_forever_",
                          r"C:\Program Files (x86)\World of Warcraft\_classic_beta_",
                          r"C:\Program Files\World of Warcraft\_classic_beta_"]   # Forever under either root first
    assert len(set(candidates)) == len(candidates)


def test_windows_needs_no_permission_change_and_has_no_translocation(tmp_path):
    key = tmp_path / "api_key"
    key.write_text("x", encoding="utf-8")
    platform = WindowsPlatform({}, tmp_path)
    platform.restrict_file(key)
    assert key.read_text(encoding="utf-8") == "x"
    assert platform.run_location_problem(Path(r"C:\Users\u\Downloads\AHSync\AHSync.exe")) is None


class FakeRunKey:
    """A Run key in memory that records each write."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.writes = 0

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, data: str) -> None:
        self.writes += 1
        self.values[name] = data

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


def test_DR9_start_at_login_is_the_run_value_with_the_quoted_exe_and_autostart():
    run_key = FakeRunKey()
    platform = WindowsPlatform({}, Path("C:/Users/u"), run_key)
    exe = Path(r"C:\Users\u\AppData\Local\Programs\AHSync\AHSync.exe")
    platform.autostart_enable(exe)
    assert run_key.values == {"AHSync": r'"C:\Users\u\AppData\Local\Programs\AHSync\AHSync.exe" --autostart'}
    assert platform.autostart_enabled()
    platform.autostart_disable()
    assert run_key.values == {} and not platform.autostart_enabled()
    platform.autostart_disable()                       # nothing to remove: fine
    platform.autostart_enable(exe)
    platform.autostart_enable(exe)
    assert list(run_key.values) == ["AHSync"]


def test_the_real_run_key_is_used_by_default():
    from ahsync_app.platform.windows import WinRegRunKey
    assert isinstance(WindowsPlatform({}, Path("C:/Users/u")).run_key, WinRegRunKey)


def test_DR9_from_source_the_run_value_quotes_pythonw_and_the_script():
    run_key = FakeRunKey()
    WindowsPlatform({}, Path("C:/Users/u"), run_key).autostart_enable(
        Path(r"C:\Python312\pythonw.exe"), (r"C:\Dev\WowAuction\ahsync_app\__main__.py",))
    assert run_key.values == {"AHSync": r'"C:\Python312\pythonw.exe" "C:\Dev\WowAuction\ahsync_app\__main__.py" --autostart'}


def test_DR20_windows_takes_the_setup_exe():
    assert WindowsPlatform({}, Path("C:/Users/u")).update_asset_name("0.4.0") == "AHSync-0.4.0-windows-x64-setup.exe"
    assert WindowsPlatform({"LOCALAPPDATA": LOCAL}, Path("C:/Users/u")).app_dirs().shown == r"%LOCALAPPDATA%\AHSync"


def test_FM58_windows_needs_no_ca_bundle():
    assert WindowsPlatform({}, Path("C:/Users/u")).ca_bundle() is None   # Python reads the Windows store


def test_DR22_the_silent_installer_starts_detached_and_relaunches_the_old_app_when_it_fails():
    from ahsync_app.platform.base import Processes
    spawned = []
    platform = WindowsPlatform({"LOCALAPPDATA": LOCAL}, Path("C:/Users/u"), run_key=object(),
                               processes=Processes(lambda args: None, lambda args, log=None: spawned.append((args, log)), 1))
    setup = Path(r"C:\Users\u\AppData\Local\AHSync\data\updates\AHSync-0.4.0-windows-x64-setup.exe")
    exe = Path(r"C:\Users\u\AppData\Local\Programs\AHSync\AHSync.exe")
    assert platform.verify_update(setup, exe) is None                       # the hash is the check: unsigned
    platform.apply_update(setup, exe)
    assert spawned == [(f'cmd /d /s /c ""{setup}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCLOSEAPPLICATIONS '
                        f'|| start "" "{exe}""', setup.parent / "install.log")]
    assert platform.can_replace(exe) is True                                  # a per-user install
    for odd in (Path(r"C:\Users\a%PATH%b\setup.exe"), Path('C:\\Users\\a"b\\setup.exe')):   # cmd would expand or end it
        with pytest.raises(OSError):
            platform.apply_update(odd, exe)
    assert len(spawned) == 1


def test_the_ai_assistant_bridge_is_the_console_exe_next_to_ahsync_exe():
    exe = Path(r"C:\Users\u\AppData\Local\Programs\AHSync\AHSync.exe")
    assert WindowsPlatform({}, Path(r"C:\Users\u")).bridge_command(exe) == (
        r"C:\Users\u\AppData\Local\Programs\AHSync\ahsync-mcp.exe",)
