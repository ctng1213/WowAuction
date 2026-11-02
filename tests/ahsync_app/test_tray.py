"""Tests for ahsync_app/tray.py: the menu as a pure function of the state (SPEC §3.7.1; DR12)."""
from __future__ import annotations

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
    assert clicked == ["quit"]
