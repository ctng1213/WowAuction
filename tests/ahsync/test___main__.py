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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ahsync.__main__ import (ALREADY_RUNNING, InstanceLock, RefreshRequests, Runtime, build_hooks, main,
                             read_savedvars_at_start, run, safe_backup, serve_dashboard)
from ahsync.collect.ahf import AhfApi
from ahsync.collect.client import ApiClient
from ahsync.clock import HOUR_MS
from ahsync.config import Config
from ahsync.state.importer import ingest_file
from ahsync.db.sqlite import Database
from ahsync.collect.catchup import SyncContext, run_catchup
from ahsync.app.scheduler import SavedVarsWatcher
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
    from ahsync.db.sqlite import DatabaseError

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

    def parser_bug_once(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 1:
            raise KeyError("a bug, not an expected error")
        return ingest_file(*args, **kwargs)

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
    assert status == 200 and body["data"]["rows"] == [] and body["data"]["hidden"] == 0 and body["built"] > 0
    with pytest.raises(ConnectionRefusedError):        # stopped: nothing listens on the port any more
        socket.create_connection(("127.0.0.1", port), timeout=5).close()
    # ...and the next start can bind it. A plain bind() is not that check: on macOS the closed request's
    # TIME_WAIT socket refuses it, while the dashboard binds with SO_REUSEADDR (ThreadingHTTPServer).
    ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler).server_close()


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


def test_S17_a_rewritten_savedvariables_file_logs_its_read_and_the_re_plan(tmp_path, caplog):
    clock = FakeClock()
    path = write_savedvars(tmp_path, clock, age_ms=60_000)
    db = Database.open(tmp_path / "ahsync.db", clock.now_ms(), None)
    ctx = SyncContext(None, db, clock, None)
    with caplog.at_level(logging.INFO, logger="ahsync"):
        build_hooks(ctx, Config(realms={"Grommash": "pvp"}), tmp_path / "backups").savedvars_changed(path)
    db.close()
    lines = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert lines[0].startswith("read AHProfit.lua: 0 scans, 1 events stored")
    assert lines[1] == "re-planned after reading AHProfit.lua: nothing (no published intelligence, or it failed)"


def test_FM36_a_stop_file_ends_the_loop_and_a_stale_one_never_stops_a_start(tmp_path):
    stop = tmp_path / "data" / "ahsync.stop"
    calls = {"n": 0}

    def appears_on_second_check() -> bool:
        calls["n"] += 1
        if calls["n"] == 2:
            stop.write_text("stop", encoding="utf-8")         # the deploy script asks AHSync to stop
        return calls["n"] > 10

    code, _ = start(tmp_path, AhfWorld(FakeClock()), should_stop=appears_on_second_check)
    assert (code, calls["n"], stop.exists()) == (0, 2, False)

    stop.write_text("stale", encoding="utf-8")                  # left by a crash: must not stop the next start
    calls["n"] = 0

    def after_three_checks() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    code, _ = start(tmp_path, AhfWorld(FakeClock()), should_stop=after_three_checks)
    assert (code, calls["n"], stop.exists()) == (0, 4, False)

    running = InstanceLock(tmp_path / "data" / "ahsync.lock")   # another AHSync runs, and deploy asked it to stop
    assert running.acquire()
    stop.write_text("stop", encoding="utf-8")
    try:
        code, printed = start(tmp_path, AhfWorld(FakeClock()))
    finally:
        running.release()
    assert (code, printed.strip(), stop.exists()) == (1, ALREADY_RUNNING, True)   # the request still stands


def test_SEC11_a_risk_tolerance_set_on_the_dashboard_reaches_the_schedulers_plan_jobs(tmp_path, monkeypatch):
    from ahsync import __main__ as app_main
    db = Database.open(tmp_path / "ahsync.db", FakeClock().now_ms(), None)
    seen = []
    monkeypatch.setattr(app_main, "full_refresh", lambda _db, _clock, config: seen.append(("full", config.risk_tolerance)))
    monkeypatch.setattr(app_main, "tracking_pass", lambda _db, _now=None: None)
    monkeypatch.setattr(app_main, "status_pass",
                        lambda _db, config, _now, _offset=0: seen.append(("status", config.risk_tolerance)))
    monkeypatch.setattr(app_main, "export_plan", lambda _db, config, *_a: seen.append(("plan", config.risk_tolerance)))
    live = app_main.LiveConfig(Config())
    hooks = build_hooks(SyncContext(None, db, FakeClock(), None), Config(), tmp_path / "backups", live=live)
    hooks.full_refresh()
    live.replace(Config(risk_tolerance=0.4))                            # what the dashboard's POST does
    hooks.full_refresh()
    hooks.status_pass()
    assert seen == [("full", 0.3), ("full", 0.4), ("status", 0.4), ("plan", 0.4)]
    db.close()


def test_the_ahledger_hook_polls_only_markets_with_an_address_and_a_failed_poll_is_logged_not_raised(tmp_path, caplog):
    from ahsync.config import MarketSources
    clock = FakeClock()
    db = Database.open(tmp_path / "ahsync.db", clock.now_ms(), None)
    polled: list[tuple[str, str]] = []
    refusals: list[str] = []

    class Source:
        """Records the polls; the second market's poll fails. Every market is refused for the backfill."""

        client = None

        def refused(self, market: str) -> bool:
            refusals.append(market)
            return True

        def poll(self, market: str, address: str) -> set[int]:
            polled.append((market, address))
            if market == "forever.pvp.alliance.us":
                raise RuntimeError("boom")
            return set()

    markets = ("forever.pvp.horde.us", "forever.pvp.alliance.us", "forever.normal.horde.us")
    config = Config(markets=markets, sources={"forever.normal.horde.us": MarketSources(addresses={"ahledger": ""})})
    hooks = build_hooks(SyncContext(None, db, clock, None, ahledger=Source()), config, tmp_path / "backups")
    hooks.ahledger_poll()                                               # no exception, the third market is skipped
    assert polled == [("forever.pvp.horde.us", "forever.pvp.horde.us"),
                      ("forever.pvp.alliance.us", "forever.pvp.alliance.us")]
    assert "AHledger poll of forever.pvp.alliance.us failed" in caplog.text
    hooks.ahledger_backfill()                                           # asks only about markets with an address
    assert refusals == ["forever.pvp.horde.us", "forever.pvp.alliance.us"]
    unwired = build_hooks(SyncContext(None, db, clock, None), config, tmp_path / "backups")
    assert unwired.ahledger_poll is None and unwired.ahledger_backfill is None
    db.close()


def test_DR1_python_m_ahsync_keeps_the_data_and_config_beside_it(monkeypatch):
    seen = {}
    monkeypatch.setattr("ahsync.__main__.run", lambda data_dir, config_path, _runtime: seen.update(
        args=(data_dir, config_path)) or 0)
    assert main([]) == 0
    assert seen["args"] == (Path("data"), Path("config.toml"))


def test_run_with_a_lock_its_caller_holds_leaves_the_release_to_the_caller(tmp_path):
    lock = InstanceLock(tmp_path / "data" / "ahsync.lock")
    assert lock.acquire()
    try:
        out = io.StringIO()
        (tmp_path / "bad.toml").write_text("dashboard_port = 0\n", encoding="utf-8")
        code = run(tmp_path / "data", tmp_path / "bad.toml",
                   Runtime({}, tmp_path, None, FakeClock(), lambda: True, out), held_lock=lock)
        assert code == 2 and ALREADY_RUNNING not in out.getvalue()
        assert InstanceLock(tmp_path / "data" / "ahsync.lock").acquire() is False   # still held
    finally:
        lock.release()


def test_FM50_a_start_that_finds_a_key_clears_setup_s_line_and_only_that_one(tmp_path):
    data = tmp_path / "data"
    db = Database.open(data / "ahsync.db", FakeClock().now_ms(), None)
    with db.transaction():
        db.set_status("api_key_setup", "warn", "api_key_not_saved", {"reason": "permission denied"}, 1)
        db.set_status("other", "warn", "lost", {}, 1)
    db.close()
    code, _printed = start(tmp_path, AhfWorld(FakeClock()), env={"AUCTIONHOUSE_APIKEY": KEY})
    db = Database.open(data / "ahsync.db", FakeClock().now_ms(), None)
    try:
        assert code == 0 and db.status("api_key_setup") is None and db.status("other") is not None
    finally:
        db.close()



def test_DR33_the_update_jobs_first_run_checks_whatever_the_last_check(tmp_path, monkeypatch):
    import calendar

    import ahsync.__main__ as app_main
    from ahsync.__main__ import LiveConfig, update_job
    from ahsync.app.update_check import write_meta
    from ahsync.web.server import AppHooks
    from tests.support.fake_transport import FakeTransport

    monkeypatch.setattr(app_main.version, "VERSION", "0.3.1")
    now = calendar.timegm((2026, 11, 6, 18, 0, 0)) * 1000
    db = Database.open(tmp_path / "ahsync.db", now, None)
    hooks = AppHooks(lambda _db, _config: {}, lambda e: ("set", e), "AHSync-{}-windows-x64-setup.exe".format,
                     lambda _path: None, lambda _app: None)
    try:
        write_meta(db, {"checked_ms": now - 12 * 3_600_000, "tag": "v0.3.1"})
        transport = FakeTransport.serve_github()
        job = update_job(db, transport, FakeClock(now), tmp_path, LiveConfig(Config(auto_update=False)), hooks)
        job()
        checks = [r for r in transport.requests if "api.github.com" in r.url]
        assert len(checks) == 1
        job()
        assert len([r for r in transport.requests if "api.github.com" in r.url]) == 1
    finally:
        db.close()


def test_DR34_a_manual_check_asks_now_retries_the_download_and_says_what_it_found(tmp_path, monkeypatch):
    import calendar

    import ahsync.__main__ as app_main
    from ahsync.__main__ import LiveConfig, update_job
    from ahsync.app.update_check import ManualCheck, read_meta, write_meta
    from ahsync.web.server import AppHooks
    from tests.support.fake_transport import FakeTransport

    monkeypatch.setattr(app_main.version, "VERSION", "0.3.1")
    now = calendar.timegm((2026, 11, 6, 9, 0, 0)) * 1000
    db = Database.open(tmp_path / "ahsync.db", now, None)
    manual = ManualCheck()
    hooks = AppHooks(lambda _db, _config: {}, lambda e: ("set", e), "AHSync-{}-windows-x64-setup.exe".format,
                     lambda _path: None, lambda _app: None, update_checks=manual)
    try:
        transport = FakeTransport.serve_github()
        job = update_job(db, transport, FakeClock(now), tmp_path, LiveConfig(Config()), hooks)
        job()                                                         # the start's check finds v0.4.0 and downloads
        write_meta(db, {"downloaded": None, "downloaded_path": None, "download_tried_ms": now - 3_600_000})
        sent = len(transport.requests)
        job()                                                         # 10 min later: nothing is due
        assert len(transport.requests) == sent
        manual.request()
        assert manual.tray_state() == "checking"
        job()                                                         # the tray asked: check and download again
        assert len(transport.requests) > sent and read_meta(db)["downloaded"] == "0.4.0"
        assert manual.tray_state() == ""                              # the menu offers Restart to update
    finally:
        db.close()

def test_DR20_the_update_job_checks_then_downloads_and_the_shell_gets_the_dashboard(tmp_path, monkeypatch):
    import calendar

    import ahsync.__main__ as app_main
    from ahsync.__main__ import LiveConfig, serve_dashboard, update_job
    from ahsync.app.update_check import read_meta
    from ahsync.web.server import AppHooks, stop_dashboard
    from tests.support.fake_transport import FakeTransport

    monkeypatch.setattr(app_main.version, "VERSION", "0.3.1")
    now = calendar.timegm((2026, 11, 6, 6, 0, 0)) * 1000
    db = Database.open(tmp_path / "ahsync.db", now, None)
    ready: list = []
    hooks = AppHooks(lambda _db, _config: {}, lambda e: ("set", e), "AHSync-{}-windows-x64-setup.exe".format,
                     lambda _path: None, ready.append)
    try:
        transport = FakeTransport.serve_github()
        update_job(db, transport, FakeClock(now), tmp_path, LiveConfig(Config()), hooks)()
        assert read_meta(db)["downloaded"] == "0.4.0"
        assert (tmp_path / "updates" / "AHSync-0.4.0-windows-x64-setup.exe").exists()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        served = serve_dashboard(tmp_path, Config(dashboard_port=port), FakeClock(now), RefreshRequests(),
                                 io.StringIO(), app_hooks=hooks)
        assert served is not None and len(ready) == 1 and ready[0].app_hooks is hooks
        stop_dashboard(served[0])
        served[1].close()
    finally:
        db.close()


def test_FM62_a_second_setup_logging_shares_the_handler_and_scrubs_its_secrets_while_it_runs(tmp_path):
    from ahsync.__main__ import setup_logging
    from ahsync.collect.client import Redactor

    logger = logging.getLogger("ahsync")
    before = list(logger.handlers)
    release_shell = setup_logging(tmp_path, Redactor())
    keyed = Redactor()
    keyed.add("SECRETKEY123")
    release_run = setup_logging(tmp_path, keyed)
    try:
        assert len(logger.handlers) == len(before) + 1          # one handler: no line written twice
        logging.getLogger("ahsync.app").warning("shell line")
        logging.getLogger("ahsync").warning("run line with SECRETKEY123")
        try:
            raise ValueError("bad key SECRETKEY123")
        except ValueError:
            logging.getLogger("ahsync").exception("traceback")   # the shell's filter formats it first
    finally:
        release_run()
    assert len(logger.handlers) == len(before) + 1               # the shell's handler stays
    logging.getLogger("ahsync.app").warning("after run")
    release_shell()
    assert logger.handlers == before
    text = (tmp_path / "ahsync.log").read_text(encoding="utf-8")
    assert "SECRETKEY123" not in text and "ValueError: bad key ***" in text
    lines = [line.split(": ", 1)[1] for line in text.splitlines() if " WARNING " in line or " ERROR " in line]
    assert lines == ["shell line", "run line with ***", "traceback", "after run"]
