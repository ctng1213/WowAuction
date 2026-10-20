"""Tests for ahsync/engine/flips.py: buy to flip (SPEC §3.4C.2), invariant PL4 (through the portfolio plan)."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ahsync.engine import craft, flips, plan, sellprob
from ahsync.engine.craft_plan import CraftWorld
from ahsync.engine.flips import FlipSettings
from ahsync.engine.plan import Lot, PlanSettings, PlanWindow, SellFacts
from ahsync.engine.portfolio import PortfolioSettings
from ahsync.engine.week import WeekInputs
from tests.support import plans
from tests.support.gen import cases

HOUR, DAY = 3_600_000, 86_400_000
MONDAY = 345_600_000 + 3000 * 7 * DAY
SAT = 5
NOW = MONDAY + 4 * DAY + 14 * HOUR            # Fri 14:00
SILK, LINEN = 4306, 2589
S, FS = PlanSettings(), FlipSettings()
RICH = 100_000_000                            # 10,000 g: gold doesn't bind
SEED_PL4 = 4044


def world(facts: dict, lots=(), no_buy=frozenset()) -> WeekInputs:
    """A plan world of flip candidates (and your stock), with no recipes.

    Args:
        facts: Item key -> facts.
        lots: Your stock.
        no_buy: Items never bought.

    Returns:
        WeekInputs.
    """
    return WeekInputs(tuple(lots), facts, CraftWorld((), {}, craft.Market({}, {}, {}, {}), {}), no_buy)


def flip(f: SellFacts, s: PlanSettings = S, **kwargs) -> list:
    """One item's whole flip, as the plan's actions would be with every auction funded (building blocks).

    Args:
        f: The item.
        s: Plan settings.
        kwargs: build_flip's options.

    Returns:
        flip_actions of build_flip, or [] when no auction pays.
    """
    stage = flips.build_flip(f, NOW, s, FS, {}, **kwargs)
    return flips.flip_actions(f, stage, "Amaya-G", NOW, s) if stage else []


def silk(**changes) -> SellFacts:
    """Silk Cloth: 1s on the AH now, level 1s, a strong Saturday 18:00 peak (1.5×), busy market."""
    how = [1.0] * 168
    how[SAT * 24 + 18] = 1.5
    base = SellFacts(SILK, level=100, trend7=0.0, closes_30d=720, post_price=100, sold_per_day=400.0, listed_per_day=100.0,
                     ref_stack=20, how=tuple(how), sales_how=tuple([1.0] * 168),
                     windows=(PlanWindow(SAT * 24 + 17, 4, SAT * 24 + 18, 6),),
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
    buy, *posts = flip(silk())
    # The three auctions at 60c and the two at 1s: 1s resold at 1s 50c still clears 20% after the cut.
    assert (buy.kind, buy.qty, buy.price, buy.chain, buy.source) == ("buy", 100, 100, "flip:4306", "ah")
    assert all(p.peak_ms == MONDAY + SAT * DAY + 18 * HOUR and p.chain == buy.chain for p in posts)
    assert sum(p.qty for p in posts) == 100 and buy.net == buy.expected - (60 * 60 + 40 * 100)


def test_R9_flip_buys_stay_within_the_budget():
    # 25s: each auction of 20 at 60c costs 12s plus a 30c deposit, so two fit (24s 60c) and the third doesn't.
    actions = plans.actions(world({SILK: silk()}), NOW, gold={"Amaya-G": 2500})
    assert sum(a.qty for a in actions if a.kind == "buy") * 60 == 2400 <= 2500


def test_R16_two_characters_never_fill_the_same_demand():
    linen = silk(key=LINEN, supply_now=40)                  # cap max(20, floor(0.5 × 40)) = 20 per window
    actions = plans.actions(world({LINEN: linen}), NOW, gold={"Main-G": RICH, "Alt-G": RICH})
    buys = [a for a in actions if a.kind == "buy"]
    assert [(b.char_id, b.qty) for b in buys] == [("Alt-G", 20)]     # one buy of 20; ties go to the first id


def test_R19_the_exposure_budget_caps_a_buy_not_a_share_of_your_gold_per_buy():
    # All your gold is 30 g; risk tolerance 30% makes the exposure budget 9 g. Silk (stack 20) is listed as 10 at
    # 40 s and 20 at 41 s: the first auction (4 g) is funded, the second (8 g 20 s) doesn't fit the 5 g left.
    expensive = silk(book=((4000, 10, 4, 1), (4100, 20, 4, 1)), cur=4000, cur_qty=10, level=10000, post_price=10000,
                     vendor_sell=0)
    result = plans.plan(world({SILK: expensive}), NOW, gold={"Amaya-G": 300_000}, ps=PortfolioSettings())
    [buy] = [a for a in result.actions if a.kind == "buy"]
    assert (buy.qty, buy.price) == (10, 4000) and result.accounting.risk_budget == 90_000
    [why] = [a.reason for a in result.alternatives]
    assert why == "Exceeds risk tolerance (needs 8g 20s, 5g left)"


def test_no_flip_without_a_selling_window_or_a_paying_auction():
    assert flip(silk(windows=())) == []
    assert flip(silk(level=50)) == []                                         # peak 75c < 60c × 1.2
    assert plans.actions(world({SILK: silk()}), NOW, gold={}) == []           # no characters
    assert plans.actions(world({SILK: silk()}, no_buy=frozenset({SILK})), NOW, gold={"A": RICH}) == []
    assert [a.qty for a in flip(silk(book=None)) if a.kind == "buy"] == [20]  # cur_qty at cur, as one auction
    assert flips.auctions(None, None, None) == [] and flip(silk(book=None, cur=None, cur_qty=None)) == []
    assert [a.qty for a in flip(silk(), max_auctions=1) if a.kind == "buy"] == [20]
    assert [a.qty for a in flip(silk(), spend_limit=2400) if a.kind == "buy"] == [40]


def test_the_ledger_is_shared_with_your_own_stock():
    full = {(SILK, None): 200.0, (SILK, plan.post_options(silk(), NOW, S)[1].window[0]): 200.0}
    assert flips.build_flip(silk(), NOW, S, FS, full) is None                 # your posts fill both windows first


def random_market(rng):
    """Random items, your stock of them, and characters with gold.

    Args:
        rng: The seeded Random.

    Returns:
        (facts, lots, gold by character).
    """
    facts = {}
    for key in rng.sample([SILK, LINEN, 2592, 2840], rng.randint(1, 3)):
        book = tuple((rng.randint(20, 120), rng.choice([1, 5, 20]), rng.randint(1, 4), rng.randint(1, 4))
                     for _ in range(rng.randint(0, 4)))
        facts[key] = silk(key=key, book=book or None, cur=rng.randint(20, 120), cur_qty=rng.randint(1, 40),
                          supply_now=rng.choice([None, 0, 20, 100, 400]), sold_per_day=rng.choice([None, 10.0, 400.0]),
                          ref_stack=rng.choice([1, 5, 20]), gear=rng.random() < 0.2)
    chars = ("A", "B", "C")[:rng.randint(1, 3)]
    lots = [Lot(char, key, rng.randint(1, 80)) for key in facts for char in ("A", "B") if rng.random() < 0.5]
    gold = {char: rng.choice([0, 1000, 50_000, RICH]) for char in sorted(set(chars) | {lot.char_id for lot in lots})}
    return facts, lots, gold


def test_PL4_planned_units_never_exceed_a_windows_capacity_across_characters():
    for i, (facts, lots, gold) in enumerate(cases(SEED_PL4, random_market, 300)):
        result = plans.plan(world(facts, lots), NOW, gold=gold, ps=PortfolioSettings())
        actions = result.actions
        for key, f in facts.items():
            for o in plan.post_options(f, NOW, S):
                planned = sum(a.qty for a in actions if a.kind == "post" and a.key == key
                              and a.window_start_ms == (o.window[0] if o.window else None) and a.at_ms == o.at_ms)
                assert planned <= o.cap, (SEED_PL4, i, key)
        for char, have in gold.items():                      # PL1: buys never exceed a character's gold
            spent = sum(a.qty * a.price for a in actions if a.kind == "buy" and a.char_id == char)
            assert spent <= have, (SEED_PL4, i)
        assert result.selection.gold <= result.accounting.remaining_capacity, (SEED_PL4, i)


def test_the_best_window_wins_and_spent_gold_ends_later_flips():
    two_windows = silk(windows=(PlanWindow(SAT * 24 + 17, 4, SAT * 24 + 18, 6), PlanWindow(2 * 24 + 20, 2, 2 * 24 + 21, 6)))
    buy, *posts = flip(two_windows)
    assert {p.peak_ms for p in posts} == {MONDAY + SAT * DAY + 18 * HOUR}     # Wed (1.0×) never beats Sat (1.5×)
    gold = {"A": 1230}                                                       # one auction of 20 at 60c and its deposit
    actions = plans.actions(world({SILK: silk(), LINEN: silk(key=LINEN)}), NOW, gold=gold)
    assert [a.key for a in actions if a.kind == "buy"] == [LINEN]             # equal ratios: the earlier id


def test_EX11_a_market_wide_flip_offer_for_one_reference_stack():
    expected, price = flips.flip_offer(49, 1.25, 0.0, 2, 0.9, 10.0, 10, 20, 0.15)
    assert price == 61                                              # floor(49 × 1.25 + 0.5)
    assert abs(expected - 53.105) < 1e-9                            # 0.9 × 61 × 0.95 + 0.1 × (10 − 10 ÷ 20)


SHARED = json.loads((Path(__file__).resolve().parents[3] / "tests" / "scenarios" / "prices.json").read_text(encoding="utf-8"))


def dumped_linen(**changes) -> SellFacts:
    """Linen (R24): 1 × 20 at 10c dumped below 5 × 20 at 50c; med7 48c, p90 55c; 54 sold a day, so 18 in 8 h."""
    base = SellFacts(LINEN, level=48, trend7=0.0, closes_30d=720, post_price=9, sold_per_day=54.0, listed_per_day=0.0, ref_stack=20,
                     how=tuple([1.0] * 168), sales_how=tuple([1.0] * 168), windows=(),
                     book=((10, 20, 4, 1), (50, 20, 4, 5)), cur=10, cur_qty=20, vendor_sell=10, supply_now=120,
                     conf=80, med7=48, p90=55)
    return replace(base, **changes)


def test_R24_a_dumped_stack_is_bought_and_relisted_now_under_the_next_level():
    s = replace(S, default_undercut_share=0.0)
    buy, post = flip(dumped_linen(), s)
    expected = 0.9 * 20 * 49 * 0.95 + 0.1 * (20 * 10 - 10)                 # 837.9 + 19 = 856.9
    assert (buy.kind, buy.qty, buy.price, buy.chain) == ("buy", 20, 10, "flip:2589")
    assert buy.expected == pytest.approx(expected) and buy.net == pytest.approx(expected - 200)
    assert (post.kind, post.qty, post.price, post.at_ms, post.window_start_ms, post.peak_ms, post.duration_h) == \
        ("post", 20, 49, NOW, None, None, 8)                                # min(50 − 1, max(48, 55)), due now, 8 h
    assert (post.p, post.chain) == (pytest.approx(0.9), "flip:2589")
    sat = dumped_linen(windows=(PlanWindow(SAT * 24 + 17, 4, SAT * 24 + 18, 6),), book=((10, 20, 4, 1), (50, 20, 1, 5)),
                       p90=75,                                                  # it has traded at 72c (TT22)
                       how=tuple(1.5 if h == SAT * 24 + 18 else 1.0 for h in range(168)))
    buy, post = flip(sat, s)
    assert (post.price, post.peak_ms, buy.expected) == (72, MONDAY + SAT * DAY + 18 * HOUR, pytest.approx(1368))


def test_R26_a_flip_relists_now_unless_its_early_window_pays_enough_more():
    s = replace(S, default_undercut_share=0.0)
    how = tuple(1.05 if h == SAT * 24 + 18 else 1.0 for h in range(168))          # Saturday 18:00: 48 × 1.05 → 50c

    def saturday(weeks: int) -> SellFacts:
        """R24's Linen with a Saturday window backed by `weeks` weeks of data."""
        return dumped_linen(windows=(PlanWindow(SAT * 24 + 17, 4, SAT * 24 + 18, weeks),), how=how,
                            book=((10, 20, 4, 1), (50, 20, 1, 5)))
    _, early = flip(saturday(1), s)
    assert (early.price, early.window_start_ms) == (49, None)       # 20 × 50 × 0.95 = 950 < 856.9 × 1.125 = 964.0
    _, trusted = flip(saturday(6), s)
    assert (trusted.price, trusted.peak_ms) == (50, MONDAY + SAT * DAY + 18 * HOUR)    # 950 > 856.9
    # In the plan the window must also pay its timing premium (SPEC §3.4D.2): from Fri 14:00 the Sat 17:00 window
    # waits 1.13 days (20.65%), and 950 ÷ 1.2065 = 787.4 < 856.9, so the funded flip relists now.
    planned = plans.actions(world({LINEN: saturday(6)}), NOW, s=s, gold={"Amaya-G": RICH})
    assert [(a.price, a.window_start_ms) for a in planned if a.kind == "post"] == [(49, None)]


def test_R28_a_relist_now_needs_a_listing_left_or_enough_closes_to_ground_its_price():
    s = replace(S, default_undercut_share=0.0)
    spaulders = dumped_linen(key=7005, level=1_500_000, med7=1_500_000, p90=1_500_000, post_price=1_500_000,
                             book=((3500, 1, 3, 1),), cur=3500, cur_qty=1, ref_stack=1, sold_per_day=1.0,
                             supply_now=1, vendor_sell=100, closes_30d=4)      # one listing at 35s, med7 150g, 4 closes
    thin = plans.plan(world({7005: spaulders}), NOW, s=s, gold={"Amaya-G": RICH})
    assert not [a for a in thin.actions if a.kind == "buy"]                    # nothing to relist against
    assert [(a.candidate.strategy, a.reason) for a in thin.alternatives] == [("flip", "No listing left to price against")]
    measured = plans.plan(world({7005: replace(spaulders, closes_30d=30)}), NOW, s=s, gold={"Amaya-G": RICH})
    assert [(a.kind, a.price) for a in measured.actions] == [("buy", 3500), ("post", 1_500_000)]  # 30 closes: med7
    linen = plans.plan(world({LINEN: dumped_linen(closes_30d=4, med7=55, p90=60)}), NOW, s=s, gold={"Amaya-G": RICH})
    assert [(a.kind, a.price) for a in linen.actions] == [("buy", 10), ("post", 49)]     # under the 50c level left
    opts = flips.relisted(flips._remaining_book(spaulders, 1), plan.post_options(spaulders, NOW, s), s)
    assert [o.cap for o in opts] == [0]                                        # posting now has no room


def gloves(**changes) -> SellFacts:
    """Embossed Leather Gloves (R27): listed at 8s and 16s, med7 and p90 15s, nobody buys them (P 0);
    Amaya disenchants one for 21s, a merchant pays 1s."""
    base = SellFacts(4239, level=1500, trend7=0.0, closes_30d=720, post_price=799, sold_per_day=0.0, listed_per_day=0.0, ref_stack=1,
                     how=tuple([1.0] * 168), sales_how=tuple([1.0] * 168), windows=(),
                     book=((800, 1, 3, 1), (1600, 1, 4, 1)), cur=800, cur_qty=1, vendor_sell=100, de_value=2100.0,
                     de_char="Amaya-G", supply_now=2, conf=75, med7=1500, p90=1500)
    return replace(base, **changes)


def test_R27_a_flip_whose_post_pays_no_more_than_disenchanting_is_not_a_flip():
    # P 0: the post is worth 1 × (21s − deposit) per glove, below the 21s of disenchanting it: a disenchant buy
    # (the Disenchant page lists it), not a flip, even though 21s against 8s paid clears the margin.
    assert flip(gloves()) == []
    # Listed at 8s only, med7 and p90 30s, 2.7 sold a day (0.9 of a glove in the 8 h window): posting at 30s is
    # worth about 0.9 × 30s × 0.95 + 0.1 × 21s ≈ 27s 75c per glove, above 21s: a flip.
    selling = gloves(book=((800, 1, 3, 1),), sold_per_day=2.7, med7=3000, p90=3000, level=3000)
    buy, post = flip(selling)
    assert (buy.qty, buy.price, post.price, post.p) == (1, 800, 3000, pytest.approx(0.9))
    assert post.expected / post.qty > 2100


def test_R25_no_flip_when_the_next_level_is_too_close():
    close = dumped_linen(book=((45, 20, 4, 1), (50, 20, 4, 5)), cur=45)
    assert flip(close, replace(S, default_undercut_share=0.0)) == []         # 856.9 < 1.2 × 900


def test_relisting_needs_something_to_price_by():
    alone = dumped_linen(book=((10, 20, 4, 1),), med7=None, p90=None)       # nothing left after the buy
    [now] = flips.relisted(flips._remaining_book(alone, 1), plan.post_options(alone, NOW, S), S)
    assert now.cap == 0
    assert flip(alone) == []


@pytest.mark.parametrize("s", [s for s in SHARED if s["call"] == "flip_now_offer"], ids=lambda s: s["name"])
def test_FN1_a_flip_seen_in_a_live_book_shared_with_the_addon(s):
    result = flips.flip_now_offer(**s["args"])
    assert (result is None) == (s["expect"] is None), s["why"]
    if result is not None:
        assert result == pytest.approx(s["expect"]), s["why"]
