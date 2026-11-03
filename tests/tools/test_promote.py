"""Tests for tools/promote.py: promote a pre-release only after checking it (SPEC §3.7.10, DR27)."""
from __future__ import annotations

import json

import pytest

import tools.promote as promote

TAG = "v0.4.0"
VIEW = ["release", "view", TAG, "--json", "tagName,isPrerelease,assets"]
LIST = ["release", "list", "--exclude-pre-releases", "--json", "tagName,isLatest"]
EDIT = ["release", "edit", TAG, "--prerelease=false", "--latest"]
ASSETS = ["AHSync-0.4.0-windows-x64-setup.exe", "AHSync-0.4.0-macos-arm64.dmg",
          "AHSync-0.4.0-macos-arm64.zip", "SHA256SUMS"]


class FakeGh:
    """A fake gh: records every call and answers view, list and edit."""

    def __init__(self, assets=ASSETS, pre=True, view_code=0, latest="v0.3.1", edit_code=0, list_code=0) -> None:
        self.calls: list[list[str]] = []
        self.assets, self.pre, self.view_code = assets, pre, view_code
        self.latest, self.edit_code, self.list_code = latest, edit_code, list_code

    def __call__(self, args: list[str]) -> tuple[int, str]:
        """Answer one gh call."""
        self.calls.append(args)
        if args[1] == "view":
            body = {"tagName": TAG, "isPrerelease": self.pre, "assets": [{"name": n} for n in self.assets]}
            return self.view_code, json.dumps(body)
        if args[1] == "list":
            rows = [{"tagName": "v0.2.0", "isLatest": False}, {"tagName": self.latest, "isLatest": True}]
            return self.list_code, json.dumps(rows) if self.list_code == 0 else ""
        return self.edit_code, ""


def run(gh: FakeGh, tag: str = TAG) -> tuple[int, list[str], list[str]]:
    """Run promote with captured output; returns (code, stdout lines, stderr lines)."""
    out: list[str] = []
    err: list[str] = []
    code = promote.promote(tag, gh, out.append, err.append)
    return code, out, err


def test_DR27_pre_release_is_promoted():
    gh = FakeGh()
    assert run(gh) == (0, ["promoted v0.4.0 to the latest release"], [])
    assert gh.calls == [VIEW, LIST, EDIT]


@pytest.mark.parametrize("gh,reason,calls", [
    (FakeGh(pre=False), "v0.4.0 is already the latest release, not a pre-release", [VIEW]),
    (FakeGh(view_code=1), "no release v0.4.0", [VIEW]),
    (FakeGh(assets=[a for a in ASSETS if not a.endswith(".zip")]),
     "release v0.4.0 lacks AHSync-0.4.0-macos-arm64.zip", [VIEW]),
    (FakeGh(assets=ASSETS[:3]), "release v0.4.0 lacks SHA256SUMS", [VIEW]),
    (FakeGh(latest="v0.5.0"), "v0.4.0 is lower than the latest release v0.5.0", [VIEW, LIST]),
    (FakeGh(list_code=1), "could not read the latest release", [VIEW, LIST]),
])
def test_DR27_refusals_make_no_edit(gh, reason, calls):
    assert run(gh) == (1, [], [reason])
    assert gh.calls == calls


@pytest.mark.parametrize("bad", ["0.4.0", "v0.4", "v0.4.0-beta", "v٠.٤.٠", "v0.4.0\n"])
def test_DR27_bad_tag_is_refused_before_any_gh_call(bad):
    gh = FakeGh()
    assert run(gh, bad) == (1, [], ["release tags are vMAJOR.MINOR.PATCH"])
    assert gh.calls == []


def test_DR27_failed_edit_exits_1():
    gh = FakeGh(edit_code=1)
    assert run(gh) == (1, [], ["gh release edit v0.4.0 failed"])


def test_DR27_main_uses_injected_gh(capsys):
    assert promote.main([TAG], FakeGh()) == 0
    assert capsys.readouterr().out == "promoted v0.4.0 to the latest release\n"
    assert promote.main([], FakeGh()) == 2
