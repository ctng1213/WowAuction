"""macOS: ~/Library/Application Support, the macOS WoW folders, translocation and the LaunchAgent; the
signed-update swap comes in M27 (SPEC §3.7.2)."""
from __future__ import annotations

import os
import plistlib
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from ahsync_app.platform.base import APP_NAME, TRANSLOCATED, WOW_NAMES, AppDirs, Platform

# UNVERIFIED: inferred from a forum post naming /Applications/World of Warcraft/_classic_beta_/WTF/ (SPEC §6 item 20)
WOW_ROOT = PurePosixPath("/Applications/World of Warcraft")
TRANSLOCATION_PREFIX = "/private/var/folders/"
TRANSLOCATION_PART = "/AppTranslocation/"
KEY_FILE_MODE = 0o600
AGENT_LABEL = "com.wowauction.ahsync"
CA_BUNDLE = "/etc/ssl/cert.pem"     # the system's PEM bundle (FM58)


class MacPlatform(Platform):
    """The macOS answers, over an injected home folder and chmod."""

    name = "macos"

    def __init__(self, home: Path, chmod: Callable[[Path, int], None] = os.chmod) -> None:
        """Keep what the answers depend on.

        Args:
            home: The user's home folder, e.g. /Users/u.
            chmod: Changes a file's mode (os.chmod; a fake in tests).
        """
        self.home = home
        self.chmod = chmod

    def app_dirs(self) -> AppDirs:
        """`~/Library/Application Support/AHSync/config.toml` and `…/data` (SPEC §3.7.3, DR1).

        Returns:
            The two paths.
        """
        root = PurePosixPath(Path(self.home).as_posix()) / "Library" / "Application Support" / APP_NAME
        return AppDirs(Path(str(root / "config.toml")), Path(str(root / "data")),
                       f"~/Library/Application Support/{APP_NAME}")

    def wow_candidates(self) -> list[Path]:
        """`/Applications/World of Warcraft/<name>`, `_classic_beta_` first (SPEC §3.7.3; UNVERIFIED, §6 item 20).

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

    def ca_bundle(self) -> Path | None:
        """`/etc/ssl/cert.pem`, the system's CA bundle (FM58): the bundled OpenSSL looks in the build machine's
        Python folder, which a user's Mac doesn't have.

        Returns:
            The bundle's path; not probed here.
        """
        return Path(CA_BUNDLE)
