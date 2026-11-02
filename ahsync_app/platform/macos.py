"""macOS: ~/Library/Application Support, the macOS WoW folders and translocation; the LaunchAgent and the
signed-update swap come in M24 and M27 (SPEC §3.7.2)."""
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from ahsync_app.platform.base import APP_NAME, TRANSLOCATED, WOW_NAMES, AppDirs, Platform

# UNVERIFIED: inferred from a forum post naming /Applications/World of Warcraft/_classic_beta_/WTF/ (SPEC §6 item 20)
WOW_ROOT = PurePosixPath("/Applications/World of Warcraft")
TRANSLOCATION_PREFIX = "/private/var/folders/"
TRANSLOCATION_PART = "/AppTranslocation/"
KEY_FILE_MODE = 0o600


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
        return AppDirs(Path(str(root / "config.toml")), Path(str(root / "data")))

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
