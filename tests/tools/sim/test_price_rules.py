"""Tests for tools/sim/price_rules.py: SM20, the price-level rules paper-traded (sold-price plan §1d)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from sim.inference import START_MS  # noqa: E402
from sim.market import DAY_MS  # noqa: E402
from sim.price_rules import (CHANGE_DAY, CLOTH, DAYS, GROUPS, LAUNCH, REAL, REAL_RATIOS, RULES,  # noqa: E402
                             SEGMENTS, Kind, Post, ask_blend, earned, group_of, regime_shifts, rule_price,
                             rules_world, segment_of, sm20, sold_level, sold_rows)

AFTER = START_MS + CHANGE_DAY * DAY_MS


def item(**fields):
    """An item's intelligence with only the fields a rule reads."""
    base = dict(med7=None, med14=None, med30=None, p90_30=None, closes_30d=100, sold_med7=None, sold_units7=0.0)
    return SimpleNamespace(**{**base, **fields})


def test_SM20_sold_levels_earn_more_per_post_than_todays_ask_ceiling_on_steady_gear(tmp_path):
    found = sm20(tmp_path, replicates=1, rules=("today", "sold half-life"))
    world = [m for m in found if m.what.startswith("world: ")]
    measures = {m.what.split(" (")[0]: m for m in found if not m.what.startswith("world: ")}
    today = measures["today: earned per post ÷ the worth, gear at 5+ a week, steady"]
    level = measures["sold half-life: earned per post ÷ the worth, gear at 5+ a week, steady"]
    assert today.band == level.band == "reported" and today.truth == 1.0
    assert level.engine > today.engine + 0.2            # the review's runs: about 0.85 against 0.35
    assert len(measures) == 2 * len(SEGMENTS) * len(GROUPS)
    assert len(world) == 2 * len(REAL_RATIOS) + len(GROUPS)       # the calibration against the real summary


def test_the_half_life_level_follows_the_newest_sales():
    old_and_new = [(100, 10.0, 3), (200, 3.0, 0)]       # 10 units at 1 s three days ago weigh 1.25; 3 at 2 s today 3
    assert sold_level(old_and_new) == (200, 13.0)
    assert sold_level(old_and_new, half_life_days=30.0) == (100, 13.0)   # nearly unweighted: 9.3 against 3
    assert sold_level([]) == (None, 0.0)


def test_the_ask_blend_weighs_the_medians_4_2_1_over_the_ones_known():
    assert ask_blend(item(med7=100, med14=130, med30=200)) == (400 + 260 + 200) // 7      # 122
    assert ask_blend(item(med7=100, med30=200)) == 600 // 5 and ask_blend(item()) is None


def test_each_rule_holds_the_undercut_under_its_own_level():
    it = item(med7=150, med14=150, med30=150, p90_30=300, sold_med7=100, sold_units7=6.0)
    sold = [(90, 5.0, 0)]
    assert rule_price("today", it, 400, sold) == 300                # the ask ceiling, max(med7, p90)
    assert rule_price("ask blend", it, 400, sold) == 150
    assert rule_price("sold 7d", it, 400, sold) == 100 and rule_price("sold 7d", it, 80, sold) == 79
    assert rule_price("sold half-life", it, 400, sold) == 90 and rule_price("sold half-life", it, None, sold) == 90
    with pytest.raises(KeyError):
        rule_price("cheapest", it, 400, sold)


def test_a_sold_level_under_its_threshold_falls_back_to_asks():
    it = item(med7=150, med14=150, med30=150, p90_30=300, sold_med7=100, sold_units7=4.0)
    assert rule_price("sold 7d", it, 400, ()) == 300                # under 5 a week: today's rule
    assert rule_price("sold half-life", it, 400, [(90, 2.0, 0)]) == 150   # under 3 in 30 days: the ask blend
    assert rule_price("sold half-life", it, 400, [(90, 3.0, 0)]) == 90    # SP2's threshold, 3 units


def test_sold_rows_reads_the_last_days_with_their_age(tmp_path):
    path = tmp_path / "history.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sold_prices_d1 (market TEXT, key INTEGER, day INTEGER, src TEXT, prices TEXT)")
    db.executemany("INSERT INTO sold_prices_d1 VALUES ('m', ?, ?, 'blizzard', ?)",
                   [(7, 100, "90:2.000,95:1.500"), (7, 98, "80:1.000"), (7, 60, "10:9.000")])
    db.commit()
    db.close()
    assert sold_rows(path, 100) == {7: [(80, 1.0, 2), (90, 2.0, 0), (95, 1.5, 0)]}     # day 60 is over 30 days old
    assert sold_rows(tmp_path / "missing.db", 100) == {}


def test_posts_are_scored_against_the_worth_at_their_time():
    kinds = {1: Kind("gear 6 a week", "rise", 100, regime_shifts("rise")), 2: Kind(CLOTH, "steady", 100)}
    posts = [Post(1, START_MS, 100, 1, 1), Post(1, AFTER, 100, 1, 1), Post(1, AFTER, 300, 1, 0),
             Post(2, AFTER, 90, 5, 4)]
    assert earned(posts, kinds, "gear at 5+ a week", "steady") == (1.0, 1.0)            # before the change
    assert earned(posts, kinds, "gear at 5+ a week", "after the rise") == (0.25, 0.5)   # 100 of a worth of 200, 1 of 2
    assert earned(posts, kinds, CLOTH, "steady") == (pytest.approx(0.72), 0.8)
    assert earned(posts, kinds, CLOTH, "after the drop") == (None, None)
    assert group_of(Kind("gear 2 a week", "steady", 1)) == "gear 2 a week"
    assert segment_of(Kind(CLOTH, "drop", 1), AFTER) == "after the drop"
    assert group_of(Kind(REAL, "fall", 1)) == REAL
    assert segment_of(Kind(CLOTH, "fall", 1), START_MS) == "falling"     # a falling item counts from the first day


def test_each_regime_moves_the_worth_as_planned():
    def worth(regime, at, launch=False):
        return Kind(CLOTH, regime, 100, regime_shifts(regime, launch)).worth_at(at)
    assert worth("steady", AFTER) == 100 and regime_shifts("steady") == ()
    assert worth("rise", AFTER - 1) == 100 and worth("rise", AFTER) == 200 and worth("drop", AFTER) == 60
    assert worth("fall", START_MS + 4 * DAY_MS) == pytest.approx(100 * 0.975 ** 4, rel=2e-3)   # 2.5% a day (a step holds its hour's end)
    multiple, days = LAUNCH
    assert worth("steady", START_MS, launch=True) == pytest.approx(100 * multiple, rel=0.02)    # opens dear
    assert worth("steady", START_MS + days * DAY_MS, launch=True) == pytest.approx(100, rel=1e-6)
    assert worth("rise", AFTER, launch=True) == pytest.approx(200, rel=1e-6)                   # then the regime
    assert len(regime_shifts("fall", launch=True)) == DAYS * 24
    with pytest.raises(KeyError):
        regime_shifts("crash")


def test_the_rules_world_has_every_shape_in_every_regime_with_its_high_asks_and_dumps():
    world, kinds = rules_world(1, replicates=1)
    assert len(kinds) == 24 and {k.regime for k in kinds.values()} == {"steady", "rise", "drop", "fall"}
    rising = next(key for key, k in kinds.items() if k.regime == "rise" and k.shape == "gear 30 a week")
    asks = sorted({p.price for p in world.planted if p.key == rising})
    assert asks == [66_000, 132_000]                                # 3.3× the worth, before and after it doubles
    assert RULES == ("today", "ask blend", "sold 7d", "sold half-life")
