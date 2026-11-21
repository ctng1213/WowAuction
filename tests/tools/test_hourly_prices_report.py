"""Tests for tools/hourly_prices_report.py: sold prices against asks by hour of the day (sold-price plan §1d)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hourly_prices_report as rep  # noqa: E402

from ahsync.collect.blizzard import raw_key  # noqa: E402
from ahsync.engine.volume import Auction, IdBook  # noqa: E402
from tests.support.cloud_world import A_MS, B_MS, C_MS, LINEN, M, SNAP_A, SNAP_B, SNAP_C  # noqa: E402

H = 3_600_000
DAY0 = 20_000 * 24                       # an hour number at 00:00 UTC
POTION = 118


def price_at(hour_of_day: int) -> int:
    """What sells in an hour: 80 c at night (0-5), 120 c in the evening (16-21), 100 c otherwise."""
    return 80 if hour_of_day < 6 else 120 if 16 <= hour_of_day < 22 else 100


def two_days() -> list[IdBook]:
    """Hourly snapshots of two UTC days: each hour a 10-unit auction posted at that hour's price is gone an hour
    later (sold), under a 500-unit listing at 150 c that never moves (the cheapest ask whenever nothing else is up)."""
    books = []
    for n in range(49):
        hour = DAY0 + n
        auctions = [Auction(1, POTION, 500, 150, 4, True)]
        if n < 48:
            auctions.append(Auction(100 + n, POTION, 10, price_at(n % 24), 4, True))
        books.append(IdBook(hour * H + 20 * 60_000, tuple(auctions)))
    return books


def test_sold_prices_and_asks_are_kept_per_clock_hour():
    hourly = rep.collect(two_days())
    assert hourly.snapshots == 49
    assert hourly.asks[POTION][DAY0] == 80 and hourly.asks[POTION][DAY0 + 17] == 120      # the hour's cheapest
    assert hourly.asks[POTION][DAY0 + 48] == 150                                          # only the standing listing
    assert hourly.sold[POTION][DAY0 + 1] == {80: 10.0}           # posted in hour 0, gone by hour 1's snapshot
    assert hourly.sold[POTION][DAY0 + 17] == {120: 10.0}         # hour 16's auction
    assert DAY0 not in hourly.sold[POTION]                       # nothing ended before the first gap


def test_a_days_hours_are_ratios_to_that_days_own_level():
    shapes = rep.day_shapes(rep.collect(two_days()), utc_offset=0)
    first = shapes[0]
    assert (first.key, first.units, len(first.sold)) == (POTION, 230.0, 23)      # hours 1-23 of day 0
    assert first.sold[3] == 800 and first.sold[12] == 1000 and first.sold[18] == 1200    # against a day level of 100 c
    assert first.asks[3] == 800 and first.asks[18] == 1200       # here asks move as sales do
    assert abs(first.share[3] - 10 / 230) < 1e-9
    assert rep.day_shapes(rep.collect(two_days()), 0, min_units=500.0) == []


def test_the_hour_table_and_the_evening_against_night():
    shapes = rep.day_shapes(rep.collect(two_days()), utc_offset=0)
    rows = {hour: (sold, asks) for hour, sold, asks, _, _ in rep.hour_rows(shapes)}
    assert rows[2] == (800, 800) and rows[12] == (1000, 1000) and rows[20] == (1200, 1200)
    assert rep.item_rows(shapes, top=5) == [(POTION, 470.0, 2, 1500, 1500)]      # 120 c ÷ 80 c, both ways
    assert rep.ratio(1043) == "1.043" and rep.ratio(None) == "—"


def test_a_local_offset_moves_the_hours():
    shapes = rep.day_shapes(rep.collect(two_days()), utc_offset=-5)
    full = next(s for s in shapes if len(s.sold) == 24)
    # 01:00 local is 06:00 UTC, whose snapshot missed the 80 c auction of 05:00; 13:00 local is 18:00 UTC
    assert full.sold[1] == 800 and full.sold[2] == 1000 and full.sold[13] == 1200


def test_the_report_reads_a_raw_folder_and_prints_both_tables(tmp_path, capsys):
    for at_ms, envelope in ((A_MS, SNAP_A), (B_MS, SNAP_B), (C_MS, SNAP_C)):
        path = tmp_path / raw_key(M, at_ms)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(envelope)
    (tmp_path / "raw" / M / "2026" / "11" / "06" / "000000.json.gz").write_bytes(b"not gzip")
    assert rep.main(["--raw", str(tmp_path / "raw" / M)]) == 0
    text = capsys.readouterr().out
    assert "3 snapshots (1 refused or empty); 0 commodity-days" in text and "local time is UTC-5" in text
    assert "| 00:00 | — |" in text and "Sold: evening ÷ night" in text
    hourly = rep.Hourly()
    rep.collect(rep.iter_books(tmp_path / "raw" / M, hourly), hourly)
    assert LINEN in hourly.asks and hourly.skipped == 1
    out = tmp_path / "report.md"
    assert rep.main(["--raw", str(tmp_path / "raw" / M), "--out", str(out)]) == 0 and out.read_text(encoding="utf-8") == text


def test_an_hour_whose_units_round_to_nothing_has_no_sold_ratio():
    hourly = rep.collect(two_days())
    hourly.sold[POTION][DAY0 + 12] = {100: 0.0001}               # a sliver of an ending that mostly expired
    first = rep.day_shapes(hourly, utc_offset=0)[0]
    assert 12 not in first.sold and first.sold[13] == 1000 and 12 in first.share
