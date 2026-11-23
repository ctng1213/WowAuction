"""Tests for tools/ci_scope.py."""
from __future__ import annotations

import json
import subprocess

from tools import ci_scope

CLOUD = ["ahcloud/hourly.py", "tests/ahcloud/test_hourly.py"]


def test_DR35_a_cloud_only_change_is_tested_once_on_linux_and_anything_else_everywhere():
    assert ci_scope.outputs(CLOUD) == {"cloud_only": "true", "oses": '["ubuntu-latest"]'}
    assert ci_scope.outputs(CLOUD + ["docs/SPEC.md"])["cloud_only"] == "true"          # documentation is neutral
    full = ci_scope.outputs(["infra/app.py", "ahsync/engine/profile.py"])
    assert full == {"cloud_only": "false", "oses": '["windows-latest", "macos-15"]'}   # ahsync/ is shared
    assert ci_scope.outputs(["README.md"])["cloud_only"] == "false"                     # no code: nothing to narrow
    assert ci_scope.outputs([])["cloud_only"] == "false"
    assert ci_scope.outputs(None)["cloud_only"] == "false"                              # unknown: test everything
    assert json.loads(ci_scope.outputs(None)["oses"]) == list(ci_scope.FULL_OSES)


def test_DR35_every_cloud_path_counts_and_nothing_near_it_does():
    for path in ("infra/stacks/market.py", "tests/infra/test_app.py", "tools/build_lambda.py",
                 "tests/tools/test_build_lambda.py", "requirements-cloud.txt", "requirements-infra.txt"):
        assert ci_scope.is_cloud(path), path
    for path in ("ahsync/intel/contract.py", "tools/sim/runner.py", "requirements-dev.txt", "ahcloudx/a.py",
                 ".github/workflows/ci.yml", "tools/ci_scope.py"):
        assert not ci_scope.is_cloud(path), path


def test_DR35_an_unknown_base_is_not_diffed(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("git must not run")
    monkeypatch.setattr(subprocess, "run", refuse)
    assert ci_scope.changed_files("", "abc") is None
    assert ci_scope.changed_files(ci_scope.UNKNOWN, "abc") is None
    assert ci_scope.changed_files("abc", "") is None


def test_DR35_git_lists_the_changed_files_and_a_failed_diff_is_unknown(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        code = 0 if args[-1] == "base...head" else 128
        return subprocess.CompletedProcess(args, code, stdout="ahcloud/a.py\ninfra/b.py\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ci_scope.changed_files("base", "head") == ["ahcloud/a.py", "infra/b.py"]
    assert calls[0] == ["git", "diff", "--name-only", "base...head"]
    assert ci_scope.changed_files("other", "head") is None


def test_DR35_main_prints_the_outputs_for_github(monkeypatch, capsys):
    monkeypatch.setattr(ci_scope, "changed_files", lambda base, head: CLOUD if (base, head) == ("b", "h") else None)
    assert ci_scope.main(["b", "h"]) == 0
    assert capsys.readouterr().out == 'cloud_only=true\noses=["ubuntu-latest"]\n'
    ci_scope.main([])
    assert capsys.readouterr().out.startswith("cloud_only=false\n")
