"""The Market Intelligence store (SPEC §5.0): versioned analysis results, mixed into db.sqlite.Database.

One JSON document per market (intel.contract). A full refresh stages it in
`intel_new`; db.sqlite.Database.publish_refresh swaps it in with the pages.
The parsed document is held once per process in an IntelCache that every
connection of the process shares (BZ42, BZ43).
"""
from __future__ import annotations

import gzip
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ahsync.intel.contract import MarketIntelligence, from_json, to_json

Stamp = tuple[Any, ...]                        # (built, schema_version) of the published row


@dataclass(frozen=True)
class IntelFacts:
    """The few facts of a published summary the dashboard's freshness pill and Status page need (D100, S21)."""

    built_ms: int                              # when its baselines were built, UTC ms
    observations_until_ms: int | None          # its prices' time, UTC ms
    coverage: tuple[tuple[int, int], ...]      # observed-hour ranges


def facts_of(intel: MarketIntelligence) -> IntelFacts:
    """A record's facts.

    Args:
        intel: The parsed summary.

    Returns:
        Its IntelFacts.
    """
    return IntelFacts(intel.built_ms, intel.observations_until_ms, intel.coverage)


class IntelCache:
    """The parsed published intelligence, one record per market, shared by the connections of one process.

    A cloud summary is the largest thing AHSync holds (88 MB of JSON, about 130 MB parsed, on 2026-11-08), so
    the scheduler's and the dashboard's connections share one record, and an old record is dropped before a new
    one is parsed, so two never coexist (BZ43). A lock makes the threads wait for one parse instead of each
    parsing its own copy. Each record's IntelFacts outlive a release(), so the dashboard reads them while a new
    summary is fetched without parsing the old one again.
    """

    def __init__(self) -> None:
        """An empty cache."""
        self._lock = threading.Lock()
        self._records: dict[str, tuple[Stamp, MarketIntelligence]] = {}
        self._facts: dict[str, tuple[Stamp, IntelFacts]] = {}

    def get(self, market: str, stamp: Stamp, parse: Callable[[], MarketIntelligence]) -> MarketIntelligence:
        """The market's record for this published row, parsing it only when the held one is another row's.

        Args:
            market: Market id.
            stamp: The published row's (built, schema_version).
            parse: Reads and parses the published row; called with the lock held, after the old record is dropped.

        Returns:
            The record.
        """
        with self._lock:
            held = self._records.get(market)
            if held is not None and held[0] == stamp:
                return held[1]
            self._records.pop(market, None)
            del held
            intel = parse()
            self._records[market] = (stamp, intel)
            self._facts[market] = (stamp, facts_of(intel))
            return intel

    def facts(self, market: str, stamp: Stamp, parse: Callable[[], MarketIntelligence]) -> IntelFacts:
        """The facts of this published row, from the held facts when they are this row's, else by get().

        Args:
            market: Market id.
            stamp: The published row's (built, schema_version).
            parse: As for get().

        Returns:
            The facts.
        """
        with self._lock:
            held = self._facts.get(market)
            if held is not None and held[0] == stamp:
                return held[1]
        return facts_of(self.get(market, stamp, parse))

    def put(self, market: str, stamp: Stamp, intel: MarketIntelligence) -> None:
        """Hold a record that was parsed elsewhere (a cloud document just published), so nobody parses it again.

        Args:
            market: Market id.
            stamp: The published row's (built, schema_version).
            intel: The record.
        """
        with self._lock:
            self._records[market] = (stamp, intel)
            self._facts[market] = (stamp, facts_of(intel))

    def release(self, market: str) -> None:
        """Drop a market's record but keep its facts: a new document is about to be parsed, and the held row is
        still the published one (BZ43).

        Args:
            market: Market id.
        """
        with self._lock:
            self._records.pop(market, None)

    def forget(self, market: str) -> None:
        """Drop a market's record and facts: its published row was replaced (BZ42).

        Args:
            market: Market id.
        """
        with self._lock:
            self._records.pop(market, None)
            self._facts.pop(market, None)


class IntelStore:
    """Staged writes and reads of market intelligence; needs `self.conn` and `self.intel_cache`."""

    conn: sqlite3.Connection
    intel_cache: IntelCache

    def stage_intel(self, intel: MarketIntelligence, body: bytes | None = None) -> None:
        """Stage one market's intelligence (published by publish_refresh).

        Args:
            intel: The intelligence.
            body: Its gzipped JSON as downloaded (a cloud document, BZ43), stored as it is, a BLOB in the TEXT
                column; None stores gzip(to_json(intel)) the same way (a local market's, BZ47).
        """
        if body is None:
            body = gzip.compress(to_json(intel).encode("utf-8"), mtime=0)
        self.conn.execute("INSERT OR REPLACE INTO intel_new(market, schema_version, body, built) VALUES (?, ?, ?, ?)",
                          (intel.market, intel.schema_version, body, intel.built_ms))

    def hold_intel(self, intel: MarketIntelligence) -> None:
        """Hold a just-published document as the market's parsed record, so the export and the dashboard reuse it.

        Args:
            intel: The intelligence publish_refresh just swapped in (its built_ms and schema_version are the row's).
        """
        self.intel_cache.put(intel.market, (intel.built_ms, intel.schema_version), intel)

    def prices_newer_than_intel(self, market: str, src: str = "ahf") -> bool:
        """Whether the primary source's prices arrived after the published intelligence was built (IU2, SRC2).

        Two indexed lookups, so every page request can ask.

        Args:
            market: Market id.
            src: The market's primary remote source (config [sources]).

        Returns:
            True when its newest stored price is newer than the intelligence; False without either.
        """
        row = self.conn.execute(
            "SELECT (SELECT MAX(at) FROM current WHERE market = ? AND src = ?), "
            "(SELECT built FROM intel WHERE market = ?)", (market, src, market)).fetchone()
        return row[0] is not None and row[1] is not None and row[0] > row[1]

    def read_intel(self, market: str) -> MarketIntelligence | None:
        """The published intelligence of a market.

        Args:
            market: Market id.

        Each re-plan reads it (SPEC §3.1.1, IU13), so the parsed document is held in the process's IntelCache and
        parsed again only after a publish replaced it: parsing a 15k-item market takes about a quarter of the
        re-plan's 1 s budget (§3.1.2). It is a frozen record shared by every connection; callers never change it.

        Args:
            market: Market id.

        Returns:
            The intelligence, or None before the first refresh.

        Raises:
            intel.contract.IntelVersionError: When it was written by a newer AHSync.
        """
        stamp = self._intel_stamp(market)
        return None if stamp is None else self.intel_cache.get(market, stamp, lambda: self._parse_intel(market))

    def intel_facts(self, market: str) -> IntelFacts | None:
        """The published summary's built time, prices time and coverage, without parsing it when they are held.

        The dashboard's freshness pill and Status page ask on every request; while a new summary is fetched the
        old record is released, and parsing it again would put two summaries in memory (BZ43).

        Args:
            market: Market id.

        Returns:
            The facts, or None before the first refresh.

        Raises:
            intel.contract.IntelVersionError: When it was written by a newer AHSync.
        """
        stamp = self._intel_stamp(market)
        return None if stamp is None else self.intel_cache.facts(market, stamp, lambda: self._parse_intel(market))

    def _intel_stamp(self, market: str) -> Stamp | None:
        """The published row's (built, schema_version).

        Args:
            market: Market id.

        Returns:
            The stamp, or None when the market has no published intelligence.
        """
        row = self.conn.execute("SELECT built, schema_version FROM intel WHERE market = ?", (market,)).fetchone()
        return None if row is None else tuple(row)

    def _parse_intel(self, market: str) -> MarketIntelligence:
        """Read and parse the published body: gzipped JSON (BZ43, BZ47), or JSON text an older AHSync stored.

        Args:
            market: Market id.

        Returns:
            The intelligence.

        Raises:
            ValueError: The stored gzip is unreadable (it was checked when stored, so the disk changed it).
        """
        body = self.conn.execute("SELECT body FROM intel WHERE market = ?", (market,)).fetchone()[0]
        if isinstance(body, bytes):
            try:
                body = gzip.decompress(body).decode("utf-8")
            except (OSError, EOFError, UnicodeDecodeError) as error:
                raise ValueError(f"the stored summary of {market} is unreadable: {error}") from None
        return from_json(body)
