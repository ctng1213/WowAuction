"""Tests for ahsync/engine/allocation.py: the portfolio plan from candidates to funded actions (SPEC §3.4D)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from ahsync.engine import allocation, craft, selection
from ahsync.engine.allocation import generate
from ahsync.engine.candidates import DeInfo, GenInputs, Run
from ahsync.engine.craft_plan import CraftWorld
from ahsync.engine.flips import FlipSettings
from ahsync.engine.plan import Lot, PlanSettings, PlanWindow, SellFacts
from ahsync.engine.portfolio import OpenPosition, PortfolioSettings
from ahsync.engine.selection import profession_detail, static_reason
from ahsync.engine.week import WeekInputs, prepare
from tests.support import plans

HOUR, DAY = 3_600_000, 86_400_000
MONDAY = 345_600_000 + 3000 * 7 * DAY
NOW = MONDAY + 10 * HOUR + 38 * 60_000              # Mon 10:38
BAR, LINEN, BRACERS = 2840, 2589, 6550
AMAYA, BRUGAN = "Amaya-G", "Brugan-G"


def facts(key: int, level: int, **changes) -> SellFacts:
    """An item with a Sunday 18:00 window (1.2×, 6 weeks), 60 sold a day, 20 listed by others.

    Args:
        key: Item key.
        level: Price level, copper.
        changes: Field overrides.

    Returns:
        The SellFacts.
    """
    how = [1.0] * 168
    how[6 * 24 + 18] = 1.2
    base = SellFacts(key, level=level, trend7=0.0, closes_30d=720, post_price=level, sold_per_day=60.0, listed_per_day=20.0,
                     ref_stack=20, how=tuple(how), sales_how=tuple([1.0] * 168),
                     windows=(PlanWindow(6 * 24 + 17, 3, 6 * 24 + 18, 6),), book=(), cur=level, cur_qty=2,
                     vendor_sell=5, supply_now=200, conf=70)
    return replace(base, **changes)


def world(lots=(), fact_map=None) -> WeekInputs:
    """A world of your stock and no recipes.

    Args:
        lots: Your stock.
        fact_map: Item key -> facts.

    Returns:
        WeekInputs.
    """
    return WeekInputs(tuple(lots), fact_map or {}, CraftWorld((), {}, craft.Market({}, {}, {}, {}), {}))


def test_committed_capital_until_m14_is_what_you_paid_for_what_you_hold():
    units = [(AMAYA, BAR, 6), (AMAYA, BAR, 4), (BRUGAN, BAR, 3), (AMAYA, LINEN, 20)]
    positions = allocation.stock_positions(units, {BAR: (3, 1000), LINEN: (0, 0)})
    # 10 bars at 1,000 ÷ 3 each: 3,333.3 → 3,333; Brugan's 3: 1,000; the Linen was found: no position.
    assert [(p.char_id, p.cost_left) for p in positions] == [(AMAYA, 3333), (BRUGAN, 1000)]
    assert allocation.stock_positions([(AMAYA, BAR, 1)], {BAR: (2, 3)})[0].cost_left == 2           # 1.5, half up


def test_PF9_an_enchanter_short_of_the_skill_leaves_the_bracers_in_the_universe_with_their_status():
    bracers = DeInfo(150, 2100.0, (), ((1500, 1),), 90)
    gi = GenInputs((AMAYA, BRUGAN), {AMAYA: 210_000, BRUGAN: 104_000}, {(AMAYA, "Enchanting"): 142}, {},
                   {BRACERS: bracers}, {}, {BRACERS: "Grunt's Bracers of Defense"}, {})
    run = Run(NOW, PlanSettings(), FlipSettings(), PortfolioSettings(), 3)
    [opp] = generate(prepare(world(), NOW, run.s), gi, run).opportunities
    [batch] = opp.batches
    assert static_reason(batch.eligibility, 7) == "Required profession unavailable: Enchanting 150"
    assert profession_detail(batch.eligibility) == "Amaya has Enchanting 142"
    result = plans.plan(world(), NOW, gold={AMAYA: 210_000, BRUGAN: 104_000}, de={BRACERS: bracers},
                        skills={(AMAYA, "Enchanting"): 142})
    assert result.actions == [] and result.selection.net == 0.0                   # in no total, no action
    assert [(a.candidate.key, a.reason) for a in result.alternatives] == [
        (BRACERS, "Required profession unavailable: Enchanting 150")]


def test_the_plan_lists_funded_opportunities_in_funding_order_then_your_stocks_rest():
    lots = [Lot(AMAYA, LINEN, 25)]
    result = plans.plan(world(lots, {LINEN: facts(LINEN, 50)}), NOW)
    kinds = [a.kind for a in result.actions]
    assert kinds[0] == "post" and kinds == sorted(kinds, key=lambda k: k != "post")   # funded posts, then the rest
    assert sum(a.qty for a in result.actions) == 25                               # every unit planned once (PL2)
    funded = {c.cand_id for c in result.funded}
    assert funded and not funded & {a.candidate.cand_id for a in result.alternatives}


def test_a_list_is_set_by_the_first_batch_and_adjusted_profit_by_each_batchs_sale():
    lots = [Lot(AMAYA, LINEN, 200)]
    w = world(lots, {LINEN: facts(LINEN, 50, supply_now=60)})                    # 30 per window: now, then Sunday
    run = Run(NOW, PlanSettings(), FlipSettings(), PortfolioSettings(), 3)
    u = generate(prepare(w, NOW, run.s), GenInputs((AMAYA,), {AMAYA: plans.RICH}, {}, {}, {}, {}, {}, {}), run)
    picks = allocation.picks(u, run)
    assert {p.lst for p in picks} == {"today"}                                    # the row's first stack sells now
    later = [p for p in picks if u.opportunities[0].batches[p.batch - 1].candidate.sale_ms != NOW]
    assert later and all(p.adjusted < p.net for p in later)                      # Sunday's stacks pay the premium


def test_committed_capital_shrinks_what_may_be_funded():
    lots = [Lot(AMAYA, LINEN, 20)]
    w = world(lots, {LINEN: facts(LINEN, 50, vendor_sell=10)})                    # a post's deposit: 20 × 10c × 5%
    free = plans.plan(w, NOW, gold={AMAYA: 1000}, ps=PortfolioSettings())
    assert free.funded and free.accounting.remaining_capacity == 300
    full = plans.plan(w, NOW, gold={AMAYA: 1000}, ps=PortfolioSettings(), positions=(OpenPosition("p", AMAYA, 500),))
    assert full.funded == () and full.accounting.remaining_capacity == 0          # 30% of 15s < 5s committed
    assert [a.kind for a in full.actions] == ["hold"]                             # the Linen waits


def test_a_selection_that_breaks_d5_is_never_published(monkeypatch):
    real = selection.select

    def overspend(picks, resources, budget):
        """Fund everything, whatever it costs."""
        good = real(picks, resources, budget)
        return replace(good, funded=tuple(p.cand_id for p in picks), reasons=())
    monkeypatch.setattr(allocation, "select", overspend)
    with pytest.raises(selection.SelectionError):
        plans.plan(world([Lot(AMAYA, LINEN, 400)], {LINEN: facts(LINEN, 50, vendor_sell=10)}), NOW,
                   gold={AMAYA: 10}, ps=PortfolioSettings())


def test_the_universe_is_the_same_at_any_risk_tolerance_only_funding_changes():
    lots = [Lot(AMAYA, LINEN, 200)]
    w = world(lots, {LINEN: facts(LINEN, 50, vendor_sell=10)})
    low = plans.plan(w, NOW, gold={AMAYA: 1000}, ps=PortfolioSettings(risk_tolerance=0.01))   # 10c: one deposit
    high = plans.plan(w, NOW, gold={AMAYA: 1000}, ps=PortfolioSettings(risk_tolerance=0.5))
    universe = lambda r: sorted((c.cand_id, c.expected_net, c.gold_needed)                  # noqa: E731
                                for c in list(r.funded) + [a.candidate for a in r.alternatives])
    assert universe(low) == universe(high) and len(low.funded) < len(high.funded)        # PF1 holds end to end
