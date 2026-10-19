"""Tests for tools/deploy.py --restart: stop AHSync, start it again, wait for its addon files (SPEC §3.5, FM37)."""
from __future__ import annotations

from pathlib import Path

import tools.deploy as deploy

MARKET, PLAN = deploy.GENERATED


class FakeWorld:
    """A fake clock, lock and AHSync: the lock is held for `held_checks` more checks, and once started AHSync
    writes each file `write_after_s` seconds later (None: never)."""

    def __init__(self, held_checks: int, write_after_s: dict[str, float | None]) -> None:
        self.t = 1000.0
        self.held_checks = held_checks
        self.write_after_s = write_after_s
        self.started_at: float | None = None
        self.starts = 0
        self.lines: list[str] = []
        self.stop_seen_while_held = False

    def env(self, data_dir: Path) -> deploy.Restart:
        """The Restart hooks over this world."""
        def lock_free() -> bool:
            if self.held_checks > 0:
                self.held_checks -= 1
                self.stop_seen_while_held |= (data_dir / deploy.STOP_FILE).exists()
                return False
            return True

        def start() -> None:
            self.starts += 1
            self.started_at = self.t

        def sleep(seconds: float) -> None:
            self.t += seconds

        def mtime(path: Path) -> float | None:
            after = self.write_after_s.get(path.name)
            if self.started_at is None or after is None or self.t < self.started_at + after:
                return 500.0                                   # the old AHSync's file
            return self.started_at + after

        return deploy.Restart(lock_free, start, lambda: self.t, sleep, mtime, self.lines.append)


def test_FM37_restart_stops_the_running_ahsync_starts_one_and_waits_for_both_files(tmp_path):
    world = FakeWorld(held_checks=4, write_after_s={MARKET: 12, PLAN: 25})   # 1 first check + 3 while waiting

    assert deploy.restart(tmp_path, tmp_path / "AHProfit_Data", world.env(tmp_path)) == 0

    assert world.stop_seen_while_held and world.starts == 1 and world.started_at == 1001.5   # 3 polls of 0.5 s
    assert world.lines == ["deploy: asking the running AHSync to stop...",
                           "deploy: AHSync started (its log: data/ahsync.log)",
                           "deploy: waiting for AHSync's full refresh...",
                           "deploy: waiting for AHSync's full refresh...",
                           "ready: /reload in game now"]
    assert world.t == world.started_at + 25


def test_FM37_restart_without_a_running_ahsync_just_starts_one(tmp_path):
    world = FakeWorld(held_checks=0, write_after_s={MARKET: 1, PLAN: 1})
    assert deploy.restart(tmp_path, tmp_path, world.env(tmp_path)) == 0
    assert world.starts == 1 and not (tmp_path / deploy.STOP_FILE).exists()
    assert world.lines[-1] == "ready: /reload in game now"


def test_FM37_an_ahsync_that_does_not_stop_within_60_s_is_left_running(tmp_path):
    world = FakeWorld(held_checks=10_000, write_after_s={})
    assert deploy.restart(tmp_path, tmp_path, world.env(tmp_path)) == 1
    assert world.starts == 0 and not (tmp_path / deploy.STOP_FILE).exists() and world.t == 1060
    assert world.lines[-1].startswith("deploy: AHSync did not stop within 60 s")


def test_FM37_files_not_rewritten_within_15_minutes_give_up_with_a_message(tmp_path):
    world = FakeWorld(held_checks=0, write_after_s={MARKET: 5, PLAN: None})
    assert deploy.restart(tmp_path, tmp_path, world.env(tmp_path)) == 1
    assert world.t == 1000 + 15 * 60 and world.lines.count("deploy: waiting for AHSync's full refresh...") == 89
    assert world.lines[-1].startswith("deploy: AHSync has not written both addon files after 15 minutes")


def test_the_lock_check_takes_and_releases_the_lock(tmp_path):
    lock = deploy.InstanceLock(tmp_path / "ahsync.lock")
    assert lock.acquire()
    try:
        assert deploy.lock_is_free(tmp_path / "ahsync.lock") is False
    finally:
        lock.release()
    assert deploy.lock_is_free(tmp_path / "ahsync.lock") is True
    assert deploy.file_mtime(tmp_path / "ahsync.lock") is not None and deploy.file_mtime(tmp_path / "none") is None
