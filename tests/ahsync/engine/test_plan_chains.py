"""Tests for ahsync/engine/plan_chains.py: how a chain becomes one row on Today and Week (SPEC §3.4B, §3.4C)."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine import plan_chains
from ahsync.engine.plan_chains import ViewAction

HOUR, MINUTE = 3_600_000, 60_000
NOW = 345_600_000 + 3000 * 7 * 86_400_000 + 10 * HOUR        # a Monday, 10:00 UTC
BAR, ROD = 2840, 6217


def act(action_id: str, kind: str, net: float = 0.0, **changes) -> ViewAction:
    """A pending chain action of Amaya's under chain "b", at NOW."""
    base = ViewAction(action_id, "b", kind, "Amaya-G", BAR, 6, 1000, NOW, net, None, "pending", in_chain=True)
    return replace(base, **changes)


def test_a_chain_without_a_profit_step_is_carried_by_its_last_craft_and_without_a_craft_by_nothing():
    first, last = act("k1", "craft", price=None), act("k2", "craft", price=None, at_ms=NOW + MINUTE)
    assert plan_chains.carrier_of([act("b", "buy", parent_id=None), first, last]) is last
    assert plan_chains.carrier_of([act("b", "buy", parent_id=None), act("m", "mail")]) is None


def test_steps_fold_only_into_a_carrier_of_the_same_character_shown_in_the_same_view():
    buy, craft = act("b", "buy", parent_id=None), act("k", "craft", 500, key=ROD, qty=3, price=None)
    chains = plan_chains.by_chain([buy, craft])
    assert plan_chains.folded([buy, craft], chains) == {"b": "k"}
    assert plan_chains.folded([buy], chains) == {}                                  # its craft isn't in this view
    theirs = replace(buy, char_id="Brugan-G")
    assert plan_chains.folded([theirs, craft], plan_chains.by_chain([theirs, craft])) == {}
    alone = replace(buy, in_chain=False, parent_id=None)
    assert plan_chains.folded([alone, craft], chains) == {}


def test_the_sale_line_says_what_happens_to_what_the_chain_buys_or_makes():
    flip = act("f", "buy", 300, parent_id=None)
    now_post = act("p", "post", 300, price=1200)
    assert plan_chains.sale_line(flip, [flip, now_post], 0) == "then: relist now"
    craft = act("k", "craft", 300, key=ROD, price=None)
    assert plan_chains.sale_line(craft, [craft, now_post], 0) == "then: post now"
    assert plan_chains.sale_line(craft, [craft, act("d", "de", key=ROD)], 0) == "then: disenchant it"
    assert plan_chains.sale_line(craft, [craft], 0) is None


def test_row_money_a_buy_risks_its_cost_a_post_nothing():
    assert plan_chains.row_money(act("b", "buy", price=38, qty=20), []) == ("38c", "7s 60c", 760)
    assert plan_chains.row_money(act("p", "post", price=45, qty=5), []) == ("45c", "2s 25c", 0)
    assert plan_chains.row_money(act("m", "mail", price=None), []) == ("—", "—", 0)


def test_the_craft_pages_wording_is_shared():
    assert plan_chains.buy_line(76, "Empty Vial", True, 1520) == "buy 76 Empty Vial from a vendor · 15s 20c"
    assert plan_chains.first_line(21, "Tin Bar") == "craft 21 Tin Bar first"
    assert plan_chains.short_name("Monstrscondm-Grommash") == "Monstrscondm"
