"""Tests for ahsync/version.py: the source tree is a dev build (SPEC §3.7.11)."""
from __future__ import annotations

from ahsync import version


def test_DR2_source_holds_the_dev_version_and_no_build_date():
    assert (version.VERSION, version.BUILT) == ("0.0.0", "")
