"""Tests for ahsync/engine/sell_record.py: your own sell record (SPEC §3.3.3)."""
from __future__ import annotations

from ahsync.engine.sell_record import Record, RecordSettings, ResolvedPost, cause, repriced, sell_record, stacks_to_post

HOUR, DAY = 3_600_000, 86_400_000
NOW = 1_760_000_000_000
S = RecordSettings()


def post(n: int, outcome: str, price: int = 50, days_ago: float = 5) -> ResolvedPost:
    """Post n, two days after post n − 1 (around days_ago + 10 days ago), resolved 24 h later."""
    at = NOW - int((days_ago + 10) * DAY) + n * 2 * DAY
    return ResolvedPost(f"p{n}", price, at, at + DAY, outcome)


def lowest_closes(*posts: ResolvedPost) -> list[tuple[int, int]]:
    """Closes at each post's price during it: you were the cheapest."""
    return [(p.at_ms + HOUR, p.price) for p in posts]


def test_O2_no_posts_in_30_days_means_no_record():
    assert sell_record([], [], NOW, None, S) is None
    assert sell_record([post(1, "cancelled")], [], NOW, None, S) is None           # cancelled posts don't count


def test_O6_a_sale_resets_the_streak():
    posts = [post(1, "expired"), post(2, "expired"), post(3, "sold")]
    assert sell_record(posts, lowest_closes(*posts), NOW, None, S) == Record(1, 2, 0, 2, False, False)


def test_O7_expiries_31_days_ago_are_ignored():
    posts = [post(n, "expired", days_ago=31) for n in range(3)]
    assert sell_record(posts, lowest_closes(*posts), NOW, None, S) is None


def test_O5_three_expiries_while_cheapest_flag_the_item_not_selling():
    posts = [post(n, "expired") for n in range(3)]
    record = sell_record(posts, lowest_closes(*posts), NOW, None, S)
    assert (record.streak, record.not_selling) == (3, True)


def test_O11_an_expiry_while_undercut_leaves_the_streak_alone_and_caps_the_next_price():
    first, undercut = post(1, "expired"), post(2, "expired")
    closes = lowest_closes(first) + [(undercut.at_ms + HOUR, 47)]                   # someone listed at 47c
    assert cause(undercut, closes) == "undercut" and cause(first, closes) == "lowest"
    record = sell_record([first, undercut], closes, NOW, None, S)
    assert (record.streak, record.undercut_last) == (1, True)


def test_O14_an_expiry_with_no_observations_counts_like_lowest():
    record = sell_record([post(1, "expired")], [], NOW, None, S)
    assert (record.streak, record.lowest_expiries) == (1, 1)


def test_O15_after_keep_only_later_posts_count():
    posts = [post(n, "expired") for n in range(3)]
    keep_at = posts[-1].at_ms + HOUR
    assert sell_record(posts, lowest_closes(*posts), NOW, keep_at, S) is None


def test_O16_after_keep_one_more_expiry_while_cheapest_flags_it_again():
    old, new = post(1, "expired", days_ago=10), post(2, "expired", days_ago=2)
    record = sell_record([old, new], lowest_closes(old, new), NOW, old.at_ms + DAY, S)
    assert (record.expired, record.streak, record.not_selling) == (1, 1, True)


def test_O17_after_keep_an_undercut_expiry_doesnt_flag_it():
    old, new = post(1, "expired", days_ago=10), post(2, "expired", days_ago=2)
    closes = [(new.at_ms + HOUR, 40)]
    record = sell_record([old, new], closes, NOW, old.at_ms + DAY, S)
    assert (record.streak, record.undercut_last, record.not_selling) == (0, True, False)


def test_O3_a_streak_of_2_posts_a_quarter_of_the_stacks():
    assert (stacks_to_post(8, 2), stacks_to_post(5, 0), stacks_to_post(0, 3)) == (2, 5, 0)


def test_O4_a_streak_never_posts_fewer_than_one_stack():
    assert stacks_to_post(1, 1) == 1


def test_O12_O13_the_price_drops_5_percent_per_step_but_never_below_the_vendor_floor():
    assert repriced(50, 2, 0.05, 1, None) == 45                 # 50 × 0.95² = 45.125 → 45
    assert repriced(50, 2, 0.05, 47, None) == 47                # floor ceil(44 ÷ 0.95) = 47
    assert repriced(50, 0, 0.05, 1, 46) == 46                   # an undercut cap of curOthers − 1
    assert repriced(100, 1, 0.05, 1, None) == 95                # floored without float slips
