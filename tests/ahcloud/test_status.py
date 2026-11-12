"""Tests for ahcloud/status.py: the status document's SLAs, headline, grid and cards (SPEC §2.4 The status page)."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ahcloud import status
from ahcloud.status import Alarm, PointerFacts, StatusInputs, build_status, to_json

N = 1_794_321_000_000                       # Tue 10 Nov 2026 14:30 UTC (the mockup's sample)
MIN, HOUR, DAY = 60_000, 3_600_000, 86_400_000
TUE = N // DAY * DAY                        # Tue 10 Nov 00:00
M = "forever.normal.horde.us"


def at(day: int, hh: int, mm: int = 0, ss: int = 0) -> int:
    """A time `day` days after Tue 10 Nov (negative before), UTC ms."""
    return TUE + day * DAY + hh * HOUR + mm * MIN + ss * 1000


def hourly(first: int, last: int, minute: int = 18) -> tuple[int, ...]:
    """One raw key at :minute of every hour from first's hour to last's hour."""
    return tuple(h * HOUR + minute * MIN for h in range(first // HOUR, last // HOUR + 1))


def pointers(until: int = at(0, 14, 14), built: int = at(0, 8, 18)) -> dict[int, PointerFacts]:
    """latest-13 … latest-16, the newest 6,190,000 bytes (CS10)."""
    sizes = {13: 5_100_000, 14: 5_400_000, 15: 5_800_000, 16: 6_190_000}
    return {s: PointerFacts(built, until, b) for s, b in sizes.items()}


def world(**changes) -> StatusInputs:
    """CS10's normal day at 14:30: hourly raw to 14:18, prices to 14:14, the daily published 09:06."""
    base = StatusInputs(
        market=M, now_ms=N, raw_ms=hourly(at(-3, 0), at(0, 14)), pointers=pointers(),
        coverage=((at(-3, 0) // HOUR, at(0, 8) // HOUR),), items=1862,
        collector={"last_modified": "Tue, 10 Nov 2026 14:18:00 GMT", "last_checked_ms": at(0, 14, 30),
                   "last_outcome": "unchanged", "last_duration_ms": 3000, "last_auctions": 3412},
        analyzer={"hourly": {"finished_ms": at(0, 14, 19, 41), "duration_ms": 41_000, "outcome": "published",
                             "observations_until_ms": at(0, 14, 18), "published_ms": at(0, 14, 19, 41)},
                  "daily": {"finished_ms": at(0, 9, 6), "duration_ms": 360_000, "outcome": "published",
                            "items": 1862, "built_ms": at(0, 8, 18), "published_ms": at(0, 9, 6)}},
        ingest={"finished_ms": at(0, 14, 11, 2), "duration_ms": 62_000, "outcome": "ingested", "ingested": 1,
                "pending": 0, "refused": 0, "last_ingested_at_ms": at(0, 14, 18)},
        alarms=tuple(Alarm(name, "OK", at(-1, 9)) for name in status.ALARM_IDS))
    return replace(base, **changes)


def tile(inputs: StatusInputs, sla_id: str) -> tuple[str, str, str]:
    """One SLA's (colour, value, sub)."""
    found = next(s for s in build_status(inputs)["slas"] if s["id"] == sla_id)
    return found["colour"], found["value"], found["sub"]


def test_CS2_every_hour_collected():
    full = hourly(at(-1, 14), at(0, 13)) + (at(0, 14, 18),)
    assert tile(world(raw_ms=full), "collection") == ("green", "24 of 24 hours", "last 24 h · newest snapshot 12 min old")
    gaps = tuple(t for t in full if not at(0, 3) <= t < at(0, 5))
    assert tile(world(raw_ms=gaps), "collection")[:2] == ("amber", "22 of 24 hours")
    to_13 = hourly(at(-1, 14), at(0, 13), minute=0)
    assert tile(world(raw_ms=to_13), "collection") == ("amber", "24 of 24 hours",
                                                       "last 24 h · newest snapshot 1 h 30 min old")
    to_11 = hourly(at(-1, 14), at(0, 11))
    assert tile(world(raw_ms=to_11), "collection") == ("red", "22 of 24 hours", "last 24 h · newest snapshot 3 h 12 min old")
    assert tile(world(raw_ms=()), "collection")[:2] == ("grey", "not yet measured")


def test_CS3_hourly_prices_published():
    assert tile(world(), "prices") == ("green", "4 min behind", "prices up to 14:14 · newest snapshot 14:18")
    assert tile(world(pointers=pointers(until=at(0, 14, 18))), "prices")[:2] == ("green", "up to date")
    no_base = world(pointers=pointers(until=at(0, 13, 50)),
                    analyzer={**world().analyzer, "hourly": {**world().analyzer["hourly"], "outcome": "no_base"}})
    assert tile(no_base, "prices")[:2] == ("amber", "28 min behind")
    assert tile(world(pointers=pointers(until=at(0, 11, 10))), "prices")[:2] == ("red", "3 h 8 min behind")


def daily_at(now: int, published: int | None) -> StatusInputs:
    """The world at `now` with the newest daily publish at `published`."""
    daily = {**world().analyzer["daily"], "published_ms": published}
    return world(now_ms=now, raw_ms=hourly(at(-3, 0), now - 12 * MIN),
                 analyzer={**world().analyzer, "daily": daily})


def test_CS4_daily_analysis():
    assert tile(daily_at(N, at(0, 9, 6)), "daily") == ("green", "Published 09:06", "today · due by 09:30 · next 09:00 Wed")
    assert tile(daily_at(N, at(0, 10, 12)), "daily")[:2] == ("amber", "Published 10:12, late")
    monday = at(-1, 9, 4)
    assert tile(daily_at(at(0, 9, 20), monday), "daily")[:2] == ("green", "Due by 09:30")
    assert tile(daily_at(at(0, 10, 0), monday), "daily")[:2] == ("amber", "Not published yet, due 09:30")
    assert tile(daily_at(at(0, 12, 30), monday), "daily") == ("red", "Not published today",
                                                              "last published Mon 09:04, 27 h 26 min ago")
    late = tile(daily_at(at(1, 9, 10), monday), "daily")                # Wed 09:10, Tuesday's missing
    assert late[:2] == ("red", "None for 48 h 6 min")


def test_CS5_no_failed_runs():
    assert tile(world(), "failures") == ("green", "0 errors", "last 24 h · dead-letter queue empty")
    assert tile(world(refused=1), "failures") == ("amber", "1 snapshot refused", "by Ingest in the last 24 h")
    assert tile(world(errors={"analyzer": 2}), "failures")[:2] == ("amber", "2 analyzer errors in 24 h, none now")
    alarms = tuple(Alarm(a.name, "ALARM", at(0, 11, 15)) if a.name == "CollectorErrors" else a for a in world().alarms)
    assert tile(world(alarms=alarms, errors={"collector": 13}), "failures") == (
        "red", "13 collector errors", "alarm CollectorErrors since 11:15")


def durations(collector: int, hourly_ms: int, daily: int, ingest: int = 62_000) -> StatusInputs:
    """The world with the newest runs' durations."""
    analyzer = {"hourly": {**world().analyzer["hourly"], "duration_ms": hourly_ms},
                "daily": {**world().analyzer["daily"], "duration_ms": daily}}
    return world(collector={**world().collector, "last_duration_ms": collector}, analyzer=analyzer,
                 ingest={**world().ingest, "duration_ms": ingest})


def test_CS6_run_time_vs_limit():
    assert tile(durations(3000, 41_000, 594_000), "runtime") == (
        "amber", "Daily 9 min 54 s of 15 min", "66% · hourly 41 s · Ingest 1 min 2 s · collector 3 s")
    assert tile(durations(3000, 41_000, 540_000), "runtime")[0] == "green"                   # 60%
    assert tile(durations(3000, 41_000, 815_000), "runtime")[:2] == ("amber", "Daily 13 min 35 s of 15 min")   # 90.5% → 90
    assert tile(durations(110_000, 41_000, 540_000), "runtime")[:2] == ("red", "Collector 1 min 50 s of 2 min")  # 91%


def first_cases() -> StatusInputs:
    """CS2-CS6's first cases together: only the run time amber."""
    return replace(durations(3000, 41_000, 594_000), raw_ms=hourly(at(-3, 0), at(0, 14)))


def test_CS7_one_verdict_the_worst_first():
    head = build_status(first_cases())["headline"]
    assert head == {"colour": "amber", "title": "Running, 1 warning",
                    "sentence": "The daily run used 66% of its time limit."}
    head = build_status(replace(first_cases(), pointers=pointers(until=at(0, 13, 50))))["headline"]
    assert (head["title"], head["sentence"]) == ("Running, 2 warnings",
                                                 "Prices are 28 min behind the newest snapshot.")
    alarms = tuple(Alarm(a.name, "ALARM", at(0, 11, 15)) if a.name == "CollectorErrors" else a for a in world().alarms)
    red = replace(first_cases(), raw_ms=hourly(at(-3, 0), at(0, 11)), alarms=alarms, errors={"collector": 13})
    assert build_status(red)["headline"] == {"colour": "red", "title": "Collection stopped",
                                             "sentence": "No new snapshot for 3 h 12 min."}
    empty = build_status(world(raw_ms=()))
    assert empty["headline"]["title"] == "Waiting for the first snapshot"
    assert {s["colour"] for s in empty["slas"]} == {"grey"}


def cs8_world() -> StatusInputs:
    """CS8: raw from Fri 16 Oct 17:05 to Tue 10 Nov 14:18 with three gaps; Thu 22 Oct 11:xx refused."""
    gaps = [(at(-17, 6), at(-17, 14)), (at(-15, 20), at(-15, 21)), (at(-4, 3), at(-4, 5))]
    raw = tuple(t for t in hourly(at(-25, 17), at(0, 14), minute=5) if not any(a <= t < b for a, b in gaps))
    refused = at(-19, 11) // HOUR
    runs, start = [], raw[0] // HOUR
    analysed = sorted({t // HOUR for t in raw if t < at(0, 9)} - {refused})
    for a, b in zip(analysed, analysed[1:] + [None]):
        if b != a + 1:
            runs.append((start, a))
            start = b
    return world(raw_ms=raw, coverage=tuple(runs), pointers=pointers(built=at(0, 8, 18)))


def test_CS8_the_week_pickers_data():
    out = build_status(cs8_world())
    blocks = out["weeks"]
    assert len(blocks) == 4 and out["daily_hour_utc"] == 9
    b0 = blocks[0]
    assert b0["label"] == "Last 7 days: 4–10 Nov · 2 h missing"
    assert b0["days"] == ["Wed 4", "Thu 5", "Fri 6", "Sat 7", "Sun 8", "Mon 9", "Tue 10"]
    assert b0["cells"][-24:] == "a" * 9 + "l" * 5 + "p" + "f" * 9
    assert b0["missing"] == "2 hours missing: Fri 6 Nov 03:00–04:00"
    assert (blocks[1]["label"], blocks[1]["missing"]) == ("28 Oct – 3 Nov · complete", "Complete: 168 of 168 hours analysed")
    assert blocks[2]["label"] == "21–27 Oct · 9 h missing"
    assert blocks[2]["missing"] == ("9 hours missing: Sat 24 Oct 06:00–13:00, Mon 26 Oct 20:00 · "
                                    "1 hour collected but not analysed: Thu 22 Oct 11:00")
    b3 = blocks[3]
    assert b3["label"] == "14–20 Oct · collection began Fri 16"
    assert b3["cells"][:48 + 17] == "f" * (48 + 17) and b3["cells"][65] == "a"
    assert b3["missing"] == "Collection began Fri 16 Oct 17:00 · complete since"
    assert all(len(b["cells"]) == 168 for b in blocks)


def test_CS8_a_complete_last_week_counts_its_hours_up_to_now():
    raw = hourly(at(-6, 0), at(0, 14))
    out = build_status(world(raw_ms=raw, coverage=((raw[0] // HOUR, at(0, 8) // HOUR),)))
    assert out["weeks"][0]["missing"] == "Complete: 153 of 158 hours analysed · 5 waiting for the daily run"
    assert build_status(world(raw_ms=()))["weeks"] == [
        {"label": "Last 7 days: 4–10 Nov · no snapshot yet", "days": out["weeks"][0]["days"],
         "cells": "f" * 158 + "p" + "f" * 9, "missing": "No snapshot yet."}]


def test_CS9_blizzards_cadence():
    steps = [60] * 11 + [118] + [60] * 11
    keys = [at(0, 14, 18) - sum(steps) * MIN]
    for step in steps:
        keys.append(keys[-1] + step * MIN)
    rows = build_status(world(raw_ms=tuple(keys), mismatch=1))["blizzard"]
    assert rows[0]["value"] == "typically 60 min · longest 1 h 58 min"
    assert (rows[1]["name"], rows[1]["value"]) == ("Mismatched answers", "1")
    even = [at(0, 14, 18) - (12 * 58 + 12 * 61) * MIN]
    for step in [58] * 12 + [61] * 12:
        even.append(even[-1] + step * MIN)
    assert build_status(world(raw_ms=tuple(even)))["blizzard"][0]["value"] == "typically 59 min · longest 1 h 1 min"
    assert build_status(world(raw_ms=(at(0, 14, 18),)))["blizzard"][0]["value"] == "not enough snapshots"


def test_CS10_the_cards_text():
    out = build_status(world())
    assert out["document"][0]["value"] == "schemas 13–16 · largest 6.2 MB · 1,862 items"
    rows = {r["name"]: r for r in out["functions"]["rows"]}
    assert (rows["Collector"]["value"], rows["Collector"]["sub"]) == (
        "14:30 · no new snapshot",
        "Blizzard hasn't refreshed since 14:18 · last new snapshot 14:18 (3,412 auctions) · runs every 15 min")
    assert (rows["Analyzer, hourly"]["value"], rows["Analyzer, hourly"]["sub"]) == (
        "14:19 · prices published", "triggered by the 14:18 snapshot")
    assert (rows["Analyzer, daily"]["value"], rows["Analyzer, daily"]["sub"]) == (
        "09:00 · baselines published", "1,862 items · history up to 08:18")
    assert (rows["Ingest"]["value"], rows["Ingest"]["sub"]) == (
        "14:10 · 1 snapshot ingested", "history up to 14:18 · 0 pending · runs at :10 and :40")
    assert rows["Failed events"]["value"] == "dead-letter queue empty"
    assert out["functions"]["alarms"] == "alarms: all OK"
    fresh = {r["name"]: r["value"] for r in out["freshness"]["rows"]}
    assert fresh == {"Newest snapshot": "14:18 · 12 min", "History": "14:18 · 12 min",
                     "Hourly prices": "up to 14:14 · 16 min", "Daily baselines": "09:06 · 5 h 24 min"}
    assert out["freshness"]["as_of"] == "ages at 14:30"
    assert (out["market_label"], out["written"]) == ("forever · normal · horde · US", "14:30 UTC")


def test_CS10_the_functions_before_any_run_and_with_an_alarm_on():
    out = build_status(world(collector={}, analyzer={}, ingest={}, alarms=(Alarm("IngestErrors", "ALARM", at(0, 13)),),
                             errors={"ingest": 2}))
    rows = {r["name"]: r for r in out["functions"]["rows"]}
    assert rows["Collector"]["value"] == "no run recorded yet" and rows["Collector"]["colour"] == "grey"
    assert (rows["Ingest"]["colour"], rows["Ingest"]["value"]) == ("red", "no run recorded yet")
    assert out["functions"]["alarms"] == "alarms in ALARM: IngestErrors"
    assert next(s for s in out["slas"] if s["id"] == "daily")["sub"] == "no daily run recorded yet"
    assert next(s for s in out["slas"] if s["id"] == "runtime")["colour"] == "grey"
    before_h2 = build_status(world(collector={"last_modified": "Tue, 10 Nov 2026 14:18:00 GMT"}))   # the old file
    row = before_h2["functions"]["rows"][0]
    assert (row["colour"], row["value"]) == ("grey", "no run recorded yet")


def test_CS11_the_page_only_draws():
    first, second = to_json(build_status(world())), to_json(build_status(world()))
    assert first == second
    doc = json.loads(first)
    assert list(doc) == sorted(doc)
    assert (doc["schema"], doc["written_ms"], doc["age_limits_ms"], doc["daily_hour_utc"]) == (
        1, 1794321000000, [4200000, 10800000], 9)
    for tile_ in doc["slas"]:
        assert tile_["colour"] in {"green", "amber", "red", "grey"}
        assert {"title", "value", "sub", "sentence"} <= set(tile_)
    assert "undefined" not in first and "NaN" not in first and "None" not in first


@pytest.mark.parametrize("name, found", [("WowAuctionMarket-CollectorErrors", "CollectorErrors"),
                                         ("WowAuctionMarket-IngestSlowRun", "IngestSlowRun"),
                                         ("WowAuctionMarket-CollectorErrors0693A698-T47Th1GLS2o6", None),  # the old names
                                         ("OtherStack-CollectorErrors", None),
                                         ("WowAuctionMarket-SomethingElse", None)])
def test_SEC21_alarm_ids_come_from_the_stacks_names(name, found):
    assert status.alarm_id(name, "WowAuctionMarket-") == found
    assert status.alarm_names("WowAuctionMarket-")[:2] == ["WowAuctionMarket-CollectorErrors", "WowAuctionMarket-NoNewSnapshot"]


@pytest.mark.parametrize("ms, text", [(0, "0 min"), (12 * MIN + 59_999, "12 min"), (60 * MIN, "1 h"),
                                      (3 * HOUR + 12 * MIN, "3 h 12 min"), (-5, "0 min")])
def test_CS2_ages_read_in_whole_minutes(ms, text):
    assert status.age_text(ms) == text


@pytest.mark.parametrize("ms, text", [(41_999, "41 s"), (60_000, "1 min"), (594_000, "9 min 54 s")])
def test_CS6_durations_read_in_whole_seconds(ms, text):
    assert status.duration_text(ms) == text


def test_CS10_document_text_edges():
    assert status.schemas_text([16]) == "16" and status.schemas_text([13, 16]) == "13, 16"
    assert status.megabytes_text(6_150_000) == "6.2 MB" and status.megabytes_text(6_149_999) == "6.1 MB"
    assert build_status(world(pointers={}))["document"][0]["value"] == "not published yet"
    assert status.hour_runs([at(0, 22) // HOUR, at(1, 1) // HOUR]) == "Tue 10 Nov 22:00, Wed 11 Nov 01:00"
    assert status.hour_runs(list(range(at(0, 22) // HOUR, at(1, 2) // HOUR))) == "Tue 10 Nov 22:00 – Wed 11 Nov 01:00"


def test_FM81_a_damaged_state_field_costs_that_value_not_the_status():
    damaged = world(analyzer={"daily": None, "hourly": {"finished_ms": "late", "outcome": "published"}},
                    ingest={"ingested": "x", "finished_ms": at(0, 14, 11), "pending": None},
                    collector={"last_checked_ms": True, "last_auctions": "many"})
    rows = {r["name"]: r for r in build_status(damaged)["functions"]["rows"]}
    assert rows["Analyzer, hourly"]["value"] == "no run recorded yet"
    assert rows["Ingest"]["value"] == "14:11 · 0 snapshots ingested"
    assert rows["Collector"]["value"] == "no run recorded yet"
    assert build_status(world(analyzer=None))["slas"][2]["sub"] == "no daily run recorded yet"


def test_CS10_an_empty_ingest_run_reads_nothing_ingested():
    row = build_status(world(ingest={**world().ingest, "outcome": "empty", "ingested": 0}))["functions"]["rows"][1]
    assert row["value"] == "14:10 · nothing ingested"
