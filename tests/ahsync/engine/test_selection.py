"""Tests for ahsync/engine/selection.py: the portfolio optimizer's selection (SPEC §3.4D.4-D.7)."""
from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path

import pytest

from ahsync.engine import selection as sel
from ahsync.engine.portfolio import CharacterGold, OpenPosition, accounting
from ahsync.engine.selection import Budget, Eligibility, Pick, Resource
from ahsync.engine.timing import adjusted_profit
from tests.support.gen import cases

SHARED = json.loads((Path(__file__).resolve().parents[3] / "tests" / "scenarios" / "portfolio.json").read_text(encoding="utf-8"))
SEED_PF25, SEED_PF27 = 3425, 3427
AMAYA, BRUGAN = "Amaya-Grommash", "Brugan-Grommash"
MON = 1_728_259_200_000             # Mon 2024-10-07 00:00 UTC
NOW = MON + 10 * 3_600_000 + 38 * 60_000
TONIGHT = MON + 18 * 3_600_000


def pick(name: str, char: str, gold: int, net: float, sale_ms: int = TONIGHT, batch: int = 1, opp: str | None = None,
         reason: str | None = None, claims: tuple = ()) -> Pick:
    """A batch from the mockup's numbers.

    Args:
        name: Row label (also its id and opportunity unless given).
        char: Acting character.
        gold: Gold needed, copper.
        net: Expected net, copper.
        sale_ms: Expected sale hour.
        batch: Batch number.
        opp: Opportunity id; the name by default.
        reason: A static exclusion reason.
        claims: (resource id, units).

    Returns:
        The Pick, adjusted with the default premium from NOW.
    """
    adj = adjusted_profit(net, NOW, sale_ms, 0.15, 0.05, 8)
    lst = "today" if sale_ms == TONIGHT else "week"
    return Pick(f"{opp or name}:{batch}", opp or name, batch, char, lst, gold, adj, net, name, reason, claims)


def at(day: int, hour: int) -> int:
    """A sale time in the mockup's week.

    Args:
        day: Days after Monday.
        hour: Hour of that day, UTC.

    Returns:
        UTC ms.
    """
    return MON + day * 86_400_000 + hour * 3_600_000


def mockup(bracers_skill: int = 142) -> list[Pick]:
    """The mockup world's pool (SPEC §3.4D, dashboard mockup sections B and C).

    Args:
        bracers_skill: Amaya's Enchanting (the bracers need 150).

    Returns:
        Its 16 candidate batches.
    """
    bracers = sel.static_reason(Eligibility("Enchanting", 150, bracers_skill, (AMAYA, bracers_skill)), 7)
    return [
        pick("Copper Rod", BRUGAN, 100, 6306), pick("Silver Rod", BRUGAN, 3000, 4000),
        pick("Scouting Gloves of the Monkey", AMAYA, 800, 625), pick("Embossed Leather Gloves", AMAYA, 500, 380),
        pick("Linen Cloth", AMAYA, 760, 203), pick("Lesser Mana Potion", BRUGAN, 4500, 80),
        pick("Grunt's Bracers of Defense", AMAYA, 1500, 600, reason=bracers),
        pick("Elixir of Fortitude", AMAYA, 12000, 900,
             reason=sel.static_reason(Eligibility(sold_per_day=3.0, planned_units=40), 7)),
        pick("Mithril Spurs", BRUGAN, 30000, 7342, at(5, 18)), pick("Thick Leather", AMAYA, 16080, 5500, at(3, 19)),
        pick("Transmute: Iron to Gold", BRUGAN, 4200, 1855, at(3, 19)),
        pick("Runed Copper Rod", AMAYA, 3700, 1630, at(6, 18)), pick("Silk Cloth", AMAYA, 2800, 730, at(5, 19)),
        pick("Bolt of Silk Cloth", AMAYA, 560, 194, at(6, 19)), pick("Heavy Grinding Stone", BRUGAN, 0, 134, at(5, 19)),
        pick("Greater Magic Essence", AMAYA, 3600, 1200, at(6, 19),
             reason=sel.static_reason(Eligibility(untrusted_weeks=1), 7)),
    ]


def mockup_budget() -> Budget:
    """The mockup's budget: 21 g and 10 g 40 s in hand, 4 g 18 s committed, risk tolerance 30%.

    Returns:
        Budget with capacity 6 g 49 s 40 c.
    """
    a = accounting([CharacterGold(AMAYA, 210_000), CharacterGold(BRUGAN, 104_000)],
                   [OpenPosition("p", BRUGAN, 41_800)], 0.30)
    return Budget(a.capacity_left, a.spendable)


def names(result: sel.Selection, picks: list[Pick]) -> list[str]:
    """The funded rows' names in funding order.

    Args:
        result: A selection.
        picks: Its batches.

    Returns:
        Names.
    """
    by_id = {p.cand_id: p.name for p in picks}
    return [by_id[i] for i in result.funded]


def run(picks, resources=(), budget=None) -> sel.Selection:
    """Select and validate.

    Args:
        picks: Batches.
        resources: Resources.
        budget: Budget; the mockup's by default.

    Returns:
        The selection (validated).
    """
    budget = budget or mockup_budget()
    result = sel.select(picks, resources, budget)
    sel.validate(picks, resources, budget, result)
    return result


def test_PF5_the_swap_pass_replaces_one_big_ratio_by_two_that_make_more():
    picks = [Pick("A", "A", 1, AMAYA, "today", 6000, 3000.0, 3000.0, "A"),
             Pick("B", "B", 1, AMAYA, "today", 5000, 2000.0, 2000.0, "B"),
             Pick("C", "C", 1, AMAYA, "today", 5000, 1900.0, 1900.0, "C")]
    budget = Budget(10_000, ((AMAYA, 50_000),))
    greedy = sel._State(10_000, {AMAYA: 50_000}, {})
    sel._fill(greedy, sel.fill_order(picks), {}, budget, 50_000)
    assert [p.cand_id for p in greedy.funded] == ["A"]                        # 0.50 first; B and C don't fit
    result = run(picks, (), budget)
    assert result.funded == ("B", "C") and result.adjusted == 3900.0           # 39s > 30s
    assert result.reason_of("A") == "Exceeds risk tolerance (needs 60s, 0c left)"


def test_PF6_the_mockup_funds_twelve_rows_in_one_pool_and_the_week_takes_the_capacity_first():
    picks = mockup()
    result = run(picks)
    funded = set(names(result, picks))
    today = {"Copper Rod", "Silver Rod", "Scouting Gloves of the Monkey", "Embossed Leather Gloves", "Linen Cloth"}
    week = {"Mithril Spurs", "Thick Leather", "Transmute: Iron to Gold", "Runed Copper Rod", "Silk Cloth",
            "Bolt of Silk Cloth", "Heavy Grinding Stone"}
    assert funded == today | week and len(result.funded) == 12
    by_id = {p.cand_id: p for p in picks}
    assert sum(by_id[i].gold for i in result.funded if by_id[i].lst == "today") == 5160       # 51s 60c
    assert sum(by_id[i].gold for i in result.funded if by_id[i].lst == "week") == 57_340      # 5g 73s 40c
    assert (result.gold, result.capacity_left) == (62_500, 2440)                            # 6g 25s, 24s 40c left
    assert result.reason_of("Lesser Mana Potion:1") == "Exceeds risk tolerance (needs 45s, 24s 40c left)"


def test_PF7_owned_bars_go_to_the_craft_once_and_the_post_is_claimed():
    bars = Resource(f"own:{BRUGAN}:2840", 6, "Copper Bar")
    post = Pick("post", "post", 1, BRUGAN, "today", 0, 3000.0, 3000.0, "Copper Bar", None, ((bars.rid, 6),))
    craft = Pick("craft", "craft", 1, BRUGAN, "today", 0, 5400.0, 5400.0, "Copper Rod", None, ((bars.rid, 6),))
    result = run([post, craft], [bars], Budget(10_000, ((BRUGAN, 10_000),)))
    assert result.funded == ("craft",)
    assert result.reason_of("post") == "Resources already claimed: 6 Copper Bar (Copper Rod)"
    assert result.net == 5400.0                                               # the bars' value counted once


def test_PF4_two_routes_for_one_window_capacity_fund_only_the_better_one():
    cap = Resource("cap:6217:now", 1, "Copper Rod")
    smelted = Pick("craft:6217:B:2770a/now/now:1", "s", 1, BRUGAN, "today", 500, 2000.0, 2000.0,
                   "Copper Rod, smelted", None, ((cap.rid, 1),))
    bought = Pick("craft:6217:B:2840a/now/now:1", "b", 1, BRUGAN, "today", 2000, 1800.0, 1800.0, "Copper Rod", None,
                  ((cap.rid, 1),))
    linen = [Pick(f"flip:{n}", "flip", n, AMAYA, "today", 200, e, e, "Linen Cloth") for n, e in ((1, 657.0), (2, 310.0))]
    result = run([smelted, bought] + linen, [cap], Budget(10_000, ((AMAYA, 10_000), (BRUGAN, 10_000))))
    assert result.reason_of(bought.cand_id) == "Resources already claimed: Copper Rod capacity (Copper Rod, smelted)"
    assert {"flip:1", "flip:2"} <= set(result.funded)
    only_two = run(linen, (), Budget(300, ((AMAYA, 10_000),)))
    assert only_two.funded == ("flip:1",)                                     # batch 2 never without batch 1


def test_PF10_once_amaya_can_disenchant_the_bracers_they_are_funded_ahead_of_the_transmute():
    picks = mockup(bracers_skill=150)
    result = run(picks)
    order = names(result, picks)
    assert order.index("Grunt's Bracers of Defense") < order.index("Transmute: Iron to Gold")      # 0.40 > 0.34
    assert result.capacity_left == 940                                                            # 9s 40c left
    assert result.reason_of("Lesser Mana Potion:1") == "Exceeds risk tolerance (needs 45s, 9s 40c left)"


def test_PF11_a_higher_return_on_a_smaller_trade_never_wins_just_for_its_ratio():
    x = Pick("X", "X", 1, AMAYA, "today", 500, 1000.0, 1000.0, "X")
    y = Pick("Y", "Y", 1, AMAYA, "today", 10_000, 4000.0, 4000.0, "Y")
    result = run([x, y], (), Budget(10_000, ((AMAYA, 50_000),)))
    assert result.funded == ("Y",)
    assert result.reason_of("X") == "Exceeds risk tolerance (needs 5s, 0c left)"


def test_PF13_the_allocation_fits_the_list_limits_and_reserves_nothing_for_a_hidden_row():
    picks = [Pick(f"t{n:02}", f"t{n:02}", 1, AMAYA, "today", 100, 1000.0 - n, 1000.0 - n, f"T{n}") for n in range(24)]
    picks.append(Pick("w", "w", 1, AMAYA, "week", 100, 10.0, 10.0, "W"))
    result = run(picks, (), Budget(100_000, ((AMAYA, 100_000),)))
    assert len([i for i in result.funded if i.startswith("t")]) == 20 and "w" in result.funded
    assert [result.reason_of(f"t{n}") for n in range(20, 24)] == ["Recommendation limit reached (20 of 20 today)"] * 4
    assert result.gold == 2100 and result.capacity_left == 100_000 - 2100     # the 21 shown rows exactly


def test_PF13_a_full_week_says_this_week():
    picks = [Pick(f"w{n:02}", f"w{n:02}", 1, AMAYA, "week", 1, 10.0, 10.0, "W") for n in range(3)]
    result = run(picks, (), Budget(100, ((AMAYA, 100),), week_limit=2))
    assert result.reason_of("w02") == "Recommendation limit reached (2 of 2 this week)"


def test_PF14_nothing_funded_is_valid_and_the_first_matching_reason_is_given():
    a = accounting([CharacterGold(AMAYA, 143_000), CharacterGold(BRUGAN, 104_000)],
                   [OpenPosition("p", AMAYA, 108_800)], 0.30)
    assert (a.capital_base, a.risk_budget, a.remaining_capacity) == (355_800, 106_740, 0)
    picks = [p for p in mockup() if p.gold > 0]
    result = run(picks, (), Budget(a.capacity_left, a.spendable))
    assert result.funded == () and result.gold == 0 and result.net == 0.0
    assert sel.empty_state(2, a.cash_gold, a.committed_capital, a.risk_budget, False, False, True) == \
        "No new opportunities: risk budget exhausted (10g 67s 40c limit, 10g 88s committed)"
    assert sel.empty_state(0, 0, 0, 0, False, False, False) == "No characters yet"
    assert sel.empty_state(2, 0, 0, 0, False, False, False) == "No new opportunities: no gold in hand"
    assert sel.empty_state(2, 100, 0, 30, True, True, True) == "No new opportunities: recommendation limit reached"
    assert sel.empty_state(2, 100, 0, 30, False, True, True) == "No new opportunities: market data too old"
    assert sel.empty_state(2, 100, 0, 30, False, False, False) == "No new opportunities: nothing pays"
    assert sel.empty_state(2, 100, 0, 30, False, False, True) == "No new opportunities: every candidate is excluded"


def test_PF15_amayas_share_funds_the_same_rows_in_the_same_order():
    picks = [p for p in mockup() if p.char_id == AMAYA]
    result = run(picks, (), Budget(27_640, ((AMAYA, 27_640),)))
    assert names(result, picks) == ["Scouting Gloves of the Monkey", "Embossed Leather Gloves", "Runed Copper Rod",
                                    "Linen Cloth", "Thick Leather", "Bolt of Silk Cloth", "Silk Cloth"]
    assert (result.gold, result.capacity_left) == (25_200, 2440)             # 2g 52s, 24s 40c left


def test_PF16_positions_take_no_slots():
    a = accounting([CharacterGold(AMAYA, 1_000_000)], [OpenPosition(f"p{n}", AMAYA, 1000) for n in range(7)], 0.30)
    picks = [Pick(f"t{n:02}", f"t{n:02}", 1, AMAYA, "today", 100, 50.0, 50.0, f"T{n}") for n in range(20)]
    result = run(picks, (), Budget(a.capacity_left, a.spendable))
    assert len(result.funded) == 20


def test_PF23_brugans_reserved_rows_leave_capacity_not_gold_binding():
    result = run(mockup())
    assert dict(result.reserved)[BRUGAN] == 37_300           # Spurs 3g, transmute 42s, Silver Rod 30s, the rod's 1s
    a = accounting([CharacterGold(AMAYA, 210_000), CharacterGold(BRUGAN, 104_000)],
                   [OpenPosition("p", BRUGAN, 41_800)], 0.30, dict(result.reserved))
    assert (a.spendable_left(BRUGAN), a.available_for(BRUGAN)) == (66_700, 2440)    # 6g 67s, but 24s 40c available
    assert result.reason_of("Lesser Mana Potion:1") == "Exceeds risk tolerance (needs 45s, 24s 40c left)"


def test_PF24_each_failing_check_has_its_status_in_d6_order():
    e = Eligibility
    assert sel.static_reason(e("Enchanting", 1, None, None), 7) == "Required profession unavailable: Enchanting 1"
    assert sel.profession_detail(e("Enchanting", 1, None, None)) == "no character has Enchanting"
    bracers = e("Enchanting", 150, 142, (AMAYA, 142))
    assert sel.static_reason(bracers, 7) == "Required profession unavailable: Enchanting 150"
    assert sel.profession_detail(bracers) == "Amaya has Enchanting 142" and sel.profession_detail(e()) == ""
    assert sel.static_reason(e(untrusted_weeks=1), 7) == "Forecast too uncertain (1 week of data)"
    assert sel.static_reason(e(untrusted_weeks=2), 7) == "Forecast too uncertain (2 weeks of data)"
    assert sel.static_reason(e(price_age_ms=30 * 3_600_000), 7) == "Forecast too stale (prices 30 h old)"
    assert sel.static_reason(e(price_age_ms=80 * 3_600_000), 7) == "Forecast too stale (prices 3 days old)"
    assert sel.static_reason(e(price_age_ms=24 * 3_600_000), 7) is None
    assert sel.static_reason(e(sold_per_day=3.0, planned_units=40), 7) == "Insufficient liquidity: 3/day, 40 planned"
    assert sel.static_reason(e(sold_per_day=2.7, planned_units=18), 7) is None and sel.per_day(2.66) == "2.7"
    assert sel.static_reason(e(missing=(2, "Iron Bar")), 7) == "Missing materials: 2 Iron Bar"
    both = e("Enchanting", 150, 142, (AMAYA, 142), missing=(2, "Iron Bar"))
    assert sel.static_reason(both, 7).startswith("Required profession unavailable")   # the first in D.6's order
    assert sel.static_reason(e("Enchanting", 150, 150), 7) is None


def test_PF24_the_dynamic_checks_say_gold_on_the_account_then_on_the_character():
    buy = Pick("buy", "buy", 1, AMAYA, "today", 16_080, 100.0, 100.0, "Thick Leather")
    poor = run([buy], (), Budget(100_000, ((AMAYA, 10_000), (BRUGAN, 5000))))
    assert poor.reason_of("buy") == "Insufficient gold (needs 1g 60s 80c, all characters hold 1g 50s)"
    split = run([buy], (), Budget(100_000, ((AMAYA, 10_000), (BRUGAN, 300_000))))
    assert split.reason_of("buy") == "Insufficient gold on Amaya (needs 1g 60s 80c, has 1g)"


@pytest.mark.parametrize("s", [s for s in SHARED if s["call"] == "static_reason"], ids=lambda s: f"{s['id']} {s['name']}")
def test_shared_static_reasons(s):
    a = s["args"]
    missing = (a["missing_units"], a["missing_name"]) if a["missing_units"] is not None else None
    e = Eligibility(a["profession"], a["skill"], a["actor_skill"], None, a["untrusted_weeks"], a["price_age_ms"],
                    a["sold_per_day"], a["planned_units"], missing, a.get("window_closes"), a.get("volume_unknown"),
                    a.get("no_listing", False))
    assert sel.static_reason(e, a["horizon_days"]) == s["expect"], s["why"]


@pytest.mark.parametrize("s", [s for s in SHARED if s["call"] == "select"], ids=lambda s: f"{s['id']} {s['name']}")
def test_shared_portfolio_scenarios(s):
    picks, resources, budget = scenario_inputs(s)
    result = run(picks, resources, budget)
    assert list(result.funded) == s["expect"]["funded"], s["why"]
    assert dict(result.reasons) == s["expect"]["reasons"], s["why"]
    assert result.capacity_left == s["expect"]["capacity_left"], s["why"]


def scenario_inputs(s: dict) -> tuple[list[Pick], list[Resource], Budget]:
    """A shared scenario's inputs as engine records.

    Args:
        s: The scenario.

    Returns:
        (picks, resources, budget).
    """
    a = s["args"]
    picks = [Pick(p["cand_id"], p["opp"], p["batch"], p["char_id"], p["lst"], p["gold"], p["adjusted"], p["net"],
                  p["name"], p["reason"], tuple((c[0], c[1]) for c in p["claims"])) for p in a["picks"]]
    resources = [Resource(r["rid"], r["limit"], r["name"]) for r in a["resources"]]
    b = a["budget"]
    return picks, resources, Budget(b["capacity"], tuple((c, g) for c, g in b["spendable"]), b["today_limit"],
                                    b["week_limit"], b["swap_candidates"])


def random_pool(rng):
    """One generated selection case (PF25, PF27).

    Args:
        rng: The seeded Random.

    Returns:
        (picks, resources, budget).
    """
    chars = ["A-R", "B-R", "C-R"][:rng.randint(1, 3)]
    resources = [Resource(f"own:A-R:{k}", rng.randint(0, 6), f"Item {k}") for k in range(3)]
    resources += [Resource(f"cap:{k}:now", rng.randint(1, 5), f"Item {k}") for k in range(3)]
    picks = []
    for o in range(rng.randint(1, 12)):
        lst, char = rng.choice(["today", "week"]), rng.choice(chars)
        for b in range(1, rng.randint(1, 3) + 1):
            claims = tuple(sorted({(r.rid, rng.randint(1, 3)) for r in rng.sample(resources, rng.randint(0, 2))}))
            claims = tuple(dict(claims).items())
            reason = "Forecast too uncertain (1 week of data)" if rng.random() < 0.1 else None
            gold = rng.choice([0, rng.randint(1, 500)])
            adj = rng.uniform(-10, 400)
            picks.append(Pick(f"o{o}:{b}", f"o{o}", b, char, lst, gold, adj, adj * 1.2, f"O{o}", reason, claims))
    budget = Budget(rng.randint(0, 1500), tuple((c, rng.randint(0, 900)) for c in chars),
                    rng.randint(1, 4), rng.randint(1, 4))
    return picks, resources, budget


def test_PF25_funded_rows_stay_within_capacity_gold_resources_and_limits():
    for n, (picks, resources, budget) in enumerate(cases(SEED_PF25, random_pool)):
        result = sel.select(picks, resources, budget)
        sel.validate(picks, resources, budget, result)                       # raises on any D.5 violation
        by_id = {p.cand_id: p for p in picks}
        funded = [by_id[i] for i in result.funded]
        assert sum(p.gold for p in funded) <= budget.capacity, (SEED_PF25, n)
        for r in resources:
            assert sum(u for p in funded for rid, u in p.claims if rid == r.rid) <= r.limit, (SEED_PF25, n)
        assert all(p.reason is None for p in funded), (SEED_PF25, n)
        net = 0.0
        for p in funded:
            net += p.net
        assert result.net == net, (SEED_PF25, n)                                # summed in funding order


def test_PF27_the_same_candidates_in_any_order_give_the_same_selection():
    rng = random.Random(SEED_PF27)
    for n, (picks, resources, budget) in enumerate(cases(SEED_PF27, random_pool)):
        first = sel.select(picks, resources, budget)
        shuffled = list(picks)
        rng.shuffle(shuffled)
        assert sel.select(shuffled, list(reversed(resources)), budget) == first, (SEED_PF27, n)
        assert repr(sel.select(picks, resources, budget)) == repr(first), (SEED_PF27, n)


def test_validate_refuses_every_d5_violation():
    bars = Resource("own:A:1", 1, "Bar")
    p = Pick("p", "p", 1, AMAYA, "today", 100, 10.0, 10.0, "P", None, (("own:A:1", 1),))
    q = Pick("q", "q", 1, AMAYA, "today", 100, 10.0, 10.0, "Q", None, (("own:A:1", 1),))
    budget = Budget(1000, ((AMAYA, 1000),), 1, 1)
    good = sel.select([p, q], [bars], budget)
    bad = [replace(good, capacity_left=0), replace(good, funded=("p", "q"), reasons=()),
           replace(good, net=99.0), replace(good, reasons=good.reasons + (("p", "x"),))]
    for broken in bad:
        with pytest.raises(sel.SelectionError):
            sel.validate([p, q], [bars], budget, broken)
    with pytest.raises(sel.SelectionError):
        sel.validate([p], [bars], Budget(1000, ((AMAYA, 50),)), replace(good, funded=("p",), reasons=()))
    with pytest.raises(sel.SelectionError):
        sel.validate([replace(p, reason="x")], [bars], budget, replace(good, reasons=()))
    two = [Pick("a:1", "a", 1, AMAYA, "today", 0, 1.0), Pick("a:2", "a", 2, AMAYA, "today", 0, 1.0)]
    with pytest.raises(sel.SelectionError):
        sel.validate(two, [], budget, sel.Selection(("a:2",), (("a:1", "x"),), 1000, (), 1.0, 0.0, 0))
    rows = [Pick("a", "a", 1, AMAYA, "today", 0, 1.0), Pick("b", "b", 1, AMAYA, "today", 0, 1.0)]
    with pytest.raises(sel.SelectionError):
        sel.validate(rows, [], budget, sel.Selection(("a", "b"), (), 1000, (), 2.0, 0.0, 0))


def test_select_refuses_malformed_input():
    p = Pick("p", "p", 1, AMAYA, "today", 0, 1.0)
    with pytest.raises(sel.SelectionError):
        sel.select([p, p], [], Budget(0, ()))
    with pytest.raises(sel.SelectionError):
        sel.select([replace(p, batch=2)], [], Budget(0, ()))
    with pytest.raises(sel.SelectionError):
        sel.select([replace(p, lst="later")], [], Budget(0, ()))
    with pytest.raises(sel.SelectionError):
        sel.select([replace(p, claims=(("own:x", 1),))], [], Budget(0, ()))


def test_a_claim_with_no_holder_and_ah_units_are_named():
    ah = Resource("ah:2589", 10, "Linen Cloth")
    big = Pick("big", "big", 1, AMAYA, "today", 0, 1.0, 1.0, "Linen Cloth", None, (("ah:2589", 20),))
    small = Pick("s", "s", 1, AMAYA, "today", 0, 5.0, 5.0, "Wool", None, (("ah:2589", 8),))
    result = run([big, small], [ah], Budget(0, ((AMAYA, 0),)))
    assert result.reason_of("big") == "Resources already claimed: 20 Linen Cloth on the AH (Wool)"
    alone = run([big], [ah], Budget(0, ((AMAYA, 0),)))
    assert alone.reason_of("big") == "Resources already claimed: 20 Linen Cloth on the AH"


def test_later_batches_inherit_their_first_batchs_reason_and_ties_sort_by_batch():
    picks = [Pick(f"o:{b}", "o", b, AMAYA, "today", 100, 10.0, 10.0, "O") for b in range(1, 12)]
    order = [p.batch for p in sel.fill_order(picks)]
    assert order == list(range(1, 12))                                        # "o:10" never before "o:9"
    result = run(picks, (), Budget(250, ((AMAYA, 1000),)))
    assert result.funded == ("o:1", "o:2")
    assert result.reason_of("o:4") == result.reason_of("o:3") == "Exceeds risk tolerance (needs 1s, 50c left)"


def test_a_swap_is_kept_only_when_the_total_rises_and_ties_go_to_the_earlier_id():
    a = Pick("a", "a", 1, AMAYA, "today", 100, 100.0, 100.0, "A")
    b = Pick("b", "b", 1, AMAYA, "today", 200, 150.0, 150.0, "B")
    c = Pick("c", "c", 1, AMAYA, "today", 200, 150.0, 150.0, "C")
    result = run([c, b, a], (), Budget(200, ((AMAYA, 1000),)))
    assert result.funded == ("b",)                                            # b and c tie; b is the earlier id
    same = run([a, replace(b, adjusted=100.0, gold=150)], (), Budget(200, ((AMAYA, 1000),)))
    assert same.funded == ("a",)                                              # 100 = 100: no swap


def test_the_swap_pruning_is_exact_it_never_changes_the_result(monkeypatch):
    pools = cases(SEED_PF27 + 1, random_pool)
    pruned = [sel.select(*pool[0:1], pool[1], pool[2]) for pool in pools]
    monkeypatch.setattr(sel, "EPSILON", float("inf"))                         # every swap is tried in full (Lua's way)
    monkeypatch.setattr(sel, "_lists_full_after", lambda *_a: False)           # and every refill walks every batch
    assert [sel.select(*pool[0:1], pool[1], pool[2]) for pool in pools] == pruned


def test_a_swap_that_removes_an_opportunitys_later_batch_skips_it_in_the_pass():
    a = [Pick(f"a:{b}", "a", b, AMAYA, "today", 100, 60.0, 60.0, "A") for b in (1, 2)]
    x = Pick("x", "x", 1, AMAYA, "today", 250, 140.0, 140.0, "X")       # 0.56 per copper, after A's 0.60
    result = run(a + [x], (), Budget(250, ((AMAYA, 1000),)))
    assert result.funded == ("x",) and result.reason_of("a:2") == result.reason_of("a:1")


def test_reasons_refuse_an_allocation_that_left_a_fitting_batch_out():
    p = Pick("p", "p", 1, AMAYA, "today", 0, 1.0)
    with pytest.raises(sel.SelectionError):
        sel._reasons(sel._State(0, {AMAYA: 0}, {}), [p], {}, Budget(0, ((AMAYA, 0),)), 0)


def test_validate_refuses_a_resource_claimed_beyond_its_limit():
    bars = Resource("own:A:1", 1, "Bar")
    p = Pick("p", "p", 1, AMAYA, "today", 0, 1.0, 1.0, "P", None, (("own:A:1", 1),))
    q = Pick("q", "q", 1, AMAYA, "today", 0, 1.0, 1.0, "Q", None, (("own:A:1", 1),))
    with pytest.raises(sel.SelectionError, match="claimed beyond its limit"):
        sel.validate([p, q], [bars], Budget(0, ((AMAYA, 0),)), sel.Selection(("p", "q"), (), 0, (), 2.0, 2.0, 0))
