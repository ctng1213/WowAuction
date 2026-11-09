"""Tests for tools/sim/decision_crafts.py: the craft rows of the decision report (SD10, SD11)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".." / "tools"))
from sim.decision_crafts import (DECOYS, RECIPES, deal_world, first_funded, planned_margin,  # noqa: E402
                                 realized_margin, sd10, sd11)
from sim.trader import Login, Recipe  # noqa: E402
from sim.truth import Event, Truth  # noqa: E402


def test_SD10_both_deals_are_scored_for_how_soon_and_at_what_margin(tmp_path):
    lines = sd10(tmp_path, days=2)
    assert {l.row for l in lines} == {"SD10"} and len(lines) == 4 and all(l.what and l.value and l.target for l in lines)


def test_SD11_every_decoy_is_counted_and_the_false_positives_summed(tmp_path):
    lines = sd11(tmp_path, days=2)
    assert len(lines) == len(DECOYS) + 1 and lines[-1].what.startswith("false positives")
    assert {key for key in DECOYS} <= {i.key for i in deal_world(1, 2).items}


def test_a_crafts_planned_and_realized_margins_are_read_from_the_login_and_the_truth():
    recipe = Recipe(1, "Tailoring", 9, ((7, 4),))
    login = Login(5, ({"kind": "craft", "key": 9}, {"kind": "post", "key": 9, "price": 800}), {}, {7: 150})
    assert first_funded([Login(1, (), {}), login], 9) == 5 and first_funded([login], 8) is None
    assert round(planned_margin(login, recipe), 4) == round((800 * 0.95 - 600) / 600, 4)    # 26.7%
    assert planned_margin(Login(5, (), {}, {7: 150}), recipe) is None                     # no planned post
    t = Truth()
    for e in (Event(1, "buy", 1, 7, 8, 150, 0, "we:Amaya"), Event(2, "post", 2, 9, 2, 800, 2, "we:Amaya"),
              Event(3, "buy", 2, 9, 2, 800, 0, "b1")):
        t.add(e)
    assert round(realized_margin(t, recipe), 4) == round((800 * 0.95 - 600) / 600, 4)
    assert realized_margin(Truth(), recipe) is None and RECIPES[0].product == 4305
