"""Tests for tools/sim/market.py: the seeded market model (SPEC §8.10; SU15)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

from ahsync.engine.volume import lifetimes  # noqa: E402
from sim.emulator import capture, id_book, snapshot_times  # noqa: E402
from sim.market import DAY_MS, HOUR_MS, Item, Market, Planted, World  # noqa: E402

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


def test_a_price_mix_rounds_away_from_the_floor_and_an_anchor_holds_the_reference_under_fair():
    item = Item(2589, "Linen Cloth", True, 100, 0, 0, price_mix=((1.0, 1.01, 1.01),), anchor=0.5)
    m = Market(World(4, START, 1, (item,)))
    assert m._reference(item) == 50                                       # an empty book: the fair price, anchored
    m.post(2589, 5, 40, 24, "s")
    assert m._reference(item) == 40                                       # the floor is under the anchor
    assert m._price(item, 40) == 41                                       # 40.4 rounds up to 1 c over the floor
    under = Item(2589, "Linen Cloth", True, 100, 0, 0, price_mix=((1.0, 0.99, 0.99),))
    at = Item(2589, "Linen Cloth", True, 100, 0, 0, price_mix=((1.0, 1.0, 1.0),))
    assert m._price(under, 40) == 39 and m._price(at, 40) == 40           # 39.6 rounds down to 1 c under


def test_a_buyer_level_raises_what_buyers_pay_and_a_stack_mix_sets_what_sellers_post():
    shy = Item(2589, "Linen Cloth", True, 100, 0, 0, buyer_spread=0.0, buyer_level=0.5, max_units=1)
    m = Market(World(5, START, 1, (shy,)))
    m.post(2589, 5, 60, 24, "s")
    m._buy_as_buyer(shy)
    assert not [e for e in m.truth.events if e.kind == "buy"]            # 60 c is over half of 100 c
    stacked = Item(2589, "Linen Cloth", True, 100, 0, 0, stacks=((3, 1.0),))
    m = Market(World(6, START, 1, (stacked,)))
    m._post_as_seller(stacked)
    assert m.book()[0].units == 3


def test_a_cancelling_seller_relists_the_same_units_and_a_bot_undercuts_every_hour():
    item = Item(2589, "Linen Cloth", True, 100, 0, 0, relist_share=1.0, durations=((8, 1.0),), bots=1, stack=7)
    m = Market(World(7, START, 1, (item,)))
    a = m.post(2589, 4, 100, 24, "s9", cancel_at_ms=START + 30 * 60_000)
    m.run_until(START + 2 * HOUR_MS)
    posts = [e for e in m.truth.events if e.kind == "post"]
    relisted = [e for e in posts if e.actor == "s9" and e.auction != a.id]
    assert len(relisted) == 1 and relisted[0].units == 4 and relisted[0].at_ms == START + 30 * 60_000
    bot = [e for e in posts if e.actor.startswith("bot")]
    assert [e.at_ms for e in bot] == [START + 7 * 60_000, START + 67 * 60_000] and all(e.units == 7 for e in bot)
    assert sum(e.kind == "cancel" and e.actor.startswith("bot") for e in m.truth.events) == 1   # its old one
    assert m.truth.relists(2589, START, START + 2 * HOUR_MS, 0)[1] == 2   # the bot's cancel and repost is one too


def test_planted_auctions_are_posted_on_time_and_cancelled_when_told():
    item = Item(6585, "Scouting Gloves", False, 900, 0, 0, stack=1)
    planted = (Planted(START + HOUR_MS, 6585, 1, 150_000, 24, START + 3 * HOUR_MS),)
    m = Market(World(8, START, 1, (item,), planted=planted))
    m.run_until(START + 4 * HOUR_MS)
    assert [(e.kind, e.at_ms, e.price, e.actor) for e in m.truth.events] == [
        ("post", START + HOUR_MS, 150_000, "p0"), ("cancel", START + 3 * HOUR_MS, 150_000, "p0")]
