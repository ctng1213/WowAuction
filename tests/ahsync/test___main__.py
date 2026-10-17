"""Start-up (SPEC §3.1, §5.2): scenarios S8 and FM15, plus the start sequence."""
from __future__ import annotations

import http.client
import io
import json
import logging
import os
import shutil
import socket
import sqlite3
from pathlib import Path

import pytest

from ahsync.__main__ import (ALREADY_RUNNING, InstanceLock, RefreshRequests, Runtime, build_hooks, main,
                             read_savedvars_at_start, run, safe_backup, serve_dashboard)
from ahsync.api.ahf import AhfApi
from ahsync.api.client import ApiClient
from ahsync.clock import HOUR_MS
from ahsync.config import Config
from ahsync.ingest.apply import ingest_file
from ahsync.store.db import Database
from ahsync.sync.catchup import SyncContext, run_catchup
from ahsync.sync.scheduler import SavedVarsWatcher
from tests.support.builders import AhfWorld, h1_path
from tests.support.fake_clock import FakeClock
from tests.support.fake_transport import DEFAULT_MARKET

KEY = "ak_live_SECRET_9f8e7d6c5b4a"
A1_GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "savedvars" / "a1.lua"


def start(tmp_path, world: AhfWorld, config_text: str = "", stop_after_ticks: int = 0, env=None,
          should_stop=None, dashboard: bool = False) -> tuple[int, str]:
    """Run AHSync against a fake world until it has ticked a few times; return (exit code, printed text).

    wow_path points into tmp_path (line 1 of config.toml), so no test ever reads a real WoW folder.
    """
    config = tmp_path / "config.toml"
    config.write_text(f'wow_path = "{(tmp_path / "wow").as_posix()}"\n' + config_text, encoding="utf-8")
    calls = {"n": 0}

    def stop_after() -> bool:
        calls["n"] += 1
        return calls["n"] > stop_after_ticks

    out = io.StringIO()
    code = run(tmp_path / "data", config, Runtime(env or {}, tmp_path, world.transport, world.clock,
                                                  should_stop or stop_after, out, serve_dashboard=dashboard))
    return code, out.getvalue()


def test_S8_api_key_in_config_never_appears_in_logs_urls_or_exceptions(tmp_path, caplog):
    clock = FakeClock()
    world = AhfWorld(clock, premium=True)
    t = world.transport
    book_path = [p for p in t.routes if p.startswith("books/")][0]
    t.fail(book_path, 403, body=('{"feature": "%s"}' % KEY).encode())   # an error echoing the key
    t.truncate(h1_path(DEFAULT_MARKET, world.sealed_h1 - 3), 0.6)
    t.fail(h1_path(DEFAULT_MARKET, world.sealed_h1 - 2), 503, times=2)
    t.network_error(h1_path(DEFAULT_MARKET, world.sealed_h1 - 1), times=1)

    with caplog.at_level(logging.DEBUG, logger="ahsync"):
        code, printed = start(tmp_path, world, f'api_key = "{KEY}"\n', stop_after_ticks=2)

    assert code == 0
    log_text = (tmp_path / "data" / "ahsync.log").read_text(encoding="utf-8")
    assert "hour" in log_text and "403" in log_text                   # errors were logged…
    assert KEY not in log_text and "cap-h1-" not in log_text           # …without the key or capability tokens
    assert KEY not in caplog.text and KEY not in printed
    assert all(KEY not in r.url for r in t.requests)
    assert any(r.headers.get("Authorization") == "Bearer " + KEY for r in t.requests)   # the header is the only place
    for exc_text in (r.exc_text or "" for r in caplog.records):
        assert KEY not in exc_text


def test_FM15_second_start_exits_with_already_running(tmp_path):
    first = InstanceLock(tmp_path / "data" / "ahsync.lock")
    assert first.acquire()
    try:
        code, printed = start(tmp_path, AhfWorld(FakeClock()))
    finally:
        first.release()
    assert code == 1 and printed.strip() == ALREADY_RUNNING == "AHSync is already running"
    assert not (tmp_path / "data" / "ahsync.db").exists()

    again = InstanceLock(tmp_path / "data" / "ahsync.lock")             # released: free to take
    assert again.acquire()
    again.release()
    again.release()


def test_bad_config_refuses_to_start(tmp_path):
    code, printed = start(tmp_path, AhfWorld(FakeClock()), 'retention_days = "ninety"\n')
    assert code == 2 and "config.toml line 2: retention_days must be a number" in printed   # line 1: wow_path


def test_start_runs_catchup_backup_and_the_hourly_store(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    code, _ = start(tmp_path, world, stop_after_ticks=1)
    assert code == 0
    data = tmp_path / "data"
    assert (data / "ahsync.db").exists() and list((data / "backups").glob("ahsync-*.db"))
    sealed_before = world.sealed_h1

    world.advance(HOUR_MS)          # an hour later the app restarts, then its :06 store picks up the next hour
    world.transport.requests.clear()
    code, _ = start(tmp_path, world, stop_after_ticks=40)
    assert code == 0
    assert h1_path(DEFAULT_MARKET, sealed_before + 1) in world.transport.paths()
    assert h1_path(DEFAULT_MARKET, sealed_before + 2) not in world.transport.paths()   # not sealed yet


def test_damaged_database_without_backup_folder_access_is_reported(tmp_path, monkeypatch):
    from ahsync import __main__ as app
    from ahsync.store.db import DatabaseError

    def refuse(*_args, **_kwargs):
        raise DatabaseError("database schema 9 is newer than this AHSync (1); update AHSync")

    monkeypatch.setattr(app.Database, "open", refuse)
    code, printed = start(tmp_path, AhfWorld(FakeClock()))
    assert code == 2 and "update AHSync" in printed


def test_main_parses_arguments(tmp_path, monkeypatch):
    seen = {}

    def fake_run(data_dir, config_path, _runtime):
        seen["args"] = (data_dir, config_path)
        return 0

    monkeypatch.setattr("ahsync.__main__.run", fake_run)
    assert main(["--data", str(tmp_path / "d"), "--config", str(tmp_path / "c.toml")]) == 0
    assert seen["args"] == (tmp_path / "d", tmp_path / "c.toml")

    def interrupted(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr("ahsync.__main__.run", interrupted)
    assert main([]) == 0


def test_hourly_store_rechecks_me_and_degrades_when_premium_ends(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock, premium=True, until_ms=clock.now_ms() + HOUR_MS)
    db = Database.open(tmp_path / "ahsync.db", clock.now_ms(), None)
    api = AhfApi(ApiClient(world.transport, clock, "ak_x"))
    result = run_catchup(api, db, clock, (DEFAULT_MARKET,))
    ctx = SyncContext(api, db, clock, result.caps)
    hooks = build_hooks(ctx, Config(), tmp_path / "backups")
    assert hooks.live_refresh is None                                   # live table unsupported for now

    world = AhfWorld(clock, premium=False, transport=world.transport)    # contributor expired mid-session
    world.advance(HOUR_MS)
    world.transport.remove_matching(f"candles/v1/h1c/")
    world.transport.requests.clear()
    hooks.store_hour()

    assert not ctx.caps.premium and api.client.per_minute == 60
    assert db.status("premium").kind == "premium_ended"
    assert not [p for p in world.transport.paths() if "h1c/" in p or "d1c/" in p or "books/" in p]
    assert db.get_sync_state(DEFAULT_MARKET).last_hour == world.sealed_h1

    world.transport.fail("api/v1/me", 503)                              # unreachable: skipped, nothing raised
    hooks.store_hour()
    hooks.daily()
    db.close()


def test_backup_failure_is_logged_not_fatal(tmp_path, caplog):
    db = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    blocker = tmp_path / "backups"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="ahsync"):
        safe_backup(db, blocker, FakeClock().now_ms())
    assert "daily backup failed" in caplog.text
    db.close()


def write_savedvars(tmp_path, clock: FakeClock, age_ms: int) -> Path:
    """Copy the a1 golden SavedVariables into the test's WoW folder, modified age_ms before now."""
    path = tmp_path / "wow" / "WTF" / "Account" / "ACC" / "SavedVariables" / "AHProfit.lua"
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(A1_GOLDEN, path)
    mtime_ns = (clock.now_ms() - age_ms) * 1_000_000
    os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def stored_characters(tmp_path) -> list[tuple]:
    """Characters in the app's database after it stopped."""
    db = Database.open(tmp_path / "data" / "ahsync.db", FakeClock().now_ms(), None)
    try:
        return db.conn.execute("SELECT id, market FROM characters ORDER BY id").fetchall()
    finally:
        db.close()


def test_start_reads_savedvariables_before_catchup(tmp_path):
    world = AhfWorld(FakeClock())
    write_savedvars(tmp_path, world.clock, age_ms=60_000)
    code, _ = start(tmp_path, world, '[realms]\nGrommash = "pvp"\n')
    assert code == 0
    assert stored_characters(tmp_path) == [("Amaya-Grommash", "forever.pvp.horde.us"),
                                           ("Brugan-Grommash", "forever.pvp.horde.us")]


def test_a_savedvariables_file_still_being_written_at_start_is_read_by_the_watcher(tmp_path):
    world = AhfWorld(FakeClock())
    write_savedvars(tmp_path, world.clock, age_ms=500)
    code, _ = start(tmp_path, world, stop_after_ticks=0)
    assert code == 0 and stored_characters(tmp_path) == []           # too fresh to read at start

    code, _ = start(tmp_path, world, stop_after_ticks=2)              # the first watch, 5 s later, reads it
    assert code == 0 and len(stored_characters(tmp_path)) == 2


def test_a_failed_start_up_read_is_logged_on_status_and_retried_by_the_watcher(tmp_path, monkeypatch, caplog):
    world = AhfWorld(FakeClock())
    write_savedvars(tmp_path, world.clock, age_ms=60_000)
    calls = []

    def parser_bug_once(*args):
        calls.append(args[0])
        if len(calls) == 1:
            raise KeyError("a bug, not an expected error")
        return ingest_file(*args)

    monkeypatch.setattr("ahsync.__main__.ingest_file", parser_bug_once)
    with caplog.at_level(logging.WARNING, logger="ahsync"):
        code, _ = start(tmp_path, world)                                  # start-up only, no ticks
    assert code == 0 and "reading AHProfit.lua failed" in caplog.text
    db = Database.open(tmp_path / "data" / "ahsync.db", world.clock.now_ms(), None)
    assert db.status("savedvars:ACC").kind == "savedvars_failed"
    db.close()

    code, _ = start(tmp_path, world, stop_after_ticks=2)                 # the next run's watcher reads it
    assert code == 0 and len(stored_characters(tmp_path)) == 2


def test_a_start_up_failure_that_cant_reach_status_is_still_only_logged(tmp_path, monkeypatch, caplog):
    clock = FakeClock()
    write_savedvars(tmp_path, clock, age_ms=60_000)

    class BrokenDb:
        def set_status(self, *_args):
            raise sqlite3.OperationalError("database or disk is full")

    def disk_full(*_args):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr("ahsync.__main__.ingest_file", disk_full)
    watcher = SavedVarsWatcher(lambda: [], lambda _p: None)
    with caplog.at_level(logging.WARNING, logger="ahsync"):
        read_savedvars_at_start(BrokenDb(), clock, Config(wow_path=str(tmp_path / "wow")), watcher)
    assert "could not record the failure on Status" in caplog.text


def test_savedvars_hook_ingests_the_changed_file(tmp_path):
    clock = FakeClock()
    path = write_savedvars(tmp_path, clock, age_ms=60_000)
    db = Database.open(tmp_path / "ahsync.db", clock.now_ms(), None)
    ctx = SyncContext(None, db, clock, None)
    build_hooks(ctx, Config(realms={"Grommash": "pvp"}), tmp_path / "backups").savedvars_changed(path)
    assert db.count("characters") == 2 and db.count("posts") == 1
    db.close()


def free_port() -> int:
    """A loopback port that was free a moment ago."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_start_serves_the_dashboard_during_the_steady_state_and_stops_it(tmp_path):
    port = free_port()
    seen = {}

    def query_then_stop() -> bool:
        """Runs on the main thread inside the steady state; the dashboard answers from its own thread."""
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/markets", headers={"Host": f"localhost:{port}"})
        reply = conn.getresponse()
        seen["markets"] = (reply.status, json.loads(reply.read()))
        conn.request("GET", "/api/views/craft", headers={"Host": f"localhost:{port}"})
        reply = conn.getresponse()
        seen["craft"] = (reply.status, json.loads(reply.read()))
        conn.close()
        return True

    code, printed = start(tmp_path, AhfWorld(FakeClock()), f"dashboard_port = {port}\n", should_stop=query_then_stop,
                          dashboard=True)
    assert code == 0 and f"Dashboard: http://localhost:{port}" in printed
    assert seen["markets"] == (200, {"markets": [DEFAULT_MARKET], "default": DEFAULT_MARKET})
    status, body = seen["craft"]                      # the start-up full refresh published it (SPEC §3.1)
    assert status == 200 and body["data"] == {"rows": [], "hidden": 0} and body["built"] > 0
    with socket.socket() as sock:                      # stopped: the port is free again
        sock.bind(("127.0.0.1", port))


def test_a_dashboard_port_in_use_is_logged_not_fatal(tmp_path, caplog):
    Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None).close()
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        out = io.StringIO()
        with caplog.at_level(logging.WARNING, logger="ahsync"):
            result = serve_dashboard(tmp_path, Config(dashboard_port=port), FakeClock(), RefreshRequests(), out)
    assert result is None
    assert f"port {port} is not available" in out.getvalue() and "dashboard not started" in caplog.text


def test_refresh_requests_merge_until_taken():
    requests = RefreshRequests()
    assert requests.take() is False
    requests.request_refresh()
    requests.request_refresh()                         # pressed twice: one refresh (IU4)
    assert requests.take() is True and requests.take() is False


def test_refresh_requests_merge_and_a_take_consumes_exactly_one():
    import threading as _threading
    requests = RefreshRequests()
    assert requests.take() is False
    workers = [_threading.Thread(target=requests.request_refresh) for _ in range(20)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert requests.take() is True and requests.take() is False
