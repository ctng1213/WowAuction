"""Catch-up (SPEC §3.1, §2.1, §5.2): scenarios S1–S4, S11, FM1–FM4, FM6."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest

from ahsync.collect.ahf import AhfApi, Capabilities, FileIndex
from ahsync.collect.client import ApiClient
from ahsync.clock import DAY_MS, HOUR_MS
from ahsync.db.sqlite import Database
from ahsync.collect import catchup
from ahsync.collect.catchup import (D1C, H1, H1C, LOST, SyncContext, format_day, format_hour, hour_source,
                                 plan_hours, run_catchup, sync_markets)
from tests.support.builders import AhfWorld, files_index, h1_path, h1c_path, hour_file, snapshot
from tests.support.fake_clock import FakeClock
from tests.support.fake_transport import DEFAULT_MARKET, SimulatedCrash

MARKET = DEFAULT_MARKET
ITEMS_PER_HOUR = 27      # items in the recorded hour file
KEY = "ak_test_key_never_shown"


def open_db(folder: Path, clock: FakeClock) -> Database:
    """Open (or reopen, as after a restart) the test database."""
    return Database.open(folder / "ahsync.db", clock.now_ms(), folder / "backups")


def api_for(world: AhfWorld, key: str = "") -> AhfApi:
    """A fresh client and API over the world's transport, as a new run would build."""
    return AhfApi(ApiClient(world.transport, world.clock, key))


def h1_hours(world: AhfWorld) -> list[int]:
    """Hours requested from the public h1 path, in request order."""
    prefix = f"candles/v1/h1/{MARKET}/"
    return [int(p.removeprefix(prefix).split(".")[0]) for p in world.transport.paths() if p.startswith(prefix)]


def h1c_hours(world: AhfWorld) -> list[int]:
    """Hours requested from the Premium h1c path."""
    prefix = f"candles/v1/h1c/{MARKET}/"
    return [int(p.rsplit("/", 1)[1].split(".")[0]) for p in world.transport.paths() if p.startswith(prefix)]


def candle_rows(db: Database) -> list[tuple]:
    """Every stored hour candle, sorted, for comparing databases."""
    return db.conn.execute("SELECT * FROM candles_h1 ORDER BY market, key, t, src").fetchall()


def status_of(db: Database, topic: str) -> tuple[str, dict] | None:
    """A Status line's (kind, data), or None when there is none."""
    line = db.status(topic)
    return (line.kind, line.data) if line else None


def start_with_gap(db: Database, world: AhfWorld, hours: int) -> int:
    """Pretend the last run stored up to `hours` before sealed.h1.

    Returns:
        The first missing hour.
    """
    last = world.sealed_h1 - hours
    db.update_sync_state(MARKET, last_hour=last, last_run=(last + 1) * HOUR_MS)
    return last + 1


# ------------------------------------------------------------ scenarios

def test_S1_first_run_without_key_fetches_public_d1_and_hours_within_7_days(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)

    result = run_catchup(api_for(world), db, clock, (MARKET,))

    assert result.aborted is None
    assert h1_hours(world) == world.public_hours()           # every hour ≤ sealed within 7 days, in order
    assert max(h1_hours(world)) == world.sealed_h1            # nothing beyond sealed.h1
    paths = world.transport.paths()
    assert paths.count(f"candles/v1/d1/{MARKET}/{world.sealed_d1}.json.gz") == 1
    assert not [p for p in paths if any(s in p for s in ("h1c/", "d1c/", "volume/", "books/"))]
    assert all("Authorization" not in r.headers for r in world.transport.requests)
    assert db.count("candles_h1") == len(world.public_hours()) * ITEMS_PER_HOUR
    assert db.count("candles_d1") == 7 * ITEMS_PER_HOUR
    assert db.count("current") == ITEMS_PER_HOUR
    assert db.get_sync_state(MARKET).last_hour == world.sealed_h1
    assert result.reports[0].lost == []                       # first run: nothing counts as lost


def test_S2_crash_after_hour_37_of_112_resumes_at_hour_38_without_duplicates(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path / "a", clock)
    first = start_with_gap(db, world, 112)
    hour_38 = first + 37
    world.transport.crash_on(h1_path(MARKET, hour_38))

    with pytest.raises(SimulatedCrash):
        run_catchup(api_for(world), db, clock, (MARKET,))
    assert db.get_sync_state(MARKET).last_hour == first + 36          # hour 37 committed
    db.close()

    db = open_db(tmp_path / "a", clock)                               # restart
    world.transport.requests.clear()
    run_catchup(api_for(world), db, clock, (MARKET,))

    assert h1_hours(world)[0] == hour_38
    assert db.count("candles_h1") == 112 * ITEMS_PER_HOUR
    reference = open_db(tmp_path / "b", clock)                        # the same run, never interrupted
    start_with_gap(reference, world, 112)
    run_catchup(api_for(world), reference, clock, (MARKET,))
    assert candle_rows(db) == candle_rows(reference)


def test_S3_ten_day_gap_without_key_reports_lost_hours_and_never_requests_them(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    last = world.now_hour - 10 * 24
    db.update_sync_state(MARKET, last_hour=last, last_run=(last + 1) * HOUR_MS)

    result = run_catchup(api_for(world), db, clock, (MARKET,))

    oldest_public = world.now_hour - 7 * 24 + 1
    assert result.reports[0].lost == [(last + 1, oldest_public - 1)]   # the hours 7–10 days old
    assert min(h1_hours(world)) == oldest_public                       # none of them requested
    assert h1c_hours(world) == []
    assert status_of(db, f"lost:{MARKET}") == ("lost", {
        "market": MARKET, "hours": oldest_public - last - 1,
        "ranges_ms": [[(last + 1) * HOUR_MS, oldest_public * HOUR_MS]]})
    kind, data = status_of(db, f"gap:{MARKET}")                        # status warning
    assert (kind, data["days"], data["last_run_ms"]) == ("long_gap", 9, (last + 1) * HOUR_MS)
    assert db.get_sync_state(MARKET).last_hour == world.sealed_h1


def test_S4_ten_day_gap_with_premium_fetches_days_8_to_10_from_h1c(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock, premium=True)
    db = open_db(tmp_path, clock)
    last = world.now_hour - 10 * 24
    db.update_sync_state(MARKET, last_hour=last, last_run=(last + 1) * HOUR_MS)

    result = run_catchup(api_for(world, KEY), db, clock, (MARKET,))

    oldest_public = world.now_hour - 7 * 24 + 1
    assert h1c_hours(world) == list(range(last + 1, oldest_public))
    hour = last + 1
    assert h1c_path(MARKET, hour) == f"candles/v1/h1c/{MARKET}/cap-h1-{hour * HOUR_MS // DAY_MS}/{hour}.json.gz"
    assert h1c_path(MARKET, hour) in world.transport.paths()          # token from caps.h1[day]
    assert h1_hours(world) == world.public_hours()
    assert result.reports[0].lost == []
    assert db.count("candles_h1") == (world.sealed_h1 - last) * ITEMS_PER_HOUR
    assert db.count("candles_d1") == 90 * ITEMS_PER_HOUR                # d1c, not public d1


def test_S11_premium_lost_since_last_run_degrades_to_public_data(tmp_path):
    clock = FakeClock()
    until = 1_792_798_058_214            # contributor.until in the recording: 23 Oct 2026
    world = AhfWorld(clock, premium=True, until_ms=until)
    db = open_db(tmp_path, clock)
    run_catchup(api_for(world, KEY), db, clock, (MARKET,))
    assert status_of(db, "premium") == ("premium_until", {"until_ms": until})

    clock.advance(DAY_MS)
    world = AhfWorld(clock, premium=False)                   # /me lost depth, prices.live, historyDays 90
    result = run_catchup(api_for(world, KEY), db, clock, (MARKET,))

    premium_paths = [p for p in world.transport.paths()
                     if any(s in p for s in ("h1c/", "d1c/", "volume/", "books/")) or p == f"api/v1/ah/{MARKET}"]
    assert premium_paths == []
    assert f"snap/v1/{MARKET}/{world.now_hour}.json.gz" in world.transport.paths()
    assert db.get_sync_state(MARKET).live_since is None
    snap_at = world.now_hour * HOUR_MS
    assert db.count("current", "src = 'ahf'") == ITEMS_PER_HOUR
    assert db.count("current", "src = 'ahf' AND at = ?", (clock.now_ms(),)) == 0   # live rows replaced
    assert snapshot(snap_at)["rows"][0][3] == db.current_price(MARKET, 774).at
    assert status_of(db, "premium") == ("premium_ended", {"ended_ms": until})     # "Premium ended 23 Oct"
    assert result.aborted is None and db.get_sync_state(MARKET).last_hour == world.sealed_h1


def test_FM1_503_on_hour_40_five_times_backs_off_then_ends_the_run_at_hour_39(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    first = start_with_gap(db, world, 112)
    hour_40 = first + 39
    world.transport.fail(h1_path(MARKET, hour_40), 503)

    result = run_catchup(api_for(world), db, clock, (MARKET,))

    assert clock.sleeps == [1, 2, 4, 8, 16]
    assert world.transport.count(h1_path(MARKET, hour_40)) == 6        # first try + 5 retries
    assert result.aborted is not None
    assert db.get_sync_state(MARKET).last_hour == hour_40 - 1
    assert not [p for p in world.transport.paths() if p.startswith("snap/")]   # the run ended there
    assert status_of(db, "network") == ("unreachable", {"reason": "retries_exhausted"})

    world.transport.clear_faults()
    world.transport.requests.clear()
    run_catchup(api_for(world), db, clock, (MARKET,))
    assert h1_hours(world)[0] == hour_40
    assert db.status("network") is None


def test_FM2_hour_41_cut_off_at_60_percent_is_not_committed_and_refetched_next_run(tmp_path, caplog):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    first = start_with_gap(db, world, 112)
    hour_41 = first + 40
    world.transport.truncate(h1_path(MARKET, hour_41), 0.6, times=1)

    with caplog.at_level(logging.WARNING, logger="ahsync"):
        run_catchup(api_for(world), db, clock, (MARKET,))

    assert db.count("candles_h1", "t = ?", (hour_41 * HOUR_MS,)) == 0
    assert db.count("candles_h1", "t = ?", ((hour_41 + 1) * HOUR_MS,)) == ITEMS_PER_HOUR
    assert f"hour {hour_41} not stored" in caplog.text
    assert db.fetch_failures(MARKET) == [(hour_41, 1)]

    world.advance(HOUR_MS)
    world.transport.requests.clear()
    run_catchup(api_for(world), db, clock, (MARKET,))
    assert h1_hours(world)[0] == hour_41                                # refetched first
    assert db.count("candles_h1", "t = ?", (hour_41 * HOUR_MS,)) == ITEMS_PER_HOUR
    assert db.fetch_failures(MARKET) == []


def test_FM3_same_hour_bad_three_runs_in_a_row_shows_bad_file_and_later_hours_sync(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    first = start_with_gap(db, world, 112)
    hour_41 = first + 40
    world.transport.truncate(h1_path(MARKET, hour_41), 0.6)

    run_catchup(api_for(world), db, clock, (MARKET,))
    assert db.count("candles_h1", "t = ?", ((hour_41 + 1) * HOUR_MS,)) == ITEMS_PER_HOUR   # later hours sync
    for _ in range(2):
        assert db.status(f"bad_file:{MARKET}:{hour_41}") is None
        world.advance(HOUR_MS)
        run_catchup(api_for(world), db, clock, (MARKET,))

    assert status_of(db, f"bad_file:{MARKET}:{hour_41}") == ("bad_file", {"market": MARKET, "hour": hour_41,
                                                                         "failures": 3})
    assert db.fetch_failures(MARKET) == [(hour_41, 3)]
    assert db.get_sync_state(MARKET).last_hour == world.sealed_h1


def test_FM4_snapshot_with_renamed_field_is_disabled_and_last_good_current_is_kept(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    run_catchup(api_for(world), db, clock, (MARKET,))
    before = db.conn.execute("SELECT * FROM current ORDER BY key").fetchall()

    world.advance(HOUR_MS)
    changed = snapshot(world.now_hour * HOUR_MS)
    changed["entries"] = changed.pop("rows")
    world.transport.serve(f"snap/v1/{MARKET}/{world.now_hour}.json.gz", changed)
    result = run_catchup(api_for(world), db, clock, (MARKET,))

    assert result.reports[0].disabled == ["snapshot"]
    assert db.conn.execute("SELECT * FROM current ORDER BY key").fetchall() == before
    assert status_of(db, "format:snapshot") == ("format_changed", {"endpoint": "snapshot"})
    assert db.get_sync_state(MARKET).last_hour == world.sealed_h1     # the rest of the run completed


def test_FM6_crash_inside_hour_38_commit_rolls_back_and_restart_resumes_at_38(tmp_path, monkeypatch):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path / "a", clock)
    first = start_with_gap(db, world, 112)
    hour_38 = first + 37
    real_update = db.update_sync_state

    def crash_at_38(market, **fields):
        if fields.get("last_hour") == hour_38:
            raise SimulatedCrash("power loss")       # after hour 38's candles were written, before commit
        real_update(market, **fields)

    monkeypatch.setattr(db, "update_sync_state", crash_at_38)
    with pytest.raises(SimulatedCrash):
        run_catchup(api_for(world), db, clock, (MARKET,))
    assert db.count("candles_h1", "t = ?", (hour_38 * HOUR_MS,)) == 0   # rolled back
    db.close()

    db = open_db(tmp_path / "a", clock)
    assert db.get_sync_state(MARKET).last_hour == hour_38 - 1
    world.transport.requests.clear()
    run_catchup(api_for(world), db, clock, (MARKET,))
    assert h1_hours(world)[0] == hour_38
    assert db.count("candles_h1") == 112 * ITEMS_PER_HOUR
    duplicates = db.conn.execute("SELECT COUNT(*) FROM (SELECT market, key, t FROM candles_h1 "
                                 "GROUP BY market, key, t HAVING COUNT(*) > 1)").fetchone()[0]
    assert duplicates == 0


# ------------------------------------------------------------ planning and edges

def files_with(sealed_h1: int, caps_h1: dict[int, str] | None = None, d1c: bool = False) -> FileIndex:
    """A minimal file index for planning tests."""
    return FileIndex(MARKET, "https://cdn.example/", sealed_h1, sealed_h1 // 24, None, None, caps_h1 or {},
                     "https://cdn.example/d1c" if d1c else None)


PREMIUM = Capabilities(frozenset({"prices.live", "books", "depth"}), 90, 600)
PUBLIC = catchup.PUBLIC_CAPS


def test_hour_source_follows_age_bands():
    now = 1000 * 24
    files = files_with(now - 1, {(now - 200) // 24: "tok"}, d1c=True)
    assert hour_source(now - 167, files, PUBLIC, now) == (H1, None)
    assert hour_source(now - 168, files, PUBLIC, now) == (LOST, None)
    assert hour_source(now - 200, files, PREMIUM, now) == (H1C, "tok")
    assert hour_source(now - 200, files, PREMIUM, now, frozenset({"h1c"})) == (D1C, None)
    assert hour_source(now - 400, files, PREMIUM, now) == (D1C, None)          # older than 14 days
    assert hour_source(now - 400, files_with(now - 1), PREMIUM, now) == (LOST, None)


def test_plan_never_goes_past_sealed_or_retention():
    now = 1000 * 24
    plan = plan_hours(now - 5, files_with(now - 3), PUBLIC, now)
    assert plan.fetch == [(now - 4, None), (now - 3, None)]
    plan = plan_hours(now - 200 * 24, files_with(now - 1), PUBLIC, now, retention_days=90)
    assert plan.lost == [(now - 90 * 24 + 1, now - 168)]
    plan = plan_hours(now - 400, files_with(now - 1, d1c=True), PREMIUM, now)
    assert plan.day_only == [(now - 399, now - 168)] and plan.lost == []


def test_formatting_uses_utc_day_and_hour():
    assert format_day(1_792_798_058_214) == "23 Oct"
    assert format_hour(497_820) == "16 Oct 12:00"


def test_premium_run_stores_books_and_volume_once(tmp_path, caplog):
    clock = FakeClock()
    world = AhfWorld(clock, premium=True)
    db = open_db(tmp_path, clock)
    world.transport.serve(f"volume/v1/{MARKET}/cap-volume/{world.sealed_d1}.json.gz", {"items": [[774, []]]})

    with caplog.at_level(logging.WARNING, logger="ahsync"):
        run_catchup(api_for(world, KEY), db, clock, (MARKET,))

    assert db.count("scans", "src = 'ahf'") == 1 and db.count("book_entries") > 0
    assert "format is unknown" in caplog.text
    assert db.get_sync_state(MARKET).last_volume == world.sealed_d1
    assert f"api/v1/ah/{MARKET}" not in world.transport.paths()            # live table not used yet
    assert db.get_sync_state(MARKET).live_since is None
    assert status_of(db, "premium")[0] == "premium_until"

    world.transport.requests.clear()
    sync_markets(SyncContext(api_for(world, KEY), db, clock, PREMIUM), (MARKET,))   # same hour again
    paths = world.transport.paths()
    assert not [p for p in paths if "books/" in p or "volume/" in p or "d1c/" in p]   # already stored today


def test_premium_features_refused_with_403_are_skipped(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock, premium=True)
    db = open_db(tmp_path, clock)
    last = world.now_hour - 10 * 24
    db.update_sync_state(MARKET, last_hour=last)
    first_h1c = last + 1
    world.transport.fail(h1c_path(MARKET, first_h1c), 403, body=b'{"feature": "candles.hourly"}')
    world.transport.fail(f"candles/v1/d1c/{MARKET}/cap-d1/{world.sealed_d1}.json.gz", 403, body=b"{}")
    book_path = [p for p in world.transport.routes if p.startswith("books/")][0]
    world.transport.fail(book_path, 403, body=b'{"feature": "books"}')

    result = run_catchup(api_for(world, KEY), db, clock, (MARKET,))

    report = result.reports[0]
    assert {"h1c", "d1c", "books"} <= set(report.disabled)
    assert status_of(db, f"feature:{MARKET}:books") == ("feature_refused", {
        "market": MARKET, "endpoint": "books", "feature": "books"})
    assert db.count("candles_d1") == 7 * ITEMS_PER_HOUR                     # fell back to public d1
    assert db.get_sync_state(MARKET).last_hour == world.sealed_h1
    assert result.aborted is None


def test_unreachable_me_or_items_ends_the_run_without_writes(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    world.transport.fail("api/v1/me", 503)
    assert run_catchup(api_for(world), db, clock, (MARKET,)).aborted
    world.transport.clear_faults()
    world.transport.fail("api/v1/items", 503)
    assert run_catchup(api_for(world), db, clock, (MARKET,)).aborted
    assert db.count("candles_h1") == 0


def test_bad_me_falls_back_to_public_features(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock, premium=True)
    db = open_db(tmp_path, clock)
    world.transport.serve_bytes("api/v1/me", b"not json")
    result = run_catchup(api_for(world, KEY), db, clock, (MARKET,))
    assert result.caps == PUBLIC and h1c_hours(world) == []


def test_items_are_refreshed_only_when_older_than_a_day(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    run_catchup(api_for(world), db, clock, (MARKET,))
    assert db.conn.execute("SELECT name FROM items WHERE key = 2589").fetchone() == ("Linen Cloth",)
    world.advance(HOUR_MS)
    world.transport.requests.clear()
    run_catchup(api_for(world), db, clock, (MARKET,))
    assert "api/v1/items" not in world.transport.paths()


def test_files_format_change_skips_the_market(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    world.transport.serve(f"api/v1/ah/{MARKET}/files", {"unexpected": True})
    result = run_catchup(api_for(world), db, clock, (MARKET,))
    assert result.reports[0].disabled == ["files"] and db.count("candles_h1") == 0


def test_failed_hour_that_ages_out_is_dropped(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    db.update_sync_state(MARKET, last_hour=world.sealed_h1)
    old_hour = world.now_hour - 8 * 24
    db.record_fetch_failure(MARKET, old_hour, "bad file", clock.now_ms())
    run_catchup(api_for(world), db, clock, (MARKET,))
    assert db.fetch_failures(MARKET) == [] and h1_hours(world) == []


def test_bad_day_candles_and_snapshot_are_skipped_not_fatal(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    world.transport.truncate(f"candles/v1/d1/{MARKET}/{world.sealed_d1}.json.gz", 0.5)
    world.transport.truncate(f"snap/v1/{MARKET}/{world.now_hour}.json.gz", 0.5)
    result = run_catchup(api_for(world), db, clock, (MARKET,))
    assert result.aborted is None
    assert db.count("candles_d1") == 0 and db.count("current") == 0
    assert db.get_sync_state(MARKET).last_d1 is None                   # retried next run


def test_FM25_expired_key_401_on_files_continues_on_the_free_path(tmp_path, caplog):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    world.transport.fail_with_key(f"api/v1/ah/{MARKET}/files", 401)

    with caplog.at_level(logging.DEBUG, logger="ahsync"):
        result = run_catchup(api_for(world, KEY), db, clock, (MARKET,))

    assert result.aborted is None and result.reports[0].aborted is None
    assert db.get_sync_state(MARKET).last_hour == world.sealed_h1
    assert status_of(db, "api_key") == ("key_rejected", {})
    files_requests = [r for r in world.transport.requests if r.url.endswith("/files")]
    assert ["Authorization" in r.headers for r in files_requests] == [True, False]
    later = world.transport.requests[world.transport.requests.index(files_requests[-1]):]
    assert all("Authorization" not in r.headers for r in later)
    assert KEY not in caplog.text and "Traceback" not in caplog.text

    world.transport.clear_faults()                    # key fixed: next run uses it and clears the line
    run_catchup(api_for(world, KEY), db, clock, (MARKET,))
    assert db.status("api_key") is None


def test_FM26_h1c_403_from_the_first_hour_disables_h1c_and_reports_the_rest(tmp_path):
    for with_d1c in (True, False):
        clock = FakeClock()
        world = AhfWorld(clock, premium=True)
        db = open_db(tmp_path / str(with_d1c), clock)
        last = world.now_hour - 10 * 24
        db.update_sync_state(MARKET, last_hour=last)
        if not with_d1c:                                   # no d1c listed: those hours are lost instead
            index = json.loads(world.transport.routes[f"api/v1/ah/{MARKET}/files"])
            del index["caps"]["d1"]
            world.transport.serve(f"api/v1/ah/{MARKET}/files", index)
        world.transport.fail(h1c_path(MARKET, last + 1), 403, body=b'{"feature": "candles.hourly"}')

        result = run_catchup(api_for(world, KEY), db, clock, (MARKET,))

        oldest_public = world.now_hour - 7 * 24 + 1
        report = result.reports[0]
        assert h1c_hours(world) == [last + 1]                       # no further h1c requests
        gap = [(last + 1, oldest_public - 1)]
        assert (report.day_only, report.lost) == ((gap, []) if with_d1c else ([], gap))
        assert h1_hours(world) == world.public_hours()              # public hours still sync
        assert status_of(db, f"feature:{MARKET}:h1c")[1]["feature"] == "candles.hourly"
        assert db.get_sync_state(MARKET).last_hour == world.sealed_h1
        db.close()


def test_FM27_hour_file_with_renamed_field_stops_the_loop_without_losing_hours(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    first = start_with_gap(db, world, 112)
    for hour in range(first, world.sealed_h1 + 1):
        changed = hour_file(hour)
        changed["candles"] = changed.pop("items")
        world.transport.serve(h1_path(MARKET, hour), changed)

    result = run_catchup(api_for(world), db, clock, (MARKET,))

    assert status_of(db, "format:h1") == ("format_changed", {"endpoint": "h1"})
    assert h1_hours(world) == [first]                              # the hour loop stopped
    assert db.get_sync_state(MARKET).last_hour == first - 1         # unchanged
    assert db.fetch_failures(MARKET) == [] and not [s for s in db.statuses() if s.kind == "bad_file"]
    assert result.reports[0].disabled == ["h1"]
    assert db.count("current") == ITEMS_PER_HOUR                     # the rest of the run went on

    world.publish()                                                  # parser fixed (files readable again)
    world.transport.requests.clear()
    run_catchup(api_for(world), db, clock, (MARKET,))
    assert h1_hours(world) == list(range(first, world.sealed_h1 + 1))
    assert db.status("format:h1") is None


def test_failed_hour_that_ages_out_joins_the_lost_report(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    db.update_sync_state(MARKET, last_hour=world.sealed_h1)
    old_hour = world.now_hour - 8 * 24
    for _ in range(3):
        db.record_fetch_failure(MARKET, old_hour, "bad file", clock.now_ms())
    db.set_status(f"bad_file:{MARKET}:{old_hour}", "warn", "bad_file", {"hour": old_hour}, clock.now_ms())

    result = run_catchup(api_for(world), db, clock, (MARKET,))

    assert result.reports[0].lost == [(old_hour, old_hour)]
    assert status_of(db, f"lost:{MARKET}")[1]["ranges_ms"] == [[old_hour * HOUR_MS, (old_hour + 1) * HOUR_MS]]
    assert db.status(f"bad_file:{MARKET}:{old_hour}") is None and db.fetch_failures(MARKET) == []


def test_lost_ranges_accumulate_across_runs(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    db.set_status(f"lost:{MARKET}", "warn", "lost", {"market": MARKET, "hours": 1,
                                                     "ranges_ms": [[0, HOUR_MS]]}, 0)
    last = world.now_hour - 8 * 24
    db.update_sync_state(MARKET, last_hour=last)
    run_catchup(api_for(world), db, clock, (MARKET,))
    data = db.status(f"lost:{MARKET}").data
    assert data["ranges_ms"][0] == [0, HOUR_MS] and data["hours"] == 1 + 24


@pytest.mark.parametrize("status", [401, 410, 400])
def test_other_http_errors_on_an_hour_file_count_as_bad_files(tmp_path, status):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    first = start_with_gap(db, world, 5)
    world.transport.fail(h1_path(MARKET, first + 1), status)
    result = run_catchup(api_for(world), db, clock, (MARKET,))
    assert result.aborted is None and result.reports[0].bad_hours == [first + 1]
    assert db.get_sync_state(MARKET).last_hour == world.sealed_h1


def test_files_error_skips_that_market_only(tmp_path):
    clock = FakeClock()
    other = "forever.pvp.alliance.us"
    world = AhfWorld(clock)
    AhfWorld(clock, market=other, transport=world.transport)
    db = open_db(tmp_path, clock)
    world.transport.fail(f"api/v1/ah/{MARKET}/files", 400)

    result = run_catchup(api_for(world), db, clock, (MARKET, other))

    assert result.aborted is None
    assert status_of(db, f"market:{MARKET}") == ("market_skipped", {"market": MARKET, "error": "ApiError"})
    assert db.get_sync_state(other).last_hour == world.sealed_h1
    world.transport.clear_faults()
    run_catchup(api_for(world), db, clock, (MARKET, other))
    assert db.status(f"market:{MARKET}") is None


def test_missing_d1_url_without_premium_is_skipped(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    index = files_index(MARKET, world.sealed_h1, world.sealed_d1)
    del index["d1"]
    world.transport.serve(f"api/v1/ah/{MARKET}/files", index)
    result = run_catchup(api_for(world), db, clock, (MARKET,))
    assert result.aborted is None and db.count("candles_d1") == 0
    assert db.get_sync_state(MARKET).last_hour == world.sealed_h1


def test_empty_snapshot_keeps_the_previous_prices(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    run_catchup(api_for(world), db, clock, (MARKET,))
    world.advance(HOUR_MS)
    empty = snapshot(world.now_hour * HOUR_MS)
    empty["rows"] = []
    world.transport.serve(f"snap/v1/{MARKET}/{world.now_hour}.json.gz", empty)
    run_catchup(api_for(world), db, clock, (MARKET,))
    assert db.count("current") == ITEMS_PER_HOUR


def test_rate_limited_run_says_so(tmp_path):
    clock = FakeClock()
    world = AhfWorld(clock)
    db = open_db(tmp_path, clock)
    world.transport.fail(f"api/v1/ah/{MARKET}/files", 429, times=1, headers={"retry-after": "3600"})
    result = run_catchup(api_for(world), db, clock, (MARKET,))
    assert result.aborted and status_of(db, "network") == ("unreachable", {"reason": "rate_limited"})


def test_database_is_plain_sqlite(tmp_path):
    clock = FakeClock()
    db = open_db(tmp_path, clock)
    db.close()
    assert sqlite3.connect(tmp_path / "ahsync.db").execute("PRAGMA journal_mode").fetchone() == ("wal",)
