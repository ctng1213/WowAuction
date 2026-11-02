"""The tray / menu-bar icon: the menu as a pure function of the state, plus a thin pystray adapter (SPEC §3.7.1; DR12).

`menu(state)` decides every item, check mark and enabled flag; `PystrayIcon` only turns those items into
pystray's and dispatches a click to the shell by its action name. The icon runs detached under the window's
GUI loop: on Windows on its own thread; on macOS in the one NSApplication (UNVERIFIED until the Mac run,
SPEC §6 item 22).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ahsync.engine.app_view import hover_text

log = logging.getLogger("ahsync.app")

ICON_FILE = Path(__file__).parent / "icon.png"

OPEN = "open"
STATUS = "status"
AUTOSTART = "autostart"
RESTART_UPDATE = "restart_update"
DOWNLOAD = "download"
AUTO_UPDATE = "auto_update"
BETA = "beta"
QUIT = "quit"


@dataclass(frozen=True)
class TrayState:
    """What the menu shows (DR12)."""

    configured: bool                     # False during setup: Status has nothing to show yet
    version: str
    autostart: bool
    auto_update: bool
    channel: str                         # "stable" or "beta"
    available: str | None = None         # a newer release known, not downloaded (M25)
    downloaded: str | None = None        # a downloaded, verified release (M27)
    synced_ms: int | None = None         # the last sync, UTC ms (the hover text)


@dataclass(frozen=True)
class MenuItem:
    """One menu line: a label with its action name, or a separator."""

    label: str = ""
    action: str = ""
    checked: bool | None = None          # None: not a checkbox
    enabled: bool = True
    separator: bool = False


SEPARATOR = MenuItem(separator=True)


def menu(state: TrayState) -> list[MenuItem]:
    """The tray menu in SPEC §3.7.1's order (DR12).

    Args:
        state: The app's state now.

    Returns:
        Open dashboard, Status, Start at login, the update item (Restart to update while one is downloaded; with
        auto_update off and a newer release known, "AHSync <v> available · download"; else none), Install updates
        automatically, Get beta updates, a separator, Quit AHSync.
    """
    items = [MenuItem("Open dashboard", OPEN), MenuItem("Status", STATUS, enabled=state.configured),
             MenuItem("Start at login", AUTOSTART, checked=state.autostart)]
    if state.downloaded:
        items.append(MenuItem(f"Restart to update ({state.downloaded})", RESTART_UPDATE))
    elif state.available and not state.auto_update:
        items.append(MenuItem(f"AHSync {state.available} available · download", DOWNLOAD))
    items += [MenuItem("Install updates automatically", AUTO_UPDATE, checked=state.auto_update),
              MenuItem("Get beta updates", BETA, checked=state.channel == "beta"),
              SEPARATOR, MenuItem("Quit AHSync", QUIT)]
    return items


def hover(state: TrayState, offset_ms: int) -> str:
    """The icon's hover text (Windows; macOS shows the icon only), from engine/app_view (DR2).

    Args:
        state: The app's state now.
        offset_ms: Local time minus UTC, ms.

    Returns:
        E.g. "AHSync 0.3.1 · synced 14:06", or "AHSync dev build".
    """
    return hover_text(state.version, state.synced_ms, offset_ms)


class PystrayIcon:
    """The pystray adapter: a menu rebuilt from the state each time it changes, clicks dispatched by action."""

    def __init__(self, state: Callable[[], TrayState], dispatch: Callable[[str], None],
                 offset_ms: Callable[[], int]) -> None:
        """Keep where the menu's state comes from and where a click goes.

        Args:
            state: The app's state now.
            dispatch: Called with an action name (OPEN, STATUS, AUTOSTART, ..., QUIT) on pystray's thread.
            offset_ms: Local time minus UTC now, ms.
        """
        self.state = state
        self.dispatch = dispatch
        self.offset_ms = offset_ms
        self.icon = None
        self.shown: TrayState | None = None

    def _items(self) -> list:
        """pystray's items for the current state (pystray calls this when it builds the menu).

        Returns:
            pystray.MenuItem objects and separators.
        """
        import pystray
        items = []
        for item in menu(self.state()):
            if item.separator:
                items.append(pystray.Menu.SEPARATOR)
                continue
            checked = self._checked(item.checked) if item.checked is not None else None
            items.append(pystray.MenuItem(item.label, self._click(item.action), checked=checked, enabled=item.enabled,
                                          default=item.action == OPEN))
        return items

    def _click(self, action: str) -> Callable[[object, object], None]:
        """A click handler for one action (pystray counts a lambda's default arguments as parameters).

        Args:
            action: The item's action name.

        Returns:
            (icon, item) -> None, which dispatches the action.
        """
        return lambda _icon, _item: self.dispatch(action)

    @staticmethod
    def _checked(value: bool) -> Callable[[object], bool]:
        """A check-mark callback for a fixed value (the menu is rebuilt when the state changes).

        Args:
            value: Whether the item is checked.

        Returns:
            (item) -> value.
        """
        return lambda _item: value

    def start(self) -> None:
        """Show the icon, detached under the running GUI loop (pystray's own `run()` would start a second loop)."""
        import pystray
        from PIL import Image
        kwargs = {}
        try:                           # macOS: share the window's NSApplication (UNVERIFIED, SPEC §6 item 22)
            from AppKit import NSApplication
            kwargs["darwin_nsapplication"] = NSApplication.sharedApplication()
        except ImportError:
            pass
        self.shown = self.state()
        self.icon = pystray.Icon("AHSync", Image.open(ICON_FILE), hover(self.shown, self.offset_ms()),
                                 pystray.Menu(self._items), **kwargs)
        self.icon.run_detached()

    def refresh(self) -> None:
        """Rebuild the menu and the hover text when the state changed (called each shell tick)."""
        if self.icon is None:
            return
        now = self.state()
        if now == self.shown:
            return
        self.shown = now
        self.icon.title = hover(now, self.offset_ms())
        self.icon.update_menu()

    def stop(self) -> None:
        """Remove the icon."""
        if self.icon is not None:
            self.icon.stop()
            self.icon = None
