"""Find and check the WoW: Forever client folder (SPEC §3.7.3, §3.7.4; DR6).

Shared by every OS: the candidate folders come from the platform layer (`ahsync_app/platform/`,
which `ahsync/` must not import), so the shell passes them in. Everything here only reads.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WowCheck:
    """What a WoW folder holds (SPEC §3.7.3)."""

    has_interface: bool    # <folder>/Interface exists
    has_addons: bool       # <folder>/Interface/AddOns exists
    accounts: int          # folders under <folder>/WTF/Account

    @property
    def usable(self) -> bool:
        """Whether setup may save this folder: Interface exists (AddOns is created when missing).

        Returns:
            True when Interface exists.
        """
        return self.has_interface


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
        return WowCheck((root / "Interface").is_dir(), (root / "Interface" / "AddOns").is_dir(), accounts)
    except OSError:
        return WowCheck(False, False, 0)


def detect(candidates: Sequence[Path]) -> Path | None:
    """The first candidate folder that holds Interface/AddOns (SPEC §3.7.3).

    Args:
        candidates: The platform's `wow_candidates()`, most likely first.

    Returns:
        That folder, or None when none has it.
    """
    return next((c for c in candidates if check(c).has_addons), None)
