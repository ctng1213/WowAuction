"""Tests for ahsync_app/gui_thread.py: AppKit work on the main thread, and the Dock icon (FM65, DR31)."""
from __future__ import annotations

import sys
import types

from ahsync_app import gui_thread


def fake_appkit(monkeypatch) -> tuple[list, list]:
    """PyObjCTools and AppKit stand-ins: callAfter queues, NSApplication records its calls."""
    queued: list = []
    calls: list = []

    class AppHelper:
        @staticmethod
        def callAfter(action):
            queued.append(action)

    class App:
        def setActivationPolicy_(self, policy):
            calls.append(("policy", policy))

        def activateIgnoringOtherApps_(self, flag):
            calls.append(("activate", flag))

    app = App()
    monkeypatch.setitem(sys.modules, "PyObjCTools", types.SimpleNamespace(AppHelper=AppHelper))
    monkeypatch.setitem(sys.modules, "PyObjCTools.AppHelper", AppHelper)
    monkeypatch.setitem(sys.modules, "AppKit", types.SimpleNamespace(
        NSApplication=types.SimpleNamespace(sharedApplication=lambda: app)))
    return queued, calls


def test_FM65_a_failure_inside_a_queued_action_is_logged_not_raised(caplog, monkeypatch):
    queued, _calls = fake_appkit(monkeypatch)

    def boom():
        raise RuntimeError("NSWindow should only be instantiated on the main thread!")

    gui_thread.on_gui_thread(boom)
    assert len(queued) == 1
    queued[0]()                                     # the run loop runs it: nothing escapes into Cocoa
    assert "GUI action failed: RuntimeError: NSWindow should only be instantiated on the main thread!" in caplog.text


def test_FM65_without_appkit_the_action_runs_at_once(monkeypatch):
    monkeypatch.setitem(sys.modules, "PyObjCTools", None)            # Windows: no pyobjc
    ran: list = []
    gui_thread.on_gui_thread(lambda: ran.append(True))
    assert ran == [True]


def test_DR31_the_dock_icon_follows_the_window_on_the_main_thread(monkeypatch):
    queued, calls = fake_appkit(monkeypatch)
    gui_thread.show_in_dock(True)
    gui_thread.show_in_dock(False)
    assert calls == []                              # nothing touched AppKit from the calling thread
    for action in queued:
        action()
    assert calls == [("policy", 0), ("activate", True), ("policy", 1)]   # regular + front, then accessory


def test_DR31_without_appkit_the_dock_is_left_alone(monkeypatch):
    monkeypatch.setitem(sys.modules, "AppKit", None)                  # Windows
    gui_thread.show_in_dock(True)                                     # nothing to do, nothing raised

