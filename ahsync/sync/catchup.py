"""CATCH-UP (SPEC §3.1): bring each market's stored history up to `sealed.h1`.

Per market and run:
- Hours in (last_hour, sealed.h1]: public h1 when ≤ 7 days old; h1c (`caps.h1[day]`)
  when 7–14 days old with Premium; older hours are covered only by d1c day candles
  with Premium, otherwise lost and reported on Status (SPEC §2.1).
- Each hour commits together with `sync_state.last_hour`, so a crash resumes at the
  next hour (S2, FM6). Every write is an upsert, so repeating a range is harmless.
- A bad hour file (truncated, bad JSON, 404) is not committed. It's recorded in
  `fetch_failures` and re-fetched on later runs; later hours still sync; 3 failures
  in a row put "bad file: hour N" on Status (FM2, FM3).
- 5 retries exhausted (5xx, 429, network) end the run where it stands (FM1).
- Day candles (d1 or d1c), current prices (snapshot, then live with Premium), AHF
  order books (Premium `books`) and the volume file (Premium `depth`) follow.
- /me is checked every run; when Premium goes away its paths are simply not used (S11).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ahsync.api.ahf import FEATURE_BOOKS, FEATURE_DEPTH, FEATURE_LIVE, AhfApi, Capabilities, FileIndex, FormatChanged
from ahsync.api.client import ApiError, BadFile, FeatureUnavailable, NotFound, RetriesExhausted
from ahsync.clock import DAY_MS, HOUR_MS, Clock
from ahsync.store.db import Database, SyncState

log = logging.getLogger("ahsync.sync")

PUBLIC_HOURS = 7 * 24        # public h1 retention (SPEC §1)
PREMIUM_HOURS = 14 * 24      # h1c retention with Premium (SPEC §2.1)
BAD_FILE_LIMIT = 3           # failures in a row before Status says "bad file" (SPEC §5.2)
GAP_WARN_DAYS = 6            # warn when the last run is older than this (SPEC §2.1)
ITEMS_MAX_AGE_MS = DAY_MS    # refresh /items when older than 24 h (SPEC §3.1)
BOOK_MAX_AGE_MS = 30 * DAY_MS
PUBLIC_CAPS = Capabilities(frozenset(), history_days=7, api_per_minute=60)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Hour sources returned by hour_source().
H1, H1C, D1C, LOST = "h1", "h1c", "d1c", "lost"


@dataclass(frozen=True)
class HourPlan:
    """What to do with each missing hour of one market."""

    fetch: list[tuple[int, str | None]]                         # (hour, h1c token or None for public h1), ascending
    lost: list[tuple[int, int]] = field(default_factory=list)      # inclusive hour ranges with no source at all
    day_only: list[tuple[int, int]] = field(default_factory=list)  # inclusive hour ranges covered only by d1c


@dataclass
class SyncReport:
    """What one market's sync did, for the log, Status and tests."""

    market: str
    hours_stored: list[int] = field(default_factory=list)
    bad_hours: list[int] = field(default_factory=list)
    lost: list[tuple[int, int]] = field(default_factory=list)
    day_only: list[tuple[int, int]] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)      # endpoints skipped this run (format change, 403)
    aborted: str | None = None                              # why the run stopped early, if it did


@dataclass
class CatchupResult:
    """Outcome of a whole catch-up run."""

    caps: Capabilities
    reports: list[SyncReport] = field(default_factory=list)
    aborted: str | None = None


def format_day(ms: int) -> str:
    """Short UTC date for Status, e.g. "23 Oct".

    Args:
        ms: UTC Unix time in ms.

    Returns:
        Day of month and English month abbreviation.
    """
    tm = time.gmtime(ms // 1000)
    return f"{tm.tm_mday} {MONTHS[tm.tm_mon - 1]}"


def format_hour(hour: int) -> str:
    """Short UTC hour for Status, e.g. "6 Oct 14:00".

    Args:
        hour: floor(ms/3600000).

    Returns:
        Date and hour.
    """
    return f"{format_day(hour * HOUR_MS)} {hour % 24:02d}:00"


def hour_source(hour: int, files: FileIndex, caps: Capabilities, now_hour: int, disabled: frozenset[str] = frozenset()) -> tuple[str, str | None]:
    """Choose where an hour's data can come from (SPEC §3.1 CATCH-UP).

    Args:
        hour: The hour to fetch.
        files: The market's file index.
        caps: This run's capabilities from /me.
        now_hour: Current hour, floor(now_ms/3600000).
        disabled: Endpoints switched off this run ("h1c", "d1c").

    Returns:
        (H1, None) when under 7 days old; (H1C, token) when 7–14 days old with a
        Premium token for its day; (D1C, None) when only Premium day candles cover
        it; (LOST, None) otherwise.
    """
    age = now_hour - hour
    if age < PUBLIC_HOURS:
        return H1, None
    if caps.premium_history and "h1c" not in disabled and age < PREMIUM_HOURS:
        token = files.caps_h1.get(hour * HOUR_MS // DAY_MS)
        if token:
            return H1C, token
    if caps.premium_history and "d1c" not in disabled and files.caps_d1_url:
        return D1C, None
    return LOST, None


def _ranges(hours: list[int]) -> list[tuple[int, int]]:
    """Collapse ascending hours into inclusive runs of consecutive hours.

    Args:
        hours: Ascending hour numbers.

    Returns:
        (first, last) pairs.
    """
    runs: list[tuple[int, int]] = []
    for hour in hours:
        if runs and runs[-1][1] == hour - 1:
            runs[-1] = (runs[-1][0], hour)
        else:
            runs.append((hour, hour))
    return runs


def plan_hours(last_hour: int | None, files: FileIndex, caps: Capabilities, now_hour: int,
               retention_days: int = 90) -> HourPlan:
    """Plan the missing hours of one market.

    Only hours ≤ sealed.h1 are ever planned (SPEC §2.1: never request unlisted files).
    On a first run (no last_hour) nothing counts as lost: it starts at the oldest
    hour any hour file still covers.

    Args:
        last_hour: sync_state.last_hour, or None on the first run.
        files: The market's file index.
        caps: This run's capabilities.
        now_hour: Current hour.
        retention_days: Nothing older than this is planned.

    Returns:
        The hours to fetch (with their h1c token or None) and the lost / day-only ranges.
    """
    oldest = now_hour - retention_days * 24 + 1
    first = oldest if last_hour is None else max(oldest, last_hour + 1)
    fetch: list[tuple[int, str | None]] = []
    lost: list[int] = []
    day_only: list[int] = []
    for hour in range(first, files.sealed_h1 + 1):
        source, token = hour_source(hour, files, caps, now_hour)
        if source in (H1, H1C):
            fetch.append((hour, token))
        elif last_hour is None:
            continue
        elif source == D1C:
            day_only.append(hour)
        else:
            lost.append(hour)
    return HourPlan(fetch, _ranges(lost), _ranges(day_only))


class MarketSync:
    """One market's sync for one run: hours, day candles, current prices, books, volume."""

    def __init__(self, api: AhfApi, db: Database, clock: Clock, market: str, caps: Capabilities,
                 retention_days: int = 90) -> None:
        """Prepare a sync.

        Args:
            api: AHF endpoints.
            db: The database.
            clock: Time source.
            market: AHF market id.
            caps: This run's capabilities.
            retention_days: History window, days.
        """
        self.api, self.db, self.clock, self.market, self.caps = api, db, clock, market, caps
        self.retention_days = retention_days
        self.report = SyncReport(market)
        self.disabled: set[str] = set()

    def run(self) -> SyncReport:
        """Sync the market. Raises nothing for bad files or format changes; those are reported.

        Returns:
            The report.

        Raises:
            RetriesExhausted: AHF unreachable; everything committed so far stays (FM1).
        """
        state = self.db.get_sync_state(self.market)
        files = self.api.files(self.market)
        now_hour = self.clock.now_ms() // HOUR_MS
        plan = plan_hours(state.last_hour, files, self.caps, now_hour, self.retention_days)
        self.report.lost, self.report.day_only = plan.lost, plan.day_only
        self._report_gaps(plan)
        self._retry_failed_hours(files, now_hour)
        for hour, token in plan.fetch:
            self._store_hour(hour, token, advance=True)
        self._advance_to(files.sealed_h1)   # past lost / day-only hours; failed ones wait in fetch_failures
        self._store_day_candles(state, files)
        self._store_current(files)
        self._store_books(files)
        self._store_volume(state, files)
        self.db.update_sync_state(self.market, last_run=self.clock.now_ms())
        return self.report

    # ------------------------------------------------------------ hours

    def _advance_to(self, hour: int) -> None:
        """Move last_hour forward to an hour (never back), e.g. past lost hours.

        Args:
            hour: The new last_hour, if it is later than the stored one.
        """
        last = self.db.get_sync_state(self.market).last_hour
        if last is None or hour > last:
            self.db.update_sync_state(self.market, last_hour=hour)

    def _store_hour(self, hour: int, token: str | None, advance: bool) -> None:
        """Fetch one hour file and commit it with sync_state in one transaction.

        Args:
            hour: The hour.
            token: h1c token, or None for the public h1 file.
            advance: Move last_hour to this hour (False when re-fetching an older failed hour).

        Raises:
            RetriesExhausted: Passed up to end the run.
        """
        now = self.clock.now_ms()
        try:
            rows = self.api.hour_candles(self.market, hour, token)
        except FeatureUnavailable as error:
            log.warning("%s: %s; h1c skipped this run", self.market, error)
            self.disabled.add("h1c")
            self._note_disabled("h1c")
            if advance:
                self._advance_to(hour)
            return
        except (BadFile, NotFound, FormatChanged) as error:
            self._record_bad_hour(hour, str(error), advance, now)
            return
        with self.db.transaction():
            self.db.upsert_candles("candles_h1", self.market, rows, "ahf")
            if advance:
                self.db.update_sync_state(self.market, last_hour=hour)
            self.db.clear_fetch_failure(self.market, hour)
            self.db.clear_status(f"bad_file:{self.market}:{hour}")
        self.report.hours_stored.append(hour)

    def _record_bad_hour(self, hour: int, reason: str, advance: bool, now: int) -> None:
        """Note a failed hour file: not committed, retried next run, Status after 3 in a row.

        Args:
            hour: The hour.
            reason: Short, secret-free reason.
            advance: Move last_hour past it (the failure row keeps it queued for retry).
            now: Current time, ms.
        """
        with self.db.transaction():
            failures = self.db.record_fetch_failure(self.market, hour, reason, now)
            if advance:
                self._advance_to(hour)
        log.warning("%s hour %d not stored (%s); failure %d, will retry next run", self.market, hour, reason, failures)
        if failures >= BAD_FILE_LIMIT:
            self.db.set_status(f"bad_file:{self.market}:{hour}", "warn",
                               f"bad file: hour {hour} ({format_hour(hour)} UTC, {self.market})", now)
        self.report.bad_hours.append(hour)

    def _retry_failed_hours(self, files: FileIndex, now_hour: int) -> None:
        """Re-fetch hours that failed on earlier runs; drop those no longer available.

        Args:
            files: The market's file index.
            now_hour: Current hour.

        Raises:
            RetriesExhausted: Passed up to end the run.
        """
        for hour, _failures in self.db.fetch_failures(self.market):
            source, token = hour_source(hour, files, self.caps, now_hour, frozenset(self.disabled))
            if source in (H1, H1C) and hour <= files.sealed_h1:
                self._store_hour(hour, token, advance=False)
            else:
                log.warning("%s hour %d can no longer be fetched; giving up on it", self.market, hour)
                self.db.clear_fetch_failure(self.market, hour)

    def _report_gaps(self, plan: HourPlan) -> None:
        """Put lost and day-only ranges on Status (S3).

        Args:
            plan: This run's hour plan.
        """
        now = self.clock.now_ms()
        if plan.lost:
            spans = ", ".join(f"{format_hour(a)} – {format_hour(b)}" for a, b in plan.lost)
            hours = sum(b - a + 1 for a, b in plan.lost)
            self.db.set_status(f"lost:{self.market}", "warn",
                               f"{hours} hours lost for {self.market} ({spans} UTC): older than 7 days and no "
                               "Premium to fetch them. Run AHSync at least every 6 days", now)
            log.warning("%s: %d hours lost (%s)", self.market, hours, spans)
        if plan.day_only:
            spans = ", ".join(f"{format_hour(a)} – {format_hour(b)}" for a, b in plan.day_only)
            self.db.set_status(f"day_only:{self.market}", "info",
                               f"{self.market}: {spans} UTC covered by day candles only", now)

    def _note_disabled(self, endpoint: str) -> None:
        """Record an endpoint switched off for this run.

        Args:
            endpoint: Endpoint name.
        """
        if endpoint not in self.report.disabled:
            self.report.disabled.append(endpoint)

    def note_format_change(self, error: FormatChanged) -> None:
        """Disable an endpoint whose format changed and say so on Status (FM4).

        Args:
            error: The format error, naming the endpoint.
        """
        log.error("%s: %s", self.market, error)
        self.disabled.add(error.endpoint)
        self._note_disabled(error.endpoint)
        self.db.set_status(f"format:{error.endpoint}", "error", f"AHF format changed: {error.endpoint}",
                           self.clock.now_ms())

    # ------------------------------------------------------------ day candles, current, books, volume

    def _store_day_candles(self, state: SyncState, files: FileIndex) -> None:
        """Store day candles once per newly sealed day: d1c with Premium, else public d1.

        Args:
            state: sync_state as read at the start of the run.
            files: The market's file index.
        """
        if state.last_d1 is not None and state.last_d1 >= files.sealed_d1:
            return
        premium = self.caps.premium_history and bool(files.caps_d1_url) and "d1c" not in self.disabled
        try:
            try:
                rows = self.api.day_candles(files.caps_d1_url if premium else files.d1_url, premium)
            except FeatureUnavailable:
                if not premium or not files.d1_url:
                    raise
                self._note_disabled("d1c")
                rows = self.api.day_candles(files.d1_url, False)
        except FormatChanged as error:
            self.note_format_change(error)
            return
        except (BadFile, NotFound, FeatureUnavailable) as error:
            log.warning("%s day candles not stored: %s", self.market, error)
            return
        with self.db.transaction():
            self.db.upsert_candles("candles_d1", self.market, rows, "ahf")
            self.db.update_sync_state(self.market, last_d1=files.sealed_d1)
        self.db.clear_status("format:d1c" if premium else "format:d1")

    def _store_current(self, files: FileIndex) -> None:
        """Replace current AHF prices with the snapshot, then apply live changes with Premium.

        On a format change the last good `current` rows stay (FM4). Without
        `prices.live` the snapshot alone is used and live_since is cleared (S11).

        Args:
            files: The market's file index.
        """
        if not files.snapshot_url:
            return
        try:
            snapshot = self.api.snapshot(files.snapshot_url)
        except FormatChanged as error:
            self.note_format_change(error)
            return
        except (BadFile, NotFound) as error:
            log.warning("%s snapshot not stored: %s", self.market, error)
            return
        self.db.replace_current(self.market, "ahf", snapshot.rows)
        self.db.clear_status("format:snapshot")
        if FEATURE_LIVE in self.caps.features:
            self.store_live(snapshot.at_ms)
        elif self.db.get_sync_state(self.market).live_since is not None:
            self.db.update_sync_state(self.market, live_since=None)

    def store_live(self, since_ms: int) -> None:
        """Apply live price changes since a time (Premium; also the 15-minute steady-state step).

        Args:
            since_ms: Fetch changes after this time, ms.
        """
        if "live" in self.disabled:
            return
        try:
            live = self.api.live(self.market, since_ms)
        except FormatChanged as error:
            self.note_format_change(error)
            return
        except (FeatureUnavailable, BadFile, NotFound) as error:
            log.warning("%s live prices skipped: %s", self.market, error)
            self.disabled.add("live")
            self._note_disabled("live")
            return
        with self.db.transaction():
            self.db.upsert_current(self.market, "ahf", live.rows)
            self.db.update_sync_state(self.market, live_since=self.clock.now_ms())

    def _store_books(self, files: FileIndex) -> None:
        """Store complete AHF order books not stored yet (Premium `books`), newer than 30 days.

        Args:
            files: The market's file index.
        """
        if FEATURE_BOOKS not in self.caps.features:
            return
        now = self.clock.now_ms()
        for ref in files.books:
            scan_id = f"ahf:{self.market}:{ref.at_ms}"
            if not ref.complete or now - ref.at_ms > BOOK_MAX_AGE_MS or self.db.has_scan(scan_id):
                continue
            try:
                at, complete, entries = self.api.book(ref.url)
            except FeatureUnavailable as error:
                log.warning("%s books skipped: %s", self.market, error)
                self._note_disabled("books")
                return
            except FormatChanged as error:
                self.note_format_change(error)
                return
            except (BadFile, NotFound) as error:
                log.warning("%s book %d not stored: %s", self.market, ref.at_ms, error)
                continue
            self.db.store_book(scan_id, self.market, "ahf", None, at, "full", 1.0 if complete else 0.0, entries)

    def _store_volume(self, state: SyncState, files: FileIndex) -> None:
        """Fetch the volume file once per sealed day (Premium `depth`).

        Args:
            state: sync_state as read at the start of the run.
            files: The market's file index.
        """
        if FEATURE_DEPTH not in self.caps.features or not files.caps_volume_url:
            return
        if state.last_volume is not None and state.last_volume >= files.sealed_d1:
            return
        try:
            items = self.api.volume(files.caps_volume_url)
        except FormatChanged as error:
            self.note_format_change(error)
            return
        except (FeatureUnavailable, BadFile, NotFound) as error:
            log.warning("%s volume skipped: %s", self.market, error)
            return
        if items:
            # SPEC GAP: row format unknown (SPEC §6 item 1); nothing is stored until it's recorded.
            log.warning("%s volume has %d rows but its format is unknown; not stored", self.market, len(items))
        self.db.update_sync_state(self.market, last_volume=files.sealed_d1)


def note_premium(db: Database, caps: Capabilities, now_ms: int) -> None:
    """Track Premium across runs and keep its Status line current (SPEC §2.1, S11).

    Args:
        db: The database (meta `premium_active`, `premium_until`).
        caps: This run's capabilities.
        now_ms: Current time.
    """
    was_premium = db.get_meta("premium_active") == "1"
    if caps.premium:
        db.set_meta("premium_active", "1")
        until = caps.contributor_until_ms
        db.set_meta("premium_until", str(until) if until else None)
        if until:
            level = "warn" if until - now_ms <= 2 * DAY_MS else "info"
            db.set_status("premium", level, f"Premium via contributor until {format_day(until)} · "
                          "upload scans to keep it", now_ms)
        else:
            db.clear_status("premium")
        return
    if was_premium:
        until = db.get_meta("premium_until")
        ended = int(until) if until else now_ms
        db.set_status("premium", "warn", f"Premium ended {format_day(ended)}", now_ms)
        log.warning("Premium ended %s; continuing with public data", format_day(ended))
    db.set_meta("premium_active", "0")


def refresh_items(api: AhfApi, db: Database, now_ms: int) -> bool:
    """Refresh item metadata when it is more than 24 h old (SPEC §3.1).

    Args:
        api: AHF endpoints.
        db: The database (meta `items_updated`).
        now_ms: Current time.

    Returns:
        True if /items was fetched and stored.

    Raises:
        RetriesExhausted: Passed up to end the run.
    """
    updated = db.get_meta("items_updated")
    if updated and now_ms - int(updated) < ITEMS_MAX_AGE_MS:
        return False
    try:
        rows = api.items()
    except (FormatChanged, BadFile, NotFound, FeatureUnavailable) as error:
        log.warning("item metadata not refreshed: %s", error)
        return False
    with db.transaction():
        db.upsert_items(rows, now_ms)
        db.set_meta("items_updated", str(now_ms))
    return True


def warn_long_gap(db: Database, market: str, now_ms: int) -> None:
    """Warn when the last run was more than 6 days ago (SPEC §2.1), else clear the warning.

    Args:
        db: The database.
        market: AHF market id.
        now_ms: Current time.
    """
    last_run = db.get_sync_state(market).last_run
    topic = f"gap:{market}"
    if last_run is not None and now_ms - last_run > GAP_WARN_DAYS * DAY_MS:
        days = (now_ms - last_run) // DAY_MS
        db.set_status(topic, "warn", f"Last run {days} days ago for {market}: without Premium, hours older "
                      "than 7 days are lost. Run AHSync at least every 6 days", now_ms)
    else:
        db.clear_status(topic)


def sync_markets(api: AhfApi, db: Database, clock: Clock, markets: tuple[str, ...], caps: Capabilities,
                 retention_days: int = 90) -> CatchupResult:
    """Sync every market with known capabilities (catch-up and the hourly :06 step).

    Args:
        api: AHF endpoints.
        db: The database.
        clock: Time source.
        markets: AHF market ids, in config order.
        caps: Capabilities from this run's /me.
        retention_days: History window, days.

    Returns:
        One report per market reached; `aborted` set when AHF became unreachable.
    """
    result = CatchupResult(caps)
    for market in markets:
        warn_long_gap(db, market, clock.now_ms())
        sync = MarketSync(api, db, clock, market, caps, retention_days)
        try:
            result.reports.append(sync.run())
        except RetriesExhausted as error:
            sync.report.aborted = str(error)
            result.reports.append(sync.report)
            result.aborted = str(error)
            db.set_status("network", "error", f"AHF unreachable; sync stopped and resumes next run ({error})",
                          clock.now_ms())
            log.error("sync stopped: %s", error)
            return result
        except FormatChanged as error:   # /files itself changed shape: skip this market
            sync.note_format_change(error)
            result.reports.append(sync.report)
    db.clear_status("network")
    return result


def run_catchup(api: AhfApi, db: Database, clock: Clock, markets: tuple[str, ...],
                retention_days: int = 90) -> CatchupResult:
    """The start-of-run CATCH-UP: /me, item metadata, then every market (SPEC §3.1).

    Args:
        api: AHF endpoints.
        db: The database.
        clock: Time source.
        markets: AHF market ids.
        retention_days: History window, days.

    Returns:
        The capabilities used and one report per market.
    """
    now = clock.now_ms()
    try:
        caps = api.me()
    except RetriesExhausted as error:
        db.set_status("network", "error", f"AHF unreachable; sync resumes next run ({error})", now)
        log.error("AHF unreachable: %s", error)
        return CatchupResult(PUBLIC_CAPS, aborted=str(error))
    except ApiError as error:
        log.warning("/me failed (%s); continuing with public features", error)
        caps = PUBLIC_CAPS
    api.client.per_minute = max(1, caps.api_per_minute)
    note_premium(db, caps, now)
    try:
        refresh_items(api, db, now)
    except RetriesExhausted as error:
        db.set_status("network", "error", f"AHF unreachable; sync resumes next run ({error})", now)
        return CatchupResult(caps, aborted=str(error))
    return sync_markets(api, db, clock, markets, caps, retention_days)
