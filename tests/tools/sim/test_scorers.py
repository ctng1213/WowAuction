"""Tests for tools/sim/scorers.py: the ledger, the scores and the oracle (SPEC §8.10; plan §1.6, §1.34, §1.35)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

from sim.scorers import DAY_MS, Ledger, Lot, oracle_profit, score, value_at  # noqa: E402
from sim.truth import Event  # noqa: E402


def test_a_lot_gives_up_its_cost_in_proportion_and_the_last_unit_takes_the_rest():
    lot = Lot(3, 100)
    assert [lot.take(1), lot.take(1), lot.take(1)] == [33, 33, 34] and (lot.units, lot.cost) == (0, 0)
    with pytest.raises(ValueError, match="cannot take"):
        lot.take(1)


def test_a_partial_sale_returns_its_share_of_the_deposit_and_marks_stock_at_a_price():
    ledger = Ledger(0)
    ledger.fund("a", 1_000)
    ledger.buy("a", 7, 10, 500)
    ledger.post("a", 7, 10, 1, 100)
    assert ledger.sale("a", 1, 4, 80) == 320 - 16 + 40 and ledger.deposits[1] == 60 and ledger.listed[1].units == 6
    assert ledger.balanced() and ledger.gains == 320 - 200 and ledger.cuts == 16
    assert value_at(ledger, {7: 90}) == 400 + 344 + 60 + 6 * 90    # cash, mail gold, deposit held, 6 listed at 90 c


def test_a_score_counts_realized_gold_capital_days_drawdown_and_what_is_left():
    ledger = Ledger(0)
    ledger.fund("a", 1_000)
    ledger.buy("a", 7, 2, 400)
    samples = [(0, 400, 0), (DAY_MS, 400, 300), (2 * DAY_MS, 0, 100), (3 * DAY_MS, 0, 250)]
    s = score("plan", ledger, samples, 1_000)
    assert s.gold_days == pytest.approx(800 / 10_000) and s.drawdown == 200
    assert (s.stranded_units, s.stranded_cost, s.made, s.oracle_share) == (2, 400, 0, 0.0)
    assert score("x", ledger, samples, None).oracle_share is None


def test_the_oracle_resells_cheap_listings_and_its_stock_into_later_dearer_sales():
    events = [Event(10, "post", 1, 7, 5, 50, 5, "s1"), Event(20, "post", 2, 7, 5, 200, 5, "s2"),
              Event(30, "buy", 2, 7, 3, 200, 2, "b1"),                    # 3 at 200: resold from listing 1 at 50
              Event(40, "buy", 3, 7, 4, 100, 0, "b2"),                    # 2 left of listing 1, then the stock
              Event(DAY_MS + 50, "buy", 4, 7, 1, 100, 0, "b3"),           # listing 1 is a day old: from the stock
              Event(60, "buy", 5, 7, 1, 100, 0, "we:a")]                  # ours: left out
    profit = oracle_profit(events, {7: 3}, frozenset({"we:a"}))
    assert profit == 3 * (190 - 50) + 2 * (95 - 50) + 2 * 95 + 1 * 95
    assert oracle_profit(events[:3]) == 3 * 140 and oracle_profit([Event(1, "buy", 1, 7, 1, 10, 0, "b")]) == 0


def test_items_returned_before_any_sale_can_be_collected():
    ledger = Ledger(0)
    ledger.fund("a", 500)
    ledger.buy("a", 7, 5, 100)
    ledger.post("a", 7, 5, 1, 100)
    ledger.returned("a", 7, 1)                                   # expired: no sale gold was ever mailed
    ledger.collect("a", 0, {7: 5})
    assert ledger.balanced() and ledger.bags[("a", 7)].units == 5 and ledger.deposits_lost == 100
