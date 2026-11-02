"""Updates, the pure half: release tags, which release to offer, and every Update-line text (SPEC §3.7.8–§3.7.10).

No I/O: `app/update_check.py` and `app/updater.py` store what happened in `meta` as an `UpdateState`, and this
module turns it into the line the Status card, the top bar's pill and the tray show.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from ahsync.engine.app_view import is_dev
from ahsync.engine.status_view import weekday_time

TAG = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)")      # vMAJOR.MINOR.PATCH, ASCII digits only (SPEC §3.7.11)
VERSION = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")
RELEASE_PAGE = "https://github.com/ctng1213/WowAuction/releases/tag/"   # + tag: never a URL from the answer
OFFLINE, NOT_FOUND, UNAVAILABLE = "offline", "not_found", "unavailable"    # a check that got no release (FM52)


@dataclass(frozen=True)
class Release:
    """The release a channel offers (SPEC §3.7.8)."""

    tag: str                 # "v0.4.0"
    url: str                 # its release page
    prerelease: bool         # a beta (only the beta channel offers one, SPEC §3.7.10)


@dataclass(frozen=True)
class UpdateState:
    """What the checker and the updater recorded in `meta`, plus the running app's settings (SPEC §3.7.8, §3.7.9)."""

    version: str                         # the running version, "0.3.1"; "0.0.0" from source
    channel: str = "stable"              # "stable" or "beta"
    auto_update: bool = True
    checked_ms: int | None = None        # the last check that was sent, UTC ms
    tag: str | None = None               # the release the channel offered at the last answer; None: none
    url: str | None = None               # its page
    prerelease: bool = False
    failure: str | None = None           # OFFLINE, NOT_FOUND or UNAVAILABLE when the last check got no release
    failed_since_ms: int | None = None   # the first failed check since the last answer
    downloaded: str | None = None        # a verified download waiting to install, "0.4.0"
    download_failed: str | None = None   # why the last download of `tag` failed, e.g. "checksum mismatch"
    cant_replace: bool = False           # macOS: the app's folder can't be written (FM54, M27)
    updated_to: str | None = None        # the version the last install brought, "0.4.0" (M27)
    updated_ms: int | None = None        # when that install was found to have worked
    left_beta: bool = False              # switched from the beta to the stable channel (DR26)


def parse_tag(tag: object) -> tuple[int, int, int] | None:
    """A release tag's version (SPEC §3.7.11; DR14).

    Args:
        tag: A tag such as "v0.10.0".

    Returns:
        (major, minor, patch); None for anything else, a suffix ("v0.4.0-rc1") included.
    """
    match = TAG.fullmatch(tag) if isinstance(tag, str) else None
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def parse_version(version: str) -> tuple[int, int, int]:
    """The running version's parts.

    Args:
        version: "MAJOR.MINOR.PATCH".

    Returns:
        (major, minor, patch).

    Raises:
        ValueError: For anything else (a build always has a plain version).
    """
    match = VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"not a version: {version!r}")
    return int(match[1]), int(match[2]), int(match[3])


def newer(tag: str | None, version: str) -> bool:
    """Whether a release is newer than the running version, by numeric parts (DR14, DR24).

    Args:
        tag: The release's tag; None for no release.
        version: The running version.

    Returns:
        True only for a plain tag above the version: never an equal, older or suffixed one.
    """
    parts = parse_tag(tag)
    return parts is not None and parts > parse_version(version)


def tag_version(tag: str) -> str:
    """A plain tag's version text.

    Args:
        tag: "v0.4.0".

    Returns:
        "0.4.0".
    """
    return tag[1:]


def pick_release(latest: object, releases: object, channel: str) -> Release | None:
    """The release a channel offers (SPEC §3.7.8, §3.7.10; DR25).

    Args:
        latest: The decoded `releases/latest` body (stable channel); None when not asked.
        releases: The decoded `releases?per_page=20` list (beta channel); None when not asked.
        channel: "stable" or "beta".

    Returns:
        Stable: the latest release when its tag is plain. Beta: the highest plain tag of the list, pre-releases
        included, drafts never (the highest version, not the newest date). None when there is none. The page is
        built from the tag, never taken from the answer (it becomes a link and a browser open).
    """
    if channel != "beta":
        candidates = [latest] if isinstance(latest, dict) else []
    else:
        candidates = [r for r in releases if isinstance(r, dict) and not r.get("draft")] \
            if isinstance(releases, list) else []
    plain = [r for r in candidates if parse_tag(r.get("tag_name")) is not None]
    if not plain:
        return None
    best = max(plain, key=lambda r: (parse_tag(r["tag_name"]), r["tag_name"]))
    return Release(best["tag_name"], RELEASE_PAGE + best["tag_name"], bool(best.get("prerelease")))


def ready(state: UpdateState) -> bool:
    """Whether a download waits to install: the channel still offers it and auto-update is on (SPEC §3.7.9; DR30).

    The texts, the tray, the updater (which removes a file that no longer applies) and M27's install all ask this.

    Args:
        state: The stored update state.

    Returns:
        True for a download of the offered tag, with `auto_update` on, that is not a pre-release on the stable channel.
    """
    return (state.downloaded is not None and state.auto_update and state.tag == f"v{state.downloaded}"
            and not (state.prerelease and state.channel != "beta"))


def line(text: str, cls: str, url: str | None = None, pill: str | None = None, restart: bool = False) -> dict:
    """One Update line.

    Args:
        text: The line.
        cls: "amber", "muted" or "" (normal).
        url: The release page it links to; None: no link.
        pill: The top bar's pill; None hides it.
        restart: Whether the Restart to update button is offered.

    Returns:
        {"text", "url", "pill", "restart", "class"} (D97).
    """
    return {"text": text, "url": url, "pill": pill, "restart": restart, "class": cls}


def when(ms: int, now_ms: int, offset_ms: int) -> str:
    """A moment for a result line: "today 06:02" on the same local day, else "Fri 06:02".

    Args:
        ms: The moment, UTC ms.
        now_ms: Now, UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        The text.
    """
    same_day = (ms + offset_ms) // 86_400_000 == (now_ms + offset_ms) // 86_400_000
    return f"today {clock_time(ms, offset_ms)}" if same_day else weekday_time(ms, offset_ms)


def clock_time(ms: int, offset_ms: int) -> str:
    """A local time of day, "06:00".

    Args:
        ms: UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        The text.
    """
    return time.strftime("%H:%M", time.gmtime((ms + offset_ms) // 1000))


def update_text(state: UpdateState, now_ms: int, offset_ms: int) -> dict:
    """The Update line, the top bar's pill and the restart flag for every state (SPEC §3.7.8–§3.7.10).

    In order: a dev build; an install's result; a download waiting (or one that can't install itself); a failed
    download; never checked; a check that got no release; a newer release; up to date.

    Args:
        state: What was recorded.
        now_ms: Now, UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        line()'s dict.
    """
    if is_dev(state.version):
        return line("not checked for a dev build", "muted")
    if state.updated_to is not None and state.updated_ms is not None and not newer(state.tag, state.version):
        return line(f"Updated to AHSync {state.updated_to} · {when(state.updated_ms, now_ms, offset_ms)}", "")
    if ready(state):
        return waiting(state)
    if state.download_failed is not None and state.tag is not None:
        return line(f"Update to {tag_version(state.tag)} failed: {state.download_failed} · still on {state.version} "
                    f"· tries again tomorrow", "amber")
    if state.checked_ms is None:
        return line("not checked yet", "muted")
    if state.failure is not None:
        return not_checked(state, offset_ms)
    if newer(state.tag, state.version):
        return line(f"AHSync {tag_version(state.tag)} is available · you run {state.version}", "amber", state.url,
                    "update available")
    stable_tag = parse_tag(state.tag) if state.channel != "beta" else None
    if state.left_beta and stable_tag is not None and stable_tag < parse_version(state.version):
        return line(f"up to date · stays on {state.version} until a newer stable release", "")
    return line(f"up to date · checked {clock_time(state.checked_ms, offset_ms)}", "")


def waiting(state: UpdateState) -> dict:
    """A verified download waiting for the next start (DR20, DR25), or one that can't install itself (FM54).

    Args:
        state: With `downloaded` set.

    Returns:
        line()'s dict.
    """
    if state.cant_replace:
        return line("Update downloaded · AHSync can't replace itself here; install it from the release page",
                    "amber", state.url)
    beta = " (beta)" if state.prerelease else ""
    return line(f"AHSync {state.downloaded}{beta} downloaded · installs at the next start · you run {state.version}",
                "amber", state.url, f"update ready · {state.downloaded}", True)


def not_checked(state: UpdateState, offset_ms: int) -> dict:
    """A check that got no release: offline, none published yet, or GitHub unavailable (FM52).

    Args:
        state: With `failure` set.
        offset_ms: Local time minus UTC, ms.

    Returns:
        line()'s dict, muted, with no pill.
    """
    if state.failure == NOT_FOUND:
        return line("not checked: no release yet", "muted")
    since = weekday_time(state.failed_since_ms or state.checked_ms or 0, offset_ms)
    what = "offline" if state.failure == OFFLINE else "GitHub unavailable"
    return line(f"not checked: {what} since {since}", "muted")
