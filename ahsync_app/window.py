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
STILL_WORKING_MS = 30_000                     # the loading page's note appears after this (DR32)
# The page the window opens on, bundled as a string (no file to package): the dashboard's colours and gold title, a
# pulse, and after STILL_WORKING_MS a note; it never names an error (SPEC §3.7.1, DR32; mockup batch-0-1-6.html §4).
LOADING_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>AHSync</title><style>
html,body{height:100%;margin:0;background:#0f1115;color:#e6e6e6;font:14px -apple-system,"Segoe UI",sans-serif}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px}
.brand{font-size:28px;font-weight:700;letter-spacing:.5px}.brand span{color:#ffd100}
.pulse{width:10px;height:10px;border-radius:50%;background:#ffd100;animation:p 1.2s ease-in-out infinite}
@keyframes p{0%,100%{opacity:.25;transform:scale(.8)}50%{opacity:1;transform:scale(1.2)}}
#later{color:#8f8a7d;font-size:12px;max-width:360px;text-align:center;visibility:hidden}
</style></head><body><div class="brand">AH<span>Sync</span></div><div class="pulse"></div><div>Starting AHSync…</div>
<div id="later">Still working. The first refresh after an update can take a minute.</div>
<script>setTimeout(function(){document.getElementById("later").style.visibility="visible"},""" + str(STILL_WORKING_MS) + """)</script>
</body></html>"""


class WindowUnavailable(Exception):
    """The webview can't start here; the browser takes over (FM56)."""


class Webview(Protocol):
    """What the window needs from a webview library (pywebview; a fake in tests)."""

    def create(self, url: str | None, title: str, width: int, height: int, on_closing: Callable[[], bool],
               html: str | None = None) -> None:
        """Create the one window, hidden.

        Args:
            url: The first page; None when html gives it.
            title: The window's title.
            width: In pixels.
            height: In pixels.
            on_closing: Called when the user closes it; returning False cancels the close.
            html: The first page's HTML, when it is not a URL (the loading page, DR32).
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
    ready: bool = False            # the server answered: a URL may load; before, the loading page stays (DR32)
    wanted: str = ""               # the URL an open asked for before the server answered
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

    def create(self) -> bool:
        """Create the window hidden on the loading page, before the loop starts (pywebview needs a window to start).

        Never on the dashboard's URL: the server is not listening yet, and the webview would show its own error page
        until the next load painted (DR32, LESSONS 2026-11-08).

        Returns:
            True when there is a window whose loop must run on the main thread.
        """
        if not self.state.available or self.webview is None:
            return False
        try:
            self.webview.create(None, TITLE, WIDTH, HEIGHT, self.closing, html=LOADING_HTML)
        except Exception as error:     # a missing backend fails here or at start: the browser takes over
            self.unavailable(error)
            return False
        self.state.created, self.state.url = True, ""
        return True

    def answered(self) -> None:
        """The server listens: load what an open asked for meanwhile, once (DR32)."""
        self.state.ready = True
        if self.state.created and self.state.wanted and self.webview is not None:
            self.webview.load(self.state.wanted)
            self.state.url, self.state.fresh, self.state.wanted = self.state.wanted, True, ""

    def restarting(self) -> None:
        """The server stops and another starts (setup, then the dashboard): wait for it again (DR32)."""
        self.state.ready = False

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

        Before the server answers, the window shows the loading page and url loads when it does (DR32).

        Args:
            url: A dashboard or setup URL.
        """
        if not self.state.created:
            self.browser(url)
            return
        assert self.webview is not None
        if not self.state.ready:
            self.state.wanted = url
        elif not self.state.fresh or self.webview.current_url() != url:   # the user may have moved on in the page
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

    def create(self, url: str | None, title: str, width: int, height: int, on_closing: Callable[[], bool],
               html: str | None = None) -> None:
        """Create the hidden window (see Webview.create).

        Args:
            url: The first page; None when html gives it.
            title: "AHSync".
            width: In pixels.
            height: In pixels.
            on_closing: The close handler; False cancels.
            html: The first page's HTML (the loading page), when url is None.
        """
        import webview
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True   # target=_blank links: the system browser
        self.handle = webview.create_window(title, url, html=html, width=width, height=height, hidden=True)
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
        """The page shown now, without waiting for one that never finished loading.

        pywebview's `get_current_url` waits up to 20 s for the page's loaded event and then raises; seen on the
        user's Mac (2026-11-07) as a white window that stayed white until a restart. A page that hasn't finished
        loading counts as none, so the next show loads it again.

        Returns:
            pywebview's current URL; None before the first load or while the page hasn't finished loading.
        """
        loaded = getattr(getattr(self.handle, "events", None), "loaded", None)
        if loaded is not None and not loaded.is_set():
            return None
        return self.handle.get_current_url()
