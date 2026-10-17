"""Tests for ahsync/engine/volume.py, including invariant VI1."""
from __future__ import annotations

import pytest

from ahsync.engine.volume import (
    Book, BookEntry, Volume, calibration_factor, daily_volume, estimate_volume, expiring_fraction, listing_turnover,
)
from tests.support.gen import cases

HOUR_MS = 3_600_000
LINEN = 2589
SHORT, MEDIUM, LONG, VERY_LONG = 1, 2, 3, 4


def books(prev: list[tuple], next_: list[tuple], hours: float = 2) -> tuple[Book, Book]:
    """Two books `hours` apart; entries as (price, stack, band, count) for Linen Cloth.

    Args:
        prev: Earlier book's entries.
        next_: Later book's entries.
        hours: Gap between them.

    Returns:
        (prev book, next book).
    """
    def book(at_ms: int, rows: list[tuple]) -> Book:
        """A book of Linen rows at a time."""
        return Book(at_ms, tuple(BookEntry(LINEN, *row) for row in rows))
    return book(0, prev), book(int(hours * HOUR_MS), next_)


def linen(prev: list[tuple], next_: list[tuple], hours: float = 2) -> Volume:
    """Linen Cloth's estimated volume between two books.

    Args:
        prev: Earlier book's entries (price, stack, band, count).
        next_: Later book's entries.
        hours: Gap between them.

    Returns:
        The Volume.
    """
    return estimate_volume(*books(prev, next_, hours))[LINEN]


def test_V1_plain_sales():
    assert linen([(50, 20, LONG, 5)], [(50, 20, LONG, 3)]) == Volume(40, 0, 0)


def test_V2_gone_auctions_that_were_about_to_expire_count_as_expired():
    assert linen([(50, 20, SHORT, 4)], []) == Volume(0, 80, 0)


def test_V3_expired_is_capped_at_the_expiring_count():
    v = linen([(50, 20, LONG, 3), (50, 20, SHORT, 1)], [(50, 20, LONG, 1)])
    assert v == Volume(40, 20, 0)


def test_V4_new_listings_are_not_sales():
    assert linen([(50, 20, LONG, 2)], [(50, 20, LONG, 5)]) == Volume(0, 0, 60)


def test_V5_an_undercut_repost_looks_like_a_sale_plus_a_new_listing():
    assert linen([(50, 20, LONG, 1)], [(45, 20, LONG, 1)]) == Volume(20, 0, 20)


def test_V6_time_left_ticking_down_is_not_a_sale():
    assert linen([(50, 20, LONG, 2)], [(50, 20, MEDIUM, 2)]) == Volume(0, 0, 0)


def test_V7_units_are_auctions_times_stack_summed_per_item():
    v = linen([(50, 20, LONG, 2), (50, 5, LONG, 4)], [(50, 20, LONG, 1), (50, 5, LONG, 2)])
    assert v.sold == 30


def test_V8_books_more_than_six_hours_apart_give_no_estimate():
    assert estimate_volume(*books([(50, 20, LONG, 1)], [], hours=7)) is None


def test_V9_own_sold_mail_calibrates_the_book_estimate():
    assert calibration_factor(confirmed_sold=3, estimated_sold=5) == 0.6


def test_V10_a_quantity_drop_is_listing_turnover_and_never_sales():
    assert listing_turnover(120, 80, 2 * HOUR_MS) == 40
    assert listing_turnover(80, 120, 2 * HOUR_MS) == 0
    assert listing_turnover(120, 80, 7 * HOUR_MS) is None
    only_turnover = daily_volume({d: {"qty_drop": Volume(40, 0, 0)} for d in range(7)}, range(7))
    assert only_turnover.sold_per_day is None and only_turnover.days_by_source == {}


def test_V11_premium_volume_is_preferred_over_an_estimate():
    rows = {1: {"ahf": Volume(50, 5, 60), "books": Volume(40, 5, 60)}}
    result = daily_volume(rows, [1])
    assert result.days_by_source == {"ahf": 1}
    assert result.sold_per_day == 50


def test_V12_medium_auctions_have_all_run_out_after_two_hours():
    assert linen([(50, 20, MEDIUM, 2)], []) == Volume(0, 40, 0)


def test_V13_long_auctions_expire_in_proportion_to_the_gap():
    # expiring = 3 × (5 − 2) / (8 − 2) = 1.5 auctions → 30 units expired, 30 sold
    assert linen([(50, 20, LONG, 3)], [], hours=5) == Volume(30, 30, 0)


def test_V14_book_estimates_alone_work_without_a_key():
    rows = {d: {"books": Volume(40, 10, 50)} for d in range(7)}
    result = daily_volume(rows, range(7))
    assert (result.sold_per_day, result.days_by_source) == (40, {"books": 7})


def test_V15_each_day_uses_its_best_source():
    rows = {d: {"ahf": Volume(50, 0, 0)} for d in range(1, 21)}
    rows.update({d: {"books": Volume(40, 0, 0)} for d in range(21, 31)})
    result = daily_volume(rows, range(1, 31))
    assert result.sold_per_day == pytest.approx((20 * 50 + 10 * 40) / 30)     # ≈ 46.7
    assert result.days_by_source == {"ahf": 20, "books": 10}


def test_V16_ahf_volume_calibrates_estimate_only_days():
    rows = {d: {"ahf": Volume(50, 0, 0), "books": Volume(40, 0, 0)} for d in range(10)}
    rows.update({d: {"books": Volume(40, 0, 0)} for d in range(10, 20)})
    # factor 500 / 400 = 1.25 from the overlap days, applied to estimate-only days
    # even in a window that has no AHF days of its own (e.g. after Premium lapsed)
    assert daily_volume(rows, range(10, 20)).sold_per_day == pytest.approx(50.0)
    assert daily_volume(rows, range(20)).sold_per_day == pytest.approx(50.0)


def test_V17_both_present_uses_ahf_and_the_estimate_is_kept():
    rows = {1: {"ahf": Volume(50, 0, 0), "books": Volume(40, 0, 0)}}
    assert daily_volume(rows, [1]).days_by_source == {"ahf": 1}
    assert rows[1]["books"] == Volume(40, 0, 0)       # nothing consumed or dropped


def test_V18_one_calibration_with_ahf_preferred_over_mail():
    rows = {0: {"ahf": Volume(50, 0, 0), "books": Volume(40, 0, 0)}, 1: {"books": Volume(40, 0, 0)}}
    result = daily_volume(rows, [0, 1], mail_factor=0.6)
    assert result.sold_per_day == pytest.approx((50 + 40 * 1.25) / 2)


def test_mail_factor_applies_without_ahf_overlap_and_lesser_sources_follow():
    rows = {0: {"books": Volume(40, 0, 0)}, 1: {"mail": Volume(3, 1, 0)}, 2: {"qty_drop": Volume(9, 0, 0)}}
    result = daily_volume(rows, [0, 1, 2, 3], mail_factor=0.5)
    assert result.sold_per_day == pytest.approx((20 + 3) / 2)          # the turnover day is not a sales day
    assert result.days_by_source == {"books": 1, "mail": 1}


def test_no_data_in_the_window_is_unknown_volume():
    assert daily_volume({}, range(7)) == daily_volume({5: {}}, [5])
    assert daily_volume({}, range(7)).sold_per_day is None


def test_calibration_with_nothing_estimated_is_none():
    assert calibration_factor(3, 0) is None


def test_expiring_fraction_bounds():
    assert expiring_fraction(SHORT, 0.25) == 0.5
    assert expiring_fraction(VERY_LONG, 2) == 0.0
    assert expiring_fraction(LONG, 9) == 1.0


SEED_VI1 = 20261016


def random_books(rng) -> tuple[Book, Book]:
    """Two random books of 1-3 items, 0-6 h apart."""
    def entries():
        """Random entries."""
        return tuple(BookEntry(rng.choice([1, 2, 3]), rng.choice([40, 45, 50]), rng.choice([1, 5, 20]),
                               rng.randint(1, 4), rng.randint(1, 6)) for _ in range(rng.randint(0, 6)))
    return Book(0, entries()), Book(rng.randint(0, 6 * HOUR_MS), entries())


def test_VI1_volume_splits_exactly_what_is_gone_into_sold_and_expired():
    for i, (prev, next_) in enumerate(cases(SEED_VI1, random_books)):
        result = estimate_volume(prev, next_)
        gap_h = next_.at_ms / HOUR_MS
        for key, v in result.items():
            group_prev = sum(e.count * e.stack for e in prev.entries if e.key == key)
            group_next = sum(e.count * e.stack for e in next_.entries if e.key == key)
            expiring = sum(e.count * e.stack * expiring_fraction(e.band, gap_h) for e in prev.entries if e.key == key)
            context = f"seed {SEED_VI1} case {i}: {prev} -> {next_}"
            assert v.sold >= 0 and v.expired >= 0 and v.listed >= 0, context
            assert v.expired <= expiring + 1e-9, context
            assert v.sold + v.expired - v.listed == pytest.approx(group_prev - group_next), context
