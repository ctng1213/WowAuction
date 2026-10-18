"""Tests for ahsync/engine/item_detail.py: heatmaps, the three selling answers and windows in local time (SPEC §3.5)."""
from __future__ import annotations

from ahsync.engine import item_detail, patterns
from ahsync.engine.item_detail import DetailFacts
from ahsync.engine.patterns import Window
from ahsync.engine.profile import Profile

HOUR = 3_600_000
MON, TUE, WED, SAT, SUN = 0, 1, 2, 5, 6


def slot(day: int, hour: int) -> int:
    """A UTC slot of the week."""
    return day * 24 + hour


def prof(peaks: dict[int, float], weeks: int = 4, empty: tuple[int, ...] = ()) -> Profile:
    """A profile of 1.0 with some peaks, `weeks` weeks per slot and none in `empty`."""
    values = [peaks.get(s, 1.0) for s in range(168)]
    counts = [0 if s in empty else weeks for s in range(168)]
    return Profile(tuple(values), tuple(counts), tuple(c >= 3 for c in counts))


def facts(**changes) -> DetailFacts:
    """Linen Cloth's detail facts: 48 sold/day (AHF, 7 of 7 days), 93 listed, flat profiles."""
    base = dict(name="Linen Cloth", quality=1, med30=49, trend7=0.4, sold_per_day=48.0, source="ahf", days_covered=7,
                units_on_ah=93, listed_per_day=61.0, conf=90, seasonality=78, how=prof({}), sales=prof({}),
                supply=prof({}), sell_windows=())
    return DetailFacts(**{**base, **changes})


def test_W9_the_three_selling_answers_are_kept_apart():
    how = prof({slot(SAT, 20): 1.5, slot(TUE, 19): 0.5, slot(WED, 19): 1.3})
    sales = prof({slot(SAT, 20): 0.2, slot(TUE, 19): 3.0, slot(WED, 19): 2.5})
    # sell_score: Sat 1.5 × 0.2 ÷ 3 = 0.10, Tue 0.5 × 3 ÷ 3 = 0.50, Wed 1.3 × 2.5 ÷ 3 = 1.08 (the best), others 0.33
    windows = tuple(patterns.sell_windows(how.values, sales.values, how.weeks, 0.9))
    answers = item_detail.three_answers(how, sales, windows, 0)
    assert answers == {"price": "Sat 20:00 · ×1.50", "sales": "Tue 19:00 · 3.0× an average hour",
                       "best": "Wed 19:00–20:00"}
    assert item_detail.item_detail(facts(how=how, sales=sales, sell_windows=windows), 0)["answers"] == answers


def test_D30_heatmaps_are_in_local_time_scaled_to_the_items_own_range_with_gaps_for_no_data():
    rows = item_detail.heatmap(prof({slot(SAT, 20): 1.2}, empty=(slot(MON, 0),)), 2 * HOUR)
    assert [r["day"] for r in rows] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert rows[SAT]["cells"][22] == {"level": 4, "title": "Sat 22:00 ×1.20 · 4 weeks"}
    assert rows[MON]["cells"][3] == {"level": 0, "title": "Mon 03:00 ×1.00 · 4 weeks"}
    assert rows[MON]["cells"][2] == {"level": None, "title": "Mon 02:00 no data"}


def test_D31_the_detail_lists_each_window_in_local_time_with_its_uplift_and_strength():
    windows = (Window(slot(SAT, 18), 4, 0.0, 1.12, 7, slot(SAT, 19)), Window(slot(SUN, 20), 3, 0.0, 1.09, 2, slot(SUN, 20)))
    detail = item_detail.item_detail(facts(sell_windows=windows), -HOUR)
    assert detail["windows"] == [
        {"when": "Sat 17:00–21:00", "uplift": "+12% price", "uplift_class": "pos", "strength": "established · 7 weeks"},
        {"when": "Sun 19:00–22:00", "uplift": "+9% price", "uplift_class": "pos", "strength": "early pattern · 2 weeks"}]
    assert item_detail.windows_short(windows, -HOUR) == "Sat 17–21 · Sun 19–22 (early pattern)"


def test_windows_crossing_midnight_show_the_end_day_and_no_windows_show_a_dash():
    late = Window(slot(SUN, 23), 3, 0.0, 0.97, 4, slot(SUN, 23))
    assert item_detail.window_long(late, 0) == "Sun 23:00–Mon 02:00"
    assert item_detail.windows_short([late], 0) == "Sun 23–Mon 02"
    assert item_detail.windows_short([], 0) == "—"
    flat = Window(slot(TUE, 1), 1, 0.0, 1.0, 4, slot(TUE, 1))
    rows = item_detail.item_detail(facts(sell_windows=(late, flat)), 0)["windows"]
    assert [(r["uplift"], r["uplift_class"]) for r in rows] == [("−3% price", "neg"), ("+0% price", "")]


def test_the_summary_and_market_activity_lines():
    detail = item_detail.item_detail(facts(how=prof({slot(SAT, 20): 1.2}, weeks=7)), 0)
    assert detail["summary"] == "med30 49c · trend +0.4%/day · 7 weeks of data"
    assert detail["activity"] == [
        ["Est. sales per day", "48 AHF · 7 of 7 days covered"], ["Current supply", "93 units"],
        ["Supply/volume", "1.9 days"], ["Volume confidence", "90%"], ["Seasonality strength", "78"],
        ["Listing turnover (info only, not sales)", "61/day"]]


def test_an_item_without_data_shows_dashes_never_zero():
    empty = Profile(tuple([1.0] * 168), tuple([0] * 168), tuple([False] * 168))
    detail = item_detail.item_detail(facts(med30=None, trend7=-1.25, sold_per_day=None, source=None, units_on_ah=None,
                                           listed_per_day=None, conf=None, seasonality=None, how=empty, sales=empty,
                                           supply=empty), 0)
    assert detail["summary"] == "med30 — · trend −1.2%/day · no weekly data yet"     # −12.5 tenths, half up
    assert detail["answers"] == {"price": "—", "sales": "—", "best": "—"}
    assert [value for _, value in detail["activity"]] == ["—", "—", "—", "—", "— · too little evidence", "—"]
    assert {c["level"] for r in detail["heat"]["price"] for c in r["cells"]} == {None}


def test_a_flat_profile_with_data_sits_in_the_middle_level():
    rows = item_detail.heatmap(prof({}), 0)
    assert {c["level"] for r in rows for c in r["cells"]} == {2}
