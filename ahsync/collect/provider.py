"""The provider adapter protocol and its normalized observations (SPEC §5.0 Collector → Historical Store).

A provider adapter turns one data source's responses into these records; everything after them (storage,
volume, analysis) is the same whatever the source. AHF's adapter (`collect/ahf.py`) predates this contract and
still writes through `collect/catchup.py`; a source with auction ids (a future Blizzard API job, shaped like
retail's hourly auctions endpoint) delivers `SnapshotObservation`s through `collect/observations.py`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ahsync.engine.volume import Auction

BONUS_SUFFIXES = Path(__file__).resolve().parents[1] / "data" / "bonus_suffixes.json"
SOURCE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")    # also a folder name in the snapshot store
RESERVED_SOURCES = ("local", "books", "mail", "ahf")  # your scans, derived volume rows, AHF's own path


@dataclass(frozen=True)
class SnapshotObservation:
    """One complete snapshot of a market's auctions, with auction ids (SPEC §3.3.1 kind "ids")."""

    market: str                       # AHSync's market id (config maps it to the provider's address)
    src: str                          # the provider's source name, e.g. "blizzard"
    at_ms: int                        # observation time: when the provider took it, never when we fetched it
    auctions: tuple[Auction, ...]     # every auction listed; a partial snapshot is never delivered


class SnapshotProvider(Protocol):
    """A source of snapshots with auction ids (SPEC §5.0)."""

    name: str                         # its source name (SOURCE_NAME)

    def newest_snapshot(self, address: str) -> SnapshotObservation | None:
        """The provider's newest complete snapshot of one market.

        Args:
            address: The market's id at the provider (config [sources] addresses).

        Returns:
            The snapshot, or None when it has none yet.
        """


def load_bonus_suffixes(path: Path = BONUS_SUFFIXES) -> dict[int, int]:
    """The bonus ID -> random suffix id table, for keying items a provider names by id and bonus list (SRC5).

    Args:
        path: The generated table (tools/gen_bonus_suffixes.py).

    Returns:
        Bonus ID -> suffix id.
    """
    return {int(bonus): suffix for bonus, suffix in json.loads(path.read_text(encoding="utf-8"))["suffixes"].items()}


def snapshot_problems(obs: SnapshotObservation) -> list[str]:
    """What is wrong with a snapshot, so a bad one is refused whole, never stored in part (SPEC §5.2).

    Args:
        obs: The snapshot.

    Returns:
        One text per problem, in order found (at most one per kind); empty when it is fine.
    """
    problems = []
    if not SOURCE_NAME.match(obs.src) or obs.src in RESERVED_SOURCES:
        problems.append(f"source name {obs.src!r} is not allowed")
    if obs.at_ms <= 0:
        problems.append("no observation time")
    ids = [a.id for a in obs.auctions]
    if len(set(ids)) != len(ids):
        problems.append("an auction id appears twice")
    if any(a.qty < 1 or a.price < 1 or a.band not in (1, 2, 3, 4) or a.key == 0 for a in obs.auctions):
        problems.append("an auction has no units, no price, an unknown time-left band or no item")
    return problems
