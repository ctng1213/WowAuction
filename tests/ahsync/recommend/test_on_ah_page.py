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
        return [("Amaya-G", LINEN, 45, 5, 3, 2, None), ("Amaya-G", ROD, 4100, 1, 4, 1, 9)]

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
