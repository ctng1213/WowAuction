"""Automatic updates, the download half: fetch, verify and keep the release this OS installs (SPEC §3.7.9; DR20, DR21, FM53).

After a check found a newer release (`app/update_check.py`) and only with `auto_update` on, the release's
`SHA256SUMS` and this OS's asset are downloaded into `<data>/updates/` (a `.part` file renamed once verified),
the hash is compared and the platform's signature check runs. Any failure deletes the file and records the reason;
the next try is a day later. Installing the file is M27's (§3.7.9 step 5).
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from pathlib import Path

from ahsync.clock import Clock
from ahsync.collect.client import USER_AGENT, HttpRequest, Transport, TransportError
from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine.app_view import is_dev
from ahsync.engine.updates import UpdateState, newer, ready, tag_version
from ahsync.app.update_check import DAY_MS, read_meta, read_state, write_meta

log = logging.getLogger("ahsync.update")

DOWNLOADS = "https://github.com/ctng1213/WowAuction/releases/download"
SUMS = "SHA256SUMS"
UPDATES_DIR = "updates"
CUT_OFF, MISMATCH, NOT_LISTED = "download cut off", "checksum mismatch", "not in SHA256SUMS"
UNVERIFIED = "could not be verified"                   # the signature check itself failed (DR30)


class DownloadFailed(Exception):
    """A download that can't be installed; the message is the reason the Update line shows (FM53)."""


def download(db: Database, transport: Transport, clock: Clock, data_dir: Path, version: str, config: Config,
             asset_name: Callable[[str], str], verify: Callable[[Path], str | None]) -> bool:
    """Download and verify the newer release, when one is known and due (SPEC §3.7.9; DR20, DR21, DR24, FM53).

    Args:
        db: The database (`update.*` in meta).
        transport: Sends the requests; it follows GitHub's redirect to its asset host.
        clock: Now.
        data_dir: The app's data folder; the file goes to `<data>/updates/`.
        version: The running version.
        config: `auto_update`.
        asset_name: Version -> this OS's asset, e.g. "AHSync-0.4.0-windows-x64-setup.exe" (the platform's).
        verify: The platform's signature check: None when the file may be installed, else the reason.

    Returns:
        True when a download was attempted.

    Side effects:
        Writes or deletes files in `<data>/updates/`; `update.*` in meta; one log line.
    """
    state = read_state(db, version, config)
    folder = data_dir / UPDATES_DIR
    if state.downloaded is not None and not ready(state):
        discard(db, folder)                            # withdrawn, replaced, another channel or auto-update off (DR30)
    if not wanted(state, config) or ready(state):
        return False
    target = tag_version(state.tag)
    tried = read_meta(db).get("download_tried_ms", "")
    now = clock.now_ms()
    if tried.isdigit() and now - int(tried) < DAY_MS:
        return False                                       # one try a day: "tries again tomorrow"
    write_meta(db, {"download_tried_ms": now})
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = fetch_verified(transport, folder, state.tag, asset_name(target))
        reason = checked(verify, path)
        if reason is not None:
            path.unlink(missing_ok=True)
            raise DownloadFailed(reason)
    except (DownloadFailed, OSError) as error:
        reason = str(error) if isinstance(error, DownloadFailed) else CUT_OFF
        log.warning("update download of %s failed: %s", target, reason)
        discard(db, folder)                                # an older waiting file goes too (DR30)
        write_meta(db, {"download_failed": reason})
        return True
    remove_others(folder, path)
    write_meta(db, {"downloaded": target, "downloaded_path": str(path), "download_failed": None})
    log.info("update %s downloaded and verified: %s", target, path.name)
    return True


def wanted(state: UpdateState, config: Config) -> bool:
    """Whether the release the channel offers should be downloaded now (SPEC §3.7.9 safety rules; DR21, DR24, DR30).

    Args:
        state: The stored update state.
        config: `update_check`, `auto_update` and the channel.

    Returns:
        False from source, with `update_check` or `auto_update` off, for a release that isn't newer, and for a
        pre-release on the stable channel.
    """
    return (not is_dev(state.version) and config.update_check and config.auto_update and newer(state.tag, state.version)
            and not (state.prerelease and config.update_channel != "beta"))


def checked(verify: Callable[[Path], str | None], path: Path) -> str | None:
    """Run the platform's signature check; a check that raises refuses the file (DR30).

    Args:
        verify: The platform's check.
        path: The file, its hash checked.

    Returns:
        None when it may be installed; else the reason.
    """
    try:
        return verify(path)
    except Exception as error:                           # ditto or codesign failing (M27): never install it
        log.warning("the update's signature check failed: %s", type(error).__name__)
        return UNVERIFIED


def discard(db: Database, folder: Path) -> None:
    """Forget a waiting download and remove every file in `updates/` (DR30, FM53).

    Args:
        db: The database.
        folder: `<data>/updates/`.
    """
    if folder.exists():
        for item in folder.iterdir():
            if item.is_file():
                try:
                    item.unlink()
                except OSError:
                    log.info("an update file is still in use: %s", item.name)
    write_meta(db, {"downloaded": None, "downloaded_path": None})


def fetch_verified(transport: Transport, folder: Path, tag: str, asset: str) -> Path:
    """Fetch SHA256SUMS and the asset, check the hash, and rename the `.part` file into place (FM53).

    Args:
        transport: Sends the requests.
        folder: `<data>/updates/`.
        tag: The release, "v0.4.0".
        asset: This OS's asset name.

    Returns:
        The verified file.

    Raises:
        DownloadFailed: "download cut off", "not in SHA256SUMS" or "checksum mismatch"; nothing is left behind.
    """
    sums = parse_sums(get(transport, f"{DOWNLOADS}/{tag}/{SUMS}").decode("utf-8", errors="replace"))
    if asset not in sums:
        raise DownloadFailed(NOT_LISTED)
    body = get(transport, f"{DOWNLOADS}/{tag}/{asset}")
    part = folder / f"{asset}.part"
    try:
        part.write_bytes(body)
        if hashlib.sha256(body).hexdigest() != sums[asset]:
            raise DownloadFailed(MISMATCH)
        path = folder / asset
        part.replace(path)
    except (OSError, DownloadFailed) as error:
        part.unlink(missing_ok=True)
        raise error if isinstance(error, DownloadFailed) else DownloadFailed(CUT_OFF) from None
    return path


def get(transport: Transport, url: str) -> bytes:
    """One download: the whole body or a failure (no AHF key, no retries: the next try is tomorrow).

    Args:
        transport: Sends the request.
        url: The release file.

    Returns:
        The body.

    Raises:
        DownloadFailed: CUT_OFF for a network error, any status but 200, or a body shorter than its Content-Length.
    """
    try:
        response = transport(HttpRequest(url, {"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}))
    except TransportError:
        raise DownloadFailed(CUT_OFF) from None
    length = response.headers.get("content-length", "")
    if response.status != 200 or (length.isdigit() and int(length) != len(response.body)):
        raise DownloadFailed(CUT_OFF)
    return response.body


def parse_sums(text: str) -> dict[str, str]:
    """`SHA256SUMS`: one `<sha256>  <file>` line per asset (sha256sum's text or binary form).

    Args:
        text: The file.

    Returns:
        File name -> lower-case hex digest; lines that don't fit are skipped.
    """
    sums = {}
    for line in text.splitlines():
        digest, _, name = line.strip().partition(" ")
        name = name.strip().lstrip("*")
        if len(digest) == 64 and name:
            sums[name] = digest.lower()
    return sums


def remove_others(folder: Path, keep: Path) -> None:
    """Remove older downloads, so only the release waiting to install stays in `updates/`.

    Args:
        folder: `<data>/updates/`.
        keep: The file just verified.
    """
    for other in folder.iterdir():
        if other != keep and other.is_file():
            try:
                other.unlink()
            except OSError:
                log.info("an older update file is still in use: %s", other.name)
