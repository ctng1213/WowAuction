"""Raw snapshots with auction ids, kept on disk, not in SQLite (SPEC §3.2, §3.3.1 kind "ids").

Hourly snapshots of a whole AH are tens of millions of auction rows a month, too many for the database, so each
snapshot is one gzipped JSON file under `<root>/<market>/<src>/<at ms>.json.gz`, listed in an `index.json` beside
it. Every write is a temp file plus rename (SPEC §5.2 rule 3): a crash leaves the old index or a file the index
doesn't list yet, which the next `put` of that snapshot adds. The store is the id-diff estimator's input; the
grouped books and volume rows derived from it live in the database.
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

from ahsync.collect.provider import SnapshotObservation
from ahsync.engine.volume import Auction, IdBook


class SnapshotStore:
    """The snapshot files of every market and source under one folder."""

    def __init__(self, root: Path) -> None:
        """Open a store; folders are made as snapshots arrive.

        Args:
            root: Its folder, e.g. data/snapshots.
        """
        self.root = root

    def _folder(self, market: str, src: str) -> Path:
        """One market's and source's folder.

        Args:
            market: Market id.
            src: Source name (already checked by provider.snapshot_problems).

        Returns:
            The path.
        """
        return self.root / market / src

    def times(self, market: str, src: str, from_ms: int, to_ms: int) -> list[int]:
        """Snapshot times in a range, from the index.

        Args:
            market: Market id.
            src: Source name.
            from_ms: Start, inclusive.
            to_ms: End, inclusive.

        Returns:
            Times in ms, ascending.
        """
        return [t for t in self._index(market, src) if from_ms <= t <= to_ms]

    def _index(self, market: str, src: str) -> list[int]:
        """The index of one folder.

        Args:
            market: Market id.
            src: Source name.

        Returns:
            Times in ms, ascending; empty without an index.
        """
        path = self._folder(market, src) / "index.json"
        return sorted(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else []

    def put(self, obs: SnapshotObservation) -> bool:
        """Store a snapshot and list it (idempotent).

        Args:
            obs: A checked snapshot.

        Returns:
            True when it was new; False when it was already listed.
        """
        folder = self._folder(obs.market, obs.src)
        times = self._index(obs.market, obs.src)
        if obs.at_ms in times:
            return False
        folder.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"at": obs.at_ms, "auctions": [[a.id, a.key, a.qty, a.price, a.band] for a in obs.auctions]},
                          separators=(",", ":")).encode("utf-8")
        _write_atomic(folder / f"{obs.at_ms}.json.gz", gzip.compress(body, mtime=0))
        _write_atomic(folder / "index.json", json.dumps(sorted(times + [obs.at_ms])).encode("utf-8"))
        return True

    def load(self, market: str, src: str, at_ms: int) -> IdBook:
        """One stored snapshot.

        Args:
            market: Market id.
            src: Source name.
            at_ms: Its time, as listed by `times`.

        Returns:
            The snapshot as an engine IdBook.

        Raises:
            FileNotFoundError: Not stored.
        """
        data = json.loads(gzip.decompress((self._folder(market, src) / f"{at_ms}.json.gz").read_bytes()))
        return IdBook(data["at"], tuple(Auction(*row) for row in data["auctions"]))

    def prune(self, market: str, src: str, before_ms: int) -> int:
        """Delete a folder's snapshots older than a cutoff (SPEC §3.2 book retention).

        The index is rewritten first, so a crash leaves only unlisted files, which the next prune removes.

        Args:
            market: Market id.
            src: Source name.
            before_ms: Snapshots with at < before_ms go.

        Returns:
            Files deleted.
        """
        folder = self._folder(market, src)
        if not folder.exists():
            return 0
        keep = [t for t in self._index(market, src) if t >= before_ms]
        _write_atomic(folder / "index.json", json.dumps(keep).encode("utf-8"))
        gone = 0
        for path in sorted(folder.glob("*.json.gz")):
            if int(path.name.split(".")[0]) < before_ms:
                path.unlink()
                gone += 1
        return gone


def _write_atomic(path: Path, data: bytes) -> None:
    """Write a file whole or not at all: a temp file beside it, then a rename.

    Args:
        path: The file.
        data: Its contents.
    """
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(data)
    os.replace(temp, path)
