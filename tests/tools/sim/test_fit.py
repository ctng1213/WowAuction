"""Tests for tools/sim/fit.py: markets fitted from collected raw (SPEC §8.10; plan §1.8)."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))

from ahsync.engine.volume import Auction, IdBook  # noqa: E402
from sim.fit import (Observed, busiest, durations, fit_item, observe, position_bin,  # noqa: E402
                     price_mix, summary)
from sim.market import HOUR_MS, Item, World  # noqa: E402
from sim.runner import market_books  # noqa: E402

START = 1_793_923_200_000                    # Fri 2026-11-06 00:00 UTC
MIX = ((0.5, 1.0, 1.0), (0.3, 1.0, 1.05), (0.2, 1.15, 1.5))
PLANTED = Item(2589, "Linen Cloth", True, 50, 200, 120, durations=((2, 0.1), (8, 0.8), (24, 0.1)),
               max_units=9, price_mix=MIX, stacks=((5, 0.5), (20, 0.5)))


def test_a_new_listing_is_binned_by_its_price_against_the_lowest_other():
    assert position_bin(50, []) == "only"
    assert [position_bin(p, [50, 70]) for p in (49, 50, 52, 57, 70, 80)] == ["below", "at", "5%", "15%", "50%", "over"]


def test_only_listings_first_seen_after_a_short_gap_are_observed_with_their_band_units_and_position():
    first = IdBook(START, (Auction(1, 7, 20, 50, 4, True), Auction(2, 9, 1, 900, 3)))
    second = IdBook(START + HOUR_MS, (Auction(1, 7, 20, 50, 4, True), Auction(3, 7, 5, 50, 3, True),
                                      Auction(4, 7, 2, 49, 2, True), Auction(2, 9, 1, 900, 3)))
    late = IdBook(START + 4 * HOUR_MS, (Auction(5, 7, 1, 60, 4, True),))       # after a 3 h gap: not fresh
    seen = observe([first, second, late])
    assert seen.positions[True] == Counter({"5%": 1, "below": 1})     # 50 against 49, 49 against 50; 1 was there before
    assert seen.bands[True] == Counter({3: 1, 2: 1}) and seen.items[7].stacks == Counter({5: 1, 2: 1})
    assert seen.items[7].new_listings == 2 and seen.items[7].floors == (50, 49, 60)
    assert seen.hours == pytest.approx(4.0)              # the lifetimes link gaps up to 6 h; only freshness needs 90 min
    assert set(observe([first, second], frozenset({9})).items) == {9}


def test_the_price_mix_takes_the_observed_positions_and_can_replace_the_undercut_share():
    seen = Observed(1.0, {True: Counter({"below": 1, "at": 6, "5%": 3, "only": 5}), False: Counter()},
                    {True: Counter({3: 3, 4: 1}), False: Counter()}, {})
    assert price_mix(seen, True) == ((0.1, 0.9, 1.0), (0.6, 1.0, 1.0), (0.3, 1.0, 1.05))
    weights = [w for w, _, _ in price_mix(seen, True, undercut=0.2)]
    assert weights == pytest.approx([0.2, 0.6 * 0.8 / 0.9, 0.3 * 0.8 / 0.9]) and sum(weights) == pytest.approx(1)
    assert durations(seen, True) == ((8, 0.75), (24, 0.25)) and durations(seen, False) == ((24, 1.0),)


def test_a_planted_market_is_recovered_from_its_own_snapshots():
    _, books = market_books(World(3, START, 2, (PLANTED,)))
    seen = observe(books)
    fitted = fit_item(seen, 2589, name="Linen Cloth")
    assert dict(fitted.durations) == pytest.approx({2: 0.1, 8: 0.8, 24: 0.1}, abs=0.05)
    assert fitted.commodity and fitted.max_units >= 1 and fitted.fair_price == pytest.approx(50, rel=0.2)
    assert {units for units, w in fitted.stacks if w > 0.1} <= {5, 20} or max(fitted.stacks, key=lambda s: s[1])[0] <= 20
    assert fitted.listings_per_day < PLANTED.listings_per_day          # some posts are bought before any snapshot
    assert busiest(seen, 5) == [2589]
    assert summary(seen, [2589])[0].startswith("Linked hours")
