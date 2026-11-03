"""The app's window: a view on the dashboard that closing never stops (SPEC §3.7.1; DR29, FM56).

`Window` holds the decisions (what a launch, a menu item, a close and Quit do) over an
injected webview, so they are tested with a fake. `PyWebview` is the thin pywebview adapter: one window,
created hidden before `webview.start()` takes the main thread. When the webview can't start (no WebView2
runtime, a missing backend), every open goes to the browser instead and nothing tries the window again.
Links that leave the dashboard carry target="_blank" (checked in the tests), which pywebview hands to the
system browser.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ahsync_app.gui_thread import show_in_dock

log = logging.getLogger("ahsync.app")

TITLE = "AHSync"
WIDTH, HEIGHT = 1200, 800
RENDERERS = ("edgechromium", "wkwebview")    # pywebview's names (FM60); MSHTML can't draw the dashboard


class WindowUnavailable(Exception):
    """The webview can't start here; the browser takes over (FM56)."""


class Webview(Protocol):
    """What the window needs from a webview library (pywebview; a fake in tests)."""

    def create(self, url: str, title: str, width: int, height: int, on_closing: Callable[[], bool]) -> None:
        """Create the one window, hidden.

        Args:
            url: The first page.
            title: The window's title.
            width: In pixels.
            height: In pixels.
            on_closing: Called when the user closes it; returning False cancels the close.
        """

    def start(self, on_started: Callable[[], None]) -> None:
        """Run the GUI loop on this (the main) thread until the window is destroyed.

        Args:
            on_started: Called once the loop runs.

        Raises:
            WindowUnavailable: When the loop can't start.
        """

    def show(self) -> None:
        """Show the window and bring it to the front."""

    def hide(self) -> None:
        """Hide the window."""

    def load(self, url: str) -> None:
        """Load a URL in the window.

        Args:
            url: The page.
        """

    def destroy(self) -> None:
        """Destroy the window, which ends the loop."""

    def current_url(self) -> str | None:
        """The page the window shows now, after the user's own navigation.

        Returns:
            The URL; None before the first load.
        """


@dataclass
class WindowState:
    """What the window shows (DR29)."""

    shown: bool = False
    url: str = ""                  # the URL last loaded in it
    available: bool = True         # False: the browser opens every URL (FM56)
    created: bool = False
    fresh: bool = False            # url was loaded once the server answered (the first load may have been refused)
    quitting: bool = False         # Quit destroys it: the close that follows is let through


class Window:
    """The window's decisions over a webview and the browser (SPEC §3.7.1)."""

    def __init__(self, webview: Webview | None, browser: Callable[[str], object]) -> None:
        """Keep the two ways to show a page.

        Args:
            webview: The webview; None runs without a window (every open goes to the browser, no Status line).
            browser: Opens a URL in the system browser.
        """
        self.webview = webview
        self.browser = browser
        self.state = WindowState(available=webview is not None)

    def create(self, url: str) -> bool:
        """Create the window hidden on url, before the loop starts (pywebview needs a window to start).

        Args:
            url: The dashboard, or the setup page.

        Returns:
            True when there is a window whose loop must run on the main thread.
        """
        if not self.state.available or self.webview is None:
            return False
        try:
            self.webview.create(url, TITLE, WIDTH, HEIGHT, self.closing)
        except Exception as error:     # a missing backend fails here or at start: the browser takes over
            self.unavailable(error)
            return False
        self.state.created, self.state.url = True, url
        return True

    def start(self, on_started: Callable[[], None]) -> None:
        """Run the GUI loop until Quit destroys the window (blocks the main thread).

        Args:
            on_started: Called once the loop runs (on another thread): starts the tray and AHSync.

        Raises:
            WindowUnavailable: When the webview can't start; the window is then unavailable.
        """
        assert self.webview is not None and self.state.created
        ran = []

        def started() -> None:
            """Note that the loop ran, then start the tray and AHSync."""
            ran.append(True)
            on_started()

        try:
            self.webview.start(started)
        except WindowUnavailable as error:
            self.unavailable(error)
            raise
        except Exception as error:     # pywebview raises its own errors for a missing runtime
            if ran:                    # an error while the loop wound down: the window was there, it ended
                log.warning("app window loop ended: %s", type(error).__name__)
                return
            self.unavailable(error)
            raise WindowUnavailable(type(error).__name__) from None
        finally:
            self.state.created, self.state.shown = False, False   # the loop is over: nothing left to destroy

    def unavailable(self, error: BaseException) -> None:
        """Fall back to the browser for good (FM56): one log line, no retry.

        Args:
            error: Why the webview failed.
        """
        self.state.available, self.state.created, self.state.shown = False, False, False
        log.warning("app window unavailable (%s): the dashboard opens in the browser", type(error).__name__)

    def open(self, url: str) -> None:
        """Show url: in the window, loading it when it shows something else; else in the browser (DR29, FM56).

        Args:
            url: A dashboard or setup URL.
        """
        if not self.state.created:
            self.browser(url)
            return
        assert self.webview is not None
        if not self.state.fresh or self.webview.current_url() != url:   # the user may have moved on in the page
            self.webview.load(url)
            self.state.url, self.state.fresh = url, True
        self.webview.show()
        self.state.shown = True

    def closing(self) -> bool:
        """The user closed the window: hide it instead; AHSync runs on in the tray (DR29).

        pywebview's destroy() closes the window too, so after Quit the close goes through.

        Returns:
            False, which cancels the close; True while quitting.
        """
        if self.state.quitting:
            return True
        if self.webview is not None:
            self.webview.hide()
        self.state.shown = False
        return False

    def quit(self) -> None:
        """Destroy the window, which ends the GUI loop (DR29); nothing when there is none."""
        if self.state.created and self.webview is not None:
            self.state.created, self.state.shown, self.state.quitting = False, False, True
            self.webview.destroy()


class PyWebview:
    """The pywebview adapter (SPEC §3.7.1): Edge WebView2 on Windows, WebKit on macOS; nothing else counts."""

    def __init__(self, storage_dir: Path, dock: Callable[[bool], None] = show_in_dock) -> None:
        """Keep where the page keeps its storage.

        Args:
            storage_dir: The webview's own profile (the dashboard's saved choices), under data/.
            dock: Puts AHSync in the Dock while the window shows (gui_thread.show_in_dock; a recorder in tests).
        """
        self.storage_dir = storage_dir
        self.dock = dock
        self.handle = None
        self.renderer = ""

    def create(self, url: str, title: str, width: int, height: int, on_closing: Callable[[], bool]) -> None:
        """Create the hidden window (see Webview.create).

        Args:
            url: The first page.
            title: "AHSync".
            width: In pixels.
            height: In pixels.
            on_closing: The close handler; False cancels.
        """
        import webview
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True   # target=_blank links: the system browser
        self.handle = webview.create_window(title, url, width=width, height=height, hidden=True)
        self.handle.events.closing += on_closing
        self.handle.events.initialized += self._renderer_ok

    def _renderer_ok(self, renderer: str) -> bool:
        """Refuse a renderer that can't draw the dashboard, so the browser takes over (FM56).

        Args:
            renderer: pywebview's renderer name, e.g. "edgechromium", "wkwebview" or "mshtml".

        Returns:
            False to abort the window.
        """
        self.renderer = renderer
        return renderer in RENDERERS

    def start(self, on_started: Callable[[], None]) -> None:
        """Run pywebview's loop (see Webview.start).

        Args:
            on_started: Called on pywebview's thread once the loop runs.

        Raises:
            WindowUnavailable: When the loop can't start or the renderer was refused.
        """
        import webview

        def started() -> None:
            """Leave the Dock while the window is hidden (DR31), let the session's end through, start the rest."""
            self.dock(False)
            self._let_session_end_close()
            on_started()

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        webview.start(started, private_mode=False, storage_path=str(self.storage_dir))
        if self.renderer and self.renderer not in RENDERERS:
            raise WindowUnavailable(f"renderer {self.renderer}")

    def _let_session_end_close(self) -> None:
        """Never block sign-out or shutdown: a close the OS asks for goes through (SPEC §3.7.1).

        The closing handler cancels every close to hide the window; WinForms asks the same handler when Windows
        ends the session, and a cancel there reads as "AHSync is preventing sign-out". A second FormClosing
        handler, run after pywebview's, undoes the cancel for that reason only; the loop then ends and AHSync
        stops. WebKit has no such form (nothing to do; macOS UNVERIFIED until the Mac run).
        """
        for _ in range(50):                        # pywebview sets `native` just after creating the form
            form = getattr(self.handle, "native", None)
            if form is not None:
                break
            time.sleep(0.1)
        closing = getattr(form, "FormClosing", None)
        if closing is None:
            return

        def session_ending(_sender: object, args: object) -> None:
            """Undo the cancel when Windows ends the session.

            Args:
                _sender: The form.
                args: FormClosingEventArgs.
            """
            if str(getattr(args, "CloseReason", "")) == "WindowsShutDown":
                args.Cancel = False

        closing += session_ending

    def show(self) -> None:
        """Join the Dock (DR31), then show and restore the window (pywebview marshals the call to the GUI thread)."""
        self.dock(True)
        self.handle.show()
        self.handle.restore()

    def hide(self) -> None:
        """Hide the window, then leave the Dock (DR31)."""
        self.handle.hide()
        self.dock(False)

    def load(self, url: str) -> None:
        """Load a URL in the window.

        Args:
            url: The page.
        """
        self.handle.load_url(url)

    def destroy(self) -> None:
        """Destroy the window; the loop ends."""
        self.handle.destroy()

    def current_url(self) -> str | None:
        """The page shown now.

        Returns:
            pywebview's current URL; None before the first load.
        """
        return self.handle.get_current_url()
