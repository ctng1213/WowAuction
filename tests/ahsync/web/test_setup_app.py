"""Tests for ahsync/web/setup_app.py: setup mode's endpoints and Security rules (SPEC §3.7.4; DR5–DR8, SEC13, FM50)."""
from __future__ import annotations

import errno
import json
import logging
from functools import partial
from pathlib import Path

import pytest

from ahsync.app.setup import save_setup
from ahsync.web.server import TOKEN_HEADER, TOKEN_PLACEHOLDER
from ahsync.web.setup_app import SETUP_HTML, SetupApp

PORT = 8765
TOKEN = "t" * 43


class World:
    """A setup app over a temp folder: a WoW client, the app's config file and the user's home."""

    def __init__(self, tmp_path: Path, write_key=None) -> None:
        self.wow = tmp_path / "wow"
        (self.wow / "Interface" / "AddOns").mkdir(parents=True)
        self.config = tmp_path / "app" / "config.toml"
        self.home = tmp_path / "home"
        self.after: list[str] = []
        self.key_lines: list[str] = []
        save = partial(save_setup, config_file=self.config, home=self.home, restrict_file=lambda _p: None)
        if write_key is not None:
            save = partial(save, write_key=write_key)
        self.app = SetupApp(TOKEN, PORT, lambda: {"setup": True}, save,
                            lambda request: self.after.append(request.market) or True, self.key_lines.append,
                            page_html=f'<meta name="ahsync-token" content="{TOKEN_PLACEHOLDER}">')

    def body(self, **changes) -> dict:
        """DR7's body."""
        return {"wow_path": str(self.wow), "ruleset": "pvp", "faction": "horde", "api_key": " abc ",
                "autostart": True, **changes}

    def post(self, payload: dict, origin: str = f"http://127.0.0.1:{PORT}", token: str | None = TOKEN,
             host: str = f"127.0.0.1:{PORT}"):
        """POST /api/setup; returns (status, JSON body, raw)."""
        headers = {"Host": host, "Origin": origin} | ({TOKEN_HEADER: token} if token else {})
        response = self.app.handle("POST", "/api/setup", headers, json.dumps(payload).encode())
        return response.status, json.loads(response.body), response.body

    def get(self, path: str):
        """GET a path with this dashboard's Host."""
        return self.app.handle("GET", path, {"Host": f"localhost:{PORT}"})


def test_DR5_setup_mode_redirects_serves_the_page_and_has_no_views(tmp_path):
    world = World(tmp_path)
    root = world.get("/")
    assert root.status == 302 and ("Location", "/setup") in root.headers
    page = world.get("/setup")
    assert page.status == 200 and TOKEN.encode() in page.body and page.content_type.startswith("text/html")
    assert json.loads(world.get("/api/app").body) == {"setup": True}
    assert (world.get("/api/views/status").status, json.loads(world.get("/api/views/status").body)) == (
        404, {"error": "no_refresh_yet"})
    assert world.get("/api/markets").status == 404 and world.get("/api/refresh").status == 404
    assert world.app.handle("POST", "/api/views/craft", {"Host": f"localhost:{PORT}"}).status == 405


def test_the_real_setup_page_holds_the_token_placeholder_and_no_inline_text_building():
    html = SETUP_HTML.read_text(encoding="utf-8")
    assert TOKEN_PLACEHOLDER in html and "/api/app" in html and "check_only" in html


def test_DR6_check_only_answers_and_writes_nothing(tmp_path):
    world = World(tmp_path)
    status, answer, _raw = world.post({"wow_path": str(tmp_path / "Games" / "WoW"), "check_only": True})
    assert (status, answer) == (200, {"ok": False, "text": f"no Interface/AddOns under {tmp_path / 'Games' / 'WoW'}"})
    assert not world.config.exists() and not world.app.done.is_set()


def test_DR7_one_post_saves_everything_and_asks_for_the_hand_over(tmp_path):
    world = World(tmp_path)
    status, answer, raw = world.post(world.body())
    assert (status, answer) == (200, {"set": True, "restart_wow": True})
    assert world.config.exists() and (world.home / ".ahsync" / "api_key").read_text().strip() == "abc"
    assert world.after == ["forever.pvp.horde.us"] and world.app.done.is_set() and b"abc" not in raw
    assert world.post(world.body())[0:2] == (409, {"error": "already_set"})


@pytest.mark.parametrize("changes, message", [
    ({"ruleset": "hardcore"}, "ruleset must be normal, pvp or rp"),
    ({"wow_path": "C:\\Games\\WoW"}, "no Interface/AddOns under C:\\Games\\WoW"),
    ({"faction": "neutral"}, "faction must be alliance or horde"),
    ({"autostart": "yes"}, "autostart must be true or false"),
])
def test_DR8_a_bad_field_is_400_with_nothing_written(tmp_path, changes, message):
    world = World(tmp_path)
    status, answer, _raw = world.post(world.body(**changes))
    assert (status, answer) == (400, {"error": "bad_request", "message": message})
    assert not world.config.exists() and not (world.home / ".ahsync").exists() and world.after == []


def test_DR8_an_empty_key_saves_without_a_key_file(tmp_path):
    world = World(tmp_path)
    assert world.post(world.body(api_key=""))[0:2] == (200, {"set": True, "restart_wow": True})
    assert world.config.exists() and not (world.home / ".ahsync").exists()


def test_SEC13_setup_writes_need_this_dashboard_s_origin_token_and_host(tmp_path, caplog):
    world = World(tmp_path)
    with caplog.at_level(logging.INFO):
        evil = world.post(world.body(), origin="https://evil.example")
        no_token = world.post(world.body(), token=None)
        bad_host = world.post(world.body(), host=f"evil.example:{PORT}")
        get = world.app.handle("GET", "/api/setup", {"Host": f"127.0.0.1:{PORT}"})
        assert (evil[0], no_token[0], bad_host[0], get.status) == (403, 403, 403, 405)
        assert not world.config.exists() and not (world.home / ".ahsync").exists() and world.after == []
        accepted = world.post(world.body())
    assert accepted[0] == 200 and world.after == ["forever.pvp.horde.us"]
    for _status, _answer, raw in (evil, no_token, bad_host, accepted):
        assert b"abc" not in raw and TOKEN.encode() not in raw
    assert b"abc" not in get.body and "abc" not in caplog.text and TOKEN not in caplog.text


def test_FM50_a_key_that_cannot_be_saved_keeps_the_config_and_a_retry_does_everything(tmp_path, caplog):
    fail = {"on": True}

    def write_key(path: Path, key: str) -> None:
        if fail["on"]:
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key, encoding="utf-8")

    world = World(tmp_path, write_key=write_key)
    with caplog.at_level(logging.INFO):
        status, answer, raw = world.post(world.body())
    assert (status, answer) == (500, {"error": "key_not_saved", "message": "the key could not be saved: permission denied"})
    assert world.config.exists() and world.key_lines == ["permission denied"] and world.after == []
    assert not world.app.done.is_set() and b"abc" not in raw and "abc" not in caplog.text

    fail["on"] = False
    assert world.post(world.body())[0:2] == (200, {"set": True, "restart_wow": True})
    assert world.after == ["forever.pvp.horde.us"] and world.app.done.is_set()


def test_a_config_that_cannot_be_written_is_500_and_names_no_path(tmp_path):
    world = World(tmp_path)
    world.config.parent.write_text("a file where the folder should be", encoding="utf-8")
    status, answer, raw = world.post(world.body())
    assert status == 500 and answer["error"] == "config_not_written" and str(tmp_path).encode() not in raw
    assert world.post({"wow_path": 1, "check_only": True})[0:2] == (200, {
        "ok": False, "text": "not found: enter the folder that holds Interface and WTF"})
    assert world.app.handle("POST", "/api/setup", {"Host": f"127.0.0.1:{PORT}", "Origin": f"http://127.0.0.1:{PORT}",
                                                   TOKEN_HEADER: TOKEN}, b"not json").status == 400


def test_a_step_after_the_save_that_fails_still_hands_over(tmp_path, caplog):
    world = World(tmp_path)

    def broken(_request):
        raise RuntimeError("launchctl said no")

    world.app.after_save = broken
    assert world.post(world.body())[0:2] == (200, {"set": True, "restart_wow": False})
    assert world.app.done.is_set() and "a step after the save failed: RuntimeError" in caplog.text


def test_the_key_reaches_the_redactor_before_anything_is_written(tmp_path):
    world = World(tmp_path)
    secrets: list[str] = []
    world.app.redact = lambda key: secrets.append((key, world.config.exists()))
    world.post(world.body())
    assert secrets == [("abc", False)]
