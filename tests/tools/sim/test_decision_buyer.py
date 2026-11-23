"""Tests for tools/sim/decision_buyer.py: SM22, the plan's flips and buys for later, taken (sold-price plan §1d item 30(f))."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ahsync.engine.fees import AH_CUT

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from sim.decision_buyer import CHANGE_DAY, DAYS, LATE_DAYS, PROTOTYPE, Trades, sm22, trades  # noqa: E402
from sim.price_rules import GROUPS, REGIMES, Kind, group_of  # noqa: E402
from sim.truth import Event  # noqa: E402


def test_SM22_earned_per_gold_spent_is_reported_per_regime_against_the_prototype(tmp_path):
    lines = sm22(tmp_path, replicates=1, days=23 + LATE_DAYS, change_day=22)
    assert [l.what.split(":")[0] for l in lines[:-1]] == [*REGIMES, *GROUPS, "all regimes"]
    assert all(l.row == "SM22" and l.target == PROTOTYPE for l in lines[:-1]) and lines[-1].what.startswith("late buys")
    for l in lines[:-1]:
        assert (l.value == "no buys" and l.met is None) or (l.met is True and l.value[0] in "+-" and float(l.value) == pytest.approx(float(l.value)))
        assert "buys" in l.what and "unsold" in l.what
    assert CHANGE_DAY == 22 and DAYS > CHANGE_DAY + LATE_DAYS and PROTOTYPE.startswith("reported")


def test_trades_sum_our_buys_and_the_resales_of_our_auctions_by_regime():
    kinds = {1001: Kind("gear 6 a week", "steady", 20_000), 1002: Kind("gear 6 a week", "drop", 20_000)}
    events = [Event(0, "post", 5, 1001, 1, 25_000, 1, "we:Amaya"),            # our resale listing
              Event(0, "buy", 3, 1001, 1, 15_000, 0, "we:Amaya"),             # our buy of someone's auction
              Event(0, "buy", 5, 1001, 1, 25_000, 0, "b7"),                   # a buyer takes our listing
              Event(0, "buy", 4, 1002, 2, 10_000, 0, "we:Amaya"),             # a buy in another regime, unsold
              Event(0, "buy", 9, 1001, 1, 30_000, 0, "b8"),                   # others trading among themselves
              Event(0, "buy", 8, 7777, 1, 30_000, 0, "we:Amaya")]             # an item outside the world's kinds
    found = trades(events, kinds)
    assert set(found) == {"steady", "drop"}
    assert trades(events, kinds, until_ms=-1)["steady"] == Trades(0, 0, 0, 0.0, 0)   # the buy and the resale are late
    assert set(trades(events, kinds, by=group_of)) == {"gear at 5+ a week"}
    assert found["steady"] == Trades(1, 15_000, 1, 25_000 * (1 - AH_CUT), 1)
    assert found["steady"].earned_per_gold == pytest.approx((25_000 * 0.95 - 15_000) / 15_000)
    assert found["drop"] == Trades(1, 20_000, 2, 0.0, 0) and found["drop"].earned_per_gold == -1.0
    assert Trades(0, 0, 0, 0.0, 0).earned_per_gold is None
