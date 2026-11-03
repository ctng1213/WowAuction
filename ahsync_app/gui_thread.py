"""AppKit work on the main thread, and the Dock icon, for the window and the tray (FM65, DR31).

AppKit makes and changes windows, status items and the app's activation policy only on the main thread, while
the shell drives the window and the tray from pywebview's start callback, the worker and click threads. On macOS
these helpers queue the work onto the main run loop (pywebview's or pystray's own); without pyobjc (Windows) they
act at once or do nothing.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger("ahsync.app")

REGULAR, ACCESSORY = 0, 1          # NSApplicationActivationPolicy: a Dock app; menu-bar only


def on_gui_thread(action: Callable[[], None]) -> None:
    """Run an action that touches AppKit objects on the main thread on macOS; elsewhere at once (FM65).

    The action runs once the main run loop spins; a failure is logged there, since an exception escaping into
    Cocoa can abort the app. On Windows pystray posts to its own window, so the action runs now.

    Args:
        action: What to do, e.g. make the tray icon or rebuild its menu.
    """
    try:
        from PyObjCTools import AppHelper
    except ImportError:
        action()
        return

    def guarded() -> None:
        """Run the action on the main thread; log a failure instead of raising into the run loop."""
        try:
            action()
        except Exception as error:
            log.warning("GUI action failed: %s: %s", type(error).__name__, error)

    AppHelper.callAfter(guarded)


def show_in_dock(shown: bool) -> None:
    """Give AHSync a Dock icon and a ⌘-Tab entry only while its window is shown (DR31); nothing without AppKit.

    pywebview makes the app a regular Dock app when its loop starts; with the window hidden that icon would do
    nothing when clicked, so AHSync is menu-bar only (accessory) then, and comes to the front as it shows.

    Args:
        shown: True just before the window shows; False once it is hidden.
    """
    try:
        from AppKit import NSApplication
    except ImportError:
        return

    def apply() -> None:
        """Switch the activation policy on the main thread; bring the app forward when it gains the Dock."""
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(REGULAR if shown else ACCESSORY)
        if shown:
            app.activateIgnoringOtherApps_(True)

    on_gui_thread(apply)
