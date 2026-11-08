"""Tests for tools/sim/market.py: the seeded market model (SPEC §8.10; SU15)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

from ahsync.engine.volume import lifetimes  # noqa: E402
from sim.emulator import capture, id_book, snapshot_times  # noqa: E402
from sim.market import DAY_MS, HOUR_MS, Item, Market, World  # noqa: E402

M = "forever.normal.horde.us"
START = 1_793_923_200_000                    # Fri 2026-11-06 00:00 UTC
LINEN = Item(2589, "Linen Cloth", True, 50, 300, 40, vendor_sell=13)
GLOVES = Item(6585, "Scouting Gloves", False, 900, 6, 10, stack=1, item_class=4, quality=2)


def test_a_posted_auction_expires_or_is_cancelled_or_is_bought_and_the_truth_log_says_which():
    world = World(1, START, 1, (Item(2589, "Linen Cloth", True, 50, 0, 0),))
    m = Market(world)
    a = m.post(2589, 20, 50, 2, "me")
    b = m.post(2589, 20, 60, 24, "me", cancel_at_ms=START + HOUR_MS)
    c = m.post(2589, 20, 55, 24, "me")
    m.take(c.id, 5, "you")
    m.run_until(START + 3 * HOUR_MS)
    assert [x.id for x in m.book()] == [c.id] and c.units == 15
    kinds = [(e.kind, e.auction, e.units, e.left) for e in m.truth.events]
    assert kinds == [("post", a.id, 20, 20), ("post", b.id, 20, 20), ("post", c.id, 20, 20), ("buy", c.id, 5, 15),
                     ("cancel", b.id, 20, 0), ("expire", a.id, 20, 0)]
    assert m.truth.endings(2589, START - 1, START + 3 * HOUR_MS) == (2, 1, 1)
    assert m.truth.sold_units(2589, START - 1, START + 3 * HOUR_MS) == 5


def test_commodity_buyers_take_the_cheapest_units_first_and_gear_buyers_one_whole_auction():
    world = World(2, START, 1, (Item(2589, "Linen Cloth", True, 100, 0, 0, buyer_spread=0.0, max_units=30,
                                     miss_cheapest=0.0),))
    m = Market(world)
    cheap, dear = m.post(2589, 20, 40, 24, "s1"), m.post(2589, 20, 60, 24, "s2")
    for _ in range(3):
        m._buy_as_buyer(world.items[0])
    bought = [(e.auction, e.left) for e in m.truth.events if e.kind == "buy"]
    assert bought[0][0] == cheap.id                                       # the cheapest first
    first_dear = next((i for i, (a, _) in enumerate(bought) if a == dear.id), len(bought))
    assert all(a == cheap.id for a, _ in bought[:first_dear]) and all(a == dear.id for a, _ in bought[first_dear:])
    assert first_dear == len(bought) or bought[first_dear - 1][1] == 0   # the dearer only once the cheaper is gone
    fixed = Item(6585, "Scouting Gloves", False, 900, 0, 0, stack=1, item_class=4, buyer_spread=0.0)
    gear = Market(World(3, START, 1, (fixed,)))
    pieces = [gear.post(6585, 1, 800 + i, 24, "s") for i in range(5)]
    gear._buy_as_buyer(fixed)                                             # it will pay up to 900c
    [(auction, units)] = [(e.auction, e.units) for e in gear.truth.events if e.kind == "buy"]
    assert auction in {p.id for p in pieces[:3]} and units == 1          # one of the three cheapest it can afford


def test_the_same_seed_gives_the_same_history_and_another_seed_a_different_one():
    def history(seed):
        m = Market(World(seed, START, 2, (LINEN, GLOVES)))
        m.run_until(START + 2 * DAY_MS)
        return m.truth.to_jsonl()
    assert history(7) == history(7) and history(7) != history(8)


def test_a_weekly_profile_shapes_when_buyers_arrive():
    weekly = tuple(3.0 if h % 24 == 20 else 1.0 for h in range(168))
    item = Item(2589, "Linen Cloth", True, 50, 2400, 400, weekly=weekly)
    m = Market(World(5, START, 7, (item,)))
    m.run_until(START + 7 * DAY_MS)
    hours = [e.at_ms // HOUR_MS % 24 for e in m.truth.events if e.kind == "buy"]
    peak, other = hours.count(20), sum(hours.count(h) for h in range(24) if h != 20) / 23
    assert 2.0 < peak / other < 4.0                                       # about 3× the arrivals at 20:00


def test_SU15_buyers_who_skip_the_cheapest_rarely_look_like_cancels():
    item = Item(2589, "Linen Cloth", True, 50, 3600, 600, max_units=2, durations=((24, 1.0),))
    world = World(15, START, 3, (item,))
    m = Market(world)
    books = []
    for at in snapshot_times(START, START + 3 * DAY_MS, __import__("random").Random(16)):
        m.run_until(at)
        books.append(id_book(capture(m.book(), at), M))
    fills = sum(e.kind == "buy" for e in m.truth.events)
    lt = lifetimes(books)
    false_cancels = sum(e.qty for e in lt.endings if e.cancelled)        # the world has no cancels at all
    sold = sum(e.units for e in m.truth.events if e.kind == "buy")
    assert fills >= 10_000 and m.truth.endings(2589, START, START + 3 * DAY_MS)[2] == 0
    assert false_cancels / sold <= 0.005, f"false cancels {false_cancels} of {sold} units (seed {world.seed})"
