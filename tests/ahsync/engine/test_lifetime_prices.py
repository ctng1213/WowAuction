"""Tests for ahsync/engine/lifetime_prices.py: new listings' positions and sold auctions' prices (SPEC §2.4)."""
from __future__ import annotations

from ahsync.engine.lifetime_prices import new_listing_positions, position_milli, sold_prices, units_milli, weighted_median
from ahsync.engine.volume import Auction, Ending, IdBook, Lifetimes, lifetimes

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
