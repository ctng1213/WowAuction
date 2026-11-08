"""Tests for ahsync/engine/unseen.py: sales a commodity listing made before its first snapshot (SPEC §2.4, BZ45)."""
from __future__ import annotations

import pytest

from ahsync.engine.unseen import unseen_sales
from ahsync.engine.volume import Auction, IdBook, life_flows, lifetimes

H = 3_600_000
LINEN, GLOVES = 2589, 6585


def test_BZ45_a_listing_sold_before_its_first_snapshot_at_the_rate_it_sells_once_seen():
    gaps = [(0, H), (H, 2 * H)]
    listed = [(1, LINEN, (0, H), True)]
    assert unseen_sales(gaps, listed, [(1, LINEN, 6, (H, 2 * H))], {}) == {(H, 2 * H): {LINEN: 3.0}}
    both = unseen_sales(gaps, listed, [(1, LINEN, 6, (H, 2 * H))], {(1, (H, 2 * H)): 4.0})
    assert both == {(H, 2 * H): {LINEN: 5.0}}                             # 6 bought down + 4 at its end: 10 × ½
    short = [(0, 40 * 60_000), (40 * 60_000, 160 * 60_000)]
    assert unseen_sales(short, [(1, LINEN, short[0], True)], [(1, LINEN, 8, short[1])], {})[short[1]][LINEN] == pytest.approx(8 * 20 / 120)
    assert unseen_sales(gaps, [(2, GLOVES, (0, H), False)], [], {(2, (H, 2 * H)): 1.0}) == {}
    assert unseen_sales([(0, H)], listed, [], {}) == {}   # its next gap isn't linked: nothing


def test_BZ45_the_estimate_is_part_of_sold_and_kept_apart_as_unseen():
    a = lambda qty: Auction(1, LINEN, qty, 50, 4, True)                      # noqa: E731
    flows = life_flows(lifetimes([IdBook(0, ()), IdBook(H, (a(20),)), IdBook(2 * H, (a(14),))]))
    f = flows[(H, 2 * H)][LINEN]
    assert (f.sold, f.unseen) == (9.0, 3.0)                              # 6 seen bought + 6 × ½ before it was seen
    assert flows[(0, H)][LINEN].sold == 0.0                              # never the posting gap (BZ44's review)
