"""Tests for tools/sim/acceptance.py: SM21, the price acceptance against the truth (sold-price plan §1d item 30(e))."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from ahsync.engine import sellprob
from ahsync.engine.sellprob import MarketSettings
from ahsync.intel.contract import ItemIntel

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from sim.acceptance import (PASSES, REPLICATES, SHAPES, STEPS, Post, acceptance_world, chances, mean_chance,  # noqa: E402
                            queue_chance, share_sold, sm21, spec_acceptance, trade, under_level)
from sim.inference import START_MS  # noqa: E402
from sim.market import HOUR_MS, Market  # noqa: E402

CURVE = (60, 80, 90, 100, 120, 150, 200)
HOUR = 3_600_000


def item(**fields) -> ItemIntel:
    """An item's intelligence with sales, a sold level and curve, and the fields the chance reads."""
    base = ItemIntel(2001, 100, 1, HOUR, 100, 100, 80, 120, 0.0, 6.0, 0.0, 0.0, "blizzard", {"blizzard": 7}, 100, 1, 0.5,
                     False, 100.0, 60, 1, None, (), level=100, level_source="sold", level_units=40.0, sold_curve=CURVE,
                     undercut_share=0.0)
    return replace(base, **fields)


def test_SM21_the_acceptance_is_measured_against_the_share_sold_per_step_and_the_read_under_the_level(tmp_path):
    found = sm21(tmp_path, replicates=2, spreads=(0.25,))
    assert len(found) == len(SHAPES) * (3 + len(PASSES) * (len(STEPS) + 1))
    world = [m for m in found if m.what.startswith("world: ")]
    assert len(world) == 3 * len(SHAPES) and all(m.band == "reported" for m in world)
    steps = [m for m in found if m.what.startswith("P28's chance against the share sold, ")]
    assert len(steps) == len(PASSES) * len(SHAPES) * len(STEPS) and all(m.band == "reported" for m in steps)
    assert all(0 <= m.engine <= 1 and 0 <= m.truth <= 1 for m in steps if m.engine is not None)
    cleared = [m for m in steps if ", nothing ahead: " in m.what]
    counts = [m.what.split("(")[1].split(" ") for m in cleared]                  # "N posts, M with nothing ahead; ..."
    assert cleared and all(n == m for n, _, m, *_ in counts)                       # the book was cleared before each post
    under = [m for m in found if m.what.startswith("the queue model's read at or under the hour's level")]
    assert len(under) == len(PASSES) * len(SHAPES) and all("a = 1 by price" in m.what for m in under)


def test_the_poster_reads_every_form_of_the_chance_and_P28_never_exceeds_todays(tmp_path):
    posts, doc, rates = trade(1, tmp_path, replicates=1, spread=0.25)
    assert set(rates) == set(SHAPES) and all(t > 0 for _, t in rates.values())
    assert any(p.under for p in posts) and all(p.chances["P28"] == p.chances["today"] for p in posts if p.under)
    assert posts and {p.shape for p in posts} == set(SHAPES) and {p.step for p in posts} <= set(STEPS)
    for p in posts:
        assert set(p.chances) == {"today", "P28", "spec", "today, nothing ahead", "P28, nothing ahead"}
        assert p.chances["P28"] <= p.chances["today"] + 1e-12
        assert p.chances["P28, nothing ahead"] <= p.chances["today, nothing ahead"] + 1e-12
        assert p.chances["P28"] <= p.chances["P28, nothing ahead"] + 1e-12          # competition only lowers it
        assert 0 <= p.sold <= p.units and p.units == (5 if p.shape == "commodity" else 1)
    assert share_sold([Post(1, "gear", 1.0, 1, 1, 1, {}, 0.0), Post(1, "gear", 1.0, 1, 5, 2, {}, 0.0)]) == 0.5
    assert share_sold([]) is None and mean_chance([], "P28") is None
    assert mean_chance([Post(1, "gear", 1.0, 1, 1, 1, {"P28": 0.2}, 0.0), Post(1, "gear", 1.0, 1, 1, 0, {"P28": 0.6}, 0.0)], "P28") == 0.4


def test_the_specs_half_form_equals_P28s_when_the_level_is_the_curves_50th_and_jumps_above_it():
    for price, a in ((100, 1.0), (120, 0.5), (135, 0.35), (150, 0.2), (175, 0.1), (200, 0.0)):
        assert spec_acceptance(price, 100, CURVE, 1.0) == pytest.approx(a)
        assert spec_acceptance(price, 100, CURVE, 1.0) == pytest.approx(sellprob.acceptance(price, 100, CURVE, 1.0))
    assert spec_acceptance(120, 120, CURVE, 1.0) == 1.0 and spec_acceptance(121, 120, CURVE, 1.0) < 0.5    # the jump
    assert sellprob.acceptance(121, 120, CURVE, 1.0) > 0.95                                              # continuous
    assert spec_acceptance(132, 100, CURVE, 1.1) == pytest.approx(0.5) and spec_acceptance(500, None, CURVE, 1.0) == 1.0


def test_the_queue_chance_with_P28s_factor_is_the_built_posting_chance():
    i = item()
    book = [(90, 1, 4, 1), (110, 1, 3, 1)]
    now = START_MS + 10 * HOUR
    settings = MarketSettings()
    for price in (95, 100, 120, 150, 250):
        read = chances(i, book, price, 1, now, settings)
        assert read["P28"] == queue_chance(i, book, price, 1, now, settings, lambda p, how: sellprob.acceptance(p, 100, CURVE, how))
        assert read["today"] == queue_chance(i, book, price, 1, now, settings, lambda p, how: 1.0)
        assert read["today, nothing ahead"] == sellprob.posting_chance(sellprob.Supply(6.0, 0.0, None, None, None), price, 1,
                                                                        now, settings, None, 0.0)[0]
    assert chances(i, book, 200, 1, now, settings)["P28"] == 0.0            # at the curve's 100th nobody buys
    assert chances(i, book, 95, 1, now, settings)["P28"] == chances(i, book, 95, 1, now, settings)["today"]
    assert under_level(i, 100, now, settings) and not under_level(i, 101, now, settings)
    assert under_level(replace(i, sold_curve=None), 500, now, settings)


def test_the_world_has_each_shape_with_the_planted_spread_and_the_live_book_reads_the_market():
    from sim.acceptance import live_book
    world, keys = acceptance_world(3, replicates=2, spread=0.15)
    assert len(world.items) == 2 * len(SHAPES) and all(i.buyer_spread == 0.15 for i in world.items)
    assert sorted({shape for shape, _ in keys.values()}) == sorted(SHAPES) and world.seed == 3
    market = Market(world)
    market.run_until(START_MS + HOUR_MS)
    key = next(k for k, (shape, _) in keys.items() if shape == "gear")
    market.post(key, 1, 1000, 8, "us")
    other = market.post(key, 1, 900, 24, "someone")
    rows = live_book(market, key, market.now_ms)
    assert (900, 1, 4, 1) in rows and all(r[0] != 1000 for r in rows)        # our own post is not in the book
    assert other.id in market.by_key[key] and rows == sorted(rows)
    assert REPLICATES % len(STEPS) == 0 and STEPS[0] < 0.8
