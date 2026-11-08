"""Tests for ahsync_app/window.py: the window's decisions over a fake webview (SPEC §3.7.1; DR29, FM56)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from ahsync_app.window import PyWebview, Window, WindowUnavailable

HOME = "http://localhost:8765"
STATIC = Path(__file__).resolve().parents[2] / "ahsync" / "web" / "static"


class Webview:
    """Records the window's calls; create or start can be made to fail."""

    def __init__(self, fail_create: Exception | None = None, fail_start: Exception | None = None) -> None:
        self.calls: list = []
        self.navigated = None          # set by a test: the user moved on inside the page
        self.fail_create, self.fail_start = fail_create, fail_start
        self.run_first = False         # True: the loop runs, then raises while winding down
        self.on_closing = None

    def create(self, url, title, width, height, on_closing) -> None:
        if self.fail_create:
            raise self.fail_create
        self.calls.append(("create", url, title, width, height))
        self.on_closing = on_closing

    def start(self, on_started) -> None:
        if self.fail_start and not self.run_first:
            raise self.fail_start
        on_started()
        if self.fail_start:
            raise self.fail_start

    def show(self) -> None:
        self.calls.append(("show",))

    def hide(self) -> None:
        self.calls.append(("hide",))

    def load(self, url: str) -> None:
        self.calls.append(("load", url))

    def destroy(self) -> None:
        self.calls.append(("destroy",))

    def current_url(self):
        loads = [c[1] for c in self.calls if c[0] in ("load", "create")]
        return self.navigated or (loads[-1] if loads else None)


def test_DR29_one_hidden_window_shows_on_each_open_loads_only_when_elsewhere_and_hides_on_close():
    view, browsed = Webview(), []
    window = Window(view, browsed.append)
    assert window.create(HOME) and view.calls == [("create", HOME, "AHSync", 1200, 800)]
    assert window.state.shown is False

    def while_the_loop_runs() -> None:
        window.open(HOME)                               # the first open reloads: the create may have been refused
        window.open(HOME)                               # already there: shown, not reloaded
        assert view.calls[1:] == [("load", HOME), ("show",), ("show",)]
        window.open(f"{HOME}/#status")
        window.open(HOME)
        assert view.calls[-4:] == [("load", f"{HOME}/#status"), ("show",), ("load", HOME), ("show",)]
        view.navigated = f"{HOME}/#portfolio"           # the user clicked on inside the page
        window.open(HOME)
        assert view.calls[-2:] == [("load", HOME), ("show",)]
        assert view.on_closing() is False and view.calls[-1] == ("hide",) and window.state.shown is False
        window.quit()
        window.quit()                                   # once
        assert view.calls[-1] == ("destroy",) and view.calls.count(("destroy",)) == 1
        assert view.on_closing() is True                # destroy() closes the window: Quit's close goes through

    window.start(while_the_loop_runs)
    assert browsed == [] and view.calls.count(("destroy",)) == 1


def test_after_the_loop_ends_nothing_is_destroyed_and_a_late_error_is_not_a_fallback(caplog):
    view, browsed = Webview(), []
    window = Window(view, browsed.append)
    window.create(HOME)
    window.start(lambda: None)                          # the loop ended (the OS closed the session)
    window.quit()
    assert ("destroy",) not in view.calls and window.state.available is True

    late = Webview(fail_start=RuntimeError("teardown"))
    late.run_first = True
    window = Window(late, browsed.append)
    window.create(HOME)
    window.start(lambda: None)                          # no WindowUnavailable: the window was there
    assert window.state.available is True and "app window loop ended: RuntimeError" in caplog.text


def test_FM56_a_webview_that_cannot_start_hands_every_open_to_the_browser(caplog):
    view, browsed = Webview(fail_start=RuntimeError("no WebView2")), []
    window = Window(view, browsed.append)
    assert window.create(HOME)
    with pytest.raises(WindowUnavailable):
        window.start(lambda: None)
    window.open(HOME)
    window.open(f"{HOME}/#status")
    window.quit()
    assert browsed == [HOME, f"{HOME}/#status"] and view.calls == [("create", HOME, "AHSync", 1200, 800)]
    assert window.state.available is False
    assert caplog.text.count("app window unavailable (RuntimeError)") == 1


def test_FM56_a_create_that_fails_or_a_refused_renderer_falls_back_too(caplog):
    browsed: list[str] = []
    window = Window(Webview(fail_create=ImportError("no pythonnet")), browsed.append)
    assert window.create(HOME) is False and window.state.available is False
    window.open(HOME)
    assert browsed == [HOME]
    refused = Window(Webview(fail_start=WindowUnavailable("renderer mshtml")), browsed.append)
    refused.create(HOME)
    with pytest.raises(WindowUnavailable):
        refused.start(lambda: None)
    assert refused.state.available is False and "app window unavailable (WindowUnavailable)" in caplog.text


def test_without_a_webview_every_open_goes_to_the_browser():
    browsed: list[str] = []
    window = Window(None, browsed.append)
    assert window.create(HOME) is False
    window.open(HOME)
    assert window.closing() is False and browsed == [HOME]


def test_FM60_the_adapter_accepts_edge_webview2_and_wkwebview_and_refuses_the_rest(tmp_path):
    adapter = PyWebview(tmp_path)
    assert adapter._renderer_ok("edgechromium") and adapter._renderer_ok("wkwebview")   # pywebview's macOS name
    assert not adapter._renderer_ok("cocoa")
    assert not adapter._renderer_ok("mshtml") and adapter.renderer == "mshtml"


@pytest.mark.parametrize("page", ["index.html", "setup.html"])
def test_DR29_every_link_that_leaves_the_dashboard_opens_in_the_browser(page):
    """pywebview hands target=_blank links to the system browser; a plain link would load in the window."""
    text = (STATIC / page).read_text(encoding="utf-8")
    leaving = [a for a in re.findall(r"<a\b[^>]*>", text) if re.search(r'href="(https?:|\$\{)', a)]
    assert all('target="_blank"' in a for a in leaving), [a for a in leaving if "_blank" not in a]
    for assigned in set(re.findall(r"(\w+)\.href\s*=", text)) - {"location"}:   # location.href stays in the app
        assert re.search(rf"{assigned}\.target\s*=\s*'_blank'", text), assigned


class FakeHandle:
    """pywebview's window as the adapter drives it."""

    def __init__(self, calls: list) -> None:
        self.calls = calls

    def show(self) -> None:
        self.calls.append("show")

    def restore(self) -> None:
        self.calls.append("restore")

    def hide(self) -> None:
        self.calls.append("hide")


def test_DR31_the_adapter_puts_ahsync_in_the_dock_only_while_the_window_shows(tmp_path):
    calls: list = []
    adapter = PyWebview(tmp_path, dock=lambda shown: calls.append(("dock", shown)))
    adapter.handle = FakeHandle(calls)
    adapter.show()
    adapter.hide()
    assert calls == [("dock", True), "show", "restore", "hide", ("dock", False)]


def test_DR31_the_loop_starts_with_ahsync_out_of_the_dock(tmp_path, monkeypatch):
    import sys
    import types

    calls: list = []
    fake_webview = types.SimpleNamespace(start=lambda func, **_kw: func())
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    adapter = PyWebview(tmp_path, dock=lambda shown: calls.append(("dock", shown)))
    adapter.handle = types.SimpleNamespace()        # no WinForms form: nothing to hook at sign-out
    monkeypatch.setattr(adapter, "_let_session_end_close", lambda: None)
    adapter.start(lambda: calls.append("started"))
    assert calls == [("dock", False), "started"]    # pywebview made it a Dock app; hidden window: none



def test_a_page_that_never_finished_loading_counts_as_none_so_the_next_show_loads_it_again(tmp_path):
    import threading
    from types import SimpleNamespace

    loaded = threading.Event()
    adapter = PyWebview(tmp_path)
    adapter.handle = SimpleNamespace(events=SimpleNamespace(loaded=loaded),
                                     get_current_url=lambda: "http://localhost:8765/")
    assert adapter.current_url() is None                          # white window: never asks pywebview, never waits
    loaded.set()
    assert adapter.current_url() == "http://localhost:8765/"
