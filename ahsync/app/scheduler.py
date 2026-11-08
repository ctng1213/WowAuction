"""STEADY STATE timers (SPEC §3.1, §3.1.1), driven by an injected clock.

The scheduler only decides *when*; each job is a callable handed in through
`SchedulerHooks`, so the full refresh, status pass and SavedVariables ingest can
be wired later without touching the timing rules here:

- every hour at :06 → `store_hour` (new h1 hour + snapshot (+ books); no recompute)
- every hour at :00 → `status_pass` (SPEC §3.1.1)
- every `live_refresh_minutes` with Premium → `live_refresh`
- every 30 minutes, the first time at the first tick → `ahledger_poll` when wired (AHledger's price table; it
  sends an ETag and a Cache-Control, so a poll with nothing new is one cheap request, SPEC §2.3)
- every 5 s, after the poll → `ahledger_backfill` when wired: one slice of the history backfill (a few keys, about
  4 s), or a cheap check that there is nothing to do (SPEC §2.3, M21)
- every 10 minutes, the first time at the first tick → `update` when wired (the app shell's update check and
  download; each sends nothing unless a day has passed, SPEC §3.7.8, §3.7.9)
- first tick after `daily_refresh_time` local each day → `full_refresh`, then `daily`
  (d1/volume refresh, prune, backup); once a week → `weekly` (VACUUM)
- every 5 s → stat the SavedVariables files; a file whose modification time changed
  and has then been unchanged for 2 s → `savedvars_changed(path)` (SPEC §2.2, S10);
  a hook that raises leaves that version unread, so the next watch tries again.
  Watching only: parsing is the ingest's job.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ahsync.clock import DAY_MS, HOUR_MS, Clock

log = logging.getLogger("ahsync.scheduler")

WATCH_INTERVAL_MS = 5_000      # one stat every 5 s (SPEC §3.1.1 "idle means idle")
STABLE_MS = 2_000              # unchanged for 2 s before reading (SPEC §2.2)
STORE_MINUTE = 6               # hourly store at :06
AHLEDGER_POLL_MS = 30 * 60_000   # AHledger poll interval (SPEC §2.3)
UPDATE_POLL_MS = 10 * 60_000     # how often the update job looks whether a day has passed (SPEC §3.7.8)
WEEK_MS = 7 * DAY_MS


def _nothing() -> None:
    """Default hook: do nothing (for jobs not wired yet)."""


def _never() -> bool:
    """Default refresh-request check: nothing requested.

    Returns:
        False.
    """
    return False


def _nothing_for(_path: Path) -> None:
    """Default SavedVariables hook: do nothing.

    Args:
        _path: The changed file.
    """


@dataclass
class SchedulerHooks:
    """The jobs the scheduler runs. Each is called with no arguments unless noted."""

    store_hour: Callable[[], None] = _nothing         # :06 hourly store (sync.catchup.sync_markets)
    status_pass: Callable[[], None] = _nothing        # :00 plan status pass (wired in M4)
    full_refresh: Callable[[], None] = _nothing       # full refresh: daily, and on "Refresh now"
    refresh_requested: Callable[[], bool] = _never    # consumes a pending "Refresh now" (IU4: repeats merge)
    daily: Callable[[], None] = _nothing              # prune + backup
    weekly: Callable[[], None] = _nothing             # VACUUM
    live_refresh: Callable[[], None] | None = None    # Premium live table; None disables it
    ahledger_poll: Callable[[], None] | None = None   # AHledger price tables, every 30 min; None disables it (SRC10)
    ahledger_backfill: Callable[[], object] | None = None   # one slice of the backfill, every 5 s; None disables it
    update: Callable[[], None] | None = None          # update check and download, every 10 min; None: no app shell
    update_requested: Callable[[], bool] = _never     # Check for updates in the tray: run `update` now (DR34)
    cloud_poll: Callable[[], object] | None = None    # the cloud market's summary, every poll_minutes; None: no [cloud]
    savedvars_changed: Callable[[Path], None] = _nothing_for   # SavedVariables re-read (ingest.apply.ingest_file)


class SavedVarsWatcher:
    """Detects SavedVariables rewrites with one stat per file per poll (SPEC §2.2)."""

    def __init__(self, paths: Callable[[], list[Path]], stat_mtime_ms: Callable[[Path], int | None]) -> None:
        """Create a watcher.

        Args:
            paths: Returns the files to watch (re-evaluated each poll, so new accounts appear).
            stat_mtime_ms: Returns a file's modification time in UTC ms, or None if it is missing.
        """
        self._paths = paths
        self._stat = stat_mtime_ms
        self._handled: dict[Path, int] = {}

    def prime(self) -> None:
        """Treat every file as already handled at its current modification time.

        Used after the start-up read, so an unchanged file isn't read twice.
        """
        for path in self._paths():
            mtime = self._stat(path)
            if mtime is not None:
                self._handled[path] = mtime

    def mark_handled(self, path: Path, mtime_ms: int) -> None:
        """Record that one version of a file has been read (by the start-up read), so it isn't read again.

        Args:
            path: The file.
            mtime_ms: Its modification time when it was read, UTC ms.
        """
        self._handled[path] = mtime_ms

    def forget(self, path: Path) -> None:
        """Un-mark a file whose read failed, so the next poll returns it again.

        Args:
            path: The file.
        """
        self._handled.pop(path, None)

    def poll(self, now_ms: int) -> list[Path]:
        """Check each file once.

        Args:
            now_ms: Current time, UTC ms.

        Returns:
            Files whose modification time differs from the last one handled and is
            at least 2 s old, so WoW has finished writing them. Each such version is
            returned once; sorted by path.
        """
        ready = []
        for path in sorted(self._paths()):
            mtime = self._stat(path)
            if mtime is None or self._handled.get(path) == mtime:
                continue
            if now_ms - mtime >= STABLE_MS:
                self._handled[path] = mtime
                ready.append(path)
        return ready


def find_savedvars(wow_path: Path) -> list[Path]:
    """Find every account's AHProfit SavedVariables file (SPEC §2.2).

    Args:
        wow_path: The client folder, e.g. .../_forever_.

    Returns:
        Paths matching WTF/Account/*/SavedVariables/AHProfit.lua, sorted.
    """
    return sorted(wow_path.glob("WTF/Account/*/SavedVariables/AHProfit.lua"))


def file_mtime_ms(path: Path) -> int | None:
    """Modification time of a file.

    Args:
        path: Any file.

    Returns:
        mtime in UTC ms, or None when the file doesn't exist.
    """
    try:
        return path.stat().st_mtime_ns // 1_000_000
    except OSError:
        return None


def parse_daily_time(text: str) -> int:
    """Convert config `daily_refresh_time` ("06:00") to ms after local midnight.

    Args:
        text: "HH:MM", already validated by config.

    Returns:
        Milliseconds after local midnight.
    """
    hours, minutes = text.split(":")
    return (int(hours) * 60 + int(minutes)) * 60_000


class Scheduler:
    """Runs the steady-state jobs at their times. `tick()` does the due work; `run()` sleeps between ticks."""

    def __init__(self, clock: Clock, hooks: SchedulerHooks, watcher: SavedVarsWatcher | None = None,
                 daily_refresh_time: str = "06:00", live_refresh_minutes: int = 15, cloud_poll_minutes: int = 15) -> None:
        """Create a scheduler whose timers start from the clock's current time.

        Args:
            clock: Time source (FakeClock in tests).
            hooks: The jobs.
            watcher: SavedVariables watcher, or None to skip watching.
            daily_refresh_time: Local "HH:MM" of the daily full refresh.
            live_refresh_minutes: Interval of the Premium live refresh, minutes.
            cloud_poll_minutes: Interval of the cloud summary's poll, minutes ([cloud] poll_minutes, SPEC §2.4).
        """
        self.clock, self.hooks, self.watcher = clock, hooks, watcher
        self.daily_ms = parse_daily_time(daily_refresh_time)
        self.live_ms = live_refresh_minutes * 60_000
        now = clock.now_ms()
        self.next_store = self._next_at_minute(now, STORE_MINUTE)
        self.next_status = self._next_at_minute(now, 0)
        self.next_watch = now + WATCH_INTERVAL_MS
        self.next_live = now + self.live_ms
        self.next_ahledger = now                       # the first poll is at the first tick (start)
        self.next_backfill = now                       # checked at every 5 s tick after the poll (M21)
        self.next_update = now                         # the first update check is at start (SPEC §3.7.8)
        self.cloud_ms = cloud_poll_minutes * 60_000
        self.next_cloud = now                          # the first summary poll is at the first tick (start)
        self.next_daily = self._next_daily(now)
        self.next_weekly = now + WEEK_MS

    @staticmethod
    def _next_at_minute(now_ms: int, minute: int) -> int:
        """Next time strictly after now at a given minute past the hour (UTC hours = local hours ± whole hours).

        Args:
            now_ms: Current time.
            minute: Minute past the hour.

        Returns:
            UTC ms of the next HH:minute after now.
        """
        at = (now_ms // HOUR_MS) * HOUR_MS + minute * 60_000
        return at if at > now_ms else at + HOUR_MS

    def _next_daily(self, now_ms: int) -> int:
        """Next local `daily_refresh_time` strictly after now.

        Args:
            now_ms: Current time.

        Returns:
            UTC ms of the next daily refresh.
        """
        offset = self.clock.local_offset_ms(now_ms)
        local = now_ms + offset
        at = (local // DAY_MS) * DAY_MS + self.daily_ms - offset
        return at if at > now_ms else at + DAY_MS

    def tick(self) -> list[str]:
        """Run every job that is due now, then schedule its next run.

        A job that raises is logged and skipped; the others still run.

        Returns:
            Names of the jobs run, in run order (for logs and tests).
        """
        now = self.clock.now_ms()
        ran: list[str] = []
        if self.watcher is not None and now >= self.next_watch:
            self.next_watch = now + WATCH_INTERVAL_MS
            for path in self.watcher.poll(now):
                if not self._run("savedvars_changed", lambda p=path: self.hooks.savedvars_changed(p), ran):
                    self.watcher.forget(path)          # read it again at the next watch
        if now >= self.next_status:
            self.next_status = self._next_at_minute(now, 0)
            self._run("status_pass", self.hooks.status_pass, ran)
        if now >= self.next_store:
            self.next_store = self._next_at_minute(now, STORE_MINUTE)
            self._run("store_hour", self.hooks.store_hour, ran)
        if self.hooks.live_refresh is not None and now >= self.next_live:
            self.next_live = now + self.live_ms
            self._run("live_refresh", self.hooks.live_refresh, ran)
        if self.hooks.ahledger_poll is not None and now >= self.next_ahledger:
            self.next_ahledger = now + AHLEDGER_POLL_MS
            self._run("ahledger_poll", self.hooks.ahledger_poll, ran)
        if self.hooks.update is not None and (now >= self.next_update or self.hooks.update_requested()):
            self.next_update = now + UPDATE_POLL_MS
            self._run("update", self.hooks.update, ran)
        if self.hooks.cloud_poll is not None and now >= self.next_cloud:
            self.next_cloud = now + self.cloud_ms
            self._run("cloud_poll", self.hooks.cloud_poll, ran)
        if self.hooks.ahledger_backfill is not None and now >= self.next_backfill:
            self.next_backfill = now + WATCH_INTERVAL_MS
            self._run("ahledger_backfill", self.hooks.ahledger_backfill, ran)
        if now >= self.next_daily:
            self.next_daily = self._next_daily(now)
            self.hooks.refresh_requested()               # a pending press is covered by this refresh
            self._run("full_refresh", self.hooks.full_refresh, ran)
            self._run("daily", self.hooks.daily, ran)
        elif self.hooks.refresh_requested():
            self._run("full_refresh", self.hooks.full_refresh, ran)
        if now >= self.next_weekly:
            self.next_weekly = now + WEEK_MS
            self._run("weekly", self.hooks.weekly, ran)
        return ran

    def _run(self, name: str, job: Callable[[], None], ran: list[str]) -> bool:
        """Run one job, logging instead of raising on failure (SPEC §5.2: degrade, don't crash).

        Args:
            name: Job name.
            job: The callable.
            ran: Names of jobs run so far; this one is appended.

        Returns:
            True when the job finished without raising.
        """
        ran.append(name)
        try:
            job()
        except Exception:
            log.exception("%s failed; will try again at its next time", name)
            return False
        return True

    def next_wake_ms(self) -> int:
        """When the next job is due.

        Returns:
            UTC ms of the earliest timer.
        """
        timers = [self.next_store, self.next_status, self.next_daily, self.next_weekly]
        if self.watcher is not None:
            timers.append(self.next_watch)
        if self.hooks.live_refresh is not None:
            timers.append(self.next_live)
        if self.hooks.ahledger_poll is not None:
            timers.append(self.next_ahledger)
        if self.hooks.update is not None:
            timers.append(self.next_update)
        if self.hooks.cloud_poll is not None:
            timers.append(self.next_cloud)
        if self.hooks.ahledger_backfill is not None:
            timers.append(self.next_backfill)
        return min(timers)

    def run(self, should_stop: Callable[[], bool]) -> None:
        """Tick and sleep until told to stop.

        Args:
            should_stop: Checked before each tick; True ends the loop.
        """
        while not should_stop():
            self.tick()
            self.clock.sleep(max(0, self.next_wake_ms() - self.clock.now_ms()) / 1000)
