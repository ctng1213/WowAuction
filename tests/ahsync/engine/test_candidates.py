"""Tests for ahsync/engine/candidates.py: Sell stock, Flip and Buy for later candidates (SPEC §3.4D.3)."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine import candidates as cand, craft, plan
from ahsync.engine.candidates import DeInfo, Universe
from ahsync.engine.craft_plan import CraftWorld
from ahsync.engine.plan import Lot, PlanWindow
from ahsync.engine.selection import Eligibility, static_reason
from ahsync.engine.timing import adjusted, premium_micro, wait_hundredths
from ahsync.engine.week import WeekInputs, prepare
from tests.support import plans
from tests.support.portfolio_worlds import (BAR, DAY, ESSENCE, HOUR, LINEN, MONDAY, NOW, RUN, S, SUN, WED,
                                            empty_world, facts, gen_inputs, linen_book, opportunity, universe)


def test_PF4_a_flips_batches_end_at_the_first_that_loses_and_each_needs_the_one_before():
    opp = opportunity(universe(empty_world(facts={LINEN: linen_book()})), f"flip:{LINEN}:")
    assert [b.candidate.batch for b in opp.batches] == [1, 2]                  # the 12c stack doesn't pay
    first, second = (b.candidate for b in opp.batches)
    assert first.expected_net > 0 and second.expected_net > 0                  # relisted under 8c, then under 12c
    gold = first.gold_needed                                                   # 1s for the stack, 1c of deposit
    funded = plans.plan(empty_world(facts={LINEN: linen_book()}), NOW, gold={"Amaya-G": gold})
    assert [a.qty for a in funded.actions if a.kind == "buy"] == [20]          # batch 1 only
    assert [a.reason for a in funded.alternatives] == ["Insufficient gold (needs 1s 61c, all characters hold 1s 1c)"]
    assert second.gold_needed == 161


def test_PF20_a_buy_for_later_needs_its_selling_window_to_be_a_pattern():
    how = [1.0] * 168
    how[WED * 24 + 21], how[SUN * 24 + 19], how[19] = 0.8, 1.3, 1.3
    monday = PlanWindow(17, 3, 19, 6)                                # its best window, 6 weeks, but before the buy
    essence = facts(ESSENCE, 1000, how=tuple(how), ref_stack=4,
                    windows=(monday, PlanWindow(SUN * 24 + 19, 3, SUN * 24 + 19, 1)),
                    buy_windows=(PlanWindow(WED * 24 + 20, 3, WED * 24 + 21, 4),), supply_now=40, post_price=None)
    w = empty_world(facts={ESSENCE: essence})
    opp = opportunity(universe(w), f"buy_later:{ESSENCE}:")
    first = opp.batches[0].candidate
    assert (first.buy_ms, first.gold_buys) == (MONDAY + 2 * DAY + 21 * HOUR, 4 * 800)          # 4 at the Wed slot price
    assert first.route == f"-/h{(MONDAY + 2 * DAY + 21 * HOUR) // HOUR}/h{(MONDAY + 6 * DAY + 19 * HOUR) // HOUR}"
    assert static_reason(opp.batches[0].eligibility, 7) == "Forecast too uncertain (1 week of data)"
    pattern = replace(essence, windows=(monday, PlanWindow(SUN * 24 + 19, 3, SUN * 24 + 19, 3)))
    trusted = opportunity(universe(empty_world(facts={ESSENCE: pattern})), f"buy_later:{ESSENCE}:")
    assert static_reason(trusted.batches[0].eligibility, 7) is None
    buy, post = trusted.realize(1)
    assert (buy.kind, buy.at_ms, buy.chain, post.window_start_ms) == ("buy", first.buy_ms, buy.chain,
                                                                     MONDAY + 6 * DAY + 19 * HOUR)


def test_PF20_trust_and_time_both_apply_to_owned_stock():
    opts = [plan.PostOption(NOW, (0,), 100, None, 999, 8), plan.PostOption(NOW, (0,), 110, (MONDAY + 5 * DAY + 18 * HOUR,
                                                                                                  0, 0), 999, 24, 1)]
    timed = cand.timed_value(opts, RUN)
    assert plan.choose_option(opts, [(0, 100.0, 1.0), (1, 110.0, 1.0)], S, timed)[0] == 0      # 110 < 112.5: trust
    trusted = [opts[0], replace(opts[1], weeks=6)]
    late = cand.timed_value(trusted, RUN)
    assert plan.choose_option(trusted, [(0, 100.0, 1.0), (1, 101.0, 1.0)], S)[0] == 1          # trusted, no premium
    assert plan.choose_option(trusted, [(0, 100.0, 1.0), (1, 101.0, 1.0)], S, late)[0] == 0    # 101 ÷ 1.4155 < 100
    hundredths = wait_hundredths(NOW, MONDAY + 5 * DAY + 18 * HOUR)
    assert hundredths == 531 and round(adjusted(101, premium_micro(hundredths, 0.15, 0.05, 8)), 1) == 71.4


def test_sell_stock_offers_each_posted_stack_and_the_rest_is_a_hold_or_a_sale():
    w = WeekInputs((Lot("Amaya-G", BAR, 3), Lot("Brugan-G", BAR, 1)), {BAR: facts(BAR, 1000, supply_now=2)},
                   CraftWorld((), {}, craft.Market({}, {}, {}, {}), {}))
    u = universe(w)
    amaya = opportunity(u, f"sell_stock:{BAR}:Amaya-G")
    assert [b.candidate.out_qty for b in amaya.batches] and amaya.realize(1)[0].kind == "post"
    world = prepare(w, NOW, S)
    rest = cand.residual(world, {("Amaya-G", BAR): 1}, RUN)
    assert [(a.kind, a.char_id, a.qty) for a in rest] == [("hold", "Amaya-G", 2), ("hold", "Brugan-G", 1)]
    sold = replace(world, vendor_sold=frozenset({BAR}))
    assert cand.residual(sold, {}, RUN) == []                                  # a vendor-sold item is never held


def test_flip_generation_needs_a_character_and_buys_for_the_richest():
    w = empty_world(facts={LINEN: linen_book()})
    assert universe(w, gen_inputs(chars=())).opportunities == []
    gi = replace(gen_inputs(), spendable={"Amaya-G": 10, "Brugan-G": 20})
    assert cand.flip_char(gi) == "Brugan-G"
    assert opportunity(universe(w, gi), f"flip:{LINEN}:").batches[0].candidate.char_id == "Brugan-G"
    low = replace(linen_book(), cur=5, p10=6, windows=(PlanWindow(SUN * 24 + 17, 3, SUN * 24 + 18, 1),),
                  how=tuple(3.0 if h == SUN * 24 + 18 else 1.0 for h in range(168)))
    later = opportunity(universe(empty_world(facts={LINEN: low})), f"buy_later:{LINEN}:")
    assert static_reason(later.batches[0].eligibility, 7) == "Forecast too uncertain (1 week of data)"


def test_buy_for_later_is_left_out_when_nothing_pays_or_no_window_follows():
    how = [1.0] * 168
    how[WED * 24 + 21] = 0.99
    flat = facts(ESSENCE, 1000, how=tuple(how), ref_stack=4, buy_windows=(PlanWindow(WED * 24 + 20, 3, WED * 24 + 21, 4),),
                 post_price=None)
    assert not [o for o in universe(empty_world(facts={ESSENCE: flat})).opportunities if o.opp.startswith("buy_later")]
    assert universe(empty_world(facts={ESSENCE: flat}), gen_inputs(chars=())).opportunities == []


def test_a_universe_resource_keeps_its_smallest_limit():
    u = Universe([], {})
    u.add_resource("cap:1:now", 5, "A")
    u.add_resource("cap:1:now", 3, "A")
    u.add_resource("cap:1:now", 9, "A")
    assert u.resources["cap:1:now"].limit == 3
    assert cand.sale_ms(plan.PostOption(NOW, (), 1, None, 1, 8), NOW) == NOW
    assert Eligibility().profession is None


def test_a_buy_window_already_under_way_is_bought_next_week():
    how = [1.0] * 168
    how[10], how[19] = 0.7, 1.3                                                # cheapest Mon 10:00, before now
    item = facts(ESSENCE, 1000, how=tuple(how), ref_stack=4, windows=(PlanWindow(17, 3, 19, 6),),
                 buy_windows=(PlanWindow(9, 3, 10, 6),), supply_now=40, post_price=None)
    run = replace(RUN, s=replace(S, horizon_days=8))                          # long enough to see next Monday
    later = [o for o in universe(empty_world(facts={ESSENCE: item}), run=run).opportunities
             if o.opp.startswith("buy_later")]
    assert [o.batches[0].candidate.buy_ms for o in later] == [MONDAY + 7 * DAY + 10 * HOUR]


def test_an_item_with_no_listing_has_no_ah_resource():
    u = universe(empty_world(facts={LINEN: facts(LINEN, 14, book=None, cur_qty=None, windows=())}))
    assert f"ah:{LINEN}" not in u.resources


def test_a_buy_for_later_that_would_pay_on_price_but_never_sells_is_left_out():
    how = [1.0] * 168
    how[WED * 24 + 21], how[SUN * 24 + 19] = 0.8, 1.3
    unsold = facts(ESSENCE, 1000, how=tuple(how), ref_stack=4, sold_per_day=0.0, post_price=None,
                   windows=(PlanWindow(SUN * 24 + 19, 3, SUN * 24 + 19, 6),),
                   buy_windows=(PlanWindow(WED * 24 + 20, 3, WED * 24 + 21, 6),), supply_now=40)
    assert not [o for o in universe(empty_world(facts={ESSENCE: unsold})).opportunities
                if o.opp.startswith("buy_later")]                              # 1.3 × 0.95 pays 20%, but P is 0


def test_PF28_generation_leaves_out_young_windows_stale_prices_and_buys_beyond_the_pool():
    how = [1.0] * 168
    how[WED * 24 + 21], how[SUN * 24 + 19] = 0.8, 1.3
    young = facts(LINEN, 1000, how=tuple(how), ref_stack=4, windows=(PlanWindow(SUN * 24 + 19, 3, SUN * 24 + 19, 2),),
                  buy_windows=(PlanWindow(WED * 24 + 20, 3, WED * 24 + 21, 6),), supply_now=40, post_price=None)
    assert not [o for o in universe(empty_world(facts={LINEN: young})).opportunities if o.opp.startswith("buy_later")]
    wool, silk, bar, ore = 2592, 4306, 2840, 2771
    books = {wool: linen_book(key=wool), silk: linen_book(key=silk),
             bar: linen_book(key=bar, book=((6, 20, 4, 1), (9, 20, 4, 1), (14, 20, 4, 5)), cur=6),
             ore: linen_book(key=ore, book=((8, 20, 4, 1), (14, 20, 4, 5)), cur=8)}
    gloves = DeInfo(1, 1425.0, (), ((800, 1),), 80)
    gi = replace(gen_inputs(de={wool: gloves}), price_age={wool: 30 * HOUR})
    u = universe(empty_world(facts=books), gi)
    assert not [o for o in u.opportunities if f":{wool}:" in o.opp]          # no flip, buy for later or disenchant
    every = {o.opp: len(o.batches) for o in u.opportunities}
    ranks = sorted(cand.first_rank(o.batches[0].candidate.gold_needed, o.batches[0].candidate.expected_net,
                                   o.batches[0].candidate.sale_ms, o.batches[0].candidate.cand_id, RUN)
                   for o in u.opportunities)
    pooled = universe(empty_world(facts=books), gi, replace(RUN, ps=replace(RUN.ps, candidate_pool=2)))
    kept = {o.opp: len(o.batches) for o in pooled.opportunities}
    assert len(kept) == 2 and all(every[k] == n for k, n in kept.items())     # the best 2 keep every batch
    assert {o.batches[0].candidate.cand_id for o in pooled.opportunities} == {r[2] for r in ranks[:2]}
    stale_stock = WeekInputs((Lot("Amaya-G", wool, 20),), {wool: facts(wool, 50, ref_stack=20, vendor_sell=1)},
                             empty_world().world)
    result = plans.plan(stale_stock, NOW, price_age={wool: 30 * HOUR})
    assert {a.reason for a in result.alternatives} == {"Forecast too stale (prices 30 h old)"}


def test_a_kept_buy_can_lose_its_room_to_the_kept_flips_before_it(monkeypatch):
    how = [1.0] * 168
    how[WED * 24 + 21], how[SUN * 24 + 19] = 0.8, 1.3
    item = facts(ESSENCE, 1000, how=tuple(how), ref_stack=4, windows=(PlanWindow(SUN * 24 + 19, 3, SUN * 24 + 19, 6),),
                 buy_windows=(PlanWindow(WED * 24 + 20, 3, WED * 24 + 21, 6),), supply_now=40, post_price=None)
    sunday = (ESSENCE, MONDAY + 6 * DAY + 19 * HOUR)

    def fill_sunday(f, char_id, gi, run, u, ledger):
        """A kept flip that takes the whole Sunday window."""
        ledger[sunday] = 999.0
    monkeypatch.setattr(cand, "_flip", fill_sunday)
    monkeypatch.setattr(cand, "_flip_first", lambda *_a: (0, 0.0, "a"))       # one flip kept first
    world = prepare(empty_world(facts={ESSENCE: item}), NOW, S)
    u = Universe([], {})
    cand.buys(world, world, gen_inputs(), RUN, u, {})
    assert u.opportunities == []                                              # the buy for later has no room left


def test_a_kept_flip_whose_room_is_gone_adds_nothing():
    f = linen_book()
    full = {(LINEN, None): 9999.0}
    u = Universe([], {})
    cand._flip(f, "Amaya-G", gen_inputs(), RUN, u, full)
    assert u.opportunities == []


def test_PF30_a_window_sale_needs_price_history_and_every_sale_a_known_sales_rate():
    thin = linen_book(closes_30d=4)                                            # a suffix green's 4 closes
    flip = opportunity(universe(empty_world(facts={LINEN: thin})), f"flip:{LINEN}:")
    assert (flip.batches[0].eligibility.window_closes, static_reason(flip.batches[0].eligibility, 7)) == (None, None)
    how = [1.0] * 168
    how[WED * 24 + 21], how[SUN * 24 + 19] = 0.6, 1.3              # thin: the Sunday sale is capped at the level
    essence = facts(ESSENCE, 1000, how=tuple(how), ref_stack=4, closes_30d=4, supply_now=40,
                    windows=(PlanWindow(SUN * 24 + 19, 3, SUN * 24 + 19, 3),),
                    buy_windows=(PlanWindow(WED * 24 + 20, 3, WED * 24 + 21, 4),))
    assert [o.window for o in plan.post_options(essence, NOW, S)] == [None]   # thin data: posting now only
    assert len(plan.post_options(essence, NOW, S, thin_windows=True)) == 2
    later = opportunity(universe(empty_world(facts={ESSENCE: essence})), f"buy_later:{ESSENCE}:")
    assert static_reason(later.batches[0].eligibility, 7) == "Forecast too uncertain (4 closes in 30 d, needs 24)"
    result = plans.plan(empty_world(facts={ESSENCE: essence}), NOW, gold={"Amaya-G": plans.RICH})
    assert "Forecast too uncertain (4 closes in 30 d, needs 24)" in {a.reason for a in result.alternatives}
    wide = {"med7": 40, "p90": 40, "book": ((5, 20, 4, 1), (40, 20, 4, 5))}  # a 5c stack under 40c: pays either way
    potion = linen_book(closes_30d=30, sold_per_day=None, **wide)
    unknown = opportunity(universe(empty_world(facts={LINEN: potion})), f"flip:{LINEN}:")
    assert static_reason(unknown.batches[0].eligibility, 7) == "Sales volume unknown"
    measured = opportunity(universe(empty_world(facts={LINEN: linen_book(closes_30d=96, sold_per_day=48.0, **wide)})),
                           f"flip:{LINEN}:")
    assert static_reason(measured.batches[0].eligibility, 7) is None
    stock = WeekInputs((Lot("Amaya-G", BAR, 3),), {BAR: facts(BAR, 1000, supply_now=2, closes_30d=4)},
                       CraftWorld((), {}, craft.Market({}, {}, {}, {}), {}))
    post = opportunity(universe(stock), f"sell_stock:{BAR}:Amaya-G").batches[0]
    assert (post.candidate.sale_ms, static_reason(post.eligibility, 7)) == (NOW, None)    # posted now, not Sunday
    enchanter = {("Amaya-G", "Enchanting"): 300}
    green = DeInfo(1, 1425.0, (), ((800, 1),), 80)                             # its dust's rate is known
    de = opportunity(universe(empty_world(), gen_inputs(skills=enchanter, de={LINEN: green})), f"disenchant:{LINEN}:")
    assert static_reason(de.batches[0].eligibility, 7) is None
    dust_unknown = replace(green, volume_unknown="Strange Dust")
    de = opportunity(universe(empty_world(), gen_inputs(skills=enchanter, de={LINEN: dust_unknown})), f"disenchant:{LINEN}:")
    assert static_reason(de.batches[0].eligibility, 7) == "Sales volume unknown (Strange Dust)"
