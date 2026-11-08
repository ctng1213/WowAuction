"""Tests for ahsync/engine/sell_through.py, including invariant VI6."""
from __future__ import annotations

import pytest

from ahsync.engine.sell_through import (DAY_MS, MIN_LISTINGS, SellThrough, Tally, cancel_counts, cancel_share, class_rates, days_of,
                                        sell_through, tally)
from tests.support.gen import cases

NOW = 20_400 * DAY_MS + 15 * 3_600_000            # day 20400, 15:00 UTC


def row(ended=0.0, expired=0.0, cancels=0.0, t1_ended=0.0, t1_left=0.0, commodity=1) -> tuple:
    """A stored lifetimes row (ended, expired, matched, cancels, cancelled units, t1_ended, t1_left, commodity)."""
    return (ended, expired, 0.0, cancels, cancels * 20, t1_ended, t1_left, commodity)


def test_BZ41_sell_through_by_lookback():
    # Linen over 7 days: 94 tier-1 listings ended, 79 left early, 5 of them cancels (BZ40): 74 bought.
    linen = {20_394: row(t1_ended=40, t1_left=30), 20_400: row(t1_ended=54, t1_left=44)}
    t = tally(linen, NOW, 7)
    assert (t.ended, t.left, t.commodity) == (94, 74, True)
    st = sell_through(t, 0.11, None)
    assert st.how == "m" and st.rate == pytest.approx(74 / 94)                  # 79%
    # The gloves: 100 tier-1 listings, 7 left early, cancels unseen; the market's commodity cancel share 11%.
    gloves = sell_through(Tally(100, 7, False), 0.11, None)
    assert gloves.how == "e" and gloves.rate == pytest.approx(0.07 * 0.89)      # 6.2% -> 6%, est.
    # A thin item: 12 listings, 3 bought; its class at 50%.
    thin = sell_through(Tally(12, 3, True), 0.11, 0.5)
    assert thin.how == "c" and thin.rate == pytest.approx((3 + 30 * 0.5) / 42)  # (12 x 25% + 30 x 50%) / 42
    assert sell_through(Tally(12, 3, True), 0.11, None) == SellThrough(None, "")  # nothing to borrow: "—"


def test_BZ41_the_lookbacks_count_their_own_days():
    lifetimes = {20_371: row(t1_ended=10, t1_left=1), 20_387: row(t1_ended=10, t1_left=2),
                 20_394: row(t1_ended=10, t1_left=3), 20_370: row(t1_ended=99, t1_left=99)}
    assert days_of(NOW, 7) == range(20_394, 20_401)
    assert [tally(lifetimes, NOW, d).ended for d in (7, 14, 30)] == [10, 20, 30]   # day 20370 is 31 days back
    assert tally({}, NOW, 7) == Tally(0, 0, False)


def test_BZ40_the_markets_commodity_cancel_share():
    silk = {20_400: row(ended=10, expired=1, cancels=1)}                         # 9 left early, 1 cancelled
    linen = {20_399: row(ended=12, expired=2, cancels=0)}                        # 10 left early
    gear = {20_400: row(ended=50, expired=0, cancels=0, commodity=0)}            # not a commodity: not counted
    old = {20_300: row(ended=100, expired=0, cancels=100)}                       # outside the 30 days
    assert cancel_counts(silk, NOW) == (1, 9) and cancel_counts(gear, NOW) == (0, 0) and cancel_counts(old, NOW) == (0, 0)
    assert cancel_share(cancel_counts(i, NOW) for i in (silk, linen, gear, old)) == pytest.approx(1 / 19)
    assert cancel_share([cancel_counts(gear, NOW)]) is None


def test_BZ41_a_class_pools_its_items():
    rates = class_rates([(7, Tally(10, 5, True)), (7, Tally(30, 10, False)), (2, Tally(0, 0, True)),
                         (None, Tally(5, 5, True))], 0.5)
    assert rates == {7: pytest.approx((5 + 10 * 0.5) / 40)}                     # gear's cancels taken out
    assert sell_through(Tally(MIN_LISTINGS, 15, True), None, 0.9) == SellThrough(0.5, "m")   # enough: its own
    assert sell_through(Tally(40, 20, False), None, None) == SellThrough(0.5, "e")            # unknown share counts 0


SEED_VI6 = 20261107


def random_item(rng) -> tuple[Tally, float | None, float | None]:
    """A tally, a cancel share and a class rate, each sometimes missing."""
    ended = rng.choice([0, rng.randint(1, 29), rng.randint(30, 400)])
    left = rng.uniform(0, ended)
    return (Tally(ended, left, rng.random() < 0.5), rng.choice([None, rng.random()]),
            rng.choice([None, rng.random()]))


def test_VI6_sell_through_is_a_share_and_a_borrow_lies_between_its_inputs():
    for i, (t, cancels, class_rate) in enumerate(cases(SEED_VI6, random_item)):
        context = f"seed {SEED_VI6} case {i}: {t} {cancels} {class_rate}"
        st = sell_through(t, cancels, class_rate)
        if st.rate is None:
            assert t.ended < MIN_LISTINGS and class_rate is None and st.how == "", context
            continue
        assert 0.0 <= st.rate <= 1.0, context
        if st.how == "c":
            own = (t.left if t.commodity else t.left * (1 - (cancels or 0))) / t.ended if t.ended else class_rate
            assert min(own, class_rate) - 1e-12 <= st.rate <= max(own, class_rate) + 1e-12, context
        else:
            assert t.ended >= MIN_LISTINGS and st.how == ("m" if t.commodity else "e"), context
