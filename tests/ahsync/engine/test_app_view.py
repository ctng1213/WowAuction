"""Tests for ahsync/engine/app_view.py: the app's version, hover and Addon texts (SPEC §3.7)."""
from __future__ import annotations

from ahsync.engine import app_view

HOUR, MINUTE = 3_600_000, 60_000
FRI_6_NOV = 1_793_923_200_000          # Fri 6 Nov 2026 00:00 UTC


def test_DR2_a_dev_build_and_a_release_read_their_version_and_hover():
    assert app_view.is_dev("0.0.0") and not app_view.is_dev("0.3.1")
    assert app_view.version_text("0.0.0", "") == "dev build"
    assert app_view.hover_text("0.0.0", FRI_6_NOV, 0) == "AHSync dev build"
    assert app_view.version_text("0.3.1", "2026-11-02") == "0.3.1 · built 2 Nov"
    assert app_view.hover_text("0.3.1", FRI_6_NOV + 14 * HOUR + 6 * MINUTE, 0) == "AHSync 0.3.1 · synced 14:06"


def test_version_pill_on_the_beta_channel_and_without_a_build_date():
    assert app_view.version_text("0.4.0", "2026-11-02", "beta") == "0.4.0 · beta · built 2 Nov"
    assert app_view.version_text("0.4.0", "") == "0.4.0"


def test_hover_before_the_first_sync_and_in_local_time():
    assert app_view.hover_text("0.3.1", None, 0) == "AHSync 0.3.1"
    assert app_view.hover_text("0.3.1", FRI_6_NOV + 13 * HOUR + 6 * MINUTE, HOUR) == "AHSync 0.3.1 · synced 14:06"


def test_DR3_DR4_the_addon_line_tells_a_wow_restart_from_a_reload():
    assert app_view.addon_text("installed", added_or_removed=3) == {
        "text": "AHProfit updated · restart WoW to load it", "class": ""}
    assert app_view.addon_text("installed", changed=1) == {"text": "AHProfit updated · /reload in game", "class": ""}
    assert app_view.addon_text("installed") == {"text": "AHProfit installed · up to date", "class": ""}


def test_FM49_the_addon_line_when_nothing_could_be_installed():
    assert app_view.addon_text("no_addons", r"C:\Games\WoW") == {
        "text": r"not installed: no Interface/AddOns under C:\Games\WoW", "class": "amber"}
    assert app_view.addon_text("not_writable") == {"text": "not installed: Interface/AddOns not writable",
                                                    "class": "amber"}
