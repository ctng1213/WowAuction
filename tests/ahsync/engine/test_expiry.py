"""Tests for ahsync/engine/expiry.py: selling and expiring compete when an auction vanishes (SPEC §2.4, BZ44)."""
from __future__ import annotations

import pytest

from tests.support.gen import cases

from ahsync.engine.expiry import RATE_WINDOW_MS, EarlyLeaveRates, expired_fraction, expired_given_vanished

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
    tallies = [(end - RATE_WINDOW_MS, {1: (500 * h, 90)}),                          # just outside the day before: ignored
               (end - 2 * h, {1: (60 * h, 6), 2: (10 * h, 0), 3: (50 * h, 0)}),
               (end, {1: (40 * h, 4)})]
    rates = EarlyLeaveRates(tallies, {1, 2, 9})
    rate = lambda key: rates.rate(key, end) * h                              # noqa: E731
    assert rate(1) == pytest.approx(15 / 155) and rate(2) == pytest.approx(5 / 65)   # pooled: 10 leaves in 110 h
    assert rate(9) == pytest.approx(10 / 110)                                        # no exposure: the pool's rate
    assert rate(3) == 0.0                                                            # gear's pool saw no leave
    assert rates.rate(1, end - RATE_WINDOW_MS - h) == 0.0                     # causal: no gap ended in its window


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
