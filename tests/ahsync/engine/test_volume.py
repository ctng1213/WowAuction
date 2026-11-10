"""Tests for ahsync/engine/volume.py, including invariant VI1."""
from __future__ import annotations

import pytest

from ahsync.engine.volume import (NO_PRIOR, VolumePrior, posterior_rate,
    MAX_GAP_MS, Auction, Book, BookEntry, Cover, IdBook, Volume, book_pairs, calibration_factor, covered_seconds, daily_volume, day_shares,
    estimate_volume, estimate_volume_ids, expiring_fraction, group_book, hourly_sales, observed_turnover, listing_turnover, supply_to_volume,
    DURATION_MS, LifeFlow, band_window, expired_fraction, gap_hours, life_flows, lifetimes, narrow, sold_by_hour,
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


def test_V42_a_partial_fill_is_measured_exactly():
    prev = Book(0, (BookEntry(ORE, 30, 200, LONG, 1),))
    next_ = Book(2 * HOUR_MS, (BookEntry(ORE, 30, 150, LONG, 1),))
    assert estimate_volume(prev, next_)[ORE] == Volume(50, 0, 0)


def test_V43_a_same_price_re_split_is_nothing_and_a_new_price_repost_is_sold_plus_listed():
    assert linen([(30, 200, LONG, 1)], [(30, 100, LONG, 2)]) == Volume(0, 0, 0)
    assert linen([(30, 100, LONG, 1)], [(28, 100, LONG, 1)]) == Volume(100, 0, 100)


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


def test_V40_hourly_sales_skip_a_cross_source_pair_seconds_apart():
    ten = 10 * HOUR_MS
    ahf = Book(ten, (BookEntry(LINEN, 50, 20, 3, 5),), "ahf")
    close = Book(ten + 30_000, (BookEntry(LINEN, 50, 20, 3, 5), BookEntry(ORE, 100, 5, 3, 2)), "local")
    later = Book(ten + HOUR_MS, (BookEntry(LINEN, 50, 20, 3, 3),), "local")
    assert hourly_sales([ahf, close, later]) == {LINEN: {ten: 40.0}}     # ahf 10:00 -> local 11:00 only
    assert book_pairs([(0, "ahf"), (30_000, "local"), (HOUR_MS, "local")], MAX_GAP_MS) == [(0, 2)]
    assert book_pairs([(0, "local"), (30_000, "local")], MAX_GAP_MS) == [(0, 1)]     # same source: any gap > 0


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


PRIOR = VolumePrior()            # 1 a day, worth 2 h = 1/12 of a day
SEED_VI4 = 20261021


def test_V41_a_short_sample_is_pulled_toward_the_prior():
    two_hours = {D: {"books": Cover("groups", TEN_TO_NOON)}}
    none_sold = daily_volume({D: {"books": Volume(0, 4, 4)}}, [D], two_hours, FLAT, PRIOR)
    assert none_sold.sold_per_day == pytest.approx((0 + 1 / 12) / (1 / 12 + 1 / 12))           # 0.5, not 0
    assert none_sold.listed_per_day == pytest.approx((4 + 1 / 12) / (2 / 12))                  # 24.5
    assert none_sold.expired_per_day == pytest.approx(48)                                      # no prior: 4 ÷ 1/12
    forty = daily_volume({D: {"books": Volume(40, 0, 0)}}, [D], two_hours, FLAT, PRIOR)
    assert forty.sold_per_day == pytest.approx(240.5)                                          # not 480 (V30)
    week = daily_volume({d: {"ahf": Volume(40, 0, 0)} for d in range(7)}, range(7), prior=PRIOR)
    assert week.sold_per_day == pytest.approx((280 + 1 / 12) / (7 + 1 / 12))                  # ≈ 39.5
    assert daily_volume({}, range(7), prior=PRIOR).sold_per_day is None                        # unknown stays unknown
    assert PRIOR.days == pytest.approx(1 / 12) and NO_PRIOR.days == 0


def random_sample(rng) -> tuple[float, float, float, float]:
    """Sold units, day-equivalents observed and a prior.

    Args:
        rng: The shared Random.

    Returns:
        (sold, days observed, prior per day, prior hours).
    """
    return rng.uniform(0, 500), rng.uniform(0.01, 30), rng.uniform(0, 50), rng.uniform(0, 48)


def test_VI4_the_prior_rate_is_monotonic_bounded_and_fades_with_more_data():
    for i, (sold, days, per_day, prior_hours) in enumerate(cases(SEED_VI4, random_sample)):
        context = f"seed {SEED_VI4} case {i}"
        prior = VolumePrior(per_day, prior_hours)
        rate = posterior_rate(sold, days, prior)
        raw = sold / days
        assert posterior_rate(sold + 1, days, prior) >= rate, context
        assert min(raw, per_day) - 1e-9 <= rate <= max(raw, per_day) + 1e-9, context
        far = posterior_rate(raw * 10_000, 10_000, prior)                    # the same rate over 10,000 days
        assert abs(far - raw) <= abs(rate - raw) + 1e-9, context
        assert far == pytest.approx(raw, rel=0.01, abs=0.05), context


# --- Auction lifetimes (SPEC §2.4, M33a) ---------------------------------------------------------------------------

THU_1702 = (20_000 + 6) * 86_400_000 + 17 * HOUR_MS + 2 * 60_000     # a Thursday, 17:02 UTC
MINUTE = 60_000


def band_left(left_ms: int) -> int:
    """The time-left band Blizzard's API gives an auction with left_ms to run (ID_BAND_HOURS)."""
    hours_left = left_ms / HOUR_MS
    return SHORT if hours_left <= 0.5 else MEDIUM if hours_left <= 2 else LONG if hours_left <= 12 else VERY_LONG


def hourly(first_ms: int, count: int, auctions: list[tuple]) -> list[IdBook]:
    """Hourly snapshots; each auction (id, key, posted ms, expires ms, gone ms, qty at ms -> units) is listed from
    its posting until it expires or is gone, with the band its true time left gives."""
    out = []
    for k in range(count):
        at = first_ms + k * HOUR_MS
        listed = [Auction(i, key, qty(at), 125, band_left(expires - at))
                  for i, key, posted, expires, gone, qty in auctions if posted <= at < min(expires, gone)]
        out.append(IdBook(at, tuple(listed)))
    return out


def test_BZ22_lifetimes_name_each_listings_fate():
    thu_1700 = THU_1702 - 2 * MINUTE
    posted = thu_1700 + 30 * MINUTE
    snaps = hourly(thu_1700, 26, [
        (1, LINEN, posted, posted + 24 * HOUR_MS, 1 << 62, lambda at: 5),        # 24 h, runs out Fri 17:30
        (2, LINEN, posted, posted + 8 * HOUR_MS, thu_1700 + 4 * HOUR_MS - MINUTE, lambda at: 5),   # 8 h, gone at 20:59
        (3, ORE, posted + 30 * MINUTE - 1, posted + 30 * MINUTE + 20 * MINUTE, 1 << 62, lambda at: 2)])  # short at 18:00
    assert snaps[24].auctions[0].band == SHORT and snaps[3].auctions[1].band == LONG
    fates = {e.auction: (e.expired, e.gap) for e in lifetimes(snaps).endings}
    assert fates[1][1] == (thu_1700 + 24 * HOUR_MS, thu_1700 + 25 * HOUR_MS)                 # its end fell in 17:00-18:00
    assert 0.99 < fates[1][0] < 1.0      # BZ44: one early leave in about 26 listing-hours leaves a 1% chance it sold
    assert fates[2] == (0.0, (thu_1700 + 3 * HOUR_MS, thu_1700 + 4 * HOUR_MS))               # sold, 5-6 h left
    assert fates[3][0] == 1.0                                                                 # treated as 2 h: expired


def test_BZ26_the_bands_narrow_the_expiry_and_the_vanish_gap_decides():
    posted, expires = THU_1702 + 43 * MINUTE, THU_1702 + 24 * HOUR_MS + 43 * MINUTE     # truth: Fri 17:45
    qty = lambda at: 14 if at < THU_1702 + 6 * HOUR_MS else 10                          # 4 bought in (22:02, 23:02]
    snaps = hourly(THU_1702, 26, [(2455, LINEN, posted, expires, 1 << 62, qty)])
    lt = lifetimes(snaps)
    assert lt.fills == ((2455, LINEN, 4, (THU_1702 + 5 * HOUR_MS, THU_1702 + 6 * HOUR_MS), 125),)
    (ending,) = lt.endings
    assert (ending.qty, ending.expired, ending.gap) == (10, 1.0, (THU_1702 + 24 * HOUR_MS, THU_1702 + 25 * HOUR_MS))
    flow = life_flows(lt)[ending.gap][LINEN]
    assert (flow.sold, flow.expired, flow.ended, flow.expired_listings) == (0.0, 10.0, 1.0, 1.0)
    assert lt.clashes == 0
    early = hourly(THU_1702, 26, [(2455, LINEN, posted, expires, THU_1702 + 18 * HOUR_MS, qty)])   # gone by Fri 11:02
    (sold,) = lifetimes(early).endings
    assert (sold.expired, sold.gap) == (0.0, (THU_1702 + 17 * HOUR_MS, THU_1702 + 18 * HOUR_MS))


def test_BZ26_the_windows_come_from_posting_plus_duration_narrowed_by_bands():
    window, clash = narrow((THU_1702 + 24 * HOUR_MS, THU_1702 + 25 * HOUR_MS), THU_1702 + 24 * HOUR_MS, MEDIUM)
    assert (window, clash) == ((THU_1702 + 24 * HOUR_MS + 30 * MINUTE, THU_1702 + 25 * HOUR_MS), False)
    assert narrow(window, THU_1702, SHORT) == (band_window(THU_1702, SHORT), True)        # a clash keeps the newest
    assert expired_fraction(window, THU_1702 + 24 * HOUR_MS + 45 * MINUTE) == 0.5
    assert DURATION_MS == {VERY_LONG: 24 * HOUR_MS, LONG: 8 * HOUR_MS, MEDIUM: 2 * HOUR_MS, SHORT: 2 * HOUR_MS}
    assert band_window(THU_1702, LONG) == (THU_1702 + 2 * HOUR_MS, THU_1702 + 12 * HOUR_MS)        # Blizzard's bounds
    assert band_window(THU_1702, VERY_LONG) == (THU_1702 + 12 * HOUR_MS, THU_1702 + 24 * HOUR_MS)


def test_BZ27_relists_are_measured_and_never_subtracted():
    spaulders = 15210 + 28644 * 10 ** 6                       # a gear key with its bonus list's suffix
    at = THU_1702 + 17 * HOUR_MS                              # Fri 10:02
    before = IdBook(at, (Auction(1, spaulders, 1, 150_000, VERY_LONG), Auction(3, LINEN, 20, 125, VERY_LONG),
                         Auction(4, LINEN, 20, 125, VERY_LONG)))
    after = IdBook(at + HOUR_MS, (Auction(9, spaulders, 1, 140_000, VERY_LONG), Auction(11, LINEN, 20, 120, VERY_LONG)))
    lt = lifetimes([before, after])
    assert [(e.auction, e.matched) for e in lt.endings] == [(1, True), (3, True), (4, False)]   # one new listing, one match
    flows = life_flows(lt)[(at, at + HOUR_MS)]
    assert (flows[spaulders].sold, flows[spaulders].ended, flows[spaulders].matched) == (1.0, 1.0, 1.0)
    assert (flows[LINEN].sold, flows[LINEN].ended, flows[LINEN].matched) == (40.0, 2.0, 1.0)
    assert flows[spaulders].listed == 1.0 and flows[LINEN].listed == 20.0


def test_BZ30_sales_land_in_the_hour_they_happened():
    at = THU_1702 + 17 * HOUR_MS                              # 10:02
    one = lifetimes([IdBook(at, (Auction(1, LINEN, 10, 125, VERY_LONG),)), IdBook(at + HOUR_MS, ())])
    hour10 = at // HOUR_MS * HOUR_MS
    assert sold_by_hour(life_flows(one)) == {LINEN: {hour10: 10.0}}
    three = lifetimes([IdBook(at, (Auction(2, ORE, 9, 50, VERY_LONG),)), IdBook(at + 3 * HOUR_MS, ())])
    assert sold_by_hour(life_flows(three)) == {ORE: {hour10: 3.0, hour10 + HOUR_MS: 3.0, hour10 + 2 * HOUR_MS: 3.0}}
    assert gap_hours((at, at + 20 * MINUTE)) == [hour10]
    pooled = sold_by_hour({(at, at + 30 * MINUTE): {LINEN: LifeFlow(sold=2.0)},
                           (at + 30 * MINUTE, at + 50 * MINUTE): {LINEN: LifeFlow(sold=3.0)}})
    assert pooled == {LINEN: {hour10: pytest.approx(5.0 * 60 / 50)}}           # 5 units over 50 min
    assert sold_by_hour(life_flows(three), hours={hour10 + HOUR_MS}) == {ORE: {hour10 + HOUR_MS: 3.0}}


def test_lifetimes_keep_only_the_records_of_the_span_asked_for_but_every_window():
    at = THU_1702
    books = [IdBook(at, ()), IdBook(at + HOUR_MS, (Auction(1, LINEN, 10, 125, VERY_LONG),)),
             IdBook(at + 2 * HOUR_MS, (Auction(1, LINEN, 8, 125, VERY_LONG),)),            # a fill before the span
             IdBook(at + 3 * HOUR_MS, ())]
    full, kept = lifetimes(books), lifetimes(books, keep_from_ms=at + 3 * HOUR_MS)
    assert kept.gaps == ((at + 2 * HOUR_MS, at + 3 * HOUR_MS),) and kept.fills == () and kept.listed == ()
    assert kept.endings == full.endings                       # the posting gap, outside the span, still set the window


def test_lifetimes_do_not_link_across_a_hole():
    at = THU_1702
    lt = lifetimes([IdBook(at, (Auction(1, LINEN, 10, 125, VERY_LONG),)), IdBook(at + 7 * HOUR_MS, ())])
    assert lt.gaps == () and lt.endings == () and lt.listed == ()


SEED_VI5 = 5


def random_world(rng) -> list[IdBook]:
    """Hourly snapshots of a few auctions that shrink, vanish and appear at random (bands random too)."""
    live: dict[int, list] = {}
    snaps, next_id, at = [], 1, THU_1702
    for _ in range(rng.randint(2, 30)):
        for i in sorted(live):
            if rng.random() < 0.2:
                del live[i]
            elif rng.random() < 0.3:
                live[i][1] = max(1, live[i][1] - rng.randint(0, 5))
        for _ in range(rng.randint(0, 3)):
            live[next_id] = [rng.choice([LINEN, ORE, 15210]), rng.randint(1, 20), rng.choice([90, 100, 110])]
            next_id += 1
        snaps.append(IdBook(at, tuple(Auction(i, k, q, p, rng.randint(1, 4), k != 15210)       # gear is no commodity
                                      for i, (k, q, p) in sorted(live.items()))))
        at += rng.choice([HOUR_MS, HOUR_MS, 2 * HOUR_MS, 7 * HOUR_MS])
    return snaps


def test_VI5_lifetimes_account_for_every_unit_once():
    for n, snaps in enumerate(cases(SEED_VI5, random_world)):
        context = f"seed {SEED_VI5} case {n}"
        lt = lifetimes(snaps)
        flows = life_flows(lt)
        for e in lt.endings:
            assert 0.0 <= e.expired <= 1.0, context
        gone = sum(e.qty for e in lt.endings) + sum(f[2] for f in lt.fills)
        total = sum(f.sold - f.unseen + f.expired + f.cancelled for by_key in flows.values() for f in by_key.values())
        assert total == pytest.approx(gone), context              # observed sold + expired + cancelled = what left
        assert all(0.0 <= f.unseen <= f.sold + 1e-9 for by_key in flows.values() for f in by_key.values()), context
        for e in lt.endings:
            assert not e.cancelled or (e.expired == 0.0 and e.key != 15210), context   # BZ40: commodities, early only
        for by_key in flows.values():
            for f in by_key.values():
                assert min(f.sold, f.expired, f.listed, f.matched, f.cancelled, f.t1_left) >= 0, context
                assert f.expired_listings + f.cancels <= f.ended + 1e-9 and f.matched <= f.ended, context
                assert f.t1_left <= f.t1_ended + 1e-9 and f.t1_ended <= f.ended, context   # BZ41's share in [0, 1]
        bought: dict[int, int] = {}
        for auction, _, units, _, _ in lt.fills:
            bought[auction] = bought.get(auction, 0) + units
        first: dict[int, int] = {}
        last: dict[int, int] = {}
        for book in snaps:
            for a in book.auctions:
                first.setdefault(a.id, a.qty)
                last[a.id] = a.qty
        for auction, units in bought.items():
            assert units <= first[auction] - last[auction], context             # a partial fill never counts twice
        rates = sold_by_hour(flows)
        credited = sum(r for by_hour in rates.values() for r in by_hour.values())
        assert credited >= 0, context


def test_BZ40_a_commodity_cancel_is_no_sale():
    silk, gloves = 4306, 4243                            # Silk Cloth (a commodity); Embossed Leather Gloves (not)
    at = THU_1702
    a, b = Auction(1, silk, 5, 315, VERY_LONG, True), Auction(2, silk, 20, 320, LONG, True)
    cheap, dear = Auction(3, gloves, 1, 100, VERY_LONG), Auction(4, gloves, 1, 120, VERY_LONG)
    snaps = [IdBook(at, (a, b, cheap, dear)),
             IdBook(at + HOUR_MS, (a, cheap)),                                  # B gone, A unchanged; 1s 20c gone
             IdBook(at + 2 * HOUR_MS, (Auction(1, silk, 3, 315, VERY_LONG, True), cheap))]   # A loses 2
    lt = lifetimes(snaps)
    assert [(e.auction, e.expired, e.cancelled) for e in lt.endings] == [(2, 0.0, True), (4, 0.0, False)]
    assert lt.commodities == frozenset({silk})
    first, second = life_flows(lt)[(at, at + HOUR_MS)], life_flows(lt)[(at + HOUR_MS, at + 2 * HOUR_MS)]
    assert (first[silk].sold, first[silk].expired, first[silk].cancelled, first[silk].cancels) == (0.0, 0.0, 20.0, 1.0)
    assert first[silk].commodity and not first[gloves].commodity
    assert (first[gloves].sold, first[gloves].cancelled) == (1.0, 0.0)          # gear: left early, cancels included
    assert second[silk].sold == 2.0                                             # A's partial fill is a sale


def test_BZ40_an_equal_price_or_a_changed_cheaper_auction_is_no_proof():
    silk, at = 4306, THU_1702
    a, b = Auction(1, 4306, 5, 320, VERY_LONG, True), Auction(2, 4306, 20, 320, LONG, True)
    equal = lifetimes([IdBook(at, (a, b)), IdBook(at + HOUR_MS, (a,))])
    assert [e.cancelled for e in equal.endings] == [False]                      # same price: a buyer could take either
    cheaper = Auction(1, silk, 5, 315, VERY_LONG, True)
    bought = lifetimes([IdBook(at, (cheaper, b)), IdBook(at + HOUR_MS, (Auction(1, silk, 4, 315, VERY_LONG, True),))])
    assert [e.cancelled for e in bought.endings] == [False]                     # the cheaper one was bought from too


def test_BZ41_a_listing_posted_at_the_lowest_price_is_tier_one():
    at = THU_1702
    old = Auction(1, LINEN, 20, 125, VERY_LONG, True)
    snaps = [IdBook(at, (old,)),
             IdBook(at + HOUR_MS, (old, Auction(2, LINEN, 20, 120, VERY_LONG, True), Auction(3, LINEN, 5, 130, VERY_LONG, True))),
             IdBook(at + 2 * HOUR_MS, (old,))]
    lt = lifetimes(snaps)
    assert [(e.auction, e.tier1) for e in lt.endings] == [(2, True), (3, False)]
    flow = life_flows(lt)[(at + HOUR_MS, at + 2 * HOUR_MS)][LINEN]
    assert (flow.t1_ended, flow.t1_left, flow.ended) == (1.0, 1.0, 2.0)          # 3 was dearer: not tier 1
    assert lifetimes([IdBook(at, (old,)), IdBook(at + HOUR_MS, ())]).endings[0].tier1 is None   # posting unseen


def test_BZ40_a_snapshot_stored_before_m33b_still_finds_the_cancel():
    silk, at = 4306, THU_1702
    old = IdBook(at, (Auction(1, silk, 5, 315, VERY_LONG), Auction(2, silk, 20, 320, LONG)))   # no commodity flag
    new = IdBook(at + HOUR_MS, (Auction(1, silk, 5, 315, VERY_LONG, True), Auction(2, silk, 20, 320, LONG, True)))
    lt = lifetimes([old, new, IdBook(at + 2 * HOUR_MS, (Auction(1, silk, 5, 315, VERY_LONG, True),))])
    assert [e.cancelled for e in lt.endings] == [True]


def test_BZ40_an_item_whose_last_auction_vanishes_in_the_first_kept_gap_is_still_a_commodity():
    silk, at = 4306, THU_1702
    before = IdBook(at, (Auction(1, silk, 5, 315, VERY_LONG, True), Auction(2, silk, 20, 320, LONG, True)))
    lt = lifetimes([before, IdBook(at + HOUR_MS, ())], keep_from_ms=at + HOUR_MS)
    assert lt.commodities == frozenset({silk})
    flow = life_flows(lt)[(at, at + HOUR_MS)][silk]
    assert flow.commodity and flow.cancels == 0.0                               # nothing cheaper survived: both sold

