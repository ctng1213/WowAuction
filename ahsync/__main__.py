"""Start AHSync: `python -m ahsync [--data DIR] [--config FILE]` (SPEC §3.1).

Order: single-instance lock (FM15) → config (FM16) → log → database (check,
restore, migrate) → daily backup → read SavedVariables → CATCH-UP → prune →
dashboard (http://localhost:<dashboard_port>, on its own thread) → steady-state loop,
which re-reads SavedVariables when WoW rewrites them. The full refresh and status
pass are scheduler hooks to be wired by later milestones.

A file data/ahsync.stop ends the loop at the next tick (`tools/deploy.py --restart`, FM36).
The instance that takes the lock deletes it at start and again on exit, so a stale one never stops a
start, and a second start that fails on the lock never clears a pending request.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sqlite3
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO

from ahsync.collect.ahf import AhfApi
from ahsync.collect.ahledger import BASE as AHLEDGER_BASE, AhledgerSource
from ahsync.collect.ahledger_backfill import AhledgerBackfill
from ahsync.collect.client import ApiClient, RedactingFilter, Redactor, Transport, UrllibTransport
from ahsync import version
from ahsync.clock import Clock, SystemClock
from ahsync.config import Config, ConfigError, load_config, market_sources, session_settings
from ahsync.state.importer import ingest_file
from ahsync.db.sqlite import Database, DatabaseError
from ahsync.history.prune import prune
from ahsync.collect.catchup import SyncContext, check_capabilities, run_catchup, sync_markets
from ahsync.app.cloud_job import cloud_transport, poll_cloud
from ahsync.app.refresh import refresh_all, replan_all
from ahsync.export.addon_files import export_market, export_plan
from ahsync.recommend.status_pass import status_pass
from ahsync.recommend.tracking_pass import tracking_pass
from ahsync.app import installer, update_check, updater
from ahsync.app.scheduler import SavedVarsWatcher, Scheduler, SchedulerHooks, file_mtime_ms, find_savedvars
from ahsync.intel.store import IntelCache
from ahsync.web.server import (AppHooks, DashboardApp, DashboardServer, new_token, open_server_db, start_dashboard,
                               stop_dashboard)

LOG_BYTES = 1_000_000
LOG_BACKUPS = 3
ALREADY_RUNNING = "AHSync is already running"
STOP_FILE = "ahsync.stop"          # in data/: its presence asks the running AHSync to stop (FM36)
LOCK_FILE = "ahsync.lock"
API_KEY_TOPIC = "api_key_setup"    # the Status topic of setup's api_key_not_saved line (FM50; "api_key" is FM25's)


class InstanceLock:
    """An OS file lock on data/ahsync.lock; the OS releases it if the process dies (SPEC §5.2, FM15)."""

    def __init__(self, path: Path) -> None:
        """Prepare the lock (not yet taken).

        Args:
            path: The lock file, created if missing.
        """
        self.path = path
        self._file: IO[bytes] | None = None

    def acquire(self) -> bool:
        """Try to take the lock without waiting.

        Returns:
            True if this process now holds it; False if another holds it.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        """Release the lock if held."""
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None


def setup_logging(data_dir: Path, redactor: Redactor) -> Callable[[], None]:
    """Log to data/ahsync.log, rotated, with secrets scrubbed (SPEC §5.2; FM62).

    The app shell holds the log for its whole run and setup and run() call this again inside it: a handler already
    writing this file is shared, so no line is written twice and no two handlers rotate one file.

    Args:
        data_dir: The data folder.
        redactor: Secrets to scrub from every record.

    Returns:
        The release: it removes this call's scrubbing from a shared handler, or removes and closes the handler
        this call attached.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    path = os.path.abspath(data_dir / "ahsync.log")
    logger = logging.getLogger("ahsync")
    logger.setLevel(logging.INFO)
    scrub = RedactingFilter(redactor)
    shared = next((h for h in logger.handlers
                   if isinstance(h, logging.FileHandler) and h.baseFilename == path), None)
    if shared is not None:
        shared.addFilter(scrub)
        return lambda: shared.removeFilter(scrub)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(scrub)
    logger.addHandler(handler)

    def release() -> None:
        """Detach and close the handler this call attached."""
        logger.removeHandler(handler)
        handler.close()

    return release


@dataclass(frozen=True)
class Runtime:
    """What run() gets from the outside world, so tests can replace all of it."""

    env: Mapping[str, str]              # environment variables (for the API key)
    home: Path                          # the user's home folder (for the key file)
    transport: Transport                # UrllibTransport in the app, FakeTransport in tests
    clock: Clock
    should_stop: Callable[[], bool]     # polled by the steady-state loop; run() adds data/ahsync.stop (FM36)
    out: IO[str]                        # user-facing start-up messages (stderr in the app)
    serve_dashboard: bool = True        # False in tests that don't exercise the dashboard
    app_hooks: AppHooks | None = None   # the app shell's dashboard paths (SPEC §3.7); None: `python -m ahsync`


def safe_backup(db: Database, backup_dir: Path, now_ms: int) -> None:
    """Make the daily backup; a failure is logged, never fatal (SPEC §5.2: degrade).

    Args:
        db: The database.
        backup_dir: data/backups.
        now_ms: Current time.
    """
    try:
        db.daily_backup(backup_dir, now_ms)
    except (OSError, sqlite3.Error) as error:
        logging.getLogger("ahsync").warning("daily backup failed: %s", error)


def full_refresh(db: Database, clock: Clock, config: Config) -> None:
    """Run the full refresh and log what it did (SPEC §3.1.1); failures are already kept on Status.

    Args:
        db: The database.
        clock: Time source.
        config: Settings.
    """
    now = clock.now_ms()
    result = refresh_all(db, now, config, clock.local_offset_ms(now))
    logging.getLogger("ahsync").info("full refresh: %d items, %d failed%s", result.items, len(result.failed),
                                     f"; NOT published: {result.error}" if result.error else "")
    if result.error is None:
        export_market(db, config, now, clock.sleep)     # SPEC §3.1.1 step 3: the addon files
        export_plan(db, config, now, clock.sleep)


class LiveConfig:
    """The running settings: the dashboard's risk tolerance change replaces them (SPEC §3.5, SEC11). Thread-safe:
    the dashboard's thread swaps the whole Config, the scheduler reads it at each job."""

    def __init__(self, config: Config) -> None:
        """Start from the config loaded at start.

        Args:
            config: Validated settings.
        """
        self.current = config

    def replace(self, config: Config) -> None:
        """Use new settings from the next job on.

        Args:
            config: The settings with the dashboard's change.
        """
        self.current = config


def build_hooks(ctx: SyncContext, config: Config, backup_dir: Path,
                refresh_requested: Callable[[], bool] = lambda: False, live: LiveConfig | None = None) -> SchedulerHooks:
    """Wire the steady-state jobs.

    Args:
        ctx: The run's API, database, clock and capabilities; the hourly store
            re-checks /me and updates ctx.caps, so Premium ending mid-session degrades.
        config: Settings.
        backup_dir: data/backups.
        refresh_requested: Consumes a pending "Refresh now" from the dashboard.
        live: The running settings, when the dashboard may change them; the plan jobs read the risk tolerance
            from here (SEC11). None: `config` throughout.

    Returns:
        Hooks with store_hour, ahledger_poll and ahledger_backfill (only when ctx.ahledger is set), full_refresh,
        refresh_requested, daily, weekly and savedvars_changed set. The live table stays off until its format is recorded (SPEC §5.2).
    """
    def current() -> Config:
        """The settings the plan jobs use now.

        Returns:
            live.current, else config.
        """
        return live.current if live is not None else config

    def store_hour() -> None:
        """Hourly :06 store: /me, new hours, snapshot, books; no recompute (SPEC §3.1)."""
        caps = check_capabilities(ctx.api, ctx.db, ctx.clock.now_ms())
        if caps is None:
            return
        ctx.caps = caps
        sync_markets(ctx, config.markets)

    def ahledger_markets() -> list[tuple[str, str]]:
        """The markets that take data from AHledger now.

        Returns:
            (market id, AHledger address) per configured market whose address is not "" (SRC10).
        """
        return [(m, a) for m in config.markets if (a := market_sources(current(), m).addresses["ahledger"])]

    backfill = None if ctx.ahledger is None else AhledgerBackfill(
        ctx.ahledger.client, ctx.db, ctx.clock, ctx.ahledger, lambda: current().ahledger_backfill)

    def ahledger_backfill() -> None:
        """Every 5 s: one slice of AHledger's history backfill, or the check that nothing is due (M21, SRC15)."""
        if backfill is not None:
            backfill.step(ahledger_markets())

    def ahledger_poll() -> None:
        """Every 30 min: poll AHledger's price table of each market that has an AHledger address (SRC10).

        A failure of one market is logged and the others still run; the scheduler and the AHF sync are never
        affected (FM43). The dirty item keys wait for the next full refresh: no job updates single items yet.
        """
        for market in config.markets:
            address = market_sources(current(), market).addresses["ahledger"]
            if not address or ctx.ahledger is None:
                continue
            try:
                ctx.ahledger.poll(market, address)
            except Exception:
                logging.getLogger("ahsync").exception("AHledger poll of %s failed; tried again in 30 minutes", market)

    def daily() -> None:
        """Daily prune and backup (SPEC §3.1)."""
        prune(ctx.db, ctx.clock.now_ms(), config.retention_days)
        safe_backup(ctx.db, backup_dir, ctx.clock.now_ms())

    def savedvars_changed(path: Path) -> None:
        """Re-read a rewritten SavedVariables file and update the items it changed (SPEC §3.1).

        Args:
            path: The file.
        """
        result = ingest_file(path, ctx.db, ctx.clock, config.realms, markets=config.markets,
                             sessions=session_settings(config))
        if result.ingested:                       # ingestedUntil may have moved: the addon may prune (SPEC §2.2)
            now = ctx.clock.now_ms()
            tracking_pass(ctx.db, now)            # a buy or post marks its action done (SPEC §3.4.1), positions
            markets = replan_all(ctx.db, now, current(), ctx.clock.local_offset_ms(now))   # the dashboard follows (IU13)
            logging.getLogger("ahsync").info("re-planned after reading %s: %s", path.name,
                                             ", ".join(markets) or "nothing (no published intelligence, or it failed)")
            export_plan(ctx.db, current(), ctx.clock.now_ms(), ctx.clock.sleep)

    def hourly_status() -> None:
        """The :00 tracking and status passes, then the plan file they changed (SPEC §3.1.1)."""
        now = ctx.clock.now_ms()
        tracking_pass(ctx.db, now)
        status_pass(ctx.db, current(), now, ctx.clock.local_offset_ms(now))
        export_plan(ctx.db, current(), ctx.clock.now_ms(), ctx.clock.sleep)

    return SchedulerHooks(store_hour=store_hour, daily=daily, weekly=ctx.db.vacuum,
                          ahledger_poll=ahledger_poll if ctx.ahledger is not None else None,
                          ahledger_backfill=ahledger_backfill if ctx.ahledger is not None else None,
                          savedvars_changed=savedvars_changed,
                          full_refresh=lambda: full_refresh(ctx.db, ctx.clock, current()),
                          status_pass=hourly_status,
                          refresh_requested=refresh_requested)


def read_savedvars_at_start(db: Database, clock: Clock, config: Config, watcher: SavedVarsWatcher) -> None:
    """Read every account's SavedVariables once at start (SPEC §3.1); a failure is logged, never fatal.

    Args:
        db: The database.
        clock: Time source.
        config: Settings (wow_path, realms).
        watcher: Told which file versions were read, so the steady state doesn't read them again. A file
            still being written, or whose read failed, is left to the watcher, which tries it again.
    """
    log = logging.getLogger("ahsync")
    for path in find_savedvars(Path(config.wow_path)):
        try:
            result = ingest_file(path, db, clock, config.realms, markets=config.markets,
                                 sessions=session_settings(config))
        except Exception:                         # like Scheduler._run: one file can't stop the start-up
            log.exception("reading %s failed; will try again", path.name)
            account = path.parent.parent.name
            try:
                db.set_status(f"savedvars:{account}", "error", "savedvars_failed", {"account": account},
                              clock.now_ms())
            except sqlite3.Error:
                log.warning("could not record the failure on Status")
            continue
        if result.mtime_ms is not None and result.reason != "unstable":
            watcher.mark_handled(path, result.mtime_ms)


class RefreshRequests:
    """"Refresh now" requests from the dashboard (SPEC §3.5 POST /api/refresh), safe across threads.

    Requests made before the refresh runs merge into one (IU4): the scheduler's
    refresh_requested hook is take(), checked at each tick (at least every 5 s).
    """

    def __init__(self) -> None:
        """Start with nothing requested."""
        self._lock = threading.Lock()
        self._pending = False

    def request_refresh(self) -> None:
        """Ask for a full refresh; called on the dashboard's thread."""
        with self._lock:
            self._pending = True

    def take(self) -> bool:
        """Consume the pending request, if any, atomically (a press can't be lost between check and clear).

        Returns:
            True when a refresh was requested since the last take().
        """
        with self._lock:
            requested, self._pending = self._pending, False
        return requested


def serve_dashboard(data_dir: Path, config: Config, clock: Clock, refresh: RefreshRequests,
                    out: IO[str], config_path: Path | None = None,
                    on_config: Callable[[Config], None] | None = None,
                    app_hooks: AppHooks | None = None,
                    intel_cache: IntelCache | None = None) -> tuple[DashboardServer, Database] | None:
    """Start the dashboard on its own thread (SPEC §3.1 "serve dashboard"); a failure is logged, never fatal.

    Args:
        data_dir: data/ (the server reads data/ahsync.db over its own connection).
        config: Settings (dashboard_port, markets).
        clock: Time source, for skip times.
        refresh: Receives "Refresh now" requests.
        out: Where the dashboard's address is printed for the user.
        config_path: config.toml, which the Risk tolerance dropdown rewrites (SEC11); None: it can't.
        on_config: Told the new settings after the dashboard changed one; None: nobody else is told.
        app_hooks: The app shell's paths (GET /api/app, POST /api/autostart); None: they answer 404.
        intel_cache: run()'s connection's IntelCache, so the dashboard shares its parsed summary (BZ42).

    Returns:
        (server, its database connection) to stop and close at exit, or None when the
        port can't be bound (for example, already in use).
    """
    server_db = open_server_db(data_dir / "ahsync.db", intel_cache)
    app = DashboardApp(server_db, config, clock, refresh.request_refresh, new_token(), config.dashboard_port,
                       config_path=config_path, config_changed=on_config or (lambda _config: None), app_hooks=app_hooks)
    try:
        server = start_dashboard(app, config.dashboard_port)
    except OSError as error:
        server_db.close()
        logging.getLogger("ahsync").warning("dashboard not started on port %d: %s", config.dashboard_port, error)
        print(f"Dashboard not started: port {config.dashboard_port} is not available", file=out)
        return None
    print(f"Dashboard: http://localhost:{app.port}", file=out)
    if app_hooks is not None:
        app_hooks.dashboard_ready(app)           # the shell's tray toggles go through the dashboard's own handlers
    return server, server_db


def update_job(db: Database, transport: Transport, clock: Clock, data_dir: Path, live: LiveConfig,
               app_hooks: AppHooks) -> Callable[[], None]:
    """The scheduler's update job: settle the last install, the check (at start, then daily), then the download (SPEC §3.7.8, §3.7.9).

    Args:
        db: run()'s database (the job runs on its thread).
        transport: The real transport; GitHub never gets the AHF key (DR15).
        clock: Now.
        data_dir: data/, whose updates/ folder takes the download.
        live: The running settings (the toggles change the channel and auto_update).
        app_hooks: The platform's asset name, signature check, can-replace test and cleanup after an update (M27).

    Returns:
        The job; its first run checks whatever the time since the last check (DR33); after that it sends
        nothing unless a day has passed since the last check or download try.
    """
    started = [True]

    def job() -> None:
        """Settle the last install, check, download, then note whether the app may replace itself (M27, FM54)."""
        settings = live.current
        installer.settle(db, data_dir, version.VERSION, clock.now_ms(), app_hooks.forget_previous)
        update_check.check(db, transport, clock, version.VERSION, settings, at_start=started[0])
        started[0] = False
        updater.download(db, transport, clock, data_dir, version.VERSION, settings, app_hooks.asset_name,
                         app_hooks.verify_update)
        update_check.write_meta(db, {"cant_replace": None if app_hooks.can_replace() else "1"})

    return job


def run(data_dir: Path, config_path: Path, runtime: Runtime, held_lock: InstanceLock | None = None) -> int:
    """Start AHSync and run until runtime.should_stop() is True or data/ahsync.stop exists (FM36).

    Args:
        data_dir: data/ (database, backups, log, lock).
        config_path: config.toml.
        runtime: Environment, transport, clock, stop check and message stream.
        held_lock: data/ahsync.lock already taken by the caller (the app shell, SPEC §3.7.6), which keeps
            it and releases it after run() returns; None: run() takes and releases it itself.

    Returns:
        Exit code: 0 after a normal stop, 1 when already running, 2 for a bad config or database.

    Side effects:
        Deletes data/ahsync.stop once the lock is taken, and on exit; a start that finds the lock held leaves
        it alone, so a pending stop request still reaches the running AHSync.
    """
    stop_file = data_dir / STOP_FILE
    lock = held_lock or InstanceLock(data_dir / LOCK_FILE)
    if held_lock is None and not lock.acquire():
        print(ALREADY_RUNNING, file=runtime.out)
        return 1
    try:
        remove_stop_file(stop_file)                # only the lock's owner clears a stale request (FM36)
        try:
            config = load_config(config_path, runtime.env, runtime.home)
        except ConfigError as error:
            print(f"AHSync can't start: {error}", file=runtime.out)
            return 2
        outer_stop = runtime.should_stop
        stoppable = replace(runtime, should_stop=lambda: outer_stop() or stop_file.exists())
        return _run_configured(data_dir, config, stoppable, config_path)
    finally:
        remove_stop_file(stop_file)
        if held_lock is None:
            lock.release()


def remove_stop_file(path: Path) -> None:
    """Delete data/ahsync.stop if it is there (FM36); a failure is ignored (the next start tries again).

    Args:
        path: The stop file.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _run_configured(data_dir: Path, config: Config, runtime: Runtime, config_path: Path | None = None) -> int:
    """Everything after the lock and config: log, database, catch-up, steady state.

    Args:
        data_dir: data/.
        config: Validated settings.
        runtime: As run().
        config_path: config.toml, for the dashboard's Risk tolerance dropdown (SEC11).

    Returns:
        0 after a normal stop, 2 when the database can't be used.
    """
    redactor = Redactor()
    redactor.add(config.api_key)
    release_log = setup_logging(data_dir, redactor)
    log = logging.getLogger("ahsync")
    clock = runtime.clock
    db = None
    dashboard = None
    try:
        log.info("starting; markets %s; %s", ", ".join(config.markets), "with key" if config.api_key else "no key")
        backup_dir = data_dir / "backups"
        try:
            db = Database.open(data_dir / "ahsync.db", clock.now_ms(), backup_dir)
        except DatabaseError as error:
            print(f"AHSync can't start: {error}", file=runtime.out)
            return 2
        safe_backup(db, backup_dir, clock.now_ms())
        if config.api_key:                       # a key found now settles setup's "not saved" line (FM50)
            db.clear_status(API_KEY_TOPIC)
        with db.transaction():                   # a changed session key re-cuts the stored sessions (SPEC §3.6)
            if db.ensure_session_settings(session_settings(config), clock.now_ms()):
                log.info("farming sessions rebuilt for the session settings")
        watcher = SavedVarsWatcher(lambda: find_savedvars(Path(config.wow_path)), file_mtime_ms)
        read_savedvars_at_start(db, clock, config, watcher)
        api = AhfApi(ApiClient(runtime.transport, clock, config.api_key, redactor=redactor))
        addresses = {m: market_sources(config, m).addresses["ahf"] for m in config.markets}   # SRC1
        result = run_catchup(api, db, clock, config.markets, config.retention_days, addresses)
        prune(db, clock.now_ms(), config.retention_days)
        full_refresh(db, clock, config)          # SPEC §3.1: FULL REFRESH, then serve the dashboard
        refresh_requests = RefreshRequests()
        live = LiveConfig(config)
        if runtime.serve_dashboard:
            dashboard = serve_dashboard(data_dir, config, clock, refresh_requests, runtime.out, config_path, live.replace,
                                        runtime.app_hooks, db.intel_cache)
        ahledger = AhledgerSource(ApiClient(runtime.transport, clock, "", api_base=AHLEDGER_BASE, per_minute=300,
                                            redactor=redactor, paced=True), db, clock)          # keyless, its own limit (SRC10)
        ctx = SyncContext(api, db, clock, result.caps, config.retention_days, addresses, ahledger)
        hooks = build_hooks(ctx, config, backup_dir, refresh_requests.take, live)
        if runtime.app_hooks is not None:        # only the app shell checks for updates (SPEC §3.7.8)
            hooks = replace(hooks, update=update_job(db, runtime.transport, clock, data_dir, live, runtime.app_hooks))
        if config.cloud.intel_url:               # the cloud market's summary (SPEC §2.4, BZ17)
            summaries = cloud_transport(runtime.transport)
            hooks = replace(hooks, cloud_poll=lambda: poll_cloud(db, summaries, clock, live.current))
        scheduler = Scheduler(clock, hooks, watcher, config.daily_refresh_time, config.live_refresh_minutes,
                              config.cloud.poll_minutes)
        scheduler.run(runtime.should_stop)
        log.info("stopped")
        return 0
    finally:
        if dashboard is not None:
            stop_dashboard(dashboard[0])
            dashboard[1].close()
        if db is not None:
            db.close()
        release_log()


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments without the program name; None reads sys.argv.

    Returns:
        The exit code from run().
    """
    parser = argparse.ArgumentParser(prog="ahsync", description="AHSync: auctionhouse.fyi sync and planner")
    parser.add_argument("--data", type=Path, default=Path("data"), help="data folder (default: ./data)")
    parser.add_argument("--config", type=Path, default=Path("config.toml"), help="config file (default: ./config.toml)")
    args = parser.parse_args(argv)
    try:
        return run(args.data, args.config, Runtime(os.environ, Path.home(), UrllibTransport(), SystemClock(),
                                                   lambda: False, sys.stderr))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
