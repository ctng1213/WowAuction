"""Steady-state timers (SPEC §3.1, §2.2): scenario S10 and the job times."""
from __future__ import annotations

from pathlib import Path

from ahsync.clock import DAY_MS, HOUR_MS
from ahsync.sync.scheduler import (SavedVarsWatcher, Scheduler, SchedulerHooks, file_mtime_ms, find_savedvars,
                                   parse_daily_time)
from tests.support.fake_clock import FakeClock

SV = Path("WTF/Account/ACC/SavedVariables/AHProfit.lua")


def watcher_for(mtimes: dict[Path, int]) -> SavedVarsWatcher:
    """A watcher over fake modification times."""
    return SavedVarsWatcher(lambda: sorted(mtimes), mtimes.get)


def test_S10_savedvars_changed_1s_ago_is_not_read_until_unchanged_for_2s():
    clock = FakeClock()
    mtimes = {SV: clock.now_ms() + 4_000}          # rewritten 1 s before the first 5 s check
    changed: list[Path] = []
    scheduler = Scheduler(clock, SchedulerHooks(savedvars_changed=changed.append), watcher_for(mtimes))

    clock.advance(5_000)
    scheduler.tick()
    assert changed == []                           # modified 1 s ago: WoW may still be writing

    clock.advance(5_000)
    scheduler.tick()
    assert changed == [SV]                         # unchanged for 6 s now

    clock.advance(5_000)
    scheduler.tick()
    assert changed == [SV]                         # the same version is read once


def test_file_rewritten_on_every_check_is_never_read():
    clock = FakeClock()
    mtimes: dict[Path, int] = {}
    watcher = watcher_for(mtimes)
    for _ in range(5):
        clock.advance(5_000)
        mtimes[SV] = clock.now_ms() - 500
        assert watcher.poll(clock.now_ms()) == []


def test_prime_skips_files_already_read_and_missing_files_are_ignored():
    clock = FakeClock()
    other = Path("WTF/Account/B/SavedVariables/AHProfit.lua")
    mtimes = {SV: clock.now_ms() - 10_000}
    watcher = SavedVarsWatcher(lambda: [SV, other], mtimes.get)
    watcher.prime()
    assert watcher.poll(clock.now_ms()) == []
    mtimes[other] = clock.now_ms() - 3_000
    assert watcher.poll(clock.now_ms()) == [other]


def test_mark_handled_skips_only_the_version_already_read():
    clock = FakeClock()
    mtimes = {SV: clock.now_ms() - 10_000}
    watcher = watcher_for(mtimes)
    watcher.mark_handled(SV, mtimes[SV])                # read at start-up
    assert watcher.poll(clock.now_ms()) == []
    mtimes[SV] = clock.now_ms() - 3_000                 # WoW saved again
    assert watcher.poll(clock.now_ms()) == [SV]


def test_a_savedvars_hook_that_fails_leaves_the_version_unread_for_the_next_watch(caplog):
    clock = FakeClock()
    mtimes = {SV: clock.now_ms() - 10_000}
    calls: list[Path] = []

    def flaky(path: Path) -> None:
        calls.append(path)
        if len(calls) == 1:
            raise RuntimeError("database is locked")

    scheduler = Scheduler(clock, SchedulerHooks(savedvars_changed=flaky), watcher_for(mtimes))
    for _ in range(3):
        clock.advance(5_000)
        scheduler.tick()
    assert calls == [SV, SV]                       # failed, retried 5 s later, then handled once
    assert "savedvars_changed failed" in caplog.text


def test_hourly_jobs_run_at_00_and_06_and_daily_after_refresh_time():
    clock = FakeClock(now_ms=FakeClock().now_ms() + 30 * 60_000)     # 12:30 UTC
    hooks = SchedulerHooks()
    scheduler = Scheduler(clock, hooks, None, daily_refresh_time="06:00")
    assert scheduler.next_status == clock.now_ms() + 30 * 60_000      # 13:00
    assert scheduler.next_store == clock.now_ms() + 36 * 60_000       # 13:06
    assert scheduler.next_daily % DAY_MS == 6 * HOUR_MS               # 06:00 tomorrow (UTC machine)
    assert scheduler.tick() == []

    clock.sleep((scheduler.next_wake_ms() - clock.now_ms()) / 1000)
    assert scheduler.tick() == ["status_pass"]
    clock.advance(6 * 60_000)
    assert scheduler.tick() == ["store_hour"]
    clock.set(scheduler.next_daily)
    ran = scheduler.tick()
    assert ran[-2:] == ["full_refresh", "daily"]


def test_daily_time_is_local_and_live_and_weekly_run_on_their_intervals():
    clock = FakeClock(local_offset_ms=2 * HOUR_MS)                   # 14:00 local
    live_calls: list[int] = []
    hooks = SchedulerHooks(live_refresh=lambda: live_calls.append(clock.now_ms()))
    scheduler = Scheduler(clock, hooks, None, daily_refresh_time="06:00", live_refresh_minutes=15)
    assert (scheduler.next_daily + 2 * HOUR_MS) % DAY_MS == 6 * HOUR_MS
    clock.advance(15 * 60_000)
    assert "live_refresh" in scheduler.tick() and len(live_calls) == 1
    clock.advance(7 * DAY_MS)
    assert "weekly" in scheduler.tick()


def test_failing_job_is_logged_and_others_still_run(caplog):
    clock = FakeClock()
    ran: list[str] = []

    def broken() -> None:
        raise RuntimeError("boom")

    scheduler = Scheduler(clock, SchedulerHooks(status_pass=broken, store_hour=lambda: ran.append("store")), None)
    clock.advance(HOUR_MS)
    assert scheduler.tick() == ["status_pass", "store_hour"]
    assert ran == ["store"] and "status_pass failed" in caplog.text


def test_run_loop_sleeps_until_the_next_timer():
    clock = FakeClock()
    scheduler = Scheduler(clock, SchedulerHooks(), watcher_for({}))
    ticks = iter([False, False, True])
    scheduler.run(lambda: next(ticks))
    assert clock.sleeps == [5.0, 5.0]


def test_helpers(tmp_path):
    assert parse_daily_time("06:30") == 6.5 * HOUR_MS
    sv = tmp_path / "WTF" / "Account" / "X" / "SavedVariables" / "AHProfit.lua"
    sv.parent.mkdir(parents=True)
    sv.write_text("AHProfitDB = {}", encoding="utf-8")
    assert find_savedvars(tmp_path) == [sv]
    assert isinstance(file_mtime_ms(sv), int) and file_mtime_ms(tmp_path / "missing") is None
