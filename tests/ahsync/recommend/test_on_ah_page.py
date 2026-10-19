"""Tests for ahsync/recommend/on_ah_page.py: the On the AH payload (SPEC §3.5)."""
from __future__ import annotations

from dataclasses import replace

from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel
from ahsync.recommend.on_ah_page import on_ah_page

HOUR = 3_600_000
LINEN, ROD = 2589, 6217
ITEMS = {LINEN: ItemInfo(LINEN, "Linen Cloth", 1, 5, 7, None, 10, 20), ROD: ItemInfo(ROD, "Copper Rod", 1, 5, 7, None, 30, 1)}
LINEN_INTEL = ItemIntel(LINEN, 45, 20, HOUR, 49, 49, 40, 60, 0.4, 48.0, 0.0, 61.0, "ahf", {"ahf": 7}, 45, 20, 0.9,
                        False, 42.0, 86, 93, None, ())


class FakeState:
    """Amaya's Linen and Copper Rod auctions; the rod's post was captured."""

    def own_auctions(self, char_ids):
        """Stored auctions."""
        return [("Amaya-G", LINEN, 45, 5, 3, 2, None, None), ("Amaya-G", ROD, 4100, 1, 4, 1, 9, None)]

    def cost_basis(self):
        """The rod's mats cost 10s."""
        return {ROD: (1, 1000)}


def test_the_page_joins_auctions_with_cost_deposit_and_intel():
    stats = {LINEN: replace(LINEN_INTEL, volume_unknown=True)}
    page = on_ah_page(FakeState(), ["Amaya-G"], stats, ITEMS)
    rod, linen = page["rows"]
    assert (rod["cost_each"], rod["deposit"], rod["position"], rod["chance"]) == ("10s", "9c", "cheapest", "—")
    assert (linen["qty"], linen["deposit"], linen["position"]) == (10, "—", "cheapest")       # cheapest 45c
    assert "help" in page and page["tiles"][3][1] == "9c"


def test_D53_the_chance_counts_only_the_time_an_auction_has_left():
    from ahsync.engine import sellprob
    from ahsync.engine.sellprob import MarketSettings, Supply
    from ahsync.recommend.on_ah_page import _below, _chance_left
    now, hour = 1_760_000_000_000, 3_600_000
    s = LINEN_INTEL
    short, long = (_chance_left(s, 45, 20, left, now, MarketSettings()) for left in (hour, 8 * hour))
    expected = sellprob.posting_chance(Supply(s.sold_per_day, s.listed_per_day, None, s.cur, s.cur_qty), 45, 20, now,
                                       replace(MarketSettings(), sell_window_hours=1))[0]
    assert short == expected and short <= long
    assert _chance_left(s, 45, 20, 0, now, MarketSettings()) == 0.0               # its time is up
    booked = replace(s, book=((38, 5, 4, 2), (40, 2, 4, 1), (45, 20, 4, 1)))
    assert (_below(booked, 45), _below(booked, 30), _below(s, 45), _below(None, 45)) == ((12, 38, 40), (0, 0, 0), None, None)
