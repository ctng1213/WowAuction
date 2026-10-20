"""Tests for ahsync/engine/craft_candidates.py: Craft, Multistage and Disenchant candidates (SPEC §3.4D.3)."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine import craft, craft_candidates as crafting
from ahsync.engine.candidates import DeInfo, Opportunity, Universe
from ahsync.engine.craft_plan import CraftWorld
from ahsync.engine.plan import Lot
from ahsync.engine.selection import profession_detail, static_reason
from ahsync.engine.week import WeekInputs, prepare
from tests.support import plans
from tests.support.portfolio_worlds import (BAR, DUST, GLOVES, NAMES, NOW, ROD, ROD_RECIPE, RUNED, S,
                                            empty_world, facts, gen_inputs, opportunity, universe)


def test_PF2_every_disenchantable_item_is_a_candidate_whether_or_not_anyone_enchants():
    gloves = DeInfo(1, 1425.0, ((DUST, 1.2), (10938, 0.3)), ((800, 1),), 80)
    u = universe(empty_world(), gen_inputs(de={GLOVES: gloves}))
    opp = opportunity(u, f"disenchant:{GLOVES}:")
    [b] = opp.batches
    assert (b.candidate.strategy, b.candidate.expected_net, b.candidate.gold_needed) == ("disenchant", 625.0, 800)
    assert static_reason(b.eligibility, 7) == "Required profession unavailable: Enchanting 1"
    assert profession_detail(b.eligibility) == "no character has Enchanting"
    result = plans.plan(empty_world(), NOW, gold={"Amaya-G": plans.RICH}, de={GLOVES: gloves})
    assert result.actions == [] and result.selection.net == 0.0                # in no total and no action
    [alt] = result.alternatives
    assert alt.reason == "Required profession unavailable: Enchanting 1"
    able = plans.plan(empty_world(), NOW, gold={"Amaya-G": plans.RICH}, de={GLOVES: gloves},
                      skills={("Amaya-G", "Enchanting"): 1})
    assert [(a.kind, a.key, a.qty) for a in able.actions] == [("buy", GLOVES, 1), ("de", GLOVES, 1)]
    assert able.actions[0].net == 625.0 and (able.actions[1].at_ms, able.actions[1].net) == (NOW + 60_000, 0.0)


def test_a_disenchant_stops_at_the_first_auction_that_loses_and_claims_its_mats_capacity():
    gloves = DeInfo(1, 1425.0, ((DUST, 1.2),), ((800, 1), (1200, 1), (1500, 1)), 80)
    w = empty_world(facts={DUST: facts(DUST, 600, supply_now=200)})
    opp = opportunity(universe(w, gen_inputs(de={GLOVES: gloves})), f"disenchant:{GLOVES}:")
    assert [b.candidate.expected_net for b in opp.batches] == [625.0, 225.0]   # the 15s auction would lose 75c
    assert (f"cap:{DUST}:now", 2) in opp.batches[0].claims                      # 1.2 dust, rounded up
    assert universe(w, gen_inputs(de={GLOVES: replace(gloves, auctions=((2000, 1),))})).opportunities == []



def test_PF4_two_routes_to_one_output_are_alternatives_only_one_is_funded():
    ore = 2770
    smelt = craft.Recipe(2657, BAR, 1, 1, ((ore, 1),), ("Brugan-G",))
    market = craft.Market({BAR: [(1000, 40.0)], ore: [(100, 40.0)]}, {}, {}, {})
    world = CraftWorld((ROD_RECIPE, smelt), {ROD: [ROD_RECIPE], BAR: [smelt]}, market,
                       {ROD: facts(ROD, 4000, supply_now=200)})
    u = universe(empty_world(world=world, facts={ROD: facts(ROD, 4000, supply_now=200)}))
    routes = [o for o in u.opportunities if f":{ROD}:" in o.opp]
    assert sorted(o.opp.split(":")[0] for o in routes) == ["craft", "multistage"]          # bars bought, or smelted
    shared = f"cap:{ROD}:route2663"
    assert u.resources[shared].limit == 1 and all((shared, 1) in o.batches[0].claims for o in routes)
    result = plans.plan(empty_world(world=world, facts={ROD: facts(ROD, 4000, supply_now=200)}), NOW, names=NAMES)
    assert len({a.chain for a in result.actions if a.kind == "craft" and a.key == ROD}) == 1
    assert any(a.reason.startswith("Resources already claimed: Copper Rod capacity (") for a in result.alternatives)


def test_R28_a_craft_posted_now_into_an_empty_book_on_thin_data_has_no_grounded_price():
    def rods(rod) -> WeekInputs:
        """Brugan's 6 bars and the Copper Rod recipe, the rod's facts as given."""
        market = craft.Market({BAR: []}, {}, {BAR: 6.0}, {})
        world = CraftWorld((ROD_RECIPE,), {ROD: [ROD_RECIPE]}, market, {ROD: rod})
        return WeekInputs((Lot("Brugan-G", BAR, 6),), {BAR: facts(BAR, 1000, windows=()), ROD: rod}, world)
    thin = facts(ROD, 4000, supply_now=200, windows=(), closes_30d=4)          # nobody lists a rod: book empty
    made = opportunity(universe(rods(thin)), f"craft:{ROD}:")
    assert {static_reason(b.eligibility, 7) for b in made.batches} == {"No listing left to price against"}
    listed = replace(thin, book=((4500, 1, 1, 2),))                           # a rod at 45s grounds the undercut
    assert {static_reason(b.eligibility, 7) for b in opportunity(universe(rods(listed)), f"craft:{ROD}:").batches} \
        == {None}
    stock = opportunity(universe(rods(thin)), f"sell_stock:{BAR}:")           # stock you own still posts
    assert {static_reason(b.eligibility, 7) for b in stock.batches} == {None}


def test_PF7_your_bars_are_offered_to_both_the_post_and_the_craft_and_go_to_the_craft():
    market = craft.Market({BAR: []}, {}, {BAR: 6.0}, {})
    world = CraftWorld((ROD_RECIPE,), {ROD: [ROD_RECIPE]}, market, {ROD: facts(ROD, 4000, supply_now=200)})
    w = WeekInputs((Lot("Brugan-G", BAR, 6),), {BAR: facts(BAR, 1000, windows=()), ROD: facts(ROD, 4000)}, world)
    u = universe(w)
    post, made = opportunity(u, f"sell_stock:{BAR}:"), opportunity(u, f"craft:{ROD}:")
    assert all((f"own:Brugan-G:{BAR}", 1) in b.claims for b in post.batches)
    assert sum(units for b in made.batches for rid, units in b.claims if rid == f"own:Brugan-G:{BAR}") == 6
    result = plans.plan(w, NOW, names=NAMES)
    assert sum(a.qty for a in result.actions if a.kind == "craft") == 3        # the bars go to 3 rods
    assert not [a for a in result.actions if a.key == BAR and a.kind == "post"]
    assert {a.reason for a in result.alternatives} == {"Resources already claimed: 1 Copper Bar (Copper Rod)"}


def test_PF12_a_crafting_chain_with_its_mail_is_one_multistage_candidate_funded_whole():
    rune = craft.Recipe(7421, RUNED, 1, 1, ((ROD, 1), (DUST, 1)), ("Amaya-G",))
    market = craft.Market({BAR: [(1000, 40.0)], DUST: [(300, 10.0)]}, {}, {}, {})
    world = CraftWorld((ROD_RECIPE, rune), {ROD: [ROD_RECIPE], RUNED: [rune]}, market,
                       {ROD: facts(ROD, 1500), RUNED: facts(RUNED, 9000)})
    w = empty_world(world=world, facts={ROD: facts(ROD, 1500), RUNED: facts(RUNED, 9000)})
    opp = opportunity(universe(w), f"multistage:{RUNED}:")
    assert opp.batches[0].candidate.char_id == "Amaya-G"
    chain = opp.realize(1)
    assert [a.kind for a in chain] == ["buy", "buy", "craft", "mail", "craft", "post"]
    assert sum(1 for a in chain if a.net) == 2 and {a.net for a in chain if a.net} == {opp.batches[0].candidate.expected_net}
    funded = plans.plan(w, NOW)
    runed = [a for a in funded.actions if a.chain == "craft:7421"]
    assert [a.kind for a in runed][:4] == ["buy", "buy", "craft", "mail"]      # every step, never one alone


def test_owned_mats_are_split_crafter_first_then_by_biggest_holder():
    held = {("Amaya-G", BAR): 2.0, ("Brugan-G", BAR): 5.0, ("Cara-G", BAR): 5.0}
    assert crafting.owned_split(10, BAR, "Amaya-G", held) == {"Amaya-G": 2, "Brugan-G": 5, "Cara-G": 3}
    assert crafting.owned_split(0, BAR, "Amaya-G", held) == {}


def test_routes_are_exclusive_only_when_there_are_two():
    u = Universe([], {})
    one = Opportunity("a", "A", (), lambda n: [])
    assert crafting.exclusive([one], "cap:1:route1", 1, gen_inputs(), u) == [one] and u.resources == {}


def test_an_opportunity_seen_twice_is_kept_once():
    twin = craft.Recipe(2664, ROD, 1, 1, ((BAR, 2),), ("Brugan-G",))          # the same product from the same mats
    market = craft.Market({BAR: [(1000, 40.0)]}, {}, {}, {})
    world = CraftWorld((ROD_RECIPE, twin), {ROD: [ROD_RECIPE, twin]}, market, {ROD: facts(ROD, 4000, supply_now=200)})
    u = universe(empty_world(world=world, facts={ROD: facts(ROD, 4000)}))
    ids = [b.candidate.cand_id for o in u.opportunities for b in o.batches]
    assert len(ids) == len(set(ids)) and len([o for o in u.opportunities if f":{ROD}:" in o.opp]) == 1


def test_a_lower_ranked_recipe_finds_its_mats_taken_by_a_better_one():
    other = craft.Recipe(2700, RUNED, 1, 1, ((BAR, 2),), ("Brugan-G",))
    market = craft.Market({BAR: [(1000, 2.0)]}, {}, {}, {})                  # bars for one craft only
    world = CraftWorld((ROD_RECIPE, other), {ROD: [ROD_RECIPE], RUNED: [other]}, market,
                       {ROD: facts(ROD, 4000), RUNED: facts(RUNED, 3000)})
    u = universe(empty_world(world=world, facts={ROD: facts(ROD, 4000), RUNED: facts(RUNED, 3000)}))
    assert [o.opp.split(":")[1] for o in u.opportunities if o.opp.startswith("craft")] == [str(ROD)]


def test_an_intermediate_claims_its_own_mats_owned_or_bought_but_never_vendor_ones():
    ore, flux = 2770, 2880
    smelt = craft.Recipe(2657, BAR, 1, 1, ((ore, 1), (flux, 1)), ("Brugan-G",))
    world = prepare(empty_world(world=CraftWorld((smelt,), {BAR: [smelt]}, craft.Market({}, {flux: 10}, {}, {}), {})),
                    NOW, S)
    option = craft.CraftOption(2663, "Amaya-G", crafted={BAR: craft.Crafted(5.0, 0.0, "Brugan-G", 2657,
                                                                            frozenset({"owned"}))},
                               steps=[craft.CraftStep(0.0, (), (), ((BAR, 2.0),))])
    owned, ah = crafting.mats_used(option, 1, world)
    assert owned == {("Brugan-G", ore): 2.0, ("Brugan-G", flux): 2.0} and ah == {}
    bought = replace(option, crafted={BAR: replace(option.crafted[BAR], raw=frozenset({"ah", "vendor"}))})
    assert crafting.mats_used(bought, 1, world) == ({}, {ore: 2.0})                # the flux comes from a vendor


def test_reusing_a_ranking_option_whose_mats_nobody_took_changes_nothing(monkeypatch):
    other = craft.Recipe(2700, RUNED, 1, 1, ((DUST, 1),), ("Brugan-G",))      # its dust is nobody else's
    market = craft.Market({BAR: [(1000, 40.0)], DUST: [(300, 10.0)]}, {}, {}, {})
    world = CraftWorld((ROD_RECIPE, other), {ROD: [ROD_RECIPE], RUNED: [other]}, market,
                       {ROD: facts(ROD, 4000, supply_now=200), RUNED: facts(RUNED, 3000, supply_now=200)})
    w = empty_world(world=world, facts={ROD: facts(ROD, 4000, supply_now=200), RUNED: facts(RUNED, 3000, supply_now=200)})
    view = lambda u: [(b.candidate.cand_id, b.candidate.expected_net, b.claims)                      # noqa: E731
                      for o in u.opportunities for b in o.batches]
    reused = view(universe(w))
    monkeypatch.setattr(crafting, "mat_closure", lambda *_a: set(range(100_000)))      # never reuse
    assert view(universe(w)) == reused and len(reused) > 2


def test_a_recipes_mat_closure_follows_your_recipes_to_the_depth_limit():
    smelt = craft.Recipe(2657, BAR, 1, 1, ((2770, 1), (DUST, 1)), ("Brugan-G",))
    rod = craft.Recipe(2663, ROD, 1, 1, ((BAR, 2), (DUST, 1)), ("Brugan-G",))
    world = CraftWorld((rod, smelt), {ROD: [rod], BAR: [smelt]}, craft.Market({}, {}, {}, {}), {})
    assert crafting.mat_closure(rod, world, 3) == {BAR, DUST, 2770}           # dust reached twice, counted once
    assert crafting.mat_closure(rod, world, 1) == {BAR, DUST}                 # no deeper than the limit
