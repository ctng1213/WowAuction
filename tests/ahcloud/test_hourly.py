"""Tests for ahcloud/hourly.py: the newest snapshot's state over the latest document (SPEC §2.4, BZ32, BZ33)."""
from __future__ import annotations

from dataclasses import replace

from ahcloud.hourly import number, run_hourly, shift_ring
from ahcloud.publish import PUBLISHED_SCHEMAS, pointer_key, publish, read_latest, settings_to_dict
from ahcloud.store import LocalStore
from ahsync.collect.blizzard import raw_key
from ahsync.engine.sellprob import MarketSettings
from ahsync.intel.contract import INTEL_SCHEMA_VERSION, ItemIntel, MarketIntelligence, to_json

from tests.support.cloud_world import (A_MS, B_MS, C_MS, HOUR, LINEN, M, SNAP_A, SNAP_B, SNAP_C, SPAULDERS)

BUILT = A_MS - 5 * HOUR                         # the daily run's newest snapshot, 08:02
LINEN, SPAULDERS = 2589, 8180


def item(key: int, cur: int, qty: int, book: tuple) -> ItemIntel:
    """An item of the daily document, with baselines a test can recognise."""
    return ItemIntel(key, cur, qty, 0, cur, cur, cur - 20, cur + 20, 0.5, 40.0, 4.0, 42.0, "blizzard", {"blizzard": 7},
                     cur, 1, 0.5, False, float(cur), 61, qty, 0, book, how=",".join(["1000"] * 168),
                     how_weeks=",".join(["2"] * 168), sales_how=",".join(["1000"] * 168),
                     sales_weeks=",".join(["2"] * 168), seasonality=40, curve="#" * 192, curve_hour=BUILT // HOUR,
                     closes_30d=200, median_daily_supply=qty)


DAILY = MarketIntelligence(M, BUILT, A_MS, 4, {
    LINEN: item(LINEN, 125, 14, ((125, 14, 4, 1),)), SPAULDERS: item(SPAULDERS, 1500, 1, ((1500, 1, 1, 1),))},
    catalog={LINEN: ("Linen Cloth", 1, 7, 13, None), SPAULDERS: ("Fortified Spaulders", 2, 4, 412, None)},
    coverage=((BUILT // HOUR, A_MS // HOUR),), settings=settings_to_dict(MarketSettings()))


def world(tmp_path, *snaps):
    """A data store with the given raw snapshots, and an intel store holding the daily document."""
    data, intel = LocalStore(tmp_path / "data"), LocalStore(tmp_path / "intel")
    for stamp_ms, envelope in snaps:
        data.put(raw_key(M, stamp_ms), envelope)
    publish(intel, DAILY)
    return data, intel


BASELINES = ("med7", "med30", "p10_30", "p90_30", "trend7", "sold_per_day", "expired_per_day", "listed_per_day",
             "how", "how_weeks", "sales_how", "sales_weeks", "supply_how", "supply_weeks", "seasonality", "sell_windows",
             "buy_windows", "curve", "curve_hour", "conf", "median_daily_supply", "closes_30d")


def test_BZ32_the_state_layer_is_a_scan_of_the_whole_house(tmp_path):
    data, intel_store = world(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    outcome = run_hourly(data, intel_store, M)
    assert (outcome.state, outcome.observations_until_ms, outcome.repriced) == ("published", B_MS, 1)
    doc, pointer = read_latest(intel_store, M)
    assert (doc.built_ms, doc.observations_until_ms, pointer.built_ms) == (BUILT, B_MS, BUILT)
    linen, spaulders = doc.items[LINEN], doc.items[SPAULDERS]
    assert (linen.cur, linen.cur_qty, linen.book, linen.units_on_ah, linen.cur_age_ms) == (125, 10, ((125, 10, 4, 1),), 10, 0)
    assert linen.last_hour == (4.0, 0.0, 0.0) and spaulders.last_hour == (0.0, 1.0, 0.0)   # a short auction expired
    assert linen.recent_closes.split(",")[-1] == "125" and linen.recent_sold.split(",")[-1] == "4"
    assert linen.recent_units.split(",")[-1] == "10" and len(linen.recent_units.split(",")) == 24
    assert spaulders.recent_units.split(",")[-1] == "0" and spaulders.recent_closes.split(",")[-1] == ""
    assert spaulders.cur == 1500 and spaulders.cur_age_ms == B_MS - A_MS     # no longer listed: aged, not erased
    for key in (LINEN, SPAULDERS):
        assert all(getattr(doc.items[key], f) == getattr(DAILY.items[key], f) for f in BASELINES)
    assert (doc.catalog, doc.coverage, doc.settings) == (DAILY.catalog, DAILY.coverage, DAILY.settings)


def test_BZ33_hourly_runs_move_prices_and_never_the_baselines(tmp_path):
    data, intel_store = world(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    run_hourly(data, intel_store, M)
    first = read_latest(intel_store, M)[0]
    data.put(raw_key(M, C_MS), SNAP_C)                    # two hours later: a cheaper Linen listing
    assert run_hourly(data, intel_store, M).state == "published"
    second = read_latest(intel_store, M)[0]
    assert second.items[LINEN].cur == 118 and second.items[LINEN].cur_qty == 20
    assert second.items[LINEN].recent_closes.split(",")[-3:] == ["125", "", "118"]   # 15:00 had no snapshot
    for f in BASELINES:
        assert getattr(second.items[LINEN], f) == getattr(first.items[LINEN], f) == getattr(DAILY.items[LINEN], f)
    assert run_hourly(data, intel_store, M).state == "up_to_date"            # the same snapshot again: nothing


def test_BZ13_the_hourly_run_takes_its_two_snapshots_from_the_listing(tmp_path):
    data, intel_store = world(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B), (C_MS, SNAP_C))
    assert run_hourly(data, intel_store, M).observations_until_ms == C_MS     # newest, whatever the event said
    doc = read_latest(intel_store, M)[0]
    assert doc.items[LINEN].last_hour == (0.0, 0.0, 20.0)                    # B to C: 20 newly listed


def test_no_document_yet_means_nothing_to_layer_over(tmp_path):
    data = LocalStore(tmp_path / "data")
    data.put(raw_key(M, B_MS), SNAP_B)
    assert run_hourly(data, LocalStore(tmp_path / "intel"), M).state == "no_base"


def test_FM75_after_a_schema_bump_the_hourly_run_layers_over_the_previous_schema(tmp_path):
    data, intel_store = LocalStore(tmp_path / "data"), LocalStore(tmp_path / "intel")
    for stamp_ms, envelope in ((A_MS, SNAP_A), (B_MS, SNAP_B)):
        data.put(raw_key(M, stamp_ms), envelope)
    publish(intel_store, DAILY, (INTEL_SCHEMA_VERSION - 1,))           # the last daily run was on the older schema
    assert intel_store.get(pointer_key(M)) is None
    outcome = run_hourly(data, intel_store, M)
    assert (outcome.state, outcome.observations_until_ms) == ("published", B_MS)
    for schema in PUBLISHED_SCHEMAS:
        assert intel_store.get(pointer_key(M, schema)) is not None
    doc = read_latest(intel_store, M)[0]
    assert (doc.built_ms, doc.items[LINEN].cur_qty) == (BUILT, 10)


def test_the_first_snapshot_has_no_last_hour(tmp_path):
    data, intel_store = world(tmp_path, (B_MS, SNAP_B))
    run_hourly(data, intel_store, M)
    assert read_latest(intel_store, M)[0].items[LINEN].last_hour is None


def test_rings_shift_and_numbers_format_without_round():
    assert shift_ring("", 1, "5") == ",".join([""] * 23 + ["5"])
    ring = ",".join(str(i) for i in range(24))
    assert shift_ring(ring, 3, "x").split(",") == [str(i) for i in range(3, 24)] + ["", "", "x"]
    assert shift_ring(ring, 30, "y").split(",") == [""] * 23 + ["y"]
    assert [number(v) for v in (4.0, 2.5, 1 / 3, 0.0, -0.0004)] == ["4", "2.5", "0.333", "0", "0"]


def test_a_republish_of_the_same_state_writes_the_same_bytes(tmp_path):
    data, intel_store = world(tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    run_hourly(data, intel_store, M)
    doc, pointer = read_latest(intel_store, M)
    again = publish(LocalStore(tmp_path / "other"), doc)[-1]   # the current schema's
    assert again.sha256 == pointer.sha256 and to_json(replace(doc)) == to_json(doc)
