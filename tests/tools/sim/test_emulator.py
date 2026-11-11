"""Tests for tools/sim/emulator.py: Blizzard-shaped snapshots, read back through the collector's parse and the engine's
lifetimes (SPEC §2.4, §8.10; SU1-SU8, SU13, SU14). Each sequence is hand-built, so every number checks by hand."""
from __future__ import annotations

import gzip
import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

from ahcloud.store import LocalStore  # noqa: E402
from ahsync.collect.blizzard import open_envelope  # noqa: E402
from ahsync.collect.provider import SnapshotObservation, snapshot_problems  # noqa: E402
from ahsync.engine.lifetimes import life_flows, lifetimes, sold_by_hour  # noqa: E402
from sim.emulator import (capture, envelope, id_book, records, snapshot_times, store_snapshot,  # noqa: E402
                          time_left, with_conflict, with_duplicate)
from sim.market import HOUR_MS, MINUTE_MS, Live  # noqa: E402

M = "forever.normal.horde.us"
T = 1_793_923_200_000 + 10 * HOUR_MS            # Fri 2026-11-06 10:00 UTC
DAY_MS = 24 * HOUR_MS
LINEN, SILK, GLOVES = 2589, 4306, 6585


def lv(auction: int, key: int, units: int, price: int, posted: int, hours: int, commodity: bool = True) -> Live:
    """One live auction, as the market model holds it."""
    return Live(auction, key, units, price, posted, posted + hours * HOUR_MS, commodity, "s")


def snap(at_ms: int, *auctions: Live):
    """A snapshot of these auctions, written as a raw envelope and read back as the engine's IdBook."""
    return id_book(capture(auctions, at_ms), M)


def ending(lt, auction: int):
    """The lifetime ending of one auction."""
    return next(e for e in lt.endings if e.auction == auction)


def test_records_are_blizzards_shapes_and_the_envelope_reads_back(tmp_path):
    gloves, linen = lv(7, GLOVES, 1, 900, T, 24, commodity=False), lv(8, LINEN, 20, 55, T, 24)
    regular, commodities = records([linen, gloves], T + HOUR_MS)
    assert regular == [{"id": 7, "item": {"id": GLOVES}, "quantity": 1, "buyout": 900, "time_left": "VERY_LONG"}]
    assert commodities == [{"id": 8, "item": {"id": LINEN}, "quantity": 20, "unit_price": 55, "time_left": "VERY_LONG"}]
    data = envelope(T + HOUR_MS, regular, commodities)
    last_modified, body, _ = open_envelope(data)
    assert last_modified == "Fri, 06 Nov 2026 11:00:00 GMT" and json.loads(body)["auctions"][0]["buyout"] == 900
    assert json.loads(gzip.decompress(data))["house"] == 113
    book = id_book(data, M)
    assert [(a.id, a.price, a.band, a.commodity) for a in book.auctions] == [(7, 900, 4, False), (8, 55, 4, True)]
    assert store_snapshot(LocalStore(tmp_path), M, T + HOUR_MS, data) == f"raw/{M}/2026/11/06/110000.json.gz"
    with pytest.raises(ValueError, match="whole second"):
        envelope(T + 1, [], [])


def test_snapshot_times_land_between_16_and_30_past_the_hour_and_can_skip():
    times = snapshot_times(T, T + 5 * HOUR_MS, random.Random(3))
    assert len(times) == 5 and all(16 <= (t % HOUR_MS) // MINUTE_MS <= 30 and t % 1000 == 0 for t in times)
    assert snapshot_times(T, T + 5 * HOUR_MS, random.Random(3), skip=1.0) == []


def test_SU1_a_commodity_partly_bought_is_an_exact_sale_and_stays_alive():
    lt = lifetimes([snap(T, lv(1, LINEN, 14, 50, T - HOUR_MS, 24)), snap(T + HOUR_MS, lv(1, LINEN, 10, 50, T - HOUR_MS, 24))])
    assert [(f[0], f[2]) for f in lt.fills] == [(1, 4)] and lt.endings == ()


def test_SU2_cancelling_the_cheapest_commodity_counts_as_a_sale():
    a, b = lv(1, LINEN, 20, 45, T - HOUR_MS, 24), lv(2, LINEN, 20, 50, T - HOUR_MS, 24)
    e = ending(lifetimes([snap(T, a, b), snap(T + HOUR_MS, b)]), 1)     # truth: cancelled, but nothing cheaper stayed
    assert (e.cancelled, e.expired) == (False, 0.0)                       # the known bias: counted sold


def test_SU3_a_commodity_gone_while_a_strictly_cheaper_one_stays_untouched_is_a_cancel():
    cheap, dear = lv(1, LINEN, 20, 45, T - HOUR_MS, 24), lv(2, LINEN, 10, 50, T - HOUR_MS, 24)
    e = ending(lifetimes([snap(T, cheap, dear), snap(T + HOUR_MS, cheap)]), 2)
    assert e.cancelled is True                                            # a buyer would have taken the 45c first (BZ40)


def test_SU4_a_buy_minutes_before_expiry_counts_as_expired():
    # A 2 h auction posted 10:30: MEDIUM at 11:00 (1.5 h left), SHORT at 12:00 (0.5 h left), bought 12:25, gone 13:00.
    posted = T + 30 * MINUTE_MS
    a = lv(1, LINEN, 20, 50, posted, 2)
    lt = lifetimes([snap(T), snap(T + HOUR_MS, a), snap(T + 2 * HOUR_MS, a), snap(T + 3 * HOUR_MS)])
    assert [a.band for a in snap(T + 2 * HOUR_MS, a).auctions] == [1]
    assert ending(lt, 1).expired == 1.0                                   # its whole expiry window lies in the gap


def test_SU5_blizzards_bands_and_an_auction_first_seen_short():
    assert [time_left(h * HOUR_MS) for h in (0.25, 0.5, 1.5, 2, 11.9, 12, 12.1)] == [
        "SHORT", "SHORT", "MEDIUM", "MEDIUM", "LONG", "LONG", "VERY_LONG"]
    # A 2 h auction posted 08:15 is first seen at 10:00 with 15 min left: SHORT, treated as a 2 h auction.
    a = lv(1, LINEN, 20, 50, T - 105 * MINUTE_MS, 2)
    lt = lifetimes([snap(T - HOUR_MS), snap(T, a), snap(T + HOUR_MS)])
    assert [x.band for x in snap(T, a).auctions] == [1] and ending(lt, 1).expired == 1.0


def test_SU6_a_long_gap_links_nothing_and_a_3_hour_gap_spreads_its_sales():
    s0 = snap(T, lv(1, LINEN, 20, 50, T - HOUR_MS, 24))
    s1 = snap(T + 3 * HOUR_MS, lv(1, LINEN, 14, 50, T - HOUR_MS, 24))
    s2 = snap(T + 10 * HOUR_MS, lv(1, LINEN, 14, 50, T - HOUR_MS, 24))
    lt = lifetimes([s0, s1, s2])
    assert lt.gaps == ((T, T + 3 * HOUR_MS),)                             # 7 h apart: the second pair never links
    hours = sold_by_hour(life_flows(lt))[LINEN]
    assert sum(hours.values()) == 6.0 and sorted(set(hours.values())) == [2.0]   # 6 units over 3 hours (BZ30)


def test_SU7_a_repeated_record_counts_once_and_two_different_records_with_one_id_are_refused():
    regular, commodities = records([lv(1, LINEN, 20, 50, T - HOUR_MS, 24)], T)
    once = id_book(envelope(T, regular, with_duplicate(commodities, 1)), M)
    assert len(once.auctions) == 1                                        # BZ38
    twice = id_book(envelope(T, regular, with_conflict(commodities, 1)), M)
    assert len(twice.auctions) == 2 and snapshot_problems(SnapshotObservation(M, "blizzard", T, twice.auctions))


def test_SU8_a_sale_after_midnight_is_credited_to_the_later_day():
    midnight = (T // DAY_MS + 1) * DAY_MS
    a = lv(1, LINEN, 20, 50, midnight - 30 * MINUTE_MS, 24)
    lt = lifetimes([snap(midnight - HOUR_MS), snap(midnight - MINUTE_MS, a), snap(midnight + 50 * MINUTE_MS)])
    sold = [(gap, f[LINEN].sold - f[LINEN].unseen) for gap, f in life_flows(lt).items() if f[LINEN].sold]   # 00:40
    assert sold == [((midnight - MINUTE_MS, midnight + 50 * MINUTE_MS), 20.0)]   # observed; BZ45's estimate is there too
    assert sold[0][0][1] // DAY_MS == midnight // DAY_MS                  # a gap counts for its later snapshot's day
    # (its hourly rate is spread over both clock hours the gap touches, sold_by_hour: a rate, not a day's units)


def test_SU13_a_partial_fill_then_the_rest_gone_counts_every_unit_once():
    posted = T - 10 * HOUR_MS
    lt = lifetimes([snap(T - 11 * HOUR_MS), snap(T, lv(1, LINEN, 200, 50, posted, 24)),
                    snap(T + HOUR_MS, lv(1, LINEN, 150, 50, posted, 24)), snap(T + 2 * HOUR_MS)])
    e = ending(lt, 1)
    assert [f[2] for f in lt.fills] == [50] and (e.qty, e.expired, e.cancelled) == (150, 0.0, False)
    flows = life_flows(lt)
    assert sum(f[LINEN].sold for f in flows.values()) == 200.0 and sum(f[LINEN].ended for f in flows.values()) == 1


@pytest.mark.parametrize("case", ["new", "bought from", "equal price"])
def test_SU14_without_a_strictly_cheaper_untouched_auction_a_gone_commodity_is_a_sale(case):
    gone = lv(1, LINEN, 10, 50, T - HOUR_MS, 24)
    cheaper = lv(2, LINEN, 20, 50 if case == "equal price" else 45, T - HOUR_MS, 24)
    before = [gone] if case == "new" else [gone, cheaper]
    after = lv(2, LINEN, 12 if case == "bought from" else 20, cheaper.price, T - HOUR_MS, 24)
    e = ending(lifetimes([snap(T, *before), snap(T + HOUR_MS, after)]), 1)
    assert e.cancelled is False
