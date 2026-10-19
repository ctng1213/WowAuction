"""Tests for ahsync/engine/volume.py, including invariant VI1."""
from __future__ import annotations

import pytest

from ahsync.engine.volume import (
    Auction, Book, BookEntry, Cover, IdBook, Volume, calibration_factor, covered_seconds, daily_volume, day_shares,
    estimate_volume, estimate_volume_ids, expiring_fraction, group_book, hourly_sales, observed_turnover, listing_turnover, supply_to_volume,
)
from tests.support.gen import cases

HOUR_MS = 3_600_000
LINEN = 2589
SHORT, MEDIUM, LONG, VERY_LONG = 1, 2, 3, 4
ORE = 2770
D = 20_000                     # a Friday: (20000 + 3) mod 7 = 4
FLAT = [1.0] * 168


def hours(first: int, last: int) -> tuple[int, ...]:
    """A cover's seconds for whole hours first ≤ h < last.

    Args:
        first: First hour observed.
        last: Hour after the last one observed.

    Returns:
        24 values in seconds.
    """
    return tuple(3600 if first <= h < last else 0 for h in range(24))


TEN_TO_NOON = hours(10, 12)


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


def test_V10_a_quantity_drop_is_listing_turnover_and_never_sales():
    assert listing_turnover(120, 80, 2 * HOUR_MS) == 40
    assert listing_turnover(80, 120, 2 * HOUR_MS) == 0
    assert listing_turnover(120, 80, 7 * HOUR_MS) is None
    only_turnover = daily_volume({d: {"qty_drop": Volume(40, 0, 0)} for d in range(7)}, range(7))
    assert only_turnover.sold_per_day is None and only_turnover.days_by_source == {}


def test_V11_a_whole_day_beats_a_partly_observed_one_of_the_same_kind():
    rows = {1: {"ahf": Volume(50, 5, 60), "books": Volume(40, 5, 60)}}
    two_hours = {1: {"books": Cover("groups", TEN_TO_NOON)}}
    result = daily_volume(rows, [1], two_hours)
    assert (result.days_by_source, result.sold_per_day) == ({"ahf": 1}, 50)


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


def test_V16_id_diffs_calibrate_estimate_only_days():
    rows = {d: {"ids": Volume(50, 0, 0), "books": Volume(40, 0, 0)} for d in range(10)}
    rows.update({d: {"books": Volume(40, 0, 0)} for d in range(10, 20)})
    cover = {d: {"ids": Cover("ids")} for d in range(10)}
    # factor 500 / 400 = 1.25 from the overlap days, applied to estimate-only days
    # even in a window that has no id days of its own
    assert daily_volume(rows, range(10, 20), cover).sold_per_day == pytest.approx(50.0)
    assert daily_volume(rows, range(20), cover).sold_per_day == pytest.approx(50.0)


def test_V17_both_present_uses_the_id_diffs_and_the_estimate_is_kept():
    rows = {1: {"ids": Volume(50, 0, 0), "books": Volume(40, 0, 0)}}
    assert daily_volume(rows, [1], {1: {"ids": Cover("ids")}}).days_by_source == {"ids": 1}
    assert rows[1]["books"] == Volume(40, 0, 0)       # nothing consumed or dropped


def test_V18_mail_never_calibrates_the_market_estimate():
    rows = {0: {"books": Volume(40, 0, 0), "mail": Volume(3, 0, 0)}, 1: {"books": Volume(40, 0, 0)}}
    assert daily_volume(rows, [0, 1]).sold_per_day == 40


def test_V30_a_partly_observed_day_is_scaled_to_a_whole_day():
    rows = {D: {"books": Volume(40, 2, 12)}}
    result = daily_volume(rows, [D], {D: {"books": Cover("groups", TEN_TO_NOON)}}, FLAT)
    assert result.sold_per_day == pytest.approx(480)          # 40 ÷ (2/24)
    assert (result.expired_per_day, result.listed_per_day) == (pytest.approx(24), pytest.approx(144))
    assert result.volume_score == pytest.approx(0.3 + 0.5 * 2 / 24)
    assert daily_volume(rows, [D], {D: {"books": Cover("groups", TEN_TO_NOON)}}).sold_per_day == pytest.approx(480)


def test_V31_the_sales_pattern_weights_the_hours_observed():
    friday = [0.0] * 168
    first = (D + 3) % 7 * 24
    assert first == 4 * 24                                     # day D is a Friday
    for h in range(24):
        friday[first + h] = 1.0
    friday[first + 10] = friday[first + 11] = 30 / 2 * 22 / 70    # 10:00-12:00 hold 30% of the day's sales
    share, time_share = day_shares(Cover("groups", TEN_TO_NOON), D, friday)
    assert (share, time_share) == (pytest.approx(0.3), pytest.approx(2 / 24))
    rows = {D: {"books": Volume(40, 0, 0)}}
    assert daily_volume(rows, [D], {D: {"books": Cover("groups", TEN_TO_NOON)}}, friday).sold_per_day == \
        pytest.approx(40 / 0.3)
    zero = [0.0] * 168                                         # no pattern on that day: by hours observed
    assert day_shares(Cover("groups", TEN_TO_NOON), D, zero) == (pytest.approx(2 / 24), pytest.approx(2 / 24))


def test_V32_sources_rank_by_kind_then_hours_observed_not_by_name():
    rows = {0: {"mail": Volume(3, 0, 0), "books": Volume(40, 0, 0), "blizzard": Volume(50, 0, 0)},
            1: {"a": Volume(10, 0, 0), "b": Volume(30, 0, 0)},
            2: {"qty_drop": Volume(9, 0, 0)}}
    cover = {0: {"blizzard": Cover("ids")},
             1: {"a": Cover("groups", hours(10, 12)), "b": Cover("groups", hours(10, 16))}}
    result = daily_volume(rows, [0, 1, 2], cover)
    assert result.days_by_source == {"blizzard": 1, "b": 1}
    assert result.sold_per_day == pytest.approx((50 + 30 * 50 / 40) / (1 + 6 / 24))   # day 0 calibrates the books
    nothing = daily_volume({0: {"x": Volume(9, 0, 0)}}, [0], {0: {"x": Cover("groups", hours(0, 0))}})
    assert nothing.sold_per_day is None                        # observed no time: no source


def test_V33_auction_ids_tell_partial_buys_and_expiries_apart():
    prev = IdBook(0, (Auction(1, LINEN, 20, 50, LONG), Auction(2, ORE, 200, 30, VERY_LONG), Auction(3, LINEN, 5, 50, SHORT)))
    next_ = IdBook(HOUR_MS, (Auction(2, ORE, 150, 30, VERY_LONG), Auction(4, LINEN, 10, 48, VERY_LONG)))
    assert estimate_volume_ids(prev, next_) == {LINEN: Volume(20, 5, 10), ORE: Volume(50, 0, 0)}
    assert estimate_volume_ids(prev, IdBook(7 * HOUR_MS, ())) is None


def test_V34_estimates_never_fall_below_your_own_sales():
    rows = {0: {"books": Volume(0, 0, 0), "mail": Volume(6, 0, 0)}}
    assert daily_volume(rows, [0]).sold_per_day == 6
    partial = daily_volume(rows, [0], {0: {"books": Cover("groups", TEN_TO_NOON)}})
    assert partial.sold_per_day == pytest.approx(6)            # 6 × 2/24 observed, scaled back to a day


def test_V35_mail_alone_is_the_last_resort_source():
    result = daily_volume({d: {"mail": Volume(3, 1, 0)} for d in range(7)}, range(7))
    assert (result.sold_per_day, result.days_by_source, result.volume_score) == (3, {"mail": 7}, pytest.approx(0.4))


def test_covered_seconds_splits_pairs_by_clock_hour_and_folds_the_day_before():
    assert covered_seconds([(10 * HOUR_MS, 12 * HOUR_MS)]) == hours(10, 12)
    folded = covered_seconds([(-HOUR_MS // 2, HOUR_MS // 2)])  # 23:30 the day before → 00:30
    assert (folded[23], folded[0], sum(folded)) == (1800, 1800, 3600)


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


SEED_VI2, SEED_VI3 = 20261019, 20261020


def random_id_books(rng) -> tuple[IdBook, IdBook]:
    """Two snapshots with ids: some auctions stay (maybe partly bought), some go, some are new.

    Args:
        rng: The shared Random.

    Returns:
        (prev, next).
    """
    prev, later, next_id = [], [], 100
    for i in range(rng.randint(0, 8)):
        a = Auction(i, rng.choice((LINEN, ORE)), rng.randint(1, 200), rng.randint(1, 99), rng.randint(1, 4))
        prev.append(a)
        fate = rng.random()
        if fate < 0.4:
            later.append(Auction(a.id, a.key, rng.randint(1, a.qty), a.price, max(1, a.band - rng.randint(0, 1))))
    for _ in range(rng.randint(0, 4)):
        later.append(Auction(next_id, rng.choice((LINEN, ORE)), rng.randint(1, 50), rng.randint(1, 99), 4))
        next_id += 1
    gap = rng.randint(0, 6 * HOUR_MS)
    return IdBook(0, tuple(prev)), IdBook(gap, tuple(later))


def test_VI2_id_diffs_split_exactly_the_units_that_left_into_sold_and_expired():
    for i, (prev, next_) in enumerate(cases(SEED_VI2, random_id_books)):
        result = estimate_volume_ids(prev, next_)
        after = {a.id: a for a in next_.auctions}
        for key in (LINEN, ORE):
            left = sum(a.qty - after[a.id].qty if a.id in after else a.qty for a in prev.auctions if a.key == key)
            new = sum(b.qty for b in next_.auctions if b.key == key and b.id not in {a.id for a in prev.auctions})
            v = result.get(key, Volume(0, 0, 0))
            context = f"seed {SEED_VI2} case {i}: {prev} -> {next_}"
            assert v.sold >= 0 and v.expired >= 0 and v.listed >= 0, context
            assert (v.sold + v.expired, v.listed) == (pytest.approx(left), new), context
            assert v.expired <= sum(a.qty for a in prev.auctions if a.key == key and a.id not in after) + 1e-9, context


def random_partial_week(rng) -> tuple[dict, dict, list[float]]:
    """A week of book rows, each day observed for some whole hours, and a random sales pattern.

    Args:
        rng: The shared Random.

    Returns:
        (rows, covers, sales_how).
    """
    rows, covers = {}, {}
    for day in range(D, D + 7):
        first = rng.randint(0, 22)
        last = rng.randint(first + 1, 24)
        rows[day] = {"books": Volume(rng.randint(0, 100), rng.randint(0, 20), rng.randint(0, 50))}
        covers[day] = {"books": Cover("groups", hours(first, last))}
    return rows, covers, [rng.uniform(0.0, 3.0) for _ in range(168)]


def test_VI3_coverage_scaling_is_a_rate_never_negative_and_whole_days_are_the_mean():
    for i, (rows, covers, how) in enumerate(cases(SEED_VI3, random_partial_week)):
        days = range(D, D + 7)
        context = f"seed {SEED_VI3} case {i}"
        result = daily_volume(rows, days, covers, how)
        assert result.sold_per_day >= 0 and 0 < result.volume_score <= 0.8, context
        doubled = {d: {"books": Volume(2 * v.sold, 2 * v.expired, 2 * v.listed) for v in rows[d].values()} for d in days}
        twice = {d: {"books": Cover("groups", tuple(2 * s for s in c["books"].covered_s))} for d, c in covers.items()}
        assert daily_volume(doubled, days, twice, how).sold_per_day == pytest.approx(result.sold_per_day), context
        whole = daily_volume(rows, days)
        assert whole.sold_per_day == pytest.approx(sum(r["books"].sold for r in rows.values()) / 7), context


def test_V22_supply_to_volume_is_never_made_up():
    assert supply_to_volume(93, 50) == pytest.approx(1.86)     # 93 listed ÷ 50 sold a day
    assert supply_to_volume(93, None) is None                  # sales unavailable: "—"
    assert supply_to_volume(93, 0) is None                     # nothing sells: "—", not infinity
    assert supply_to_volume(None, 50) is None                  # never seen listed


def linen_book(at_hours: float, auctions: int) -> Book:
    """A book with Linen 50c ×20 long × auctions, at a time of day.

    Args:
        at_hours: Hours after midnight (day 0).
        auctions: Number of auctions.

    Returns:
        The Book.
    """
    return Book(int(at_hours * HOUR_MS), (BookEntry(LINEN, 50, 20, 3, auctions),))


def test_V25_hourly_sales_need_books_at_most_two_hours_apart():
    rates = hourly_sales([linen_book(10, 5), linen_book(11, 3), linen_book(14, 2)])
    assert rates == {LINEN: {10 * HOUR_MS: 40.0}}


def test_V26_pairs_in_one_hour_pool_their_sold_units_and_gaps():
    rates = hourly_sales([linen_book(10, 5), linen_book(10.5, 4), linen_book(11, 2)])
    assert rates == {LINEN: {10 * HOUR_MS: 60.0}}


def test_hourly_sales_of_nothing_sold_is_an_observed_zero():
    assert hourly_sales([linen_book(10, 5), linen_book(11, 5)]) == {LINEN: {10 * HOUR_MS: 0.0}}
    assert hourly_sales([linen_book(10, 5), linen_book(10, 4)]) == {}          # same time: no gap, no pair
    assert hourly_sales([linen_book(10, 5)]) == {}


def test_V23_median_daily_supply_leaves_days_without_observations_out():
    from ahsync.engine.volume import median_daily_supply
    day, now = 86_400_000, 1_760_000_000_000
    obs = [(now - 2 * day, 80), (now - 5 * day, 100), (now - 9 * day, 120)]   # 3 days seen, the rest of 30 not
    assert median_daily_supply(obs, now) == 100                                # not dragged to 0 by the empty days
    assert median_daily_supply([(now - day, 90), (now - day + 3_600_000, 110)], now) == 100   # one day: its median
    assert median_daily_supply([(now - 31 * day, 50)], now) is None and median_daily_supply([], now) is None


def test_K4_backtest_median_ratio_and_days_within_25_percent():
    from ahsync.engine.volume import Backtest, backtest
    assert backtest([0.8] * 4 + [1.0] * 4 + [1.5] * 2) == Backtest(10, 1.0, 8)     # 1.25 would count; 1.5 doesn't
    assert backtest([0.75, 1.25, 1.3]) == Backtest(3, 1.25, 2) and backtest([]) == Backtest(0, None, 0)


def test_V36_a_snapshot_groups_into_an_order_book():
    snapshot = IdBook(5, (Auction(1, LINEN, 20, 50, LONG), Auction(2, LINEN, 20, 50, LONG), Auction(3, ORE, 7, 9, SHORT)))
    assert group_book(snapshot) == Book(5, (BookEntry(LINEN, 50, 20, LONG, 2), BookEntry(ORE, 9, 7, SHORT, 1)))


def test_V37_observed_listing_turnover_is_units_that_left_per_day_observed_never_sales():
    ten = 10 * HOUR_MS
    supply = [(ten + 10 * HOUR_MS, 50), (ten, 120), (ten + 2 * HOUR_MS, 80), (ten + 3 * HOUR_MS, 90)]
    # 120 → 80 (40 left), 80 → 90 (none), 90 → 50 is 7 h later (not a pair): 40 units over 3 h → 320 a day
    assert observed_turnover(supply, ten + 11 * HOUR_MS) == pytest.approx(320)
    assert observed_turnover(supply, ten + 30 * 86_400_000) is None and observed_turnover([], 0) is None
