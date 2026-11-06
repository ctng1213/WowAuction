"""Tests for tools/build_lambda.py: the Lambda asset holds our two packages and nothing else (AR9)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import build_lambda

ROOT = Path(__file__).resolve().parents[2]


def test_AR9_the_asset_holds_ahsync_and_ahcloud_with_their_data_and_no_third_party_code(tmp_path):
    files = build_lambda.build(ROOT, tmp_path / "lambda")
    names = {f.as_posix() for f in files}
    assert {"ahcloud/collector.py", "ahsync/collect/blizzard.py", "ahsync/data/bonus_suffixes.json",
            "ahsync/db/schema.sql"} <= names
    assert {f.parts[0] for f in files} == {"ahsync", "ahcloud"}               # no ahsync_app, tools, infra, tests
    assert not any("web/static" in n or "__pycache__" in n for n in names)
    assert all((tmp_path / "lambda" / f).read_bytes() == (ROOT / f).read_bytes() for f in files)


def test_a_rebuild_removes_stale_files_and_the_root_is_never_replaced(tmp_path):
    out = tmp_path / "lambda"
    build_lambda.build(ROOT, out)
    (out / "stale.py").write_text("x")
    build_lambda.build(ROOT, out)
    assert not (out / "stale.py").exists()
    with pytest.raises(ValueError, match="refusing"):
        build_lambda.build(ROOT, ROOT)
    with pytest.raises(ValueError, match="refusing"):
        build_lambda.build(ROOT, ROOT.parent)
