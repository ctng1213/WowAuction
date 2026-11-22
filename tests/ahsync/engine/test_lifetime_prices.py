"""Tests for ahsync/engine/lifetime_prices.py: new listings' positions and sold auctions' prices (SPEC §2.4)."""
from __future__ import annotations

from ahsync.engine.lifetime_prices import (Level, SoldHour, ask_level, clean_sold_prices, new_listing_positions,
                                          position_milli, price_level, sold_curve, sold_prices, units_milli,
                                          weighted_median, weighted_sold)
from ahsync.engine.volume import Auction, IdBook
from ahsync.engine.lifetimes import Ending, Lifetimes, lifetimes

HOUR_MS = 3_600_000
LINEN, ORE, BRACERS = 2589, 2770, 6584
VERY_LONG = 4
THU_1702 = (20_000 + 6) * 86_400_000 + 17 * HOUR_MS + 2 * 60_000     # a Thursday, 17:02 UTC


def test_BZ29_new_listings_are_placed_against_the_book_they_joined():
    at = THU_1702
    before = IdBook(at, (Auction(1, LINEN, 20, 125, VERY_LONG),))
    after = IdBook(at + HOUR_MS, (Auction(1, LINEN, 20, 125, VERY_LONG), Auction(2, LINEN, 20, 120, VERY_LONG),
                                  Auction(3, LINEN, 10, 130, VERY_LONG), Auction(4, ORE, 5, 50, VERY_LONG)))
    lt = lifetimes([before, after])
    assert [(a, price, lowest) for a, _, _, _, price, lowest in lt.listed] == [(2, 120, 125), (3, 130, 120), (4, 50, None)]
    assert new_listing_positions(lt) == {(at, at + HOUR_MS): {LINEN: {960: 20.0, 1083: 10.0}}}   # ore has no book: left out
    assert position_milli(120, 125) == 960 and position_milli(1, 3) == 333 and position_milli(5, 6) == 833


def test_BZ52_a_gap_keeps_what_sold_at_the_price_it_sold_at():
    gap = (THU_1702, THU_1702 + HOUR_MS)
    endings = (Ending(1, BRACERS, 3, gap, 0.0, False, price=200),                   # left early: 3 sold at 2 s
               Ending(2, LINEN, 20, gap, 0.5, False, price=50),                     # half expired: 10 sold at 50 c
               Ending(3, LINEN, 8, gap, 0.0, False, cancelled=True, price=45),      # a cancel sold nothing
               Ending(5, ORE, 4, gap, 1.0, False, price=30),                        # expired whole: nothing
               Ending(6, ORE, 2, gap, 0.0, False))                                  # no price known: nothing
    lt = Lifetimes((gap,), {gap: frozenset({LINEN, ORE, BRACERS})}, endings, ((4, LINEN, 5, gap, 48),), (), 0)
    assert sold_prices(lt) == {gap: {BRACERS: {200: 3.0}, LINEN: {50: 10.0, 48: 5.0}}}


def test_BZ52_the_lifetimes_keep_each_sale_at_its_auctions_price():
    at = THU_1702
    run = lifetimes([IdBook(at, (Auction(1, LINEN, 10, 125, VERY_LONG),)),
                     IdBook(at + HOUR_MS, (Auction(1, LINEN, 6, 125, VERY_LONG),)),    # 4 bought from it
                     IdBook(at + 2 * HOUR_MS, ())])                                    # the other 6 left early
    assert [f[4] for f in run.fills] == [125] and [e.price for e in run.endings] == [125]
    assert sold_prices(run) == {(at, at + HOUR_MS): {LINEN: {125: 4.0}}, (at + HOUR_MS, at + 2 * HOUR_MS): {LINEN: {125: 6.0}}}


def test_BZ53_the_sold_median_is_weighted_by_units_with_the_even_count_rule_at_exactly_half():
    assert weighted_median([(100, 60.0), (200, 40.0)]) == (100, 100.0)           # 60 of 100 reach half at 1 s
    assert weighted_median([(200, 50.0), (100, 50.0)]) == (150, 100.0)           # exactly half at 1 s: (100 + 200) // 2
    assert weighted_median([(100, 25.0), (100, 25.0), (300, 50.0)]) == (200, 100.0)   # a price may repeat
    assert weighted_median([(100, 50.0)]) == (100, 50.0)                          # half at the last price: itself
    assert weighted_median([]) == (None, 0.0) and weighted_median([(100, 0.0004)]) == (None, 0.0)
    # in floats 0.1 + 0.2 passes 0.3 and the median would read 1 s; in thousandths, as stored, it is exactly half
    assert weighted_median([(100, 0.1), (100, 0.2), (200, 0.3)]) == (150, 0.6)
    assert units_milli(0.0005) == 1 and units_milli(2.0) == 2000


NOW = 500_000 * HOUR_MS + 20 * 60_000                 # an hour's 20th minute: its hour is age 0


def sold(hours_ago: int, price: int, units: float, clean: float | None = None) -> SoldHour:
    """A stored price `hours_ago` hours before NOW's hour, every unit clean unless said."""
    return SoldHour((NOW // HOUR_MS - hours_ago) * HOUR_MS, price, units, units if clean is None else clean)


def fields(level: Level, *names: str) -> tuple:
    """Some of a Level's fields, in order."""
    return tuple(getattr(level, n) for n in names)


def test_the_clean_units_are_those_that_cannot_have_expired():
    gap = (THU_1702, THU_1702 + HOUR_MS)
    lt = Lifetimes((gap,), {}, (Ending(1, LINEN, 4, gap, 0.0, False, price=50),           # left early, no expiry chance
                                Ending(2, LINEN, 2, gap, 0.25, False, price=50),          # 1.5 sold, may have expired
                                Ending(3, LINEN, 8, gap, 0.0, False, cancelled=True, price=45)),
                   ((9, LINEN, 3, gap, 48),), (), (), set(), {}, ())
    assert sold_prices(lt) == {gap: {LINEN: {50: 5.5, 48: 3.0}}}
    assert clean_sold_prices(lt) == {gap: {LINEN: {50: 4.0, 48: 3.0}}}       # the fill is a certain sale


def test_BZ69_the_level_is_the_recency_weighted_sold_median_else_the_ask_blend():
    three = [sold(0, 100, 10.0), sold(24, 200, 10.0), sold(48, 300, 10.0)]   # weights 10, 5 and 2.5
    assert price_level(three, NOW, None, (None, None, None), None) == Level(100, "sold", 30.0, None, 100, 100)
    hour = NOW // HOUR_MS
    how = [1.0] * 168
    how[(hour // 24 + 3) % 7 * 24 + hour % 24] = 0.8
    assert price_level(three, NOW, how, (None, None, None), None).level == 125     # the switch on: 100 ÷ 0.8
    asks = (60, 90, 120)
    assert fields(price_level([sold(0, 80, 2.999)], NOW, None, asks, None), "level", "source") == (77, "asks")   # floor(540 ÷ 7)
    assert fields(price_level([sold(0, 80, 3.0)], NOW, None, asks, None), "level", "source") == (80, "sold")
    pieces = [sold(0, 80, u) for u in (0.020, 0.356, 0.244, 0.245, 2.135)]      # 3.000 stored; 2.9999… as floats
    assert fields(price_level(pieces, NOW, None, asks, None), "source", "units") == ("sold", 3.0)
    assert ask_level(60, None, 120) == 72 and price_level([], NOW, None, (None, None, None), None).level is None
    assert price_level([sold(30 * 24, 80, 9.0)], NOW, None, asks, None).source == "asks"   # older than 30 days


def test_BZ72_a_thin_items_ask_level_is_held_under_its_few_sales():
    asks = (60, 90, 120)                                                       # the ask level 77 c
    assert price_level([sold(0, 50, 2.0)], NOW, None, asks, None) == Level(50, "asks", 2.0, None, 50, 50)
    assert price_level([sold(0, 100, 2.0)], NOW, None, asks, None).level == 77           # never raised
    assert fields(price_level([sold(0, 50, 2.999)], NOW, None, asks, None), "level", "source") == (50, "asks")
    assert price_level([sold(48, 50, 1.0)], NOW, None, asks, None).level == 50          # one sale, 48 h old
    assert price_level([sold(0, 50, 2.0)], NOW, None, asks, 40) == Level(50, "asks", 2.0, None, 40, 50)   # p10_30 40 c
    assert price_level([], NOW, None, asks, None).level == 77


def test_BZ70_the_bounds_come_from_the_clean_sold_curve_at_20_weighted_units():
    rows = [sold(0, 80, 2.0), sold(0, 100, 14.0), sold(0, 150, 4.0)]
    assert price_level(rows, NOW, None, (None, None, None), 60) ==         Level(100, "sold", 20.0, (80, 80, 100, 100, 100, 150, 150), 80, 150)
    ambiguous = rows[:2] + [sold(0, 150, 4.0, clean=0.0)]                  # 16 clean units: no curve
    assert price_level(ambiguous, NOW, None, (None, None, None), 60) == Level(100, "sold", 20.0, None, 60, 100)
    a_day_old = [sold(24, p, u) for p, u in ((80, 2.0), (100, 14.0), (150, 4.0))]   # 20 units weigh 10
    assert price_level(a_day_old, NOW, None, (None, None, None), 60).curve is None
    nineteen = [sold(0, 80, 2.0), sold(0, 100, 13.0), sold(0, 150, 4.0)]
    assert fields(price_level(nineteen, NOW, None, (None, None, None), 60), "curve", "low", "high") == (None, 60, 100)
    bounds = [fields(price_level([], NOW, None, (60, 90, 120), p), "low", "high") for p in (50, 90, None)]
    assert bounds == [(50, 77), (77, 77), (77, 77)]           # a low bound is never above the level
    assert sold_curve([(100, 19.999)]) is None and sold_curve([(100, 20.0)]) == (100,) * 7
    assert sold_curve([(100, 0.4996)] * 40) is None                    # 19.984 in all, not 40 roundings up to 20
    assert weighted_sold([sold(0, 100, 0.0)], NOW, None, clean=False) == []
