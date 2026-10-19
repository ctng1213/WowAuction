"""Tests for ahsync/engine/flips.py: buy to flip (SPEC §3.4C.2), invariant PL4."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ahsync.engine import flips, plan, sellprob
from ahsync.engine.flips import FlipSettings
from ahsync.engine.plan import Lot, PlanSettings, PlanWindow, SellFacts
from tests.support.gen import cases

HOUR, DAY = 3_600_000, 86_400_000
MONDAY = 345_600_000 + 3000 * 7 * DAY
SAT = 5
NOW = MONDAY + 4 * DAY + 14 * HOUR            # Fri 14:00
SILK, LINEN = 4306, 2589
S, FS = PlanSettings(), FlipSettings()
RICH = 100_000_000                            # 10,000 g: the risk limit doesn't bind
SEED_PL4 = 4044


def silk(**changes) -> SellFacts:
    """Silk Cloth: 1s on the AH now, level 1s, a strong Saturday 18:00 peak (1.5×), busy market."""
    how = [1.0] * 168
    how[SAT * 24 + 18] = 1.5
    base = SellFacts(SILK, level=100, trend7=0.0, post_price=100, sold_per_day=400.0, listed_per_day=100.0,
                     ref_stack=20, how=tuple(how), sales_how=tuple([1.0] * 168),
                     windows=(PlanWindow(SAT * 24 + 17, 4, SAT * 24 + 18),),
                     book=((60, 20, 4, 3), (100, 20, 4, 2)), cur=60, cur_qty=20, vendor_sell=10,
                     supply_now=400, conf=80)
    return replace(base, **changes)


def test_R8_the_chance_to_sell_counts_not_the_price_spread():
    a = sellprob.expected_post(0.4, 1, 300, 0, 0)          # 0.4 × 3 s × 0.95 = 1 s 14 c
    b = sellprob.expected_post(0.95, 1, 150, 0, 0)         # 0.95 × 1 s 50 c × 0.95 = 1 s 35 c
    assert (flips.pays(a, 100, 0.20), flips.pays(b, 100, 0.20)) == (False, True)    # 14% vs 35%


def test_R13_a_flip_counts_what_an_unsold_unit_is_worth_and_its_lost_deposit():
    e = sellprob.expected_post(0.5, 1, 3000, 100, 100)     # 0.5 × 28 s 50 c + 0.5 × (1 s − 1 s)
    assert e == 1425 and flips.pays(e, 1000, 0.20)          # profit 4 s 25 c = 42.5% of 10 s


def test_a_flip_buys_the_cheap_auctions_and_posts_for_the_peak():
    actions = flips.flip_plan({SILK: silk()}, {"Amaya-G": RICH}, RICH, NOW, S, FS, {})
    buy, *posts = actions
    # The three auctions at 60c and the two at 1s: 1s resold at 1s 50c still clears 20% after the cut.
    assert (buy.kind, buy.qty, buy.price, buy.chain, buy.source) == ("buy", 100, 100, "flip:4306", "ah")
    assert all(p.peak_ms == MONDAY + SAT * DAY + 18 * HOUR and p.chain == buy.chain for p in posts)
    assert sum(p.qty for p in posts) == 100 and buy.net == buy.expected - (60 * 60 + 40 * 100)


def test_R9_flip_buys_stay_within_the_budget():
    budgets = {"Amaya-G": 2500}                              # 25s: two auctions of 20 at 60c cost 24 s
    actions = flips.flip_plan({SILK: silk()}, budgets, RICH, NOW, S, FS, {})
    assert sum(a.qty * 60 for a in actions if a.kind == "buy") <= 2500 and budgets["Amaya-G"] == 100


def test_R16_two_characters_never_fill_the_same_demand():
    linen = silk(key=LINEN, supply_now=40)                  # cap max(20, floor(0.5 × 40)) = 20 per window
    budgets = {"Main-G": RICH, "Alt-G": RICH}
    actions = flips.flip_plan({LINEN: linen}, budgets, RICH, NOW, S, FS, {})
    buys = [a for a in actions if a.kind == "buy"]
    assert [(b.char_id, b.qty) for b in buys] == [("Alt-G", 20)]     # one buy of 20; ties go to the first id


def test_R19_a_buy_never_risks_more_than_a_quarter_of_your_gold():
    expensive = silk(book=((4000, 10, 4, 1), (4100, 20, 4, 1)), cur=4000, cur_qty=10, level=10000, post_price=10000)
    actions = flips.flip_plan({SILK: expensive}, {"Amaya-G": RICH}, 300_000, NOW, S, FS, {})   # 30 g
    [buy] = [a for a in actions if a.kind == "buy"]
    assert (buy.qty, buy.price) == (10, 4000)                # 4 g; the 20 would make 12 g 20 s, over 7 g 50 s


def test_no_flip_without_a_selling_window_or_a_paying_auction():
    assert flips.flip_plan({SILK: silk(windows=())}, {"A": RICH}, RICH, NOW, S, FS, {}) == []
    assert flips.flip_plan({SILK: silk(level=50)}, {"A": RICH}, RICH, NOW, S, FS, {}) == []    # peak 75c < 60c × 1.2
    assert flips.flip_plan({SILK: silk()}, {}, RICH, NOW, S, FS, {}) == []
    assert flips.flip_plan({SILK: silk()}, {"A": RICH}, RICH, NOW, S, FS, {}, exclude={SILK}) == []
    no_book = flips.flip_plan({SILK: silk(book=None)}, {"A": RICH}, RICH, NOW, S, FS, {})
    assert [a.qty for a in no_book if a.kind == "buy"] == [20]          # cur_qty at cur, as one auction
    assert flips.auctions(None, None, None) == [] and flips.flip_plan(
        {SILK: silk(book=None, cur=None, cur_qty=None)}, {"A": RICH}, RICH, NOW, S, FS, {}) == []


def test_the_ledger_is_shared_with_your_own_stock():
    ledger: dict = {}
    plan.sell_plan([Lot("Amaya-G", SILK, 400)], {SILK: silk()}, NOW, S, ledger)
    assert flips.flip_plan({SILK: silk()}, {"Amaya-G": RICH}, RICH, NOW, S, FS, ledger) == []   # windows full


def random_market(rng):
    """Random items, your stock of them, and characters with budgets."""
    facts = {}
    for key in rng.sample([SILK, LINEN, 2592, 2840], rng.randint(1, 3)):
        book = tuple((rng.randint(20, 120), rng.choice([1, 5, 20]), rng.randint(1, 4), rng.randint(1, 4))
                     for _ in range(rng.randint(0, 4)))
        facts[key] = silk(key=key, book=book or None, cur=rng.randint(20, 120), cur_qty=rng.randint(1, 40),
                          supply_now=rng.choice([None, 0, 20, 100, 400]), sold_per_day=rng.choice([None, 10.0, 400.0]),
                          ref_stack=rng.choice([1, 5, 20]), gear=rng.random() < 0.2)
    lots = [Lot(char, key, rng.randint(1, 80)) for key in facts for char in ("A", "B") if rng.random() < 0.5]
    budgets = {char: rng.choice([0, 1000, 50_000, RICH]) for char in ("A", "B", "C")[:rng.randint(1, 3)]}
    return facts, lots, budgets, rng.choice([300_000, RICH])


def test_PL4_planned_units_never_exceed_a_windows_capacity_across_characters():
    for i, (facts, lots, budgets, gold) in enumerate(cases(SEED_PL4, random_market, 300)):
        ledger: dict = {}
        start = dict(budgets)
        actions = plan.sell_plan(lots, facts, NOW, S, ledger) + flips.flip_plan(facts, budgets, gold, NOW, S, FS, ledger)
        for key, f in facts.items():
            for o in plan.post_options(f, NOW, S):
                planned = sum(a.qty for a in actions if a.kind == "post" and a.key == key
                              and a.window_start_ms == (o.window[0] if o.window else None) and a.at_ms == o.at_ms)
                assert planned <= o.cap, (SEED_PL4, i, key)
        for char, left in budgets.items():                   # PL1: buys never exceed the budget
            spent = start[char] - left
            assert left >= 0 and spent <= max(start[char], 0), (SEED_PL4, i)
            assert spent <= gold * FS.max_risk_pct / 100 * len(facts), (SEED_PL4, i)


def test_the_best_window_wins_and_a_spent_budget_ends_later_flips():
    two_windows = silk(windows=(PlanWindow(SAT * 24 + 17, 4, SAT * 24 + 18), PlanWindow(2 * 24 + 20, 2, 2 * 24 + 21)))
    buy, *posts = flips.flip_plan({SILK: two_windows}, {"A": RICH}, RICH, NOW, S, FS, {})
    assert {p.peak_ms for p in posts} == {MONDAY + SAT * DAY + 18 * HOUR}     # Wed (1.0×) never beats Sat (1.5×)
    budgets = {"A": 1200}                                                    # one auction of 20 at 60c
    actions = flips.flip_plan({SILK: silk(), LINEN: silk(key=LINEN)}, budgets, RICH, NOW, S, FS, {})
    assert [a.key for a in actions if a.kind == "buy"] == [LINEN] and budgets == {"A": 0}


def test_EX11_a_market_wide_flip_offer_for_one_reference_stack():
    expected, price = flips.flip_offer(49, 1.25, 0.0, 2, 0.9, 10.0, 10, 20)
    assert price == 61                                              # floor(49 × 1.25 + 0.5)
    assert abs(expected - 53.105) < 1e-9                            # 0.9 × 61 × 0.95 + 0.1 × (10 − 10 ÷ 20)


SHARED = json.loads((Path(__file__).resolve().parents[3] / "tests" / "scenarios" / "prices.json").read_text(encoding="utf-8"))


def dumped_linen(**changes) -> SellFacts:
    """Linen (R24): 1 × 20 at 10c dumped below 5 × 20 at 50c; med7 48c, p90 55c; 54 sold a day, so 18 in 8 h."""
    base = SellFacts(LINEN, level=48, trend7=0.0, post_price=9, sold_per_day=54.0, listed_per_day=0.0, ref_stack=20,
                     how=tuple([1.0] * 168), sales_how=tuple([1.0] * 168), windows=(),
                     book=((10, 20, 4, 1), (50, 20, 4, 5)), cur=10, cur_qty=20, vendor_sell=10, supply_now=120,
                     conf=80, med7=48, p90=55)
    return replace(base, **changes)


def test_R24_a_dumped_stack_is_bought_and_relisted_now_under_the_next_level():
    s = replace(S, default_undercut_share=0.0)
    buy, post = flips.flip_plan({LINEN: dumped_linen()}, {"Amaya-G": RICH}, RICH, NOW, s, FS, {})
    expected = 0.9 * 20 * 49 * 0.95 + 0.1 * (20 * 10 - 10)                 # 837.9 + 19 = 856.9
    assert (buy.kind, buy.qty, buy.price, buy.chain) == ("buy", 20, 10, "flip:2589")
    assert buy.expected == pytest.approx(expected) and buy.net == pytest.approx(expected - 200)
    assert (post.kind, post.qty, post.price, post.at_ms, post.window_start_ms, post.peak_ms, post.duration_h) == \
        ("post", 20, 49, NOW, None, None, 8)                                # min(50 − 1, max(48, 55)), due now, 8 h
    assert (post.p, post.chain) == (pytest.approx(0.9), "flip:2589")
    sat = dumped_linen(windows=(PlanWindow(SAT * 24 + 17, 4, SAT * 24 + 18),), book=((10, 20, 4, 1), (50, 20, 1, 5)),
                       how=tuple(1.5 if h == SAT * 24 + 18 else 1.0 for h in range(168)))
    buy, post = flips.flip_plan({LINEN: sat}, {"Amaya-G": RICH}, RICH, NOW, s, FS, {})
    assert (post.price, post.peak_ms, buy.expected) == (72, MONDAY + SAT * DAY + 18 * HOUR, pytest.approx(1368))


def test_R25_no_flip_when_the_next_level_is_too_close():
    close = dumped_linen(book=((45, 20, 4, 1), (50, 20, 4, 5)), cur=45)
    assert flips.flip_plan({LINEN: close}, {"Amaya-G": RICH}, RICH, NOW, replace(S, default_undercut_share=0.0), FS,
                           {}) == []                                        # 856.9 < 1.2 × 900


def test_relisting_needs_something_to_price_by():
    alone = dumped_linen(book=((10, 20, 4, 1),), med7=None, p90=None)       # nothing left after the buy
    [now] = flips.relisted(flips._remaining_book(alone, 1), plan.post_options(alone, NOW, S), S)
    assert now.cap == 0
    assert flips.flip_plan({LINEN: alone}, {"Amaya-G": RICH}, RICH, NOW, S, FS, {}) == []


@pytest.mark.parametrize("s", [s for s in SHARED if s["call"] == "flip_now_offer"], ids=lambda s: s["name"])
def test_FN1_a_flip_seen_in_a_live_book_shared_with_the_addon(s):
    result = flips.flip_now_offer(**s["args"])
    assert (result is None) == (s["expect"] is None), s["why"]
    if result is not None:
        assert result == pytest.approx(s["expect"]), s["why"]
