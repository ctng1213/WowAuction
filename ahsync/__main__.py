"""Start AHSync: `python -m ahsync [--data DIR] [--config FILE]` (SPEC §3.1).

Order: single-instance lock (FM15) → config (FM16) → log → database (check,
restore, migrate) → daily backup → CATCH-UP → prune → steady-state loop.
The full refresh, status pass and SavedVariables ingest are scheduler hooks to
be wired by later milestones.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO

from ahsync.api.ahf import FEATURE_LIVE, AhfApi
from ahsync.api.client import ApiClient, RedactingFilter, Redactor, Transport, UrllibTransport
from ahsync.clock import Clock, SystemClock
from ahsync.config import Config, ConfigError, load_config
from ahsync.store.db import Database, DatabaseError
from ahsync.store.prune import prune
from ahsync.sync.catchup import MarketSync, run_catchup, sync_markets
from ahsync.sync.scheduler import SavedVarsWatcher, Scheduler, SchedulerHooks, file_mtime_ms, find_savedvars

LOG_BYTES = 1_000_000
LOG_BACKUPS = 3
ALREADY_RUNNING = "AHSync is already running"


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


def setup_logging(data_dir: Path, redactor: Redactor) -> logging.Handler:
    """Log to data/ahsync.log, rotated, with secrets scrubbed (SPEC §5.2).

    Args:
        data_dir: The data folder.
        redactor: Secrets to scrub from every record.

    Returns:
        The file handler, attached to the "ahsync" logger (remove it to stop logging).
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(data_dir / "ahsync.log", maxBytes=LOG_BYTES,
                                                   backupCount=LOG_BACKUPS, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter(redactor))
    logger = logging.getLogger("ahsync")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler


def build_hooks(api: AhfApi, db: Database, clock: Clock, config: Config, caps_live: bool,
                backup_dir: Path) -> SchedulerHooks:
    """Wire the steady-state jobs that exist in M1.

    Args:
        api: AHF endpoints.
        db: The database.
        clock: Time source.
        config: Settings.
        caps_live: Whether this run has Premium live prices.
        backup_dir: data/backups.

    Returns:
        Hooks with store_hour, daily, weekly and (with Premium) live_refresh set.
    """
    caps_holder = {"caps": None}

    def store_hour() -> None:
        """Hourly :06 store: new hours, snapshot, books; no recompute (SPEC §3.1)."""
        caps = caps_holder["caps"] or api.me()
        caps_holder["caps"] = caps
        sync_markets(api, db, clock, config.markets, caps, config.retention_days)

    def live_refresh() -> None:
        """Premium 15-minute live table for each market."""
        caps = caps_holder["caps"] or api.me()
        caps_holder["caps"] = caps
        for market in config.markets:
            since = db.get_sync_state(market).live_since or clock.now_ms() - config.live_refresh_minutes * 60_000
            MarketSync(api, db, clock, market, caps, config.retention_days).store_live(since)

    def daily() -> None:
        """Daily prune and backup (SPEC §3.1); /me is re-checked at the next store."""
        caps_holder["caps"] = None
        prune(db, clock.now_ms(), config.retention_days)
        db.daily_backup(backup_dir, clock.now_ms())

    return SchedulerHooks(store_hour=store_hour, daily=daily, weekly=db.vacuum,
                          live_refresh=live_refresh if caps_live else None)


def run(data_dir: Path, config_path: Path, env: Mapping[str, str], home: Path, transport: Transport,
        clock: Clock, should_stop: Callable[[], bool], out: IO[str]) -> int:
    """Start AHSync and run until should_stop() is True.

    Args:
        data_dir: data/ (database, backups, log, lock).
        config_path: config.toml.
        env: Environment variables (for the API key).
        home: The user's home folder (for the key file).
        transport: HTTP transport (UrllibTransport in the app, FakeTransport in tests).
        clock: Time source.
        should_stop: Polled by the steady-state loop.
        out: Where user-facing start-up messages go (stderr in the app).

    Returns:
        Exit code: 0 after a normal stop, 1 when already running, 2 for a bad config or database.
    """
    lock = InstanceLock(data_dir / "ahsync.lock")
    if not lock.acquire():
        print(ALREADY_RUNNING, file=out)
        return 1
    handler = None
    db = None
    try:
        try:
            config = load_config(config_path, env, home)
        except ConfigError as error:
            print(f"AHSync can't start: {error}", file=out)
            return 2
        redactor = Redactor()
        redactor.add(config.api_key)
        handler = setup_logging(data_dir, redactor)
        log = logging.getLogger("ahsync")
        log.info("starting; markets %s; %s", ", ".join(config.markets), "with key" if config.api_key else "no key")
        backup_dir = data_dir / "backups"
        try:
            db = Database.open(data_dir / "ahsync.db", clock.now_ms(), backup_dir)
        except DatabaseError as error:
            print(f"AHSync can't start: {error}", file=out)
            return 2
        db.daily_backup(backup_dir, clock.now_ms())
        api = AhfApi(ApiClient(transport, clock, config.api_key, redactor=redactor))
        result = run_catchup(api, db, clock, config.markets, config.retention_days)
        prune(db, clock.now_ms(), config.retention_days)
        hooks = build_hooks(api, db, clock, config, FEATURE_LIVE in result.caps.features, backup_dir)
        watcher = SavedVarsWatcher(lambda: find_savedvars(Path(config.wow_path)), file_mtime_ms)
        scheduler = Scheduler(clock, hooks, watcher, config.daily_refresh_time, config.live_refresh_minutes)
        scheduler.run(should_stop)
        log.info("stopped")
        return 0
    finally:
        if db is not None:
            db.close()
        if handler is not None:
            logging.getLogger("ahsync").removeHandler(handler)
            handler.close()
        lock.release()


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
        return run(args.data, args.config, os.environ, Path.home(), UrllibTransport(), SystemClock(),
                   lambda: False, sys.stderr)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
