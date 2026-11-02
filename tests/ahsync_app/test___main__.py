"""Tests for ahsync_app/__main__.py: the bundle folder, the port probe and the argument (SPEC §3.7.1, §3.7.7)."""
from __future__ import annotations

import socket
from pathlib import Path

import ahsync_app.__main__ as app_main


def test_the_bundle_s_addon_folder_from_source_and_when_frozen(tmp_path):
    assert app_main.bundle_addon_dir(False, None, tmp_path) == tmp_path / "addon"
    assert app_main.bundle_addon_dir(True, str(tmp_path / "_internal"), tmp_path) == tmp_path / "_internal" / "addon"


def test_the_port_probe_sees_a_listening_dashboard():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert app_main.port_open(port) is True
    assert app_main.port_open(port) is False


def test_main_passes_autostart_to_the_shell_and_builds_the_real_world(monkeypatch):
    seen = {}
    monkeypatch.setattr(app_main, "start", lambda autostart, world: seen.update(autostart=autostart, world=world) or 0)
    assert app_main.main(["--autostart"]) == 0 and seen["autostart"] is True
    assert app_main.main([]) == 0 and seen["autostart"] is False
    world = seen["world"]
    assert world.bundle_addon_dir == Path(app_main.__file__).resolve().parents[1] / "addon"
    assert world.make_runtime(lambda: True).should_stop() is True
    assert app_main.real_world(None).out is not None
