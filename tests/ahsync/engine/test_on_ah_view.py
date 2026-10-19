"""Tests for ahsync/engine/on_ah_view.py: the On the AH page (SPEC §3.5, D18)."""
from __future__ import annotations

from ahsync.engine.on_ah_view import Listing, on_ah_view
from ahsync.engine.plan_views import ItemLook

ROD, LINEN = 6217, 2589
LOOKS = {ROD: ItemLook("Copper Rod", 1, 70, 3.0, "ahf", 4), LINEN: ItemLook("Linen Cloth", 1)}


def test_D18_on_the_ah_rows_and_tiles_from_t9_t11():
    view = on_ah_view([Listing("Amaya-G", LINEN, 45, 5, 3, 1, None, None, 50, None),
                       Listing("Amaya-G", ROD, 4100, 1, 4, 1, 9, 1000.0, 4100, 0.6)], LOOKS)
    rod, linen = view["rows"]
    assert (rod["item"], rod["listed_total"], rod["cost_total"], rod["deposit"], rod["profit"]) == \
        ("Copper Rod", "41s", "10s", "9c", "+28s 95c")                       # 41s × 0.95 − 10s
    assert (rod["position"], rod["time_left"], rod["chance"], rod["character"]) == ("cheapest", "8 – 24 h", "60%", "Amaya")
    assert (linen["cost_each"], linen["cost_note"], linen["deposit"], linen["profit"], linen["qty"]) == \
        ("0c", "before tracking", "—", "+2s 14c", 5)                          # 2s 25c × 0.95 = 213.75c
    assert view["tiles"] == [["Listed value", "43s 25c", "2 auctions · 1 characters"],
                             ["Cost to you", "10s", "bought + crafted; found items 0"],
                             ["Profit if all sell", "+31s 9c", "after the 5% cut"],
                             ["Deposits at stake", "9c", "lost only if they expire"]]


def test_an_undercut_auction_names_the_cheapest_and_a_loss_is_negative():
    row = on_ah_view([Listing("Brugan-G", ROD, 4100, 1, 1, 2, None, 5000.0, 3900, None)], LOOKS)["rows"][0]
    assert (row["position"], row["position_class"], row["profit_class"], row["qty"]) == ("undercut: 39s", "neg", "neg", 2)
    assert row["sort"]["chance"] == -1


def test_no_listing_seen_counts_as_cheapest_and_an_unknown_band_shows_a_dash():
    row = on_ah_view([Listing("Brugan-G", ROD, 4100, 1, 9, 1, None, None, None, None)], LOOKS)["rows"][0]
    assert (row["position"], row["time_left"]) == ("cheapest", "—")
