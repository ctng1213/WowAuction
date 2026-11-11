"""Tests for engine/updates.py: tags, the release a channel offers, and the Update line (SPEC §3.7.8–§3.7.10)."""
from __future__ import annotations

import calendar

import pytest

from ahsync.engine import app_view, updates
from ahsync.engine.updates import Release, UpdateState

FRI_0600 = calendar.timegm((2026, 11, 6, 6, 0, 0)) * 1000      # Fri 6 Nov 06:00 UTC; offset 0 keeps it local
HOUR = 3_600_000
PAGE = "https://github.com/ctng1213/WowAuction/releases/tag/v0.4.0"


def text(state: UpdateState, now_ms: int = FRI_0600) -> dict:
    """The Update line at Fri 06:00, UTC."""
    return updates.update_text(state, now_ms, 0)


def test_DR14_tags_parse_by_numeric_parts_and_a_suffix_is_no_release():
    assert updates.parse_tag("v0.10.0") == (0, 10, 0)
    assert updates.parse_tag("v0.4.0-rc1") is None and updates.parse_tag("0.4.0") is None
    assert updates.parse_tag(None) is None and updates.parse_tag(4) is None
    assert updates.newer("v0.10.0", "0.9.2") and not updates.newer("v0.3.1", "0.3.1")
    assert not updates.newer("v0.3.0", "0.3.1") and not updates.newer("v0.4.0-rc1", "0.3.1")
    assert not updates.newer(None, "0.3.1")
    with pytest.raises(ValueError):
        updates.parse_version("dev")


def test_DR14_equal_older_and_suffixed_tags_read_up_to_date_and_numeric_order_wins():
    for tag in ("v0.3.1", "v0.3.0", None):                         # a suffixed tag is stored as no release
        assert text(UpdateState("0.3.1", checked_ms=FRI_0600, tag=tag)) == {
            "text": "up to date · checked 06:00", "url": None, "pill": None, "restart": False, "class": ""}
    assert text(UpdateState("0.9.2", checked_ms=FRI_0600, tag="v0.10.0", url=PAGE))["text"] == \
        "AHSync 0.10.0 is available · you run 0.9.2"


def test_DR13_a_newer_release_with_auto_update_off_is_announced():
    state = UpdateState("0.3.1", auto_update=False, checked_ms=FRI_0600, tag="v0.4.0", url=PAGE)
    assert text(state) == {"text": "AHSync 0.4.0 is available · you run 0.3.1", "url": PAGE,
                           "pill": "update available", "restart": False, "class": "amber"}


def test_DR20_DR25_a_verified_download_waits_for_the_next_start():
    ready = UpdateState("0.3.1", checked_ms=FRI_0600, tag="v0.4.0", url=PAGE, downloaded="0.4.0")
    assert text(ready) == {"text": "AHSync 0.4.0 downloaded · installs at the next start · you run 0.3.1",
                           "url": PAGE, "pill": "update ready · 0.4.0", "restart": True, "class": "amber"}
    beta = UpdateState("0.3.1", channel="beta", checked_ms=FRI_0600, tag="v0.4.0", url=PAGE, prerelease=True,
                       downloaded="0.4.0")
    assert text(beta)["text"] == "AHSync 0.4.0 (beta) downloaded · installs at the next start · you run 0.3.1"


def test_FM87_a_download_held_back_by_the_bridge_says_to_quit_claude_and_keeps_its_button():
    state = UpdateState("0.3.1", checked_ms=FRI_0600, tag="v0.4.0", url=PAGE, downloaded="0.4.0",
                        blocked=updates.BRIDGE_IN_USE)
    assert text(state) == {"text": "AHSync 0.4.0 downloaded · quit Claude Desktop (it runs the AI assistant bridge), "
                                   "then Restart to update · you run 0.3.1", "url": PAGE,
                           "pill": "update ready · 0.4.0", "restart": True, "class": "amber"}


def test_a_download_that_cannot_install_itself_points_at_the_release_page():     # FM54's text (M27 installs)
    state = UpdateState("0.3.1", checked_ms=FRI_0600, tag="v0.4.0", url=PAGE, downloaded="0.4.0", cant_replace=True)
    assert text(state) == {"text": "Update downloaded · AHSync can't replace itself here; install it from the release "
                                   "page", "url": PAGE, "pill": None, "restart": False, "class": "amber"}


@pytest.mark.parametrize("reason", ["download cut off", "checksum mismatch", "not in SHA256SUMS"])
def test_FM53_a_failed_download_says_why_and_tries_again_tomorrow(reason):
    state = UpdateState("0.3.1", checked_ms=FRI_0600, tag="v0.4.0", url=PAGE, download_failed=reason)
    assert text(state) == {"text": f"Update to 0.4.0 failed: {reason} · still on 0.3.1 · tries again tomorrow",
                           "url": None, "pill": None, "restart": False, "class": "amber"}


def test_FM52_a_check_without_a_release_is_muted_with_no_pill_and_dates_from_the_first_failure():
    offline = UpdateState("0.3.1", checked_ms=FRI_0600 + 24 * HOUR, failure=updates.OFFLINE, failed_since_ms=FRI_0600)
    assert text(offline) == {"text": "not checked: offline since Fri 06:00", "url": None, "pill": None,
                             "restart": False, "class": "muted"}
    assert text(UpdateState("0.3.1", checked_ms=FRI_0600, failure=updates.NOT_FOUND))["text"] == \
        "not checked: no release yet"
    assert text(UpdateState("0.3.1", checked_ms=FRI_0600, failure=updates.UNAVAILABLE))["text"] == \
        "not checked: GitHub unavailable since Fri 06:00"            # no first-failure time: the check's own


def test_DR24_a_dev_build_and_a_build_never_checked():
    assert text(UpdateState("0.0.0", checked_ms=FRI_0600, tag="v0.4.0")) == {
        "text": "not checked for a dev build", "url": None, "pill": None, "restart": False, "class": "muted"}
    assert text(UpdateState("0.3.1"))["text"] == "not checked yet"


def test_DR26_leaving_the_beta_never_downgrades_and_says_so():
    assert text(UpdateState("0.4.1", checked_ms=FRI_0600, tag="v0.4.0", left_beta=True))["text"] == \
        "up to date · stays on 0.4.1 until a newer stable release"
    assert text(UpdateState("0.4.1", channel="beta", checked_ms=FRI_0600, tag="v0.4.0", left_beta=True))["text"] == \
        "up to date · checked 06:00"
    assert text(UpdateState("0.4.1", checked_ms=FRI_0600, tag="v0.4.1", left_beta=True))["text"] == \
        "up to date · checked 06:00"                                 # a stable release caught up
    assert text(UpdateState("0.4.1", checked_ms=FRI_0600, left_beta=True))["text"] == "up to date · checked 06:00"


def test_an_install_s_result_reads_until_a_newer_release_is_found():
    done = UpdateState("0.4.0", checked_ms=FRI_0600, tag="v0.4.0", updated_to="0.4.0", updated_ms=FRI_0600 + 2 * 60_000)
    assert text(done)["text"] == "Updated to AHSync 0.4.0 · today 06:02"
    assert text(done, FRI_0600 + 24 * HOUR)["text"] == "Updated to AHSync 0.4.0 · Fri 06:02"
    later = UpdateState("0.4.0", checked_ms=FRI_0600, tag="v0.5.0", url=PAGE, updated_to="0.4.0", updated_ms=FRI_0600)
    assert text(later)["text"] == "AHSync 0.5.0 is available · you run 0.4.0"


def test_DR25_the_stable_channel_reads_latest_and_the_beta_the_highest_of_the_list():
    latest = {"tag_name": "v0.3.1", "html_url": "p31", "prerelease": False}
    listed = [{"tag_name": "v0.4.0", "html_url": "p40", "prerelease": True},
              {"tag_name": "v0.3.1", "html_url": "p31", "prerelease": False},
              {"tag_name": "v0.9.0", "html_url": "p90", "draft": True},
              {"tag_name": "v0.6.0-rc1", "html_url": "rc"}, "junk"]
    assert updates.pick_release(latest, None, "stable") == Release("v0.3.1", updates.RELEASE_PAGE + "v0.3.1", False)
    assert updates.pick_release(None, listed, "beta") == Release("v0.4.0", updates.RELEASE_PAGE + "v0.4.0", True)
    newest_first = [{"tag_name": "v0.4.0", "html_url": "p40"}, {"tag_name": "v0.5.0", "html_url": "p50",
                                                                  "prerelease": True}]
    assert updates.pick_release(None, newest_first, "beta").tag == "v0.5.0"      # the highest, not the newest date
    assert updates.pick_release({"tag_name": "v0.4.0-rc1"}, None, "stable") is None
    assert updates.pick_release(None, None, "beta") is None and updates.pick_release(None, None, "stable") is None
    hostile = {"tag_name": "v1.0.0", "html_url": "javascript:alert(1)"}
    assert updates.pick_release(hostile, None, "stable") == \
        Release("v1.0.0", updates.RELEASE_PAGE + "v1.0.0", False)            # the page never comes from the answer


def test_D98_the_line_links_the_release_name_only():
    ready = updates.line("AHSync 0.4.0 downloaded · installs at the next start · you run 0.3.1", "amber", PAGE)
    assert app_view.link_parts(ready) == [["AHSync 0.4.0", PAGE],
                                         [" downloaded · installs at the next start · you run 0.3.1", None]]
    assert app_view.link_parts(updates.line("up to date · checked 06:00", "")) == [["up to date · checked 06:00", None]]
    page = updates.line("Update downloaded · install it from the release page", "amber", PAGE)
    assert app_view.link_parts(page) == [["Update downloaded · install it from the release page", PAGE]]


def test_only_ascii_digits_make_a_tag_and_a_bad_stored_tag_never_breaks_the_line():
    assert updates.parse_tag("v٠.٤.٠") is None                  # Arabic-Indic digits
    assert text(UpdateState("0.4.1", checked_ms=FRI_0600, tag="vbad", left_beta=True))["text"] == \
        "up to date · checked 06:00"
