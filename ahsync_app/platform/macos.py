"""macOS: ~/Library/Application Support, the macOS WoW folders, translocation, the LaunchAgent and the
signed-update swap (SPEC §3.7.2, §3.7.9)."""
from __future__ import annotations

import os
import plistlib
import re
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from ahsync_app.platform.base import APP_NAME, NOT_SIGNED, TRANSLOCATED, WOW_NAMES, AppDirs, Platform, Processes

# Verified 2026-11-06 on the user's Mac: the release installs into /Applications/World of Warcraft/_forever_ (§6 item 20)
WOW_ROOT = PurePosixPath("/Applications/World of Warcraft")
TRANSLOCATION_PREFIX = "/private/var/folders/"
TRANSLOCATION_PART = "/AppTranslocation/"
KEY_FILE_MODE = 0o600
AGENT_LABEL = "com.wowauction.ahsync"
CA_BUNDLE = "/etc/ssl/cert.pem"     # the system's PEM bundle (FM58)
APP_BUNDLE = f"{APP_NAME}.app"
TEAM_ID = re.compile(r"[A-Z0-9]{10}")    # an Apple Developer Team ID, e.g. ABCDE12345
SWAP_SCRIPT = "swap.sh"
# The swap helper (SPEC §3.7.9; DR23, FM55): waits for AHSync to end, moves the running bundle aside, moves the new
# one in and opens it; when either move or the open fails it puts the previous app back and opens that. Its arguments
# are the pid, the bundle and the new app, so no path is ever spliced into the script; it refuses anything that isn't
# an .app, waits for the app at most 2 minutes (240 × 0.5 s; a reused pid can't hold it forever) and stops when an
# old roll-back copy can't be removed.
SWAP = """#!/bin/sh
# AHSync's update swap (SPEC §3.7.9), written and started by the app as it quits.
pid="$1"; bundle="$2"; new="$3"; tries="${4:-240}"; previous="$2.previous"
case "$bundle" in *.app) ;; *) exit 1 ;; esac
case "$new" in *.app) ;; *) exit 1 ;; esac
while kill -0 "$pid" 2>/dev/null; do
  tries=$((tries - 1)); [ "$tries" -le 0 ] && exit 1
  sleep 0.5
done
rm -rf "$previous"
[ -e "$previous" ] && { open "$bundle"; exit 1; }
mv "$bundle" "$previous" || { open "$bundle"; exit 1; }
if mv "$new" "$bundle" && open "$bundle"; then exit 0; fi
rm -rf "$bundle"
mv "$previous" "$bundle"
open "$bundle"
exit 1
"""


class MacPlatform(Platform):
    """The macOS answers, over an injected home folder and chmod."""

    name = "macos"

    def __init__(self, home: Path, chmod: Callable[[Path, int], None] = os.chmod, processes: Processes | None = None,
                 writable: Callable[[Path], bool] = lambda folder: os.access(folder, os.W_OK)) -> None:
        """Keep what the answers depend on.

        Args:
            home: The user's home folder, e.g. /Users/u.
            chmod: Changes a file's mode (os.chmod; a fake in tests).
            processes: Runs `ditto`, `codesign` and the swap helper (real ones by default; a recorder in tests).
            writable: Whether the user may write into a folder (FM54).
        """
        self.home = home
        self.chmod = chmod
        self.processes = processes or Processes()
        self.writable = writable

    def bridge_command(self, exe: Path) -> tuple[str, ...]:
        """The app's own binary started with `--mcp`: a bundle's binary has stdin and stdout (SPEC §3.8.2).

        Args:
            exe: The binary inside AHSync.app.

        Returns:
            (its path, "--mcp").
        """
        return (str(exe), "--mcp")

    def app_dirs(self) -> AppDirs:
        """`~/Library/Application Support/AHSync/config.toml` and `…/data` (SPEC §3.7.3, DR1).

        Returns:
            The two paths.
        """
        root = PurePosixPath(Path(self.home).as_posix()) / "Library" / "Application Support" / APP_NAME
        return AppDirs(Path(str(root / "config.toml")), Path(str(root / "data")),
                       f"~/Library/Application Support/{APP_NAME}")

    def wow_candidates(self) -> list[Path]:
        """`/Applications/World of Warcraft/<name>`, `_forever_` first (SPEC §3.7.3, §6 item 20).

        Returns:
            The folders, most likely first.
        """
        return [Path(str(WOW_ROOT / name)) for name in WOW_NAMES]

    def restrict_file(self, path: Path) -> None:
        """chmod 0600, so only the user can read the key file (SPEC §3.7.4).

        Args:
            path: The key file.

        Raises:
            OSError: When the mode cannot be changed.
        """
        self.chmod(path, KEY_FILE_MODE)

    def run_location_problem(self, exe: Path) -> str | None:
        """An app opened from the dmg or Downloads runs translocated under a random path (SPEC §3.7.5, DR11).

        Args:
            exe: The running executable.

        Returns:
            TRANSLOCATED under /private/var/folders/…/AppTranslocation/; else None.
        """
        posix = Path(exe).as_posix()
        return TRANSLOCATED if posix.startswith(TRANSLOCATION_PREFIX) and TRANSLOCATION_PART in posix else None

    def agent_file(self) -> Path:
        """`~/Library/LaunchAgents/com.wowauction.ahsync.plist` (SPEC §3.7.5).

        Returns:
            The LaunchAgent's path.
        """
        return Path(self.home) / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"

    def autostart_enable(self, exe: Path, args: tuple[str, ...] = ()) -> None:
        """Write the LaunchAgent that starts `<exe> --autostart` at login (SPEC §3.7.5, DR10).

        Only the file: launchd loads the folder at each login, and `launchctl` is never run (`bootout` would stop
        an AHSync that login started). Unchanged bytes are left alone; a change is a temp file and a rename.

        Args:
            exe: The executable inside the app bundle.
            args: Arguments before --autostart (from source: the script).

        Raises:
            OSError: When the file can't be written; no temp file is left.
        """
        agent = self.agent_file()
        text = plistlib.dumps({"Label": AGENT_LABEL, "ProgramArguments": [Path(exe).as_posix(), *args, "--autostart"],
                               "RunAtLoad": True})
        try:
            if agent.read_bytes() == text:
                return
        except OSError:
            pass                       # missing or unreadable: write it
        agent.parent.mkdir(parents=True, exist_ok=True)
        temp = agent.with_name(f"{agent.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_bytes(text)
            os.replace(temp, agent)
        except OSError:
            temp.unlink(missing_ok=True)
            raise

    def autostart_disable(self) -> None:
        """Delete the LaunchAgent; nothing happens when there is none (DR10). AHSync keeps running.

        Raises:
            OSError: When the file can't be removed.
        """
        self.agent_file().unlink(missing_ok=True)

    def autostart_enabled(self) -> bool:
        """Whether the LaunchAgent file exists (DR10).

        Returns:
            True when AHSync starts at login.
        """
        return self.agent_file().exists()

    def update_asset_name(self, version: str) -> str:
        """The signed app zip this OS takes from a release (SPEC §3.7.9; DR20).

        Args:
            version: "MAJOR.MINOR.PATCH".

        Returns:
            "AHSync-<version>-macos-arm64.zip".
        """
        return f"{APP_NAME}-{version}-macos-arm64.zip"

    def verify_update(self, path: Path, exe: Path) -> str | None:
        """Unpack the downloaded zip with `ditto -xk` and check the app's signature against the running one (DR23).

        Never Python's `zipfile`: it drops the bundle's symlinks and executable bits, which breaks the signature and
        the launch. The download's Team ID must equal the running app's own, never a constant (a fork's build then
        updates only from its own team).

        Args:
            path: `<data>/updates/AHSync-<ver>-macos-arm64.zip`, its hash checked.
            exe: The running executable, `…/AHSync.app/Contents/MacOS/AHSync`.

        Returns:
            None when `<data>/updates/AHSync.app` may be installed; else NOT_SIGNED (the unpacked app removed).

        Raises:
            OSError, RuntimeError: When `ditto` or `codesign` can't run or the zip doesn't unpack; the updater then
                refuses the file as unverifiable.
        """
        app = path.parent / APP_BUNDLE
        shutil.rmtree(app, ignore_errors=True)
        if self.processes.run(["ditto", "-xk", str(path), str(path.parent)]).code != 0 or not app.is_dir():
            raise RuntimeError("the update zip did not unpack")
        reason = self.check_app(app, exe)
        if reason is not None:
            shutil.rmtree(app, ignore_errors=True)
        return reason

    def check_app(self, app: Path, exe: Path) -> str | None:
        """Whether an unpacked app is validly signed by the running app's own team (DR23).

        Args:
            app: The unpacked `AHSync.app`.
            exe: The running executable.

        Returns:
            None when it is; else NOT_SIGNED (also when the running app isn't a bundle, so has no team to match).
        """
        bundle = bundle_of(exe)
        ours = self.team_id(bundle) if bundle is not None else None
        if ours is None or not TEAM_ID.fullmatch(ours):
            return NOT_SIGNED
        # The requirement makes codesign itself demand an Apple-issued certificate of our team, not just a bundle that
        # matches its own embedded requirement (a self-signed one would; the security review, 2026-11-07).
        requirement = f'=anchor apple generic and certificate leaf[subject.OU] = "{ours}"'
        signed = self.processes.run(["codesign", "--verify", "--deep", "--strict", f"-R{requirement}", str(app)]).code == 0
        return None if signed and self.team_id(app) == ours else NOT_SIGNED

    def team_id(self, bundle: Path) -> str | None:
        """A bundle's TeamIdentifier, from `codesign -dv` (it writes to stderr).

        Args:
            bundle: An `.app` folder.

        Returns:
            The Team ID; None when unsigned, ad hoc ("not set") or unreadable.
        """
        ran = self.processes.run(["codesign", "-dv", str(bundle)])
        for line in (ran.err + ran.out).splitlines():
            if line.startswith("TeamIdentifier="):
                team = line.split("=", 1)[1].strip()
                return team if team and team != "not set" else None
        return None

    def can_replace(self, exe: Path) -> bool:
        """Whether the user may write into the folder holding the bundle (FM54).

        Args:
            exe: The running executable.

        Returns:
            True when the swap can move the bundle; False when the app isn't a bundle (a source run).
        """
        bundle = bundle_of(exe)
        return bundle is not None and self.writable(bundle.parent)

    def apply_update(self, path: Path, exe: Path) -> None:
        """Write the swap helper into `updates/` and start it detached; the shell exits right after (DR23, FM55).

        Args:
            path: The verified zip; its unpacked `AHSync.app` sits beside it.
            exe: The running executable; the bundle is derived from it, never a constant path.

        Raises:
            OSError: When the running app isn't a bundle, the unpacked app no longer verifies (it may be days old), or
                the helper can't be written or started.
        """
        folder = path.parent
        bundle = bundle_of(exe)
        if bundle is None or self.check_app(folder / APP_BUNDLE, exe) is not None:
            raise OSError("the update can't be installed: the app or the unpacked update no longer verifies")
        script = folder / SWAP_SCRIPT
        script.write_text(SWAP, encoding="utf-8")
        self.chmod(script, 0o700)
        self.processes.spawn(["/bin/sh", str(script), str(self.processes.pid), str(bundle), str(folder / APP_BUNDLE)],
                             folder / "swap.log")

    def forget_previous(self, exe: Path) -> None:
        """Remove `AHSync.app.previous` beside the bundle, the swap's roll-back copy, once the update worked.

        Args:
            exe: The running executable, the new version.
        """
        bundle = bundle_of(exe)
        previous = bundle.with_name(bundle.name + ".previous") if bundle is not None else None
        if previous is not None and previous.is_dir():
            shutil.rmtree(previous, ignore_errors=True)

    def ca_bundle(self) -> Path | None:
        """`/etc/ssl/cert.pem`, the system's CA bundle (FM58): the bundled OpenSSL looks in the build machine's
        Python folder, which a user's Mac doesn't have.

        Returns:
            The bundle's path; not probed here.
        """
        return Path(CA_BUNDLE)


def bundle_of(exe: Path) -> Path | None:
    """The `.app` bundle holding an executable, three folders up from `…/AHSync.app/Contents/MacOS/AHSync`.

    The swap moves and removes what this returns, so the path's shape is checked, never trusted.

    Args:
        exe: The running executable.

    Returns:
        The bundle folder; None unless the executable sits in `<name>.app/Contents/MacOS/` (a source run's Python).
    """
    if len(exe.parts) < 4 or exe.parts[-3:-1] != ("Contents", "MacOS") or exe.parents[2].suffix != ".app":
        return None
    return exe.parents[2]
