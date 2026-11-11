"""Tests for ahsync/engine/expiry.py: selling and expiring compete when an auction vanishes (SPEC §2.4, BZ44)."""
from __future__ import annotations

import pytest

from tests.support.gen import cases

from ahsync.engine.expiry import RATE_WINDOW_MS, LeaveWindow, expired_fraction, expired_given_vanished, tally_early_leaves

HOUR_MS = 3_600_000
SEED_VI7 = 4242


def test_BZ44_selling_and_expiring_compete_when_an_auction_vanishes_in_its_expiry_hour():
    h = HOUR_MS
    rate = 0.1 / h                                    # the item's listings leave early at 0.1 an hour
    assert expired_given_vanished((0, h), (0, h), rate) == pytest.approx(0.95163, abs=1e-5)   # (1 − e^−0.1) ÷ 0.1
    assert expired_given_vanished((h // 2, 3 * h // 2), (0, h), rate) == pytest.approx(0.84722, abs=1e-5)
    assert expired_fraction((h // 2, 3 * h // 2), h) == 0.5                                   # the old split
    assert expired_given_vanished((0, h), (0, h), 0.0) == 1.0                                 # nothing ever sold early
    assert expired_given_vanished((2 * h, 3 * h), (0, h), rate) == 0.0                        # the gap is before it
    assert expired_given_vanished((0, h), (2 * h, 3 * h), rate) == 1.0                        # a band clash: old rule
    end = 30 * h
    window = LeaveWindow().advance(end - RATE_WINDOW_MS, {1: (500 * h, 90, True)})   # just outside the day before
    assert window.rate(1, True) * h == pytest.approx(0.18)    # alone in its pool: 95 ÷ (500 + 5 × 500 ÷ 90) a listing-hour
    window = window.advance(end - 2 * h, {1: (60 * h, 6, True), 2: (10 * h, 0, True), 3: (50 * h, 0, False)})
    window = window.advance(end, {1: (40 * h, 4, True)})                     # drops the tally ended 24 h before
    rate = lambda key, commodity=True: window.rate(key, commodity) * h      # noqa: E731
    assert rate(1) == pytest.approx(15 / 155) and rate(2) == pytest.approx(5 / 65)   # pooled: 10 leaves in 110 h
    assert rate(9) == pytest.approx(10 / 110)                                        # no exposure: the pool's rate
    assert rate(3, False) == 0.0                                                     # gear's pool saw no leave
    assert LeaveWindow().rate(1, True) == 0.0                                        # nothing ended yet


def test_VI7_the_split_is_a_share_falling_as_selling_gets_likelier():
    def case(rng):
        last = rng.randint(0, 10) * HOUR_MS
        absent = last + rng.randint(10, 6 * 60) * 60_000
        start = last + rng.randint(-3 * 60, 6 * 60) * 60_000
        end = start + rng.randint(1, 3 * 60) * 60_000
        rates = sorted(rng.uniform(0, 2) / HOUR_MS for _ in range(4))
        return (start, end), (last, absent), rates
    for window, gap, rates in cases(SEED_VI7, case):
        shares = [expired_given_vanished(window, gap, r) for r in rates]
        assert all(0.0 <= s <= 1.0 for s in shares), (SEED_VI7, window, gap)
        assert all(a >= b - 1e-12 for a, b in zip(shares, shares[1:])), (SEED_VI7, window, gap, shares)
        overlap = max(window[0], gap[0]) < min(window[1], gap[1])
        if overlap and window[1] > gap[0]:
            assert expired_given_vanished(window, gap, 0.0) == 1.0, (SEED_VI7, window, gap)
        if gap[1] <= window[0]:
            assert shares == [0.0] * len(rates), (SEED_VI7, window, gap)


def test_BZ44_the_window_holds_the_day_before_and_its_sums_stay_exact():
    h = HOUR_MS
    window = LeaveWindow()
    for k in range(1, 50):                                   # hourly gaps for two days, one leave in each
        window = window.advance(k * h, {7: (3 * h, 1, False)})
    assert [at for at, _ in window.tallies] == [k * h for k in range(26, 50)]   # (49 h − 24 h, 49 h]
    assert window.own == {7: (24 * 3 * h, 24)} and window.pools == {True: (0, 0), False: (24 * 3 * h, 24)}
    window = window.advance(100 * h, {8: (h, 0, True)})      # a day's hole: everything before it is dropped
    assert window.own == {8: (h, 0)} and window.pools == {True: (h, 0), False: (0, 0)}
    with pytest.raises(ValueError):                          # time order is what the drop relies on
        window.advance(100 * HOUR_MS, {8: (HOUR_MS, 0, True)})


def test_BZ44_a_tally_row_carries_the_flag_its_listings_had_then():
    live = {1: (5, 10, (10 * HOUR_MS, 20 * HOUR_MS), 100, False, None, None),
            2: (5, 4, (9 * HOUR_MS, 21 * HOUR_MS), 90, True, None, None),          # the same key, flagged
            3: (6, 1, (0, HOUR_MS), 50, False, None, None)}                         # could expire: not tallied
    vanished = [(2, 5, 4, 0.0, False, None, 90)]
    assert tally_early_leaves(live, vanished, HOUR_MS, 2 * HOUR_MS) == {5: (2 * HOUR_MS, 1, True)}
