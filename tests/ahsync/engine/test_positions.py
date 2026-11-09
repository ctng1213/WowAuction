"""Tests for ahsync/engine/positions.py: positions opened, replayed and reconciled (SPEC §3.4D.9, PO2-PO13)."""
from __future__ import annotations

from ahsync.engine.portfolio import CharacterGold, OpenPosition, accounting
from ahsync.engine.positions import (ACQUIRING, COMPLETED, HOLDING, LISTED, PARTIAL, PROCESSING, RECONCILE, Existing,
                                     Lot, Move, Opening, Seed, World, committed, fact_action, place, rolled_events,
                                     strategy_of, track)

A, B = "Amaya-G", "Brugan-G"
M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
T = 1_728_259_200_000                      # Mon 00:00 UTC
NOW = T + 10 * HOUR
BAR, ROD, RUNED, DUST, ESSENCE, SILK = 2840, 6217, 6218, 10940, 10938, 4306
MAGEWEAVE, LEATHER, COIF, THICK, ORE, IRON_BAR = 4338, 2319, 7920, 4304, 2772, 3575


def seed(pid: str, char: str, key: int, units: int, cost: int, at: int, strategy: str = "buy_later",
         product: int | None = None, net: float = 0.0, **kw) -> Seed:
    """A position with one lot whose event id is its id."""
    cand = f"{strategy}:{product or key}:{char}:-/now/now:1"
    return Seed(pid, char, M, cand, key, at, (Lot(pid, units, cost, at),), net, **kw)


def world(holdings: dict | None = None, at: int | None = None, chars=(A, B)) -> World:
    """Holdings and every place of the characters captured at `at` (none when None)."""
    snaps = {} if at is None else {(c, p): at for c in chars for p in ("bags", "bank", "mail")}
    return World(holdings or {}, snaps)


def test_PO2_a_confirmed_purchase_matching_a_funded_buy_opens_a_position_that_commits_its_cost():
    o = Opening("B:1:0", B, BAR, 6, 500, T, "chain1", f"buy_later:{BAR}:{B}:-/h1/h2:1", 3157.0, T + 5 * DAY)
    [pl] = place([o], [])
    assert (pl.position_id, pl.new) == ("B:1:0", True)                 # its id is the opening event's
    s = Seed(pl.position_id, B, M, o.cand_id, BAR, T, (Lot(o.event_id, 6, 500, T),), 3157.0, T + 5 * DAY)
    [p] = track([s], [], world({(B, BAR): 6}, T + 1), NOW)
    assert (p.state, p.units, p.cost_total, p.cost_left, p.expected_net_open, p.target_window_ms) == \
        (HOLDING, 6, 3000, 3000, 3157.0, T + 5 * DAY)
    assert (p.strategy, p.next_action, p.closed_at, p.events) == ("buy_later", "", None, ("B:1:0",))
    before = accounting([CharacterGold(A, 210_000), CharacterGold(B, 104_000)], [], 0.30)
    after = accounting([CharacterGold(A, 210_000), CharacterGold(B, 101_000)], committed([p]), 0.30)   # 30 s spent
    assert after.committed_capital - before.committed_capital == 3000                 # committed rises by 30 s
    assert before.remaining_capacity - after.remaining_capacity == 3000                # and the capacity falls by 30 s
    assert after.capital_base == before.capital_base and committed([p]) == [OpenPosition("B:1:0", B, 3000)]


def test_PO6_an_extra_purchase_matched_to_a_new_funded_buy_opens_its_own_position():
    first = Existing("B:1:0", B, BAR, "chain1", HOLDING)
    more = Opening("B:2:0", B, BAR, 4, 600, T + HOUR, "chain2", f"buy_later:{BAR}:{B}:-/h3/h4:1")
    again = Opening("B:3:0", B, BAR, 2, 500, T + 2 * HOUR, "chain1", f"buy_later:{BAR}:{B}:-/h1/h2:1")
    assert [(p.position_id, p.new) for p in place([again, more], [first])] == [("B:2:0", True), ("B:1:0", False)]
    seeds = [seed("B:1:0", B, BAR, 6, 500, T), seed("B:2:0", B, BAR, 4, 600, T + HOUR)]
    one, two = track(seeds, [], world({(B, BAR): 15}, T + 3 * HOUR), NOW)      # 5 more bars bought outside any plan
    assert [(p.units, p.cost_left, p.state) for p in (one, two)] == [(6, 3000, HOLDING), (4, 2400, HOLDING)]
    done = Existing("B:1:0", B, BAR, "chain1", COMPLETED)              # a closed position takes no new lot
    assert [(p.position_id, p.new) for p in place([again], [done])] == [("B:3:0", True)]


def test_PO4_sold_units_keep_their_cost_committed_until_the_payout_is_collected():
    s = seed("A:1:0", A, MAGEWEAVE, 25, 120, T, net=600.0)
    posts = [Move(T + HOUR, "p1", "post", A, MAGEWEAVE, 15, "p1", deposit=30),
             Move(T + HOUR, "p2", "post", A, MAGEWEAVE, 10, "p2", deposit=20),
             Move(T + 5 * HOUR, "m1", "sold", A, MAGEWEAVE, 15, "p1", "m1", 180)]
    [p] = track([s], posts, world({}, T), NOW)
    assert (p.state, p.units, p.units_left, p.cost_left, p.unpaid, p.listed, p.held) == (PARTIAL, 25, 10, 3000, 15, 10, 0)
    assert p.next_action == "collect mail: 15 sold, 25s 65c waiting"          # 15 × 1s 80c × 0.95
    payout = Move(T + 6 * HOUR, "o1", "payout", A, MAGEWEAVE, 15, mail_id="m1", money=2595, deposit=30)
    [paid] = track([s], [*posts, payout], world({}, T), NOW)
    assert (paid.cost_left, paid.unpaid, paid.state, paid.next_action) == (1200, 0, PARTIAL, "")   # 18 s left
    assert paid.realized == 2595 - 30 - 15 * 120                                # money − deposit − cost
    assert rolled_events([paid]) == {"A:1:0", "p1", "p2", "m1", "o1"}


def test_PO7_mailed_units_wait_as_collect_mail_on_the_recipient_until_her_snapshot_shows_them():
    s = seed("B:1:0", B, ROD, 1, 400, T, "multistage", RUNED)
    mail = Move(T + HOUR, "s1", "mail", B, ROD, 1, to_char=A)
    [p] = track([s], [mail], world({}, T), NOW)
    assert (p.state, p.char_id, p.transit_to, p.next_action) == (PROCESSING, A, A, "collect mail on Amaya")
    arrived = World({(A, ROD): 1}, {(A, "mail"): T + 2 * HOUR, (A, "bags"): T + 2 * HOUR, (A, "bank"): T + 2 * HOUR})
    [here] = track([s], [mail], arrived, NOW)
    assert (here.transit_to, here.held, here.next_action) == (None, 1, "")
    craft = Move(T + 3 * HOUR, "c1", "craft", A, RUNED, 1, reagents=((ROD, 1), (DUST, 1), (ESSENCE, 1)))
    [made] = track([s], [mail, craft], world({}, None), NOW)              # she crafts it: it arrived
    assert (made.key, made.units, made.held, made.state, made.cost_left) == (RUNED, 1, 1, HOLDING, 400)


def test_PO9_a_position_sold_and_paid_out_is_completed_with_its_profit_realized():
    s = seed("B:1:0", B, BAR, 6, 500, T, net=3157.0)
    moves = [Move(T + 5 * DAY, "p1", "post", B, BAR, 6, "p1", deposit=60),
             Move(T + 5 * DAY + 2 * HOUR, "m1", "sold", B, BAR, 6, "p1", "m1", 1200),
             Move(T + 5 * DAY + 3 * HOUR, "o1", "payout", B, BAR, 6, mail_id="m1", money=6900)]
    [p] = track([s], moves, world({}, T), NOW)
    assert (p.state, p.closed_at, p.cost_left, p.units_left, p.next_action) == (COMPLETED, T + 5 * DAY + 3 * HOUR, 0, 0, "—")
    assert p.realized == 6900 - 60 - 3000                              # 68s 40c − 30s: the post's deposit came back
    assert committed([p]) == []


def test_PO10_missing_units_need_reconciliation_and_close_as_a_loss_only_after_31_days():
    s = seed("A:1:0", A, LEATHER, 20, 200, T)
    [p] = track([s], [], world({(A, LEATHER): 14}, T + HOUR), NOW)
    assert (p.state, p.missing, p.cost_left, p.next_action) == (RECONCILE, 6, 4000, "check bags and mail: 14 of 20 found")
    expired = Move(T + 2 * HOUR, "m1", "expired", A, LEATHER, 6, None, "m1")     # the 6 were on the AH after all
    [back] = track([s], [expired], world({(A, LEATHER): 14}, T + HOUR), NOW)
    assert (back.state, back.missing) == (HOLDING, 0)                  # an older snapshot can't show a gap
    [lost] = track([s], [], world({(A, LEATHER): 14}, T + HOUR), T + 32 * DAY)
    assert (lost.state, lost.lost, lost.held, lost.units_left, lost.cost_left) == (HOLDING, 6, 14, 14, 2800)
    assert lost.realized == -1200                                       # 6 × 2s at their cost, never a sale
    [unknown] = track([s], [], world({(A, LEATHER): 14}, T + HOUR), None)        # no clock: still to reconcile
    assert unknown.state == RECONCILE


def test_PO11_a_listed_position_commits_its_cost_until_the_sale_is_collected():
    s = seed("B:1:0", B, COIF, 1, 30_000, T, "flip", net=4500.0)
    post = Move(T + HOUR, "p1", "post", B, COIF, 1, "p1", deposit=300)
    nothing_held = Move(T + 90 * 60_000, "d1", "de", B, COIF, 1)          # all of it is listed: nothing to take
    [listed] = track([s], [post, nothing_held], world({}, T + 2 * HOUR), NOW)
    assert (listed.state, listed.listed, listed.held, listed.cost_left) == (LISTED, 1, 0, 30_000)
    sold = Move(T + 3 * HOUR, "m1", "sold", B, COIF, 1, "p1", "m1", 45_000)
    [waiting] = track([s], [post, sold], world({}, T), NOW)
    assert (waiting.state, waiting.cost_left, waiting.units_left) == (PARTIAL, 30_000, 0)
    paid = Move(T + 4 * HOUR, "o1", "payout", B, COIF, 1, mail_id="m1", money=43_050)
    [done] = track([s], [post, sold, paid], world({}, T), NOW)
    assert (done.state, done.cost_left, committed([done])) == (COMPLETED, 0, [])          # committed falls by 3 g
    assert done.realized == 43_050 - 300 - 30_000


def test_PO13_a_partial_buy_is_acquiring_while_its_window_is_open_and_mats_become_their_product():
    thick = seed("A:1:0", A, THICK, 10, 536, T, acquiring_until=T + 3 * HOUR, to_buy=20)
    [open_] = track([thick], [], world(), T + HOUR)
    assert (open_.state, open_.next_action, open_.to_buy) == (ACQUIRING, "buy 20 more", 20)
    [after] = track([thick], [], world(), T + 4 * HOUR)
    assert (after.state, after.units) == (HOLDING, 10)                 # the other 20 need a new decision
    assert track([thick], [], world(), None)[0].state == ACQUIRING    # no clock: still open
    smelted = Opening("B:9:0", B, IRON_BAR, 10, 40, T + HOUR, "smelt", f"craft:{IRON_BAR}:{B}:-/now/now:1", craft=True)
    assert place([smelted], [Existing("B:1:0", B, ORE, "smelt", PROCESSING)]) == []      # it converts the ore instead
    ore = seed("B:1:0", B, ORE, 10, 40, T, "craft", IRON_BAR, net=150.0)
    [proc] = track([ore], [], world(), NOW)
    assert proc.state == PROCESSING and proc.key == ORE
    smelt = Move(T + HOUR, "c1", "craft", B, IRON_BAR, 10, reagents=((ORE, 10),))
    [bars] = track([ore], [smelt], world(), NOW)
    assert (bars.state, bars.key, bars.units, bars.held, bars.cost_left) == (HOLDING, IRON_BAR, 10, 10, 400)


def test_a_craft_from_two_processing_positions_merges_their_cost_and_uses_held_stock_otherwise():
    bars = seed("B:1:0", B, BAR, 2, 500, T, "craft", ROD)
    dust = seed("B:2:0", B, DUST, 1, 300, T + 1, "craft", ROD)
    stock = seed("B:3:0", B, ESSENCE, 1, 700, T + 2)                    # a held position used as a reagent
    craft = Move(T + HOUR, "c1", "craft", B, ROD, 1, reagents=((BAR, 2), (DUST, 1), (ESSENCE, 1)))
    rod, gone, used = track([bars, dust, stock], [craft], world(), NOW)
    assert (rod.key, rod.units, rod.cost_total, rod.cost_left, rod.state) == (ROD, 1, 1300, 1300, HOLDING)
    assert (gone.merged, gone.state, gone.cost_left) == (True, COMPLETED, 0)
    assert (used.state, used.realized, used.cost_left) == (COMPLETED, -700.0, 0)


def test_a_disenchant_uses_its_unit_and_an_unknown_payout_sells_from_what_is_held():
    green = seed("A:1:0", A, COIF, 1, 1500, T, "disenchant")
    [de] = track([green], [Move(T + HOUR, "d1", "de", A, COIF, 1)], world(), NOW)
    assert (de.state, de.realized, de.strategy) == (COMPLETED, -1500.0, "disenchant")
    held = seed("A:2:0", A, BAR, 4, 500, T)
    blind = Move(T + HOUR, "o1", "payout", A, BAR, 2, money=1900)          # its sold mail was never captured
    [p] = track([held], [blind], world(), NOW)
    assert (p.sold, p.held, p.cost_left, p.state, p.realized) == (2, 2, 1000, PARTIAL, 1900 - 1000)
    unlinked = Move(T + HOUR, "m9", "sold", A, BAR, 1, None, "m9")        # a sold mail without its post
    [q] = track([held], [unlinked], world(), NOW)
    assert (q.unpaid, q.waiting, q.held) == (1, 0.0, 3)


def test_PO14_expired_and_cancelled_posts_both_lose_their_deposit_and_bring_the_units_back():
    s = seed("A:1:0", A, BAR, 4, 500, T)
    posts = [Move(T + HOUR, "p1", "post", A, BAR, 2, "p1", deposit=40),
             Move(T + HOUR, "p2", "post", A, BAR, 2, "p2", deposit=30)]
    ends = [Move(T + 2 * HOUR, "m1", "expired", A, BAR, 2, "p1", "m1"),
            Move(T + 2 * HOUR, "m2", "cancelled", A, BAR, 2, "p2", "m2")]
    [p] = track([s], [*posts, *ends], world(), NOW)
    assert (p.held, p.listed, p.state, p.realized) == (4, 0, HOLDING, -70.0)      # 40c + 30c, both kept by the AH


def test_events_before_a_position_opened_or_of_other_characters_or_items_leave_it_alone():
    s = seed("A:1:0", A, BAR, 2, 500, T)
    others = [Move(T - HOUR, "p0", "post", A, BAR, 2, "p0"), Move(T + HOUR, "p1", "post", B, BAR, 2, "p1"),
              Move(T + HOUR, "p2", "post", A, ROD, 2, "p2"), Move(T + HOUR, "p3", "post", A, BAR, 0, "p3"),
              Move(T + HOUR, "x", "bought", A, BAR, 2)]
    [p] = track([s], others, world(), NOW)
    assert (p.held, p.listed, p.events) == (2, 0, ("A:1:0",))
    partial = Move(T + HOUR, "s1", "mail", A, BAR, 1, to_char=B)          # half mailed: she still holds the other
    [q] = track([s], [partial, Move(T + 2 * HOUR, "p4", "post", A, BAR, 3, "p4")], world(), NOW)
    assert (q.char_id, q.transit_to, q.listed) == (A, B, 1)


def test_a_position_without_lots_commits_nothing_and_a_merged_one_is_never_reconciled():
    empty = Seed("A:9:0", A, M, f"flip:{BAR}:{A}:-/now/now:1", BAR, T, ())
    [p] = track([empty], [], world({(A, BAR): 0}, T + HOUR), NOW)
    assert (p.cost_left, p.state) == (0, COMPLETED)
    assert strategy_of("craft:1:A:-/-/now:2") == "craft"
    assert fact_action(HOLDING, 3, 0, 0, 0.0, None, 0) == ""


def test_PO15_each_sold_mail_moves_only_its_own_units_of_a_commodity_post():
    s = seed("A:1:0", A, SILK, 110, 65 * 110, T)
    post = Move(T + HOUR, "p", "post", A, SILK, 110, "p", deposit=814)
    sold30 = Move(T + 2 * HOUR, "s30", "sold", A, SILK, 30, "p", "s30", money=139)
    [p] = track([s], [post, sold30], world(), NOW)
    assert (p.held, p.listed) == (0, 80)                                     # 30 sold; the other 80 still up
    sold10 = Move(T + 3 * HOUR, "s10", "sold", A, SILK, 10, "p", "s10", money=139)
    back = Move(T + 4 * HOUR, "x70", "expired", A, SILK, 70, "p", "x70")
    [q] = track([s], [post, sold30, sold10, back], world(), NOW)
    assert (q.held, q.listed, q.realized) == (70, 0, -518.0)                 # 814 × 70 ÷ 110 lost with the return

    # a post drawn from two positions: the oldest's units go first, then the next one's
    s1, s2 = seed("A:1:0", A, SILK, 50, 65 * 50, T), seed("A:2:0", A, SILK, 60, 65 * 60, T + 1)
    sold60 = Move(T + 2 * HOUR, "s60", "sold", A, SILK, 60, "p", "s60", money=139)
    first, second = track([s1, s2], [post, sold60, sold10], world(), NOW)
    assert (first.listed, second.listed) == (0, 40)                          # 50 + 10, then 10 more from the second
    # T25's own order: the expiry's mail before the last sale's; then that sale's payout
    late10 = Move(T + 4 * HOUR + 30 * 60_000, "s10", "sold", A, SILK, 10, "p", "s10", money=139)
    pay10 = Move(T + 5 * HOUR, "o10", "payout", A, SILK, 10, mail_id="s10", money=1320)
    [late] = track([s], [post, sold30, back, late10, pay10], world(), NOW)
    assert (late.held, late.listed, late.sold) == (70, 0, 40)                # the late 10 still sold, the 70 came back
    back50 = Move(T + 4 * HOUR, "x50", "expired", A, SILK, 50, "p", "x50")
    first, second = track([s1, s2], [post, sold60, back50], world(), NOW)
    assert (first.held, first.listed, second.held, second.listed) == (0, 0, 50, 0)   # the first had nothing left up
