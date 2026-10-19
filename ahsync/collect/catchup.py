"""CATCH-UP (SPEC §3.1): bring each market's stored history up to `sealed.h1`.

Per market and run:
- Hours in (last_hour, sealed.h1]: public h1 when ≤ 7 days old; h1c (`caps.h1[day]`)
  when 7–14 days old with Premium; older hours are covered only by d1c day candles
  with Premium, otherwise lost and reported on Status (SPEC §2.1).
- Each hour commits together with `sync_state.last_hour`, so a crash resumes at the
  next hour (S2, FM6). Every write is an upsert, so repeating a range is harmless.
- A bad hour file (truncated, bad JSON, 404, other HTTP error) is not committed. It's
  recorded in `fetch_failures` and re-fetched on later runs; later hours still sync;
  3 failures in a row put a "bad file" line on Status (FM2, FM3). An hour that ages
  out while failing joins the lost report.
- An hour file with a new shape is an API change: the hour loop stops and
  `last_hour` stays, so nothing is lost while the parser is fixed (FM27).
- A Premium file refused with 403 disables that endpoint for the run; its remaining
  hours are reported lost or day-only, not requested (FM26).
- 5 retries exhausted (5xx, 429, network) end the run where it stands (FM1). Any
  other API error on /files skips that market with a Status line.
- Day candles (d1 or d1c), current prices (snapshot), AHF order books (Premium
  `books`) and the volume file (Premium `depth`) follow. The live table is not used
  until its format is recorded (SPEC §5.2).
- /me is checked every run; when Premium goes away its paths are simply not used (S11).

Status lines hold structured fields (SPEC §3.2); `views` renders them.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ahsync.collect.ahf import FEATURE_BOOKS, FEATURE_DEPTH, FEATURE_LIVE, AhfApi, Capabilities, FileIndex, FormatChanged
from ahsync.collect.client import ApiError, FeatureUnavailable, RateLimited, RetriesExhausted
from ahsync.collect.local_scans import book_days, recompute_book_volume, recompute_sales_hours
from ahsync.clock import DAY_MS, HOUR_MS, Clock
from ahsync.db.sqlite import BookScan, Database, SyncState

log = logging.getLogger("ahsync.collect")

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


@dataclass
class SyncContext:
    """What every market's sync shares within one run."""

    api: AhfApi
    db: Database
    clock: Clock
    caps: Capabilities
    retention_days: int = 90


def format_day(ms: int) -> str:
    """Short UTC date for log lines, e.g. "23 Oct".

    Args:
        ms: UTC Unix time in ms.

    Returns:
        Day of month and English month abbreviation.
    """
    tm = time.gmtime(ms // 1000)
    return f"{tm.tm_mday} {MONTHS[tm.tm_mon - 1]}"


def format_hour(hour: int) -> str:
    """Short UTC hour for log lines, e.g. "6 Oct 14:00".

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


def hour_ranges(hours: list[int]) -> list[tuple[int, int]]:
    """Collapse hours into inclusive runs of consecutive hours.

    Args:
        hours: Hour numbers, any order, duplicates allowed.

    Returns:
        (first, last) pairs, ascending.
    """
    runs: list[tuple[int, int]] = []
    for hour in sorted(set(hours)):
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
    return HourPlan(fetch, hour_ranges(lost), hour_ranges(day_only))


def _range_data(market: str, ranges: list[tuple[int, int]]) -> dict:
    """Structured Status fields for hour ranges.

    Args:
        market: AHF market id.
        ranges: Inclusive hour ranges.

    Returns:
        {"market", "ranges_ms": [[start ms, end ms (exclusive)], …], "hours": count}.
    """
    return {"market": market, "ranges_ms": [[a * HOUR_MS, (b + 1) * HOUR_MS] for a, b in ranges],
            "hours": sum(b - a + 1 for a, b in ranges)}


class MarketSync:
    """One market's sync for one run: hours, day candles, current prices, books, volume."""

    def __init__(self, ctx: SyncContext, market: str) -> None:
        """Prepare a sync.

        Args:
            ctx: What the run shares (API, database, clock, capabilities, retention).
            market: AHF market id.
        """
        self.ctx, self.api, self.db, self.clock, self.caps = ctx, ctx.api, ctx.db, ctx.clock, ctx.caps
        self.market = market
        self.report = SyncReport(market)
        self.disabled: set[str] = set()
        self.lost_hours: list[int] = []
        self.day_only_hours: list[int] = []
        self.files: FileIndex | None = None
        self.now_hour = 0

    def run(self) -> SyncReport:
        """Sync the market. Bad files, refused features and format changes are reported, not raised.

        Returns:
            The report.

        Raises:
            RetriesExhausted: AHF unreachable; everything committed so far stays (FM1).
            ApiError: /files failed; the caller skips the market.
        """
        state = self.db.get_sync_state(self.market)
        files = self.files = self.api.files(self.market)
        self.now_hour = self.clock.now_ms() // HOUR_MS
        plan = plan_hours(state.last_hour, files, self.caps, self.now_hour, self.ctx.retention_days)
        self.lost_hours = [h for a, b in plan.lost for h in range(a, b + 1)]
        self.day_only_hours = [h for a, b in plan.day_only for h in range(a, b + 1)]
        if self._retry_failed_hours() and self._fetch_hours(plan.fetch):
            self._advance_to(files.sealed_h1)   # past lost / day-only hours; failed ones wait in fetch_failures
        self._report_gaps()
        self._store_day_candles(state)
        self._store_current()
        self._store_books()
        self._store_volume(state)
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

    def _fetch_hours(self, fetch: list[tuple[int, str | None]]) -> bool:
        """Fetch the planned hours in order.

        Args:
            fetch: (hour, h1c token or None), ascending.

        Returns:
            False when the loop stopped on a format change (last_hour stays put).

        Raises:
            RetriesExhausted: Passed up to end the run.
        """
        for hour, token in fetch:
            if token is not None and "h1c" in self.disabled:
                self._unfetchable(hour)          # h1c refused earlier this run (FM26)
                self._advance_to(hour)
            elif not self._store_hour(hour, token, advance=True):
                return False
        return True

    def _unfetchable(self, hour: int) -> None:
        """File an hour that can't be fetched any more under lost or day-only.

        Args:
            hour: The hour.
        """
        source, _ = hour_source(hour, self.files, self.caps, self.now_hour, frozenset(self.disabled))
        (self.day_only_hours if source == D1C else self.lost_hours).append(hour)

    def _store_hour(self, hour: int, token: str | None, advance: bool) -> bool:
        """Fetch one hour file and commit it with sync_state in one transaction.

        Args:
            hour: The hour.
            token: h1c token, or None for the public h1 file.
            advance: Move last_hour to this hour (False when re-fetching an older failed hour).

        Returns:
            False when the hour loop must stop (the file's format changed, FM27).

        Raises:
            RetriesExhausted: Passed up to end the run.
        """
        endpoint = "h1c" if token else "h1"
        try:
            rows = self.api.hour_candles(self.market, hour, token)
        except FeatureUnavailable as error:
            if token is None:                  # a public file shouldn't need a feature: treat as a bad file
                self._record_bad_hour(hour, str(error), advance)
                return True
            self._feature_refused("h1c", error)
            self._unfetchable(hour)
            if advance:
                self._advance_to(hour)
            return True
        except FormatChanged as error:
            self.note_format_change(error)
            return False
        except RetriesExhausted:
            raise
        except ApiError as error:              # truncated, bad JSON, 404, 401/410 …: retry next run
            self._record_bad_hour(hour, str(error), advance)
            return True
        with self.db.transaction():
            self.db.upsert_candles("candles_h1", self.market, rows, "ahf")
            if advance:
                self.db.update_sync_state(self.market, last_hour=hour)
            self.db.clear_fetch_failure(self.market, hour)
            self.db.clear_status(f"bad_file:{self.market}:{hour}")
            self.db.clear_status(f"format:{endpoint}")
            if token:
                self.db.clear_status(f"feature:{self.market}:h1c")
        self.report.hours_stored.append(hour)
        return True

    def _record_bad_hour(self, hour: int, reason: str, advance: bool) -> None:
        """Note a failed hour file: not committed, retried next run, Status after 3 in a row.

        Args:
            hour: The hour.
            reason: Short, secret-free reason.
            advance: Move last_hour past it (the failure row keeps it queued for retry).
        """
        now = self.clock.now_ms()
        with self.db.transaction():
            failures = self.db.record_fetch_failure(self.market, hour, reason, now)
            if advance:
                self._advance_to(hour)
        log.warning("%s hour %d not stored (%s); failure %d, will retry next run", self.market, hour, reason, failures)
        if failures >= BAD_FILE_LIMIT:
            self.db.set_status(f"bad_file:{self.market}:{hour}", "warn", "bad_file",
                               {"market": self.market, "hour": hour, "failures": failures}, now)
        self.report.bad_hours.append(hour)

    def _retry_failed_hours(self) -> bool:
        """Re-fetch hours that failed on earlier runs; those that aged out join the lost report.

        Returns:
            False when a format change stopped the hour loop.

        Raises:
            RetriesExhausted: Passed up to end the run.
        """
        for hour, _failures in self.db.fetch_failures(self.market):
            source, token = hour_source(hour, self.files, self.caps, self.now_hour, frozenset(self.disabled))
            if source in (H1, H1C) and hour <= self.files.sealed_h1:
                if not self._store_hour(hour, token, advance=False):
                    return False
                continue
            log.warning("%s hour %d aged out while failing; reported lost", self.market, hour)
            (self.day_only_hours if source == D1C else self.lost_hours).append(hour)
            self.db.clear_fetch_failure(self.market, hour)
            self.db.clear_status(f"bad_file:{self.market}:{hour}")
        return True

    def _report_gaps(self) -> None:
        """Put lost and day-only ranges in the report and on Status, merged with earlier ones (S3)."""
        self.report.lost = hour_ranges(self.lost_hours)
        self.report.day_only = hour_ranges(self.day_only_hours)
        for kind, level, ranges in (("lost", "warn", self.report.lost), ("day_only", "info", self.report.day_only)):
            if not ranges:
                continue
            topic = f"{kind}:{self.market}"
            earlier = self.db.status(topic)
            hours = [h for a, b in ranges for h in range(a, b + 1)]
            if earlier is not None:
                hours += [h for a, b in earlier.data["ranges_ms"] for h in range(a // HOUR_MS, b // HOUR_MS)]
            self.db.set_status(topic, level, kind, _range_data(self.market, hour_ranges(hours)), self.clock.now_ms())
            log.warning("%s: %s hours %s", self.market, kind,
                        ", ".join(f"{format_hour(a)}–{format_hour(b)} UTC" for a, b in ranges))

    # ------------------------------------------------------------ endpoint status

    def _note_disabled(self, endpoint: str) -> None:
        """Record an endpoint switched off for this run.

        Args:
            endpoint: Endpoint name.
        """
        self.disabled.add(endpoint)
        if endpoint not in self.report.disabled:
            self.report.disabled.append(endpoint)

    def note_format_change(self, error: FormatChanged) -> None:
        """Disable an endpoint whose format changed and say so on Status (FM4, FM27).

        Args:
            error: The format error, naming the endpoint.
        """
        log.error("%s: %s", self.market, error)
        self._note_disabled(error.endpoint)
        self.db.set_status(f"format:{error.endpoint}", "error", "format_changed", {"endpoint": error.endpoint},
                           self.clock.now_ms())

    def _feature_refused(self, endpoint: str, error: FeatureUnavailable) -> None:
        """Disable a Premium endpoint refused with 403 for this run, with a Status line (FM26).

        Args:
            endpoint: Our endpoint name ("h1c", "d1c", "books", "volume").
            error: The 403, naming AHF's feature.
        """
        log.warning("%s: %s; %s skipped this run", self.market, error, endpoint)
        self._note_disabled(endpoint)
        self.db.set_status(f"feature:{self.market}:{endpoint}", "warn", "feature_refused",
                           {"market": self.market, "endpoint": endpoint, "feature": error.feature},
                           self.clock.now_ms())

    # ------------------------------------------------------------ day candles, current, books, volume

    def _store_day_candles(self, state: SyncState) -> None:
        """Store day candles once per newly sealed day: d1c with Premium, else public d1.

        Args:
            state: sync_state as read at the start of the run.
        """
        files = self.files
        if state.last_d1 is not None and state.last_d1 >= files.sealed_d1:
            return
        premium = self.caps.premium_history and bool(files.caps_d1_url) and "d1c" not in self.disabled
        url = files.caps_d1_url if premium else files.d1_url
        if not url:
            log.info("%s: no day-candle file listed", self.market)
            return
        try:
            try:
                rows = self.api.day_candles(url, premium)
            except FeatureUnavailable as error:
                if not premium or not files.d1_url:
                    raise
                self._feature_refused("d1c", error)
                premium = False
                rows = self.api.day_candles(files.d1_url, False)
        except FormatChanged as error:
            self.note_format_change(error)
            return
        except RetriesExhausted:
            raise
        except ApiError as error:
            log.warning("%s day candles not stored: %s", self.market, error)
            return
        with self.db.transaction():
            self.db.upsert_candles("candles_d1", self.market, rows, "ahf")
            self.db.update_sync_state(self.market, last_d1=files.sealed_d1)
            self.db.clear_status("format:d1c" if premium else "format:d1")
            if premium:
                self.db.clear_status(f"feature:{self.market}:d1c")

    def _store_current(self) -> None:
        """Replace current AHF prices with the snapshot.

        On a format change the last good `current` rows stay (FM4); a valid but
        empty snapshot doesn't wipe them either. The live table isn't used until its
        format is recorded (SPEC §5.2), so live_since is cleared.
        """
        if self.db.get_sync_state(self.market).live_since is not None:
            self.db.update_sync_state(self.market, live_since=None)
        if not self.files.snapshot_url:
            return
        try:
            snapshot = self.api.snapshot(self.files.snapshot_url)
        except FormatChanged as error:
            self.note_format_change(error)
            return
        except RetriesExhausted:
            raise
        except ApiError as error:
            log.warning("%s snapshot not stored: %s", self.market, error)
            return
        self.db.clear_status("format:snapshot")
        if not snapshot.rows:
            log.warning("%s snapshot at %d has no rows; keeping the previous prices", self.market, snapshot.at_ms)
            return
        self.db.replace_current(self.market, "ahf", snapshot.rows)

    def _store_books(self) -> None:
        """Store complete AHF order books not stored yet (Premium `books`), newer than 30 days."""
        if FEATURE_BOOKS not in self.caps.features:
            return
        now = self.clock.now_ms()
        stored: list[int] = []
        try:
            self._fetch_books(now, stored)
        except Exception:
            self._recompute_sales(stored)       # what was stored still feeds hourly sales when a later book fails
            raise
        self._recompute_sales(stored)

    def _recompute_sales(self, stored: list[int]) -> None:
        """Recompute hourly sales rates and daily book volume around newly stored AHF books (SPEC §3.3.1, V29).

        A crash before this runs only leaves those hours and days unobserved, never wrong (missing is not
        zero); the next stored book or the method check recomputes them.

        Args:
            stored: Times of the complete books just stored.
        """
        if stored:
            with self.db.transaction():
                recompute_sales_hours(self.db, self.market, stored)
                for day in sorted(book_days(self.db, self.market, stored)):
                    recompute_book_volume(self.db, self.market, day)

    def _fetch_books(self, now: int, stored: list[int]) -> None:
        """Fetch and store the missing complete AHF books.

        Args:
            now: Now, UTC ms.
            stored: Collects the time of each complete book stored.

        Raises:
            RetriesExhausted: Passed on; the books stored before it are kept.
        """
        for ref in self.files.books:
            scan_id = f"ahf:{self.market}:{ref.at_ms}"
            if not ref.complete or now - ref.at_ms > BOOK_MAX_AGE_MS or self.db.has_scan(scan_id):
                continue
            try:
                at, complete, entries = self.api.book(ref.url)
            except FeatureUnavailable as error:
                self._feature_refused("books", error)
                return
            except FormatChanged as error:
                self.note_format_change(error)
                return
            except RetriesExhausted:
                raise
            except ApiError as error:
                log.warning("%s book %d not stored: %s", self.market, ref.at_ms, error)
                continue
            self.db.store_book(BookScan(scan_id, self.market, "ahf", None, at, "full", 1.0 if complete else 0.0),
                               entries)
            if complete:
                stored.append(at)
            self.db.clear_status(f"feature:{self.market}:books")

    def _store_volume(self, state: SyncState) -> None:
        """Fetch the volume file once per sealed day (Premium `depth`).

        Args:
            state: sync_state as read at the start of the run.
        """
        files = self.files
        if FEATURE_DEPTH not in self.caps.features or not files.caps_volume_url:
            return
        if state.last_volume is not None and state.last_volume >= files.sealed_d1:
            return
        try:
            items = self.api.volume(files.caps_volume_url)
        except FeatureUnavailable as error:
            self._feature_refused("volume", error)
            return
        except FormatChanged as error:
            self.note_format_change(error)
            return
        except RetriesExhausted:
            raise
        except ApiError as error:
            log.warning("%s volume skipped: %s", self.market, error)
            return
        if items:
            # SPEC GAP: row format unknown (SPEC §6 item 1); nothing is stored until it's recorded.
            log.warning("%s volume has %d rows but its format is unknown; not stored", self.market, len(items))
        self.db.update_sync_state(self.market, last_volume=files.sealed_d1)
        self.db.clear_status(f"feature:{self.market}:volume")


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
            db.set_status("premium", level, "premium_until", {"until_ms": until}, now_ms)
        else:
            db.clear_status("premium")
        return
    if was_premium:
        until = db.get_meta("premium_until")
        ended = int(until) if until else now_ms
        db.set_status("premium", "warn", "premium_ended", {"ended_ms": ended}, now_ms)
        log.warning("Premium ended %s; continuing with public data", format_day(ended))
    db.set_meta("premium_active", "0")


def check_capabilities(api: AhfApi, db: Database, now_ms: int) -> Capabilities | None:
    """GET /me and apply it: rate limit, Premium status (every run and every hourly store).

    Args:
        api: AHF endpoints.
        db: The database.
        now_ms: Current time.

    Returns:
        The capabilities (the public tier when /me fails or has a bad shape), or
        None when AHF is unreachable, so the caller skips this run.
    """
    try:
        caps = api.me()
    except RetriesExhausted as error:
        _unreachable(db, error, now_ms)
        return None
    except ApiError as error:
        log.warning("/me failed (%s); continuing with public features", error)
        caps = PUBLIC_CAPS
    api.client.per_minute = max(1, caps.api_per_minute)
    note_premium(db, caps, now_ms)
    return caps


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
    except RetriesExhausted:
        raise
    except ApiError as error:
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
        db.set_status(topic, "warn", "long_gap", {"market": market, "last_run_ms": last_run,
                                                  "days": (now_ms - last_run) // DAY_MS}, now_ms)
    else:
        db.clear_status(topic)


def _unreachable(db: Database, error: RetriesExhausted, now_ms: int) -> None:
    """Status line for a run stopped because AHF couldn't be reached (FM1).

    Args:
        db: The database.
        error: Why the retries gave up.
        now_ms: Current time.
    """
    reason = "rate_limited" if isinstance(error, RateLimited) else "retries_exhausted"
    db.set_status("network", "error", "unreachable", {"reason": reason}, now_ms)
    log.error("sync stopped: %s", error)


def _note_key(db: Database, api: AhfApi, now_ms: int) -> None:
    """Status "API key rejected" while a 401 made us drop the key; cleared once it works (FM25).

    Args:
        db: The database.
        api: AHF endpoints (their client knows whether the key was rejected or used).
        now_ms: Current time.
    """
    if api.client.key_rejected:
        db.set_status("api_key", "warn", "key_rejected", {}, now_ms)
    elif api.client.key_used:
        db.clear_status("api_key")


def sync_markets(ctx: SyncContext, markets: tuple[str, ...]) -> CatchupResult:
    """Sync every market with known capabilities (catch-up and the hourly :06 step).

    Args:
        ctx: API, database, clock, this run's capabilities and retention.
        markets: AHF market ids, in config order.

    Returns:
        One report per market reached; `aborted` set when AHF became unreachable.
    """
    result = CatchupResult(ctx.caps)
    db = ctx.db
    for market in markets:
        warn_long_gap(db, market, ctx.clock.now_ms())
        sync = MarketSync(ctx, market)
        result.reports.append(sync.report)
        try:
            sync.run()
            db.clear_status(f"market:{market}")
        except RetriesExhausted as error:
            sync.report.aborted = result.aborted = str(error)
            _unreachable(db, error, ctx.clock.now_ms())
            _note_key(db, ctx.api, ctx.clock.now_ms())
            return result
        except FormatChanged as error:   # /files itself changed shape: skip this market
            sync.note_format_change(error)
        except ApiError as error:        # any other HTTP error on /files: skip this market (SPEC §5.2)
            log.error("%s skipped this run: %s", market, error)
            sync.report.aborted = str(error)
            db.set_status(f"market:{market}", "error", "market_skipped",
                          {"market": market, "error": type(error).__name__}, ctx.clock.now_ms())
    db.clear_status("network")
    _note_key(db, ctx.api, ctx.clock.now_ms())
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
    caps = check_capabilities(api, db, now)
    if caps is None:
        return CatchupResult(PUBLIC_CAPS, aborted="AHF unreachable")
    if FEATURE_LIVE in caps.features:
        log.info("live prices not used yet (format not recorded); using the hourly snapshot")
    try:
        refresh_items(api, db, now)
    except RetriesExhausted as error:
        _unreachable(db, error, now)
        return CatchupResult(caps, aborted=str(error))
    return sync_markets(SyncContext(api, db, clock, caps, retention_days), markets)
