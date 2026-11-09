"""Tests for tools/sim/decision_gathering.py: the gathering row of the decision report (SD9)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".." / "tools"))
from sim.decision_gathering import (DEAL, DEAL_FAIR, DEAL_RATE, FARM_MINUTES, FARMS_A_DAY, MARKET_ITEMS,  # noqa: E402
                                    drop_world, monster_drops, our_sales, sd9)
from sim.truth import Event, Truth  # noqa: E402


def test_SD9_a_monster_drop_is_scored_for_its_gathering_rank_and_its_sales(tmp_path):
    lines = sd9(tmp_path, days=2)
    assert [l.row for l in lines] == ["SD9", "SD9"] and all(l.what and l.value and l.target for l in lines)


def test_the_world_puts_the_deal_among_about_50_catalog_monster_drops_and_farms_it():
    world, farm = drop_world(1, 2)
    keys = [i.key for i in world.items]
    assert len(world.items) == MARKET_ITEMS and keys[0] == DEAL and DEAL in {k for k, _ in monster_drops()}
    assert farm.loot[0] == (DEAL, 12) and len(farm.loot) == 8 and all(k in keys for k, _ in farm.loot)


def test_the_other_farmed_drops_sell_as_fast_as_farmed_and_two_out_earn_the_deal():
    world, farm = drop_world(1, 2)
    items = {i.key: i for i in world.items}
    others = farm.loot[1:]
    assert all(items[k].buys_per_day >= rate * FARM_MINUTES * FARMS_A_DAY / 60 for k, rate in others)  # plan §1.47
    assert sum(rate * items[k].fair_price > DEAL_RATE * DEAL_FAIR for k, rate in others) == 2


def test_our_sales_count_only_units_others_bought_from_us():
    t = Truth()
    for e in (Event(1, "post", 1, DEAL, 5, 900, 5, "we:Amaya"), Event(2, "post", 2, DEAL, 5, 900, 5, "s1"),
              Event(3, "buy", 1, DEAL, 3, 900, 2, "b1"), Event(4, "buy", 2, DEAL, 5, 900, 0, "b2")):
        t.add(e)
    assert our_sales(t, DEAL) == 3
