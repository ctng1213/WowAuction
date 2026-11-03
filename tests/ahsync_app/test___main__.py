"""Tests for ahsync_app/__main__.py: the bundle folder, the port probe and the argument (SPEC §3.7.1, §3.7.7)."""
from __future__ import annotations

import socket
import ssl
from pathlib import Path

import ahsync_app.__main__ as app_main
from ahsync.web.server import AppHooks
from ahsync_app.tray import PystrayIcon
from ahsync_app.window import PyWebview


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
    hooks = AppHooks(dict, lambda enabled: ("set", enabled))
    runtime = world.make_runtime(lambda: True, hooks)
    assert runtime.should_stop() is True and runtime.app_hooks is hooks
    assert world.webview is PyWebview and world.make_tray is PystrayIcon
    assert app_main.real_world(None).out is not None


def test_the_login_entry_starts_the_build_or_from_source_pythonw_with_this_script(tmp_path):
    exe = tmp_path / "AHSync.exe"
    assert app_main.login_command(True, exe, Path("x.py")) == (exe, ())
    python = tmp_path / "python.exe"
    script = tmp_path / "ahsync_app" / "__main__.py"
    assert app_main.login_command(False, python, script) == (python, (str(script),))
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")
    assert app_main.login_command(False, python, script) == (tmp_path / "pythonw.exe", (str(script),))


def test_the_script_runs_from_any_working_folder_as_the_login_entry_does(tmp_path):
    import subprocess
    import sys

    script = Path(app_main.__file__).resolve()
    done = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path, capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0 and "--autostart" in done.stdout
    # As a script, Python puts ahsync_app/ first on sys.path; its platform/ must not shadow the stdlib's
    # (pywebview's Windows backend does `from platform import machine`).
    probe = ("import runpy, sys, pathlib; script = sys.argv[1]; sys.path.insert(0, str(pathlib.Path(script).parent)); "
             "runpy.run_path(script, run_name='probe'); import platform; print('stdlib platform', "
             "hasattr(platform, 'machine'))")
    seen = subprocess.run([sys.executable, "-c", probe, str(script)], cwd=tmp_path, capture_output=True, text=True,
                          timeout=60)
    assert "stdlib platform True" in seen.stdout, seen.stdout + seen.stderr


def test_FM58_a_missing_default_ca_file_points_tls_at_the_platform_s_bundle(tmp_path):
    bundle = tmp_path / "cert.pem"
    bundle.write_text("", encoding="utf-8")
    missing = ssl.DefaultVerifyPaths(None, None, "SSL_CERT_FILE", "/build/etc/openssl/cert.pem", "SSL_CERT_DIR",
                                     "/build/etc/openssl/certs")
    present = missing._replace(cafile=str(tmp_path / "other.pem"))
    assert app_main.tls_ca_env({}, missing, bundle) == {"SSL_CERT_FILE": str(bundle)}
    assert app_main.tls_ca_env({"SSL_CERT_FILE": "/mine.pem"}, missing, bundle) == {}     # the user's choice
    assert app_main.tls_ca_env({}, present, bundle) == {}                                  # OpenSSL has its own
    assert app_main.tls_ca_env({}, missing, tmp_path / "absent.pem") == {}
    assert app_main.tls_ca_env({}, missing, None) == {}                                    # Windows: the store


def test_FM58_main_sets_ssl_cert_file_before_the_shell_starts(monkeypatch, tmp_path):
    bundle = tmp_path / "cert.pem"
    bundle.write_text("", encoding="utf-8")
    env = {k: v for k, v in app_main.os.environ.items() if k != "SSL_CERT_FILE"}   # keeps HOME / USERPROFILE
    monkeypatch.setattr(app_main.os, "environ", env)         # restored after the test: main writes into it
    monkeypatch.setattr(app_main.ssl, "get_default_verify_paths",
                        lambda: ssl.DefaultVerifyPaths(None, None, "SSL_CERT_FILE", "/x", "SSL_CERT_DIR", "/y"))

    class Mac:
        def ca_bundle(self):
            return bundle

    monkeypatch.setattr(app_main.platform, "current", Mac)
    seen = {}
    monkeypatch.setattr(app_main, "start", lambda _autostart, world: seen.update(env=world.env) or 0)
    assert app_main.main([]) == 0
    assert app_main.os.environ["SSL_CERT_FILE"] == str(bundle) == seen["env"]["SSL_CERT_FILE"]
