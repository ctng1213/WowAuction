"""Tests for ahsync/engine/portfolio.py: the portfolio contracts (SPEC §3.4D.1, §3.4D.3, §3.4D.8; PF1, PF3, PF8, PF21-PF23)."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ahsync.engine import craft, portfolio
from ahsync.engine.portfolio import (Accounting, CapacityClaim, Candidate, CandidateVersionError, CharacterGold,
                                     OpenPosition, OwnedClaim, Requirements, accounting, available_investment,
                                     candidate_from_json, candidate_id, candidate_to_json, committed_share_pct,
                                     export_subset, remaining_capacity, risk_budget, route_text, timing_tag)

G, S = 10_000, 100                       # copper per gold, per silver
HOUR = 3_600_000
NOW = 1_700_000_000_000
AMAYA, BRUGAN = "Amaya-Grommash", "Brugan-Grommash"
COPPER_BAR, COPPER_ROD, WOOL = 2840, 6217, 2592
# The mockups' world (SPEC §3.4D): Amaya 21 g, Brugan 10 g 40 s, 4 g 18 s committed.
MOCKUP_CHARS = [CharacterGold(AMAYA, 21 * G), CharacterGold(BRUGAN, 10 * G + 40 * S)]
MOCKUP_POSITIONS = [OpenPosition("p1", AMAYA, 3 * G), OpenPosition("p2", BRUGAN, 1 * G + 18 * S)]


def rod_candidate(**changes) -> Candidate:
    """Brugan crafts 5 Copper Rods from 10 bars he smelted, posted now (a Craft candidate, batch 1)."""
    base = Candidate(
        strategy="craft", key=COPPER_ROD, char_id=BRUGAN, route=route_text({COPPER_BAR: ["crafted"]}, "-", "now"),
        batch=1, out_qty=5, inputs=((COPPER_BAR, 10),), gold_mats=0, gold_deposits=1 * S, cost_basis=50 * S,
        expected_net=6306.0, chance=0.58, exec_qty=5, conf=72,
        needs=Requirements("Blacksmithing", 1, 2663, 0), sale_ms=NOW,
        owned_claims=(OwnedClaim(BRUGAN, "bags", COPPER_BAR, 10),), capacity_claims=(CapacityClaim(COPPER_ROD, None, 5),),
        built_ms=NOW - 2 * HOUR, method_versions=(("curve", 1), ("volume", 3)))
    return replace(base, **changes)


def test_PF1_candidates_are_the_same_at_any_risk_tolerance_only_funding_changes():
    # The record has no risk input, so generating at 30% or 10% can't change a candidate or its id.
    names = portfolio.candidate_fields()
    assert not [n for n in names if "risk" in n or "tolerance" in n]
    rod = rod_candidate()
    assert rod.cand_id == "craft:6217:Brugan-Grommash:2840c/-/now:1"
    at_30 = accounting(MOCKUP_CHARS, MOCKUP_POSITIONS, 0.30)
    at_10 = accounting(MOCKUP_CHARS, MOCKUP_POSITIONS, 0.10)
    assert (at_30.risk_budget, at_30.remaining_capacity) == (10 * G + 67 * S + 40, 6 * G + 49 * S + 40)
    # 35 g 58 s x 0.1 = 3 g 55 s 80 c, below the 4 g 18 s committed: capacity 0, nothing new can be funded.
    assert (at_10.capital_base, at_10.risk_budget) == (35 * G + 58 * S, 3 * G + 55 * S + 80)
    assert (at_10.remaining_capacity, at_10.available, at_10.available_for(BRUGAN)) == (0, 0, 0)


def test_PF3_the_export_keeps_the_funded_first_then_the_best_alternatives():
    universe = [replace(rod_candidate(), strategy="flip", key=1000 + i, route="-/now/now") for i in range(230)]
    ids = [c.cand_id for c in universe]
    funded = ids[200:220] + ids[5:12]                 # 20 Today rows, then 7 week rows, in funding order
    # Adjusted profit (float copper) for the 203 others: falls with i, every pair tied to test the id tie-break.
    adjusted = {i: float(1000 - n // 2) for n, i in enumerate(ids) if i not in funded}
    kept = export_subset(universe, funded, adjusted, 150)
    assert len(kept) == 150 and [c.cand_id for c in kept[:27]] == funded
    alternatives = [c.cand_id for c in kept[27:]]
    expected = sorted(adjusted, key=lambda i: (-adjusted[i], i))[:123]
    assert alternatives == expected and len(set(ids) - {c.cand_id for c in kept}) == 80
    assert [c.cand_id for c in export_subset(universe, funded, adjusted, 20)] == funded[:20]
    with pytest.raises(ValueError):
        export_subset(universe, ["flip:1:x:-/now/now:1"], adjusted, 150)
    with pytest.raises(ValueError):
        export_subset(universe, [ids[0], ids[0]], adjusted, 150)


def test_PF8_capital_base_risk_budget_and_available_investment():
    big = accounting([CharacterGold(AMAYA, 2000 * G)], [OpenPosition("p", AMAYA, 250 * G)], 0.30)
    assert (big.cash_gold, big.committed_capital, big.capital_base) == (2000 * G, 250 * G, 2250 * G)
    assert (big.risk_budget, big.remaining_capacity, big.available) == (675 * G, 425 * G, 425 * G)
    mockup = accounting(MOCKUP_CHARS, MOCKUP_POSITIONS, 0.30)
    assert (mockup.cash_gold, mockup.capital_base) == (31 * G + 40 * S, 35 * G + 58 * S)
    assert (mockup.risk_budget, mockup.remaining_capacity) == (10 * G + 67 * S + 40, 6 * G + 49 * S + 40)
    assert committed_share_pct(mockup) == 12                     # 41,800 / 355,800 = 11.7%, half up
    over = accounting([CharacterGold(AMAYA, 1550 * G)], [OpenPosition("p", AMAYA, 700 * G)], 0.30)
    assert (over.risk_budget, over.remaining_capacity, over.available) == (675 * G, 0, 0)   # never negative
    assert remaining_capacity(675 * G, 700 * G) == 0


def test_PF22_found_items_add_nothing_to_the_capital_or_the_budget():
    before = accounting(MOCKUP_CHARS, MOCKUP_POSITIONS, 0.30)
    # Amaya's 20 found Wool Cloth: no gold was committed, so it is at most a Holding row with cost 0.
    found = accounting(MOCKUP_CHARS, [*MOCKUP_POSITIONS, OpenPosition("wool", AMAYA, 0)], 0.30)
    assert found == before
    assert before.spendable == ((AMAYA, 21 * G), (BRUGAN, 10 * G + 40 * S))
    # As a craft mat it is still priced at its hold value (R4), never free.
    recipe = craft.Recipe(1, COPPER_ROD, 1, 1, ((WOOL, 2),), (AMAYA,))
    option = craft.craft_option(recipe, craft.Market({}, {}, {WOOL: 20}, {WOOL: 30.0}), {COPPER_ROD: [recipe]},
                                lambda n: n * 100.0, max_crafts=1)
    assert option.profits == [100.0 - 60.0]


def test_PF23_reserved_gold_is_subtracted_and_capacity_binds_before_gold():
    # Brugan's funded rows reserve 3 g 73 s, Amaya's 2 g 52 s: 6 g 25 s of the 6 g 49 s 40 c capacity.
    run = accounting(MOCKUP_CHARS, MOCKUP_POSITIONS, 0.30,
                     {BRUGAN: 3 * G + 73 * S, AMAYA: 2 * G + 52 * S, "Zed-Grommash": 0})
    assert (run.reserved, run.capacity_left) == (6 * G + 25 * S, 24 * S + 40)
    assert run.spendable_left(BRUGAN) == 6 * G + 67 * S
    assert run.available_for(BRUGAN) == 24 * S + 40          # min(24 s 40 c, 6 g 67 s)
    assert 45 * S > run.available_for(BRUGAN)                # Lesser Mana Potion: exceeds risk tolerance
    assert run.reserved_by_char == ((AMAYA, 2 * G + 52 * S), (BRUGAN, 3 * G + 73 * S))
    assert run.available == 24 * S + 40 and run.available_for("Nobody-Grommash") == 0
    assert available_investment(10_000, 5_000, 6_000) == 0   # more reserved than spendable: 0, never negative


def test_a_candidate_round_trips_through_json_byte_identically():
    rod = rod_candidate(buy_ms=NOW + HOUR)
    text = candidate_to_json(rod)
    assert candidate_from_json(text) == rod and candidate_to_json(candidate_from_json(text)) == text
    body = json.loads(text)
    assert body["cand_id"] == rod.cand_id and body["v"] == portfolio.CANDIDATE_SCHEMA_VERSION
    assert list(body) == sorted(body) and rod.gold_needed == 1 * S and rod.label == "Craft"
    with pytest.raises(CandidateVersionError):
        candidate_from_json(json.dumps({**body, "v": 99}))
    with pytest.raises(ValueError):
        candidate_from_json(json.dumps({**body, "cand_id": "craft:1:x:-/-/-:1"}))


def test_candidate_ids_and_routes_have_one_fixed_format():
    assert candidate_id("buy_later", 4306, AMAYA, "-/h10/h20", 2) == "buy_later:4306:Amaya-Grommash:-/h10/h20:2"
    assert route_text({3371: ["vendor"], 2840: ["crafted", "ah", "owned"]}, "now", "h7") == "2840oac+3371v/now/h7"
    assert route_text({}, "-", "now") == "-/-/now"
    assert (timing_tag(None, NOW), timing_tag(NOW, NOW), timing_tag(NOW + 5 * HOUR, NOW)) == \
        ("-", "now", f"h{NOW // HOUR + 5}")
    for bad in (("swap", 1, AMAYA, "-/-/-", 1), ("flip", 1, AMAYA, "-/-/-", 0), ("flip", 1, "A:B", "-/-/-", 1),
                ("flip", 1, AMAYA, "", 1), ("flip", 1, AMAYA, "a;b", 1)):
        with pytest.raises(ValueError):
            candidate_id(*bad)
    for sources in ({1: []}, {1: ["stolen"]}):
        with pytest.raises(ValueError):
            route_text(sources, "-", "-")


def test_accounting_refuses_bad_money_and_uses_budgets():
    assert accounting([CharacterGold(AMAYA, 21 * G, budget=15 * G)], [], 0.30).spendable == ((AMAYA, 15 * G),)
    assert risk_budget(100, 0.29) == 29 and risk_budget(355_800, 0.4) == 14 * G + 23 * S + 20   # D79
    assert committed_share_pct(accounting([], [], 0.30)) is None
    bad = ([CharacterGold(AMAYA, -1)], [], 0.3, None), ([CharacterGold(AMAYA, 1, budget=-1)], [], 0.3, None), \
        ([], [OpenPosition("p", AMAYA, -1)], 0.3, None), ([], [], 0.3, {AMAYA: -1}), ([], [], 0.0, None), \
        ([], [], 1.5, None), (MOCKUP_CHARS + MOCKUP_CHARS[:1], [], 0.3, None)
    for chars, positions, tolerance, reserved in bad:
        with pytest.raises(ValueError):
            accounting(chars, positions, tolerance, reserved)
    assert isinstance(accounting(MOCKUP_CHARS, [], 1.0), Accounting)


def test_PF21_profit_is_a_return_on_cash_and_found_stock_counts_none():
    rod = rod_candidate(gold_deposits=100, cost_basis=5000, expected_net=6306.0)     # 5 rods bought at 10s, 1s deposit
    assert portfolio.cash_of(rod) == 5100 and portfolio.profit_pct(rod.expected_net, portfolio.cash_of(rod)) == 124
    today = [(6306.0, 5100), (4000.0, 3000), (625.0, 800), (380.0, 500), (203.0, 760)]
    assert portfolio.pool_profit_pct(today) == 113                                   # 1g 15s 14c ÷ 1g 1s 60c
    week = [(7342.0, 30000), (5500.0, 16080), (1855.0, 4200), (1630.0, 3700), (730.0, 2800), (194.0, 560), (134.0, 0)]
    assert portfolio.pool_profit_pct(week) == 30                                     # Heavy Grinding Stone left out
    assert portfolio.profit_pct(134.0, 0) is None and portfolio.pool_profit_pct([(134.0, 0)]) is None
    assert portfolio.pool_profit_pct(today + week) == 43                             # 2g 87s 65c ÷ 6g 75s
