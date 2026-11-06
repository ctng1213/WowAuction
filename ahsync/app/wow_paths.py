"""Find and check the WoW: Forever client folder (SPEC §3.7.3, §3.7.4; DR6).

Shared by every OS: the candidate folders come from the platform layer (`ahsync_app/platform/`,
which `ahsync/` must not import), so the shell passes them in. Everything here only reads.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


FOREVER_FLAVOR = "wowf"    # Battle.net's product flavor for WoW: Forever, in <folder>/.flavor.info (seen 2026-11-06)


@dataclass(frozen=True)
class WowCheck:
    """What a WoW folder holds (SPEC §3.7.3)."""

    has_interface: bool    # <folder>/Interface exists
    has_addons: bool       # <folder>/Interface/AddOns exists
    accounts: int          # folders under <folder>/WTF/Account
    flavor: str = ""       # the product flavor Battle.net wrote in <folder>/.flavor.info ("wowf"); "" when none

    @property
    def is_forever(self) -> bool:
        """Whether the folder is a WoW: Forever install.

        Returns:
            True when its flavor is wowf.
        """
        return self.flavor == FOREVER_FLAVOR

    @property
    def usable(self) -> bool:
        """Whether setup may save this folder: Interface exists, or it is a Forever install (setup creates
        Interface/AddOns: a fresh install has no addon folder until an addon is installed).

        Returns:
            True when Interface exists or the folder is Forever's.
        """
        return self.has_interface or self.is_forever


def check(folder: str | Path) -> WowCheck:
    """Probe a WoW folder without changing it (DR6: a Check only reads).

    Args:
        folder: The folder that should hold Interface and WTF.

    Returns:
        Whether Interface and Interface/AddOns exist, and how many account folders WTF/Account holds.
    """
    root = Path(folder)
    try:
        account_dir = root / "WTF" / "Account"
        accounts = sum(1 for p in account_dir.iterdir() if p.is_dir()) if account_dir.is_dir() else 0
        return WowCheck((root / "Interface").is_dir(), (root / "Interface" / "AddOns").is_dir(), accounts,
                        flavor_of(root))
    except OSError:
        return WowCheck(False, False, 0)


def flavor_of(root: Path) -> str:
    """The product flavor in a WoW folder's .flavor.info (Battle.net writes "Product Flavor!STRING:0" then it).

    Args:
        root: The folder.

    Returns:
        The flavor, e.g. "wowf"; "" when the file is missing or unreadable.
    """
    try:
        lines = (root / ".flavor.info").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    return lines[1].strip() if len(lines) > 1 else ""


def detect(candidates: Sequence[Path]) -> Path | None:
    """The WoW: Forever folder among the candidates (SPEC §3.7.3, DR6).

    A Forever install (flavor wowf) wins even before its first addon, since a fresh install has no AddOns;
    otherwise the first candidate that holds Interface/AddOns (the beta's folder, say).

    Args:
        candidates: The platform's `wow_candidates()`, most likely first.

    Returns:
        That folder, or None when none qualifies.
    """
    checks = [(c, check(c)) for c in candidates]
    return next((c for c, found in checks if found.is_forever), None) or \
        next((c for c, found in checks if found.has_addons), None)
