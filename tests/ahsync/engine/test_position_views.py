"""Tests for ahsync/engine/position_views.py: what a position should do next and how it shows (SPEC §3.4D.9; PO3, PO5,
PO11, PF22)."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine.position_views import (Clock, MarketNow, Outlook, PositionView, SellStep, evaluate, found_rows,
                                          history_judged, history_positions, ready_to_sell, relist_price,
                                          sell_now_text, step_text, when)
from ahsync.engine.positions import (ACQUIRING, COMPLETED, HOLDING, LISTED, PARTIAL, PROCESSING, READY, RECONCILE,
                                     Position)
from ahsync.engine.tracking import OPEN_POSITION, Judged

A, B = "Amaya-G", "Brugan-G"
HOUR, DAY = 3_600_000, 86_400_000
MON = 1_728_259_200_000                    # Mon 00:00 UTC
NOW = MON + 10 * HOUR + 38 * 60_000        # Mon 10:38
C = Clock(NOW)
BAR, ELIXIR, COIF, ORE, IRON_BAR, WOOL = 2840, 2457, 7920, 2772, 3575, 2592


def at(day: int, hour: int) -> int:
    """A moment in the mockup's week."""
    return MON + day * DAY + hour * HOUR


def pos(key: int, state: str = HOLDING, units: int = 6, cost: int = 3000, **kw) -> Position:
    """A position as engine.positions.track leaves it, held unless said otherwise."""
    base = dict(position_id="p", char_id=B, market="m", cand_id=f"buy_later:{key}:{B}:-/now/now:1", key=key,
                state=state, opened_at=MON, units=units, units_left=units, cost_total=cost, cost_left=cost,
                expected_net_open=0.0, realized=0.0, closed_at=None, next_action="", held=units, strategy="buy_later")
    return Position(**{**base, **kw})


def post(key: int, qty: int, at_ms: int, window: int | None, expected: float, net: float, char: str = B) -> SellStep:
    """A plan post (a 3 h window peaking at its start)."""
    return SellStep("post", char, key, qty, at_ms, window, None if window is None else window + 3 * HOUR, window,
                    expected, net)


def test_PO5_selling_now_beats_waiting_once_the_window_is_worth_less_after_its_premium_and_what_you_paid_is_sunk():
    elixir = pos(ELIXIR, units=2, cost=1400)
    market = MarketNow(450.0, 450, 326)                                # 9 s now for both; 38% over the median
    o = Outlook((post(ELIXIR, 2, at(2, 12), at(2, 20), 1000.0, 800.0),), 1.0, market)
    v = evaluate(elixir, o, C)
    # Wed 20:00 is 2.39 d away: premium 26.95%, so 10 s is worth 7 s 88 c now, under 9 s.
    assert (v.state, v.next_action, v.net_now, v.window_ms) == (READY, "sell now: price spike +38%", 800.0, at(2, 20))
    cheap = evaluate(elixir, replace(o, market=MarketNow(390.0, 390, 400)), C)          # 7 s 80 c now: wait
    assert (cheap.state, cheap.next_action) == (HOLDING, "post Wed 12:00 for the 20:00 peak · below what you paid")
    paid_more = evaluate(replace(elixir, cost_total=100_000, cost_left=100_000), o, C)
    assert paid_more.state == READY                                    # the 10 g paid plays no part
    assert ready_to_sell(1.0, 10_000.0, NOW - HOUR, None, C) and ready_to_sell(1.0, 10_000.0, NOW - HOUR, NOW + HOUR, C)
    assert sell_now_text(MarketNow(None, None, None)) == "sell now" and sell_now_text(MarketNow(1.0, 300, 326)) == "sell now"


def test_PO3_a_position_outlives_a_replan_and_only_its_next_action_changes_flagged_below_what_you_paid():
    bars = pos(BAR, expected_net_open=3157.0)
    funded = evaluate(bars, Outlook((post(BAR, 6, at(5, 11), at(5, 19), 6157.0, 3157.0),), 1.0,
                                    MarketNow(600.0, 650, 600)), C)
    assert (funded.next_action, funded.paid, funded.cost_left) == ("post Sat 11:00 for the 19:00 peak", 3000, 3000)
    fell = evaluate(bars, Outlook((post(BAR, 6, NOW, None, 2400.0, -600.0),), 1.0, MarketNow(380.0, 400, 600)), C)
    assert (fell.state, fell.next_action) == (READY, "sell now")       # the plan sells now; nothing waits
    held = evaluate(bars, Outlook((SellStep("hold", B, BAR, 6, NOW, None, None, None, 2000.0, -1000.0),)), C)
    assert (held.next_action, held.net_now, held.cost_left) == ("hold · below what you paid", -1000.0, 3000)


def test_PO11_a_listed_position_someone_lists_at_or_below_says_undercut_relist_one_copper_under():
    coif = pos(COIF, LISTED, 1, 30_000, held=0, listed=1, expected_net_open=4500.0)
    o = Outlook((), 1.0, MarketNow(42_750.0, 45_000, 45_000, ((45_000, 2),)), ((45_000, 1),))
    v = evaluate(coif, o, C)
    assert (v.state, v.next_action, v.net_now, v.window_ms) == (LISTED, "undercut: relist at 4g 49s 99c", 4500.0, None)
    alone = evaluate(coif, replace(o, market=MarketNow(42_750.0, 45_000, 45_000, ((45_000, 1), (46_000, 3)))), C)
    assert alone.next_action == "listed · cheapest"
    assert relist_price([], [(1, 1)]) is None and relist_price([(500, 2)], [(400, 1), (500, 2)]) == 399
    assert evaluate(coif, Outlook(), C).next_action == "listed · cheapest"            # no market: nothing to compare


def test_the_facts_next_action_comes_first_and_a_reconciliation_has_no_estimate():
    leather = pos(2319, RECONCILE, 20, 4000, missing=6, next_action="check bags and mail: 14 of 20 found")
    v = evaluate(leather, Outlook((post(2319, 20, NOW, None, 5000.0, 1000.0),)), C)
    assert (v.state, v.net_now, v.next_action) == (RECONCILE, None, "check bags and mail: 14 of 20 found")
    thick = pos(4304, ACQUIRING, 10, 5360, acquiring_until=at(1, 23), to_buy=20, next_action="buy 20 more")
    assert evaluate(thick, Outlook(), C).next_action == "buy 20 more by Tue 23:00"
    clockless = replace(thick, acquiring_until=None)
    assert evaluate(clockless, Outlook(), C).next_action == "buy 20 more"
    mageweave = pos(4338, PARTIAL, 25, 3000, held=0, listed=10, unpaid=15, units_left=10, expected_net_open=600.0,
                    next_action="collect mail: 15 sold, 25s 65c waiting")
    mv = evaluate(mageweave, Outlook(), C)
    assert (mv.next_action, mv.net_now, mv.units_left) == ("collect mail: 15 sold, 25s 65c waiting", 600.0, 10)
    nothing = evaluate(pos(BAR, COMPLETED, 0, 0, held=0, units_left=0, next_action="—"), Outlook(), C)
    assert (nothing.net_now, nothing.next_action) == (0.0, "—")


def test_processing_says_what_to_do_and_when_then_the_products_post():
    ore = pos(ORE, PROCESSING, 10, 400, cand_id=f"craft:{IRON_BAR}:{B}:-/now/now:1", expected_net_open=150.0)
    then = post(IRON_BAR, 10, at(3, 11), at(3, 19), 550.0, 150.0)
    v = evaluate(ore, Outlook((), 1.0, None, (), "smelt", NOW + HOUR, then), C)
    assert (v.next_action, v.window_ms, v.net_now) == ("smelt today · post Thu 11:00 for the 19:00 peak", at(3, 19), 150.0)
    later = evaluate(ore, Outlook((), 1.0, None, (), "craft", at(1, 9)), C)
    assert (later.next_action, later.window_ms) == ("craft Tue", None)
    assert evaluate(ore, Outlook(verb="disenchant"), C).next_action == "disenchant today"
    unwindowed = evaluate(ore, Outlook(then=post(IRON_BAR, 10, NOW, None, 1.0, 1.0)), C)
    assert (unwindowed.next_action, unwindowed.window_ms) == ("craft today · post now", None)


def test_plan_actions_read_as_next_actions():
    assert step_text(None, C) == "hold"
    assert [step_text(SellStep(k, B, 1, 1, NOW, None, None, None, 0.0, 0.0), C) for k in ("de", "vendor", "craft", "odd")] \
        == ["disenchant", "sell to a vendor", "craft", "odd"]
    assert step_text(post(1, 1, NOW + HOUR, NOW + 2 * HOUR, 1.0, 1.0), C) == "post today 11:38 for the 12:38 peak"
    assert step_text(post(1, 1, NOW - HOUR, NOW + HOUR, 1.0, 1.0), C) == "post now for the 11:38 peak"
    assert when(NOW, C) == "now" and when(at(4, 9), Clock(NOW, 2 * HOUR)) == "Fri 11:00"
    no_market = evaluate(pos(BAR), Outlook((post(BAR, 6, NOW, None, 9000.0, 6000.0),)), C)
    assert (no_market.state, no_market.next_action) == (HOLDING, "post now")       # no market price: never Ready
    unplanned = evaluate(pos(BAR), Outlook((), 1.0, MarketNow(600.0, 650, 600)), C)
    assert (unplanned.state, unplanned.next_action) == (HOLDING, "hold")


def test_PF22_found_stock_held_for_a_later_window_is_a_holding_row_that_costs_nothing():
    hold = SellStep("hold", A, WOOL, 20, NOW, at(6, 19), at(6, 22), at(6, 19), 472.0, 472.0)
    steps = {(A, WOOL): [hold], (A, BAR): [post(BAR, 6, NOW, None, 1.0, 1.0, A)],
             (B, 1): [post(1, 1, at(1, 11), at(1, 19), 9.0, 9.0)], (B, 2): [hold], (B, 3): [hold],
             (B, 4): [SellStep("hold", B, 4, 1, NOW, None, None, None, 5.0, 5.0)]}
    rows = found_rows([(A, WOOL, 20), (A, BAR, 6), (B, 1, 1), (B, 2, 3), (B, 3, 0), (B, 4, 1), (B, 5, 2)],
                      {2: 300}, steps, {(B, 1)}, C)
    # Bars posted now are no later window; item 1 is already a position, item 2 was paid for, item 3 is gone,
    # item 5 has no plan.
    assert rows == [PositionView(f"found:{A}:{WOOL}", A, WOOL, HOLDING, 20, 20, 0, 0, 472.0, at(6, 19), "hold", True),
                    PositionView(f"found:{B}:4", B, 4, HOLDING, 1, 1, 0, 0, 5.0, None, "hold", True)]


def test_PO10_PO9_history_judges_a_position_once_it_is_completed():
    done = pos(BAR, COMPLETED, 6, 3000, held=0, units_left=0, cost_left=0, realized=3840.0, closed_at=at(5, 22),
               expected_net_open=3157.0)
    assert history_judged(done) == Judged(3157.0, 3840.0, "completed")
    assert history_judged(replace(done, lost=6)) == Judged(3157.0, 3840.0, "6 lost (untracked)")
    still = pos(2319, HOLDING, 20, 4000, units_left=14, lost=6, realized=-1200.0)
    assert history_judged(still) == Judged(0.0, None, "holding · 14 of 20 left · 6 lost (untracked)", OPEN_POSITION)
    assert history_judged(pos(BAR)).note == "holding · 6 of 6 left"
    green = pos(COIF, COMPLETED, strategy="disenchant", realized=-1500.0, closed_at=NOW)
    assert history_judged(green) == Judged(0.0, None, "mats sold separately")
    old = replace(done, closed_at=NOW - 40 * DAY)
    merged = replace(done, merged=True)
    assert history_positions([done, old, merged, still], NOW - 30 * DAY) == [done, still]
