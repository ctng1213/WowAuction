"""Tests for tools/sim/sold_prices.py: the sold-price rows SM10, SM11 and SM18 (sold-price plan §1c)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from sim.sold_prices import GLOVES, HIGH, confirmed_floor, gloves_world, sm10, sm11, sm18  # noqa: E402


def test_SM10_a_high_ask_that_doesnt_sell_sets_med7_while_sold_med7_reads_what_sold(tmp_path):
    med7, post, sold = sm10(tmp_path)
    assert med7.truth == 20_000 and med7.engine == HIGH and post.engine == HIGH - 1    # closes are asks
    assert sold.passed and sold.engine == 20_000


def test_SM11_when_demand_catches_up_sold_med7_rises_to_what_sells_now(tmp_path):
    sold, post = sm11(tmp_path)
    assert sold.passed and sold.truth == HIGH and post.band == "reported"


def test_SM18_a_lone_cheap_listing_is_undercut_today_and_a_confirmed_floor_prices_at_the_crowd(tmp_path):
    measures = {m.what.split(" (")[0].split(",")[0]: m for m in sm18(tmp_path)}
    assert measures["today's undercut while it shows"].engine == 59
    floor = measures["confirmed floor − 1 c"]                       # the first pair: N 20 or K 2
    assert floor.engine > 59 and abs(floor.engine - floor.truth) <= 0.05 * floor.truth


def test_the_gloves_world_keeps_its_high_ask_up_and_rises_only_when_asked():
    world = gloves_world(1, 3)
    assert [p.price for p in world.planted] == [HIGH] * 3 and world.items[0].key == GLOVES
    assert world.items[0].shifts == () and gloves_world(1, 3, 99).items[0].shifts == ((99, 7.5, 1.0),)


def test_a_confirmed_floor_needs_n_units_or_k_auctions_within_the_band():
    book = [(60, 5), (100, 10), (102, 15), (130, 40)]
    assert confirmed_floor(book, 20, 3, 0.03) == 100                # 25 units in 100-103 c; the lone 60 c has 5
    assert confirmed_floor(book, 50, 2, 0.03) == 100                # 2 auctions in 100-103 c
    assert confirmed_floor(book, 40, 9, 0.03) == 130 and confirmed_floor(book, 41, 9, 0.03) is None
