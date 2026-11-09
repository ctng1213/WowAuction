"""Tests for ahsync/mcp/claude_config.py: finding Claude Desktop's config, read-only (SPEC §3.8.1, MCP18-MCP21)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ahsync.mcp.claude_config import find_claude_config, read_config

STORE = "Claude_pzs8sxrjxfjjc"


def windows_env(tmp_path: Path) -> dict[str, str]:
    """A Windows environment rooted in tmp_path; nothing exists yet."""
    return {"APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(tmp_path / "Local")}


def store_folder(tmp_path: Path, package: str = STORE) -> Path:
    """The Store build's copy of Claude's folder, created."""
    folder = tmp_path / "Local" / "Packages" / package / "LocalCache" / "Roaming" / "Claude"
    folder.mkdir(parents=True)
    return folder


def appdata_folder(tmp_path: Path) -> Path:
    """%APPDATA%\\Claude, created."""
    folder = tmp_path / "Roaming" / "Claude"
    folder.mkdir(parents=True)
    return folder


def touch(path: Path, mtime_s: int, text: str = "x") -> None:
    """Write a file and set its modification time."""
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime_s, mtime_s))


def test_MCP18_appdata_only(tmp_path):
    folder = appdata_folder(tmp_path)
    found = find_claude_config(windows_env(tmp_path), tmp_path, "win32")
    assert (found.system, found.state, found.path, found.other_folder) == (
        "windows", "no_file", str(folder / "claude_desktop_config.json"), None)


def test_MCP18_store_only(tmp_path):
    folder = store_folder(tmp_path)
    found = find_claude_config(windows_env(tmp_path), tmp_path, "win32")
    assert (found.state, found.path) == ("no_file", str(folder / "claude_desktop_config.json"))


def test_MCP18_both_the_one_holding_the_config_wins_and_the_other_is_named(tmp_path):
    appdata = appdata_folder(tmp_path)
    touch(appdata / "logs.txt", 2_000_000_000)                       # newer, but holds no config
    store = store_folder(tmp_path)
    touch(store / "claude_desktop_config.json", 1_000_000_000, "{}")
    found = find_claude_config(windows_env(tmp_path), tmp_path, "win32")
    assert (found.path, found.other_folder) == (str(store / "claude_desktop_config.json"), str(appdata))


def test_MCP18_both_without_a_config_the_one_used_last_wins_and_a_tie_is_appdata(tmp_path):
    appdata, store = appdata_folder(tmp_path), store_folder(tmp_path)
    touch(appdata / "a.log", 1_000_000_000)
    touch(store / "b.log", 1_500_000_000)
    found = find_claude_config(windows_env(tmp_path), tmp_path, "win32")
    assert found.path == str(store / "claude_desktop_config.json")
    touch(store / "b.log", 1_000_000_000)
    assert find_claude_config(windows_env(tmp_path), tmp_path, "win32").path == str(
        appdata / "claude_desktop_config.json")


def test_MCP18_package_names_off_the_pattern_are_ignored(tmp_path):
    store_folder(tmp_path, "Claude_x")
    store_folder(tmp_path, "Other_pzs8sxrjxfjjc")
    store_folder(tmp_path, "Claude_PZS8SXRJXFJJC")
    found = find_claude_config(windows_env(tmp_path), tmp_path, "win32")
    assert (found.state, found.path) == ("not_found", None)


def test_MCP18_a_store_folder_that_links_outside_packages_is_ignored(tmp_path):
    outside = tmp_path / "Elsewhere" / "LocalCache" / "Roaming" / "Claude"
    outside.mkdir(parents=True)
    link = tmp_path / "Local" / "Packages" / STORE
    link.parent.mkdir(parents=True)
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(tmp_path / "Elsewhere"), str(link))   # a junction needs no privileges
        else:
            link.symlink_to(tmp_path / "Elsewhere", target_is_directory=True)
    except OSError as error:
        pytest.skip(f"can't make a link here: {error}")
    assert find_claude_config(windows_env(tmp_path), tmp_path, "win32").state == "not_found"


def test_MCP18_macos_and_other_systems(tmp_path):
    folder = tmp_path / "Library" / "Application Support" / "Claude"
    folder.mkdir(parents=True)
    found = find_claude_config({}, tmp_path, "darwin")
    assert (found.system, found.state, found.path) == ("macos", "no_file", str(folder / "claude_desktop_config.json"))
    assert find_claude_config({}, tmp_path, "linux").state == "not_found"
    assert find_claude_config({}, tmp_path, "win32").state == "not_found"           # no APPDATA, no LOCALAPPDATA


def test_MCP19_each_kind_of_file(tmp_path):
    path = tmp_path / "claude_desktop_config.json"
    cases = [("", "no_file"), ("  \n", "no_file"), ('{"preferences": {}}', "no_servers"),
             ('{"mcpServers": {"other": {"command": "x"}}}', "no_entry"),
             ('{"mcpServers": []}', "invalid"), ("[1]", "invalid")]
    for text, state in cases:
        path.write_text(text, encoding="utf-8")
        assert read_config(path).state == state, text
    path.write_text('{\n  "mcpServers": {\n', encoding="utf-8")
    cut = read_config(path)
    assert (cut.state, cut.error_line) == ("invalid", 3)
    path.write_text('{"mcpServers": {"ahsync": {"command": "C:\\\\Old\\\\ahsync-mcp.exe", "args": ["--mcp"]}}}',
                    encoding="utf-8")
    assert (read_config(path).state, read_config(path).entry) == ("entry", ("C:\\Old\\ahsync-mcp.exe", "--mcp"))
    path.write_text('{"mcpServers": {"ahsync": {"command": 5}}}', encoding="utf-8")
    assert read_config(path).entry == ()
    path.write_bytes(b"\xef\xbb\xbf" + b'{"mcpServers": {}}')                     # Notepad's UTF-8 with a BOM
    assert read_config(path).state == "no_entry"
    path.write_bytes(b'{"a": "\xff"}')
    assert (read_config(path).state, read_config(path).reason) == ("unreadable", "it isn't UTF-8 text")
    path.write_bytes(b" " * 1_000_001)
    assert (read_config(path).state, read_config(path).reason) == ("unreadable", "it's over 1 MB")
    assert read_config(tmp_path / "missing.json").state == "no_file"


def test_MCP20_nothing_but_the_ahsync_command_leaves_the_finder(tmp_path):
    folder = appdata_folder(tmp_path)
    (folder / "claude_desktop_config.json").write_text(json.dumps({"mcpServers": {
        "github": {"command": "gh-mcp", "env": {"TOKEN": "ghp_secret123"}},
        "ahsync": {"command": "C:\\Old\\ahsync-mcp.exe", "env": {"X": "hidden"}}}}), encoding="utf-8")
    found = find_claude_config(windows_env(tmp_path), tmp_path, "win32")
    assert found.entry == ("C:\\Old\\ahsync-mcp.exe",)
    assert not any(secret in repr(found) for secret in ("github", "gh-mcp", "TOKEN", "ghp_secret123", "hidden"))


def test_MCP21_finding_changes_nothing_in_claudes_folder(tmp_path):
    folder = appdata_folder(tmp_path)
    store_folder(tmp_path)
    config = folder / "claude_desktop_config.json"
    touch(config, 1_000_000_000, '{"mcpServers": {"other": {"command": "x"}}}')
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()}
    dirs_before = sorted(p for p in tmp_path.rglob("*"))
    for _ in range(2):
        assert find_claude_config(windows_env(tmp_path), tmp_path, "win32").state == "no_entry"
    assert sorted(p for p in tmp_path.rglob("*")) == dirs_before
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()} == before
    config.write_text('{"mcpServers": {"ahsync": {"command": "x"}}}', encoding="utf-8")
    assert find_claude_config(windows_env(tmp_path), tmp_path, "win32").state == "entry"     # nothing cached


def test_MCP19_files_that_break_the_parser_read_as_invalid_never_raise(tmp_path):
    path = tmp_path / "claude_desktop_config.json"
    for text in ('{"n": ' + "9" * 5000 + "}", "[" * 200_000, '{"a":' * 100_000):
        path.write_text(text, encoding="utf-8")
        assert read_config(path).state == "invalid"
    path.write_text(r'{"mcpServers": {"ahsync": {"command": "\ud800"}}}', encoding="utf-8")  # valid JSON: a lone surrogate
    assert (read_config(path).state, read_config(path).entry) == ("entry", ())       # never shown


def test_MCP19_empty_objects_are_reported_so_no_comma_is_asked(tmp_path):
    path = tmp_path / "claude_desktop_config.json"
    for text, state, empty in (("", "no_file", True), ("{}", "no_servers", True), ('{"a": 1}', "no_servers", False),
                               ('{"mcpServers": {}}', "no_entry", True),
                               ('{"mcpServers": {"x": {}}}', "no_entry", False)):
        path.write_text(text, encoding="utf-8")
        assert (read_config(path).state, read_config(path).empty) == (state, empty), text
    assert read_config(tmp_path / "missing.json").empty is False


def test_MCP19_folders_and_files_it_may_not_open(tmp_path, monkeypatch):
    folder = appdata_folder(tmp_path)
    store_folder(tmp_path)
    (folder / "claude_desktop_config.json").write_text("{}", encoding="utf-8")

    def refuse(*_args, **_kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(Path, "open", refuse)
    found = find_claude_config(windows_env(tmp_path), tmp_path, "win32")
    assert (found.state, found.reason) == ("unreadable", "it couldn't be opened")
    monkeypatch.setattr(Path, "is_dir", refuse)
    assert find_claude_config(windows_env(tmp_path), tmp_path, "win32").state == "not_found"
    monkeypatch.undo()
    monkeypatch.setattr(Path, "is_file", refuse)
    assert find_claude_config(windows_env(tmp_path), tmp_path, "win32").state == "unreadable"
    monkeypatch.undo()
    monkeypatch.setattr(os, "scandir", refuse)                     # Packages can't be listed: only %APPDATA%
    found = find_claude_config(windows_env(tmp_path), tmp_path, "win32")
    assert (found.path, found.other_folder) == (str(folder / "claude_desktop_config.json"), None)


def data_folder(tmp_path: Path, name: str = "claude_pzs8sxrjxfjjc") -> Path:
    r"""The Store build's data folder, %LOCALAPPDATA%\Claude-Data\<name>, created."""
    folder = tmp_path / "Local" / "Claude-Data" / name
    folder.mkdir(parents=True)
    return folder


def test_MCP22_claude_data_wins_over_a_newer_file_made_by_mistake(tmp_path):
    data = data_folder(tmp_path)
    touch(data / "claude_desktop_config.json", 1_000_000_000, '{"coworkUserFilesPath": "x", "preferences": {}}')
    store = store_folder(tmp_path)
    touch(store / "claude_desktop_config.json", 2_000_000_000, '{"mcpServers": {"ahsync": {"command": "a"}}}')
    found = find_claude_config(windows_env(tmp_path), tmp_path, "win32")
    assert (found.state, found.path, found.other_folder) == (
        "no_servers", str(data / "claude_desktop_config.json"), str(store))


def test_MCP22_claude_data_wins_without_a_config_and_its_case_does_not_matter(tmp_path):
    data = data_folder(tmp_path, "Claude_PZS8SXRJXFJJC")
    touch(appdata_folder(tmp_path) / "claude_desktop_config.json", 2_000_000_000, "{}")
    found = find_claude_config(windows_env(tmp_path), tmp_path, "win32")
    assert (found.state, found.path) == ("no_file", str(data / "claude_desktop_config.json"))


def test_MCP22_names_off_the_pattern_and_links_outside_are_ignored(tmp_path):
    data_folder(tmp_path, "claude_x")
    assert find_claude_config(windows_env(tmp_path), tmp_path, "win32").state == "not_found"
    outside = tmp_path / "Elsewhere"
    outside.mkdir()
    link = tmp_path / "Local" / "Claude-Data" / "claude_pzs8sxrjxfjjc"
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"can't make a link here: {error}")
    assert find_claude_config(windows_env(tmp_path), tmp_path, "win32").state == "not_found"


def test_MCP23_mcpservers_inside_another_section_is_named_and_nothing_else_of_it(tmp_path):
    path = tmp_path / "claude_desktop_config.json"
    path.write_text(json.dumps({"coworkUserFilesPath": r"C:\Users\you\Claude", "preferences": {
        "sidebarMode": "chat", "mcpServers": {"ahsync": {"command": r"C:\A.exe"}}}}), encoding="utf-8")
    found = read_config(path)
    assert (found.state, found.section, found.entry) == ("nested", "preferences", ())
    assert not any(text in repr(found) for text in ("sidebarMode", "chat", "coworkUserFilesPath", "A.exe"))
    path.write_text(json.dumps({"preferences": {"mcpServers": {}}, "mcpServers": {"ahsync": {"command": r"C:\B"}}}),
                    encoding="utf-8")
    assert (read_config(path).state, read_config(path).entry) == ("entry", (r"C:\B",))
    path.write_text(r'{"\ud800": {"mcpServers": {}}}', encoding="utf-8")      # a name that can't be shown
    assert read_config(path).state == "no_servers"
