"""AHledger's per-item history backfill: fills the hours and days AHSync is missing (SPEC §2.3, M21).

For every key of a market's newest price table it asks `/v1/prices/<market>/<key>` for `range=7d` (hourly buckets ->
`candles_h1` closes under `ahledger_h1`, only for hours with no `local` or `ahf` row of that item) and `range=90d`
(daily buckets -> `candles_d1` closes under `ahledger`, only for days with no d1 row of any source). Every received
bucket's hour goes to `observed_hours` under `ahledger_h1`. It runs at most once per local day per market, only when
there is something to fill, in slices of a few keys per scheduler tick so it never blocks a sync, a refresh or a stop,
and commits a checkpoint with each chunk of 100 keys so a crash, a stop or a long 429 resumes at the next key.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ahsync.clock import Clock
from ahsync.collect.ahledger import BASE, SRC, AhledgerSource, FormatChanged, PriceHistory, parse_prices
from ahsync.collect.client import ApiClient, ApiError, BadFile, NotFound, cache_max_age_s, decode_body
from ahsync.db.sqlite import Database

log = logging.getLogger(__name__)

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
SRC_HOURLY = "ahledger_h1"
CHUNK_KEYS = 100          # keys per committed chunk and checkpoint (SRC14)
STEP_KEYS = 10            # keys per scheduler tick: 20 paced requests, about 4 s, so a stop is honoured quickly
GAP_HOURS = 168           # the last 7 days: a backfill runs only when one of their hours has no observation
PAUSE_MS = HOUR_MS        # after a long 429 or an unreachable AHledger: try again from the checkpoint an hour later
RANGES = ("7d", "90d")


@dataclass
class _Run:
    """One market's backfill in progress: its keys, where it is, and the chunk not yet committed."""

    market: str
    address: str
    day: int                                  # the local day number it started
    keys: list[int]                           # the keys still to do, ascending
    total: int                                # keys in the whole run (the table's)
    done: int                                 # keys committed so far
    pending: list[tuple[int, list[PriceHistory]]] = field(default_factory=list)


class AhledgerBackfill:
    """Runs the backfill one slice at a time from the scheduler (SPEC §2.3, SRC12-SRC15, FM47)."""

    def __init__(self, client: ApiClient, db: Database, clock: Clock, source: AhledgerSource,
                 enabled: Callable[[], bool]) -> None:
        """Create the backfill.

        Args:
            client: The AHledger client shared with the price table poll (paced: 300 requests a minute in all).
            db: Database.
            clock: Time source (local days come from its offset).
            source: The price table poller: a market it refused today gets no backfill (FM46).
            enabled: Reads config `ahledger_backfill` now (SRC15).
        """
        self.client, self.db, self.clock, self.source, self.enabled = client, db, clock, source, enabled
        self._run: _Run | None = None
        self._checked: dict[str, int] = {}        # market -> local day it was found to have nothing to do
        self._paused: dict[str, int] = {}         # market -> ms before which it is not resumed
        self._fresh: dict[str, int] = {}          # URL -> ms its answer is fresh until (Cache-Control)

    def step(self, markets: Sequence[tuple[str, str]]) -> bool:
        """Do one slice: start a due market's run if none is active, then fetch up to STEP_KEYS keys.

        Args:
            markets: (market id, AHledger address) of each market polling AHledger, in config order.

        Returns:
            True while a run is still active (the scheduler calls again at its next tick).
        """
        if self._run is None:
            self._run = next((r for r in (self._start(m, a) for m, a in markets) if r is not None), None)
            if self._run is None:
                return False
        run = self._run
        try:
            for _ in range(min(STEP_KEYS, len(run.keys))):
                key = run.keys[0]
                run.pending.append((key, self._histories(run.address, key)))
                run.keys.pop(0)
                if len(run.pending) == CHUNK_KEYS or not run.keys:
                    self._commit(run)
        except ApiError as error:                 # a Retry-After over 300 s, or AHledger unreachable (FM47)
            log.info("AHledger backfill of %s paused at %d of %d items: %s", run.market, run.done, run.total, error)
            self._pause(run)
            return False
        if not run.keys:
            self._finish(run)
            return False
        return True

    def _start(self, market: str, address: str) -> _Run | None:
        """A market's run if it is due: resumed from an unfinished checkpoint, or new after a gap (SRC14).

        Args:
            market: AHSync's market id.
            address: Its id at AHledger.

        Returns:
            The run, or None when it has nothing to do now.
        """
        now = self.clock.now_ms()
        today = (now + self.clock.local_offset_ms(now)) // DAY_MS
        if not self.enabled() or self.source.refused(market) or now < self._paused.get(market, 0) \
                or self._checked.get(market) == today:
            return None
        checkpoint = self._checkpoint(market)
        keys = [k for (k,) in self.db.conn.execute(
            "SELECT key FROM ahledger_rows WHERE market = ? AND updated = "
            "(SELECT MAX(updated) FROM ahledger_rows WHERE market = ?) ORDER BY key", (market, market))]
        if checkpoint and not checkpoint["done"]:
            rest = [k for k in keys if checkpoint["last_key"] is None or k > checkpoint["last_key"]]
            return _Run(market, address, checkpoint["day"], rest, checkpoint["total"], checkpoint["count"])
        if not keys or (checkpoint and checkpoint["day"] == today) or not self._has_gap(market, now):
            self._checked[market] = today
            return None
        return _Run(market, address, today, keys, len(keys), 0)

    def _has_gap(self, market: str, now: int) -> bool:
        """Whether there is something to fill: no ahledger_h1 close yet, or an hour of the last 7 days unobserved.

        Args:
            market: AHSync's market id.
            now: Now, UTC ms.

        Returns:
            True on the first run or after a gap (SPEC §2.3 "When").
        """
        if self.db.conn.execute("SELECT 1 FROM candles_h1 WHERE market = ? AND src = ? LIMIT 1",
                                (market, SRC_HOURLY)).fetchone() is None:
            return True
        hour = now // HOUR_MS
        seen = self.db.conn.execute(
            "SELECT COUNT(DISTINCT hour) FROM observed_hours WHERE market = ? AND hour BETWEEN ? AND ?",
            (market, hour - GAP_HOURS, hour - 1)).fetchone()[0]
        return seen < GAP_HOURS

    def _histories(self, address: str, key: int) -> list[PriceHistory]:
        """One key's 7-day and 90-day histories; a range still fresh, unknown or malformed is left out.

        Args:
            address: The market's id at AHledger.
            key: The item key (a base id).

        Returns:
            The histories received.

        Raises:
            ApiError: A Retry-After over 300 s (RateLimited) or AHledger unreachable after the retries.
        """
        out = []
        for rng in RANGES:
            url = f"{BASE}/v1/prices/{address}/{key}?range={rng}"
            now = self.clock.now_ms()
            if now < self._fresh.get(url, 0):
                continue
            try:
                response = self.client.get_conditional(url)
                self._fresh[url] = self.clock.now_ms() + (cache_max_age_s(response.headers) or 0) * 1000
                out.append(parse_prices(decode_body(response.body), key))
            except (NotFound, BadFile, FormatChanged) as error:     # one item's answer never costs the rest
                log.info("AHledger history of %d (%s) skipped: %s", key, rng, error)
        return out

    def _commit(self, run: _Run) -> None:
        """Write a chunk's buckets where nothing better exists, with the checkpoint, in one transaction (SRC12).

        Args:
            run: The run; its pending chunk is written and cleared.
        """
        with self.db.transaction():
            for key, histories in run.pending:
                for history in histories:
                    self._write(run.market, key, history)
            run.done += len(run.pending)
            self._save(run, done=False)
        last_key = run.pending[-1][0]
        run.pending.clear()
        self.db.set_status(f"ahledger:backfill:{run.market}", "info", "ahledger_backfill",
                           {"market": run.market, "done": run.done, "total": run.total, "paused": False},
                           self.clock.now_ms())
        log.info("AHledger backfill of %s: %d of %d items (to key %d)", run.market, run.done, run.total, last_key)

    def _write(self, market: str, key: int, history: PriceHistory) -> None:
        """Write one history's buckets: hourly closes under ahledger_h1, daily closes under ahledger, never over others.

        Args:
            market: AHSync's market id.
            key: The item key.
            history: Its parsed history.
        """
        if not history.buckets:
            return
        unit = HOUR_MS if history.hourly else DAY_MS
        times = sorted({b.t_ms // unit * unit for b in history.buckets})
        if history.hourly:
            taken = {t for (t,) in self.db.conn.execute(
                "SELECT t FROM candles_h1 WHERE market = ? AND key = ? AND src IN ('local', 'ahf') AND t BETWEEN ? AND ?",
                (market, key, times[0], times[-1]))}
            table, src = "candles_h1", SRC_HOURLY
            self.db.add_observed_hours(market, SRC_HOURLY, [t // HOUR_MS for t in times])
        else:
            taken = {t for (t,) in self.db.conn.execute(
                "SELECT t FROM candles_d1 WHERE market = ? AND key = ? AND src != ? AND t BETWEEN ? AND ?",
                (market, key, SRC, times[0], times[-1]))}
            table, src = "candles_d1", SRC
        rows = {b.t_ms // unit * unit: b for b in history.buckets if b.min is not None}
        self.db.upsert_candles(table, market, [(key, t, None, None, None, b.min, b.quantity)
                                               for t, b in sorted(rows.items()) if t not in taken], src)

    def _checkpoint(self, market: str) -> dict | None:
        """The stored checkpoint (SPEC §3.2 source_state).

        Args:
            market: AHSync's market id.

        Returns:
            {"day", "range", "last_key", "done", "total", "count"}, or None when there is none or it can't be read.
        """
        text = self.db.source_state(market, SRC)[3]
        try:
            checkpoint = json.loads(text) if text else None
        except ValueError:
            return None
        return checkpoint if isinstance(checkpoint, dict) else None

    def _save(self, run: _Run, done: bool) -> None:
        """Store the run's checkpoint: its day, the last key committed, how far it got.

        Args:
            run: The run.
            done: Whether every key is done.
        """
        last = self._checkpoint(run.market) or {}
        last_key = run.pending[-1][0] if run.pending else last.get("last_key")
        self.db.set_source_state(run.market, SRC, checkpoint=json.dumps(
            {"day": run.day, "range": RANGES[-1], "last_key": last_key, "done": done, "total": run.total,
             "count": run.done}, sort_keys=True))

    def _finish(self, run: _Run) -> None:
        """Mark the run done for its day and clear its Status line.

        Args:
            run: The finished run.
        """
        with self.db.transaction():
            self._save(run, done=True)
        self.db.clear_status(f"ahledger:backfill:{run.market}")
        log.info("AHledger backfill of %s done: %d items", run.market, run.total)
        self._run = None

    def _pause(self, run: _Run) -> None:
        """End the run at its last committed chunk; resume from the checkpoint an hour later or at the next start.

        Args:
            run: The run; its uncommitted chunk is dropped (fetched again on resume).
        """
        self._paused[run.market] = self.clock.now_ms() + PAUSE_MS
        self.db.set_status(f"ahledger:backfill:{run.market}", "info", "ahledger_backfill",
                           {"market": run.market, "done": run.done, "total": run.total, "paused": True},
                           self.clock.now_ms())
        self._run = None
