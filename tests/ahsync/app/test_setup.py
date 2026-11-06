"""Tests for ahsync/app/setup.py: setup's checks and its writes (SPEC §3.7.4; DR6–DR8, FM50)."""
from __future__ import annotations

import errno
import logging
import tomllib
from pathlib import Path

import pytest

from ahsync.app import setup
from ahsync.config import load_config


def client(root: Path, addons: bool = True) -> Path:
    """A fake WoW client folder."""
    (root / "Interface" / ("AddOns" if addons else "")).mkdir(parents=True, exist_ok=True)
    return root


def body(wow: Path, **changes) -> dict:
    """DR7's body for a folder."""
    return {"wow_path": str(wow), "ruleset": "pvp", "faction": "horde", "api_key": " abc ", "autostart": True,
            **changes}


def test_DR7_the_body_becomes_a_checked_request_with_the_key_stripped(tmp_path):
    request = setup.parse_setup(body(client(tmp_path / "wow")))
    assert (request.market, request.api_key, request.autostart) == ("forever.pvp.horde.us", "abc", True)
    assert "abc" not in repr(request)


@pytest.mark.parametrize("changes, message", [
    ({"ruleset": "hardcore"}, "ruleset must be normal, pvp or rp"),
    ({"faction": "neutral"}, "faction must be alliance or horde"),
    ({"autostart": "yes"}, "autostart must be true or false"),
    ({"api_key": 5}, "api_key must be one line"),
])
def test_DR8_every_field_is_checked(tmp_path, changes, message):
    with pytest.raises(setup.SetupError, match=message):
        setup.parse_setup(body(client(tmp_path / "wow"), **changes))


def test_DR8_a_folder_without_interface_is_refused_with_its_check_text(tmp_path):
    games = tmp_path / "Games" / "WoW"
    with pytest.raises(setup.SetupError, match="no Interface/AddOns under"):
        setup.parse_setup(body(games))
    with pytest.raises(setup.SetupError, match="not found: enter the folder"):
        setup.parse_setup(body(games, wow_path=None))
    with pytest.raises(setup.SetupError, match="JSON object"):
        setup.read_body(b"[1]")
    with pytest.raises(setup.SetupError, match="JSON object"):
        setup.read_body(b"\xff")


def test_DR6_the_check_answers_ok_and_text_only(tmp_path):
    assert setup.folder_check(str(tmp_path / "Games" / "WoW")) == {
        "ok": False, "text": f"no Interface/AddOns under {tmp_path / 'Games' / 'WoW'}"}
    assert setup.folder_check(str(client(tmp_path / "fresh", addons=False))) == {
        "ok": True, "text": "found Interface/AddOns · no accounts yet"}


def test_DR7_save_writes_addons_then_the_config_then_the_restricted_key_file(tmp_path, caplog):
    wow = client(tmp_path / "it's World of Warcraft", addons=False)     # a quote; backslashes on Windows
    restricted = []
    request = setup.parse_setup(body(wow))
    with caplog.at_level(logging.INFO):
        setup.save_setup(request, tmp_path / "app" / "config.toml", tmp_path / "home", restricted.append)

    assert (wow / "Interface" / "AddOns").is_dir()
    text = (tmp_path / "app" / "config.toml").read_text(encoding="utf-8")
    assert "docs/config.toml" in text and tomllib.loads(text) == {"markets": ["forever.pvp.horde.us"],
                                                                     "wow_path": str(wow)}
    config = load_config(tmp_path / "app" / "config.toml", {}, tmp_path / "home")
    assert list(config.markets) == ["forever.pvp.horde.us"]
    assert config.api_key == "abc" and config.wow_path == str(wow)
    key_file = tmp_path / "home" / ".ahsync" / "api_key"
    assert key_file.read_text(encoding="utf-8").strip() == "abc" and restricted == [key_file]
    assert not (tmp_path / "app" / "config.toml.tmp").exists()
    assert "abc" not in caplog.text


def test_DR8_no_key_writes_no_key_file(tmp_path):
    request = setup.parse_setup(body(client(tmp_path / "wow"), api_key=""))
    setup.save_setup(request, tmp_path / "config.toml", tmp_path / "home", lambda _p: None)
    assert (tmp_path / "config.toml").exists() and not (tmp_path / "home" / ".ahsync").exists()


def test_FM50_a_key_file_that_fails_keeps_the_config_and_says_why_without_the_key(tmp_path, caplog):
    def refuse(path: Path, key: str) -> None:
        raise PermissionError(errno.EACCES, "Permission denied", str(path))

    request = setup.parse_setup(body(client(tmp_path / "wow")))
    with pytest.raises(setup.KeyNotSaved) as raised:
        setup.save_setup(request, tmp_path / "config.toml", tmp_path / "home", lambda _p: None, write_key=refuse)
    assert raised.value.reason == "permission denied" and str(raised.value) == "permission denied"
    assert (tmp_path / "config.toml").exists() and "abc" not in caplog.text


def test_a_folder_name_toml_cannot_hold_is_refused_before_any_write(tmp_path):
    request = setup.SetupRequest("C:/wow\x7f", "pvp", "horde", False)
    with pytest.raises(OSError, match="would not read back"):
        setup.write_config(tmp_path / "config.toml", request)
    assert not (tmp_path / "config.toml").exists()


@pytest.mark.parametrize("key", ["ab\ncd", "ab cd", "x" * 257, "clé"])
def test_DR8_a_key_that_cannot_travel_in_a_header_is_refused_without_quoting_it(tmp_path, key):
    with pytest.raises(setup.SetupError) as raised:
        setup.parse_setup(body(client(tmp_path / "wow"), api_key=key))
    assert str(raised.value) == setup.KEY_MESSAGE and key not in str(raised.value)


def test_a_pasted_quoted_or_home_relative_folder_is_read_as_meant_and_a_relative_one_fails(tmp_path, monkeypatch):
    wow = client(tmp_path / "wow")
    assert setup.parse_setup(body(wow, wow_path=f'  "{wow}" ')).wow_path == str(wow)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert setup.typed_folder("~/wow") == str(tmp_path / "wow")
    monkeypatch.chdir(tmp_path)
    assert setup.folder_check("wow") == {"ok": False, "text": "no Interface/AddOns under wow"}
    assert setup.typed_folder(None) == ""


def test_a_failed_key_write_keeps_the_earlier_key_file_and_leaves_no_temp(tmp_path, monkeypatch):
    key_file = tmp_path / ".ahsync" / "api_key"
    key_file.parent.mkdir()
    key_file.write_text("old\n", encoding="utf-8")

    def full_disk(*_args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(setup.os, "replace", full_disk)
    with pytest.raises(OSError):
        setup.write_key_file(key_file, "new")
    assert key_file.read_text(encoding="utf-8") == "old\n" and sorted(p.name for p in key_file.parent.iterdir()) == ["api_key"]
    monkeypatch.undo()
    setup.write_key_file(key_file, "new")
    assert key_file.read_text(encoding="utf-8") == "new\n"


@pytest.mark.skipif(not hasattr(setup.os, "fchmod"), reason="file modes are POSIX")
def test_the_key_file_is_never_readable_by_others_even_over_a_loose_one(tmp_path):
    key_file = tmp_path / ".ahsync" / "api_key"
    key_file.parent.mkdir()
    key_file.write_text("old\n", encoding="utf-8")
    key_file.chmod(0o644)
    setup.write_key_file(key_file, "new")
    assert key_file.stat().st_mode & 0o077 == 0


def test_a_folder_name_utf8_cannot_encode_fails_as_oserror_without_a_temp_file(tmp_path):
    request = setup.SetupRequest("C:/wow\ud800", "pvp", "horde", False)
    with pytest.raises(OSError, match="config.toml not written"):
        setup.write_config(tmp_path / "config.toml", request)
    assert list(tmp_path.iterdir()) == []


def test_DR7_a_fresh_forever_install_without_interface_is_accepted_and_gets_interface_addons(tmp_path):
    wow = tmp_path / "World of Warcraft" / "_forever_"
    wow.mkdir(parents=True)
    (wow / ".flavor.info").write_text("Product Flavor!STRING:0\nwowf\n", encoding="utf-8")   # no Interface, no WTF
    assert setup.folder_check(str(wow)) == {"ok": True, "text": "found WoW: Forever · AddOns will be created"}
    request = setup.parse_setup(body(wow))
    setup.save_setup(request, tmp_path / "app" / "config.toml", tmp_path / "home", lambda path: None)
    assert (wow / "Interface" / "AddOns").is_dir()
