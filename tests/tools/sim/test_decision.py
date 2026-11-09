"""Tests for tools/sim/decision.py: one world traded by the plan and each benchmark (SPEC §8.10; plan §1.6)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

from sim.decision import (STARTER_CHARACTERS, Line, benchmark, funded, over_liquidity, sd15,  # noqa: E402
                          sd28, sd29, sd31, starter)
from sim.trader import Login  # noqa: E402
from sim.truth import Event, Truth  # noqa: E402


def test_the_starter_world_is_scored_for_the_plan_the_naive_trader_nothing_and_the_oracle(tmp_path):
    plan, naive, nothing, oracle = benchmark(starter(1), STARTER_CHARACTERS, tmp_path)
    assert [s.name for s in (plan, naive, nothing, oracle)] == ["plan", "naive", "nothing", "oracle"]
    assert nothing.made == 0 and nothing.stranded_units == 260 and oracle.made > plan.made > 0
    assert 0 < plan.oracle_share < 1 and plan.stranded_units == 0       # the plan sold the starting stock
    assert naive.stranded_units > 0                                    # it bought the dump and could not resell it



def complete(lines: list[Line], rows: set[str]) -> bool:
    """Every line names its row, measure, value and target (the SD tests check the report, never the mark)."""
    return {l.row for l in lines} == rows and all(l.what and l.value and l.target for l in lines)


def test_SD31_a_brand_new_item_with_a_300_percent_apparent_margin_is_never_funded(tmp_path):
    lines = sd31(tmp_path, days=4)                                    # the report runs it on a mature market
    assert complete(lines, {"SD31"}) and len(lines) == 2


def test_SD28_a_held_flips_lasting_drop_is_scored_against_holding_to_the_target(tmp_path):
    lines = sd28(tmp_path, days=4, dip_day=2, hourly=False)           # too young to buy: "never" is a line too
    assert complete(lines, {"SD28"}) and len(lines) == 3


def test_SD29_a_one_snapshot_dip_is_compared_with_the_same_login_without_it(tmp_path):
    assert complete(sd29(tmp_path, days=3, dip_day=1, hourly=False), {"SD29"})


def test_SM17_our_part_of_a_thin_items_sold_per_day_is_reported(tmp_path):
    lines = sd15(tmp_path, days=3)
    assert complete([l for l in lines if l.row == "SM17"], {"SM17"})


def test_SD15_our_posts_as_a_large_share_of_supply_are_held_to_the_liquidity_rule(tmp_path):
    lines = sd15(tmp_path, days=3)
    assert complete([l for l in lines if l.row == "SD15"], {"SD15"}) and len(lines) == 3


def test_our_listings_are_checked_against_the_liquidity_rule_at_each_snapshot():
    t = Truth()
    for e in (Event(1, "post", 1, 7, 10, 50, 10, "s1"), Event(2, "post", 2, 7, 5, 60, 5, "we:a"),
              Event(4, "post", 3, 7, 5, 60, 5, "we:a"), Event(6, "buy", 1, 7, 10, 50, 0, "b")):
        t.add(e)
    assert over_liquidity(t, 7, [3, 5, 7], "we:", 0.5) == (2, 3)    # 5 ≤ max(5, 5); 10 > 5; 10 > max(5, 0)
    logins = [Login(1, ({"kind": "buy", "key": 7},), {}), Login(2, ({"kind": "post", "key": 7},), {})]
    assert funded(logins, 7) == logins[:1] and funded(logins, 7, "post") == logins[1:]
