"""Tests for ahsync_app/tray.py: the menu as a pure function of the state (SPEC §3.7.1; DR12)."""
from __future__ import annotations

import threading
import time

import pytest

from ahsync_app import tray
from ahsync_app.tray import PystrayIcon, TrayState


def labels(items: list[tray.MenuItem]) -> list[str]:
    """Each item's label, "---" for a separator."""
    return ["---" if i.separator else i.label for i in items]


def test_DR12_the_menu_is_a_pure_function_of_the_state():
    ready = TrayState(configured=True, version="0.3.1", autostart=True, auto_update=True, channel="stable",
                      downloaded="0.4.0")
    items = tray.menu(ready)
    assert labels(items) == ["Open dashboard", "Status", "Start at login", "Restart to update (0.4.0)",
                             "Install updates automatically", "Get beta updates", "---", "Quit AHSync"]
    assert [i.checked for i in items] == [None, None, True, None, True, False, None, None]
    assert [i.action for i in items] == ["open", "status", "autostart", "restart_update", "auto_update", "beta", "",
                                         "quit"]

    notify = TrayState(configured=True, version="0.3.1", autostart=True, auto_update=False, channel="stable",
                       available="0.4.0")
    items = tray.menu(notify)
    assert labels(items)[3] == "AHSync 0.4.0 available · download" and items[3].action == "download"
    assert items[4].checked is False

    current = TrayState(configured=True, version="0.3.1", autostart=False, auto_update=True, channel="beta",
                        available="0.4.0")                          # auto_update on: nothing to click until downloaded
    assert labels(tray.menu(current)) == ["Open dashboard", "Status", "Start at login",
                                          "Install updates automatically", "Get beta updates", "---", "Quit AHSync"]
    assert tray.menu(current)[4].checked is True and tray.menu(current)[2].checked is False


def test_during_setup_status_has_nothing_to_show():
    setup = TrayState(configured=False, version="0.0.0", autostart=False, auto_update=True, channel="stable")
    assert [(i.label, i.enabled) for i in tray.menu(setup)[:2]] == [("Open dashboard", True), ("Status", False)]


def test_DR2_the_hover_text_comes_from_the_engine():
    dev = TrayState(configured=True, version="0.0.0", autostart=False, auto_update=True, channel="stable")
    assert tray.hover(dev, 0) == "AHSync dev build"
    synced = TrayState(configured=True, version="0.3.1", autostart=False, auto_update=True, channel="stable",
                       synced_ms=1_762_000_000_000)
    assert tray.hover(synced, 0).startswith("AHSync 0.3.1 · synced ")


class FakeIcon:
    """pystray's Icon as refresh() uses it."""

    def __init__(self) -> None:
        self.title = ""
        self.updates = 0
        self.stopped = False

    def update_menu(self) -> None:
        self.updates += 1

    def stop(self) -> None:
        self.stopped = True


def test_the_adapter_rebuilds_the_menu_only_when_the_state_changed():
    state = {"now": TrayState(configured=True, version="0.3.1", autostart=False, auto_update=True, channel="stable")}
    adapter = PystrayIcon(lambda: state["now"], lambda _action: None, lambda: 0)
    adapter.gui = lambda action: action()               # Windows' behaviour (FM65 tests the queue)
    adapter.refresh()                                   # not started: nothing to do
    icon = FakeIcon()
    adapter.icon, adapter.shown = icon, state["now"]
    adapter.refresh()
    assert icon.updates == 0
    state["now"] = TrayState(configured=True, version="0.3.1", autostart=True, auto_update=True, channel="stable")
    adapter.refresh()
    assert icon.updates == 1 and icon.title == "AHSync 0.3.1"
    adapter.stop()
    adapter.stop()
    assert icon.stopped and adapter.icon is None


def test_the_adapter_s_items_are_accepted_by_pystray_and_a_click_dispatches():
    pystray = pytest.importorskip("pystray")     # installed with requirements-app.txt only
    clicked: list[str] = []
    state = TrayState(configured=True, version="0.3.1", autostart=True, auto_update=False, channel="stable")
    items = PystrayIcon(lambda: state, clicked.append, lambda: 0)._items()
    menu_items = [i for i in items if i is not pystray.Menu.SEPARATOR]
    assert [i.text for i in menu_items][:3] == ["Open dashboard", "Status", "Start at login"]
    assert menu_items[2].checked is True and menu_items[3].checked is False and menu_items[0].default
    menu_items[-1](None)
    deadline = time.monotonic() + 5
    while clicked != ["quit"]:                      # the action runs on its own thread (FM66)
        assert time.monotonic() < deadline, clicked
        time.sleep(0.002)


class LoopIcon(FakeIcon):
    """pystray's Icon as run() uses it: run(setup) blocks until stop(); setup runs once the loop is ready."""

    def __init__(self, ready_hook=None) -> None:
        super().__init__()
        self.visible = False
        self.ran = False
        self.ready_hook = ready_hook
        self.done = threading.Event()

    def run(self, setup) -> None:
        self.ran = True
        if self.ready_hook is not None:
            self.ready_hook()                     # e.g. a stop that arrives before the loop is ready
        setup(self)
        assert self.done.wait(5), "the loop was never stopped"

    def stop(self) -> None:
        super().stop()
        self.done.set()


def loop_adapter(icon: LoopIcon) -> PystrayIcon:
    adapter = PystrayIcon(lambda: None, lambda _a: None, lambda: 0)
    adapter._make_icon = lambda: icon
    adapter.gui = lambda action: action()          # Windows' behaviour; FM65's tests queue instead
    return adapter


def test_FM61_the_tray_s_own_loop_runs_until_stop_and_shows_the_icon():
    icon = LoopIcon()
    adapter = loop_adapter(icon)
    stopper = threading.Thread(target=lambda: (time.sleep(0.05), adapter.stop()))
    stopper.start()
    adapter.run()
    stopper.join()
    assert icon.ran and icon.visible and icon.stopped and adapter.icon is None


def test_FM61_a_stop_before_the_loop_is_ready_ends_it_once_it_is():
    icon = LoopIcon()
    adapter = loop_adapter(icon)
    icon.ready_hook = adapter.stop
    adapter.run()
    assert icon.ran and icon.stopped


def test_FM61_a_stop_before_the_loop_starts_means_it_never_starts():
    icon = LoopIcon()
    adapter = loop_adapter(icon)
    adapter.stop()
    adapter.run()
    assert not icon.ran


def test_FM61_run_never_makes_a_second_icon_beside_a_detached_one():
    icon = LoopIcon()
    adapter = loop_adapter(icon)
    adapter.icon = FakeIcon()                      # start() ran: the window's loop has the icon
    adapter.run()
    assert not icon.ran


class DetachedIcon(FakeIcon):
    """pystray's Icon as start() uses it: run_detached(setup) calls setup on its own thread, here at once."""

    def __init__(self) -> None:
        super().__init__()
        self.visible = False
        self.detached = False

    def run_detached(self, setup) -> None:
        self.detached = True
        setup(self)


def queued_adapter(icon: FakeIcon, state=None):
    """An adapter whose GUI-thread actions wait in a queue until the test runs them (macOS's run loop)."""
    queue: list = []
    adapter = PystrayIcon(state or (lambda: None), lambda _a: None, lambda: 0)
    made: list = []
    adapter._make_icon = lambda: made.append(icon) or icon
    adapter.gui = queue.append
    return adapter, queue, made


def drain(queue: list) -> None:
    while queue:
        queue.pop(0)()


def test_FM65_the_icon_is_made_and_shown_only_on_the_gui_thread():
    icon = DetachedIcon()
    adapter, queue, made = queued_adapter(icon)
    adapter.start()
    assert made == [] and adapter.icon is None            # nothing touched AppKit from the calling thread
    drain(queue)
    assert made == [icon] and icon.detached and icon.visible and adapter.icon is icon


def test_FM65_a_stop_before_the_queued_start_keeps_no_icon():
    icon = DetachedIcon()
    adapter, queue, made = queued_adapter(icon)
    adapter.start()
    adapter.stop()
    drain(queue)
    assert made == [] and adapter.icon is None


def test_FM65_the_menu_tooltip_and_stop_are_queued_too():
    state = {"now": TrayState(True, "0.4.0", False, True, "stable")}
    icon = DetachedIcon()
    adapter, queue, _made = queued_adapter(icon, lambda: state["now"])
    adapter.start()
    drain(queue)
    state["now"] = TrayState(True, "0.4.0", True, True, "stable")
    adapter.refresh()
    assert icon.updates == 0 and len(queue) == 1
    drain(queue)
    assert icon.updates == 1 and icon.title.startswith("AHSync 0.4.0")
    adapter.stop()
    assert not icon.stopped
    drain(queue)
    assert icon.stopped


def test_FM66_a_click_returns_at_once_and_its_action_runs_off_the_gui_thread():
    release = threading.Event()
    ran: list = []

    def dispatch(action: str) -> None:
        ran.append((action, threading.current_thread()))
        release.wait(5)                            # Open dashboard waiting for the GUI thread

    adapter = PystrayIcon(lambda: None, dispatch, lambda: 0)
    started = time.monotonic()
    adapter._click("open")(None, None)
    adapter._click("status")(None, None)
    assert time.monotonic() - started < 0.5        # the menu (the GUI thread) is never held
    deadline = time.monotonic() + 5
    while len(ran) < 2:
        assert time.monotonic() < deadline, ran
        time.sleep(0.002)
    release.set()
    assert sorted(a for a, _t in ran) == ["open", "status"]
    assert all(t is not threading.current_thread() for _a, t in ran)

