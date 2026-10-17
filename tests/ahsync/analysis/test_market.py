"""Tests for ahsync/analysis/market.py: analysis on any HistoryReader (AR1), patterns in the intelligence (AR5), per-item failures (FM8)."""
from __future__ import annotations

import pytest

from ahsync.analysis import market
from ahsync.db.sqlite import Database
from ahsync.engine.profile import unpack
from ahsync.engine.sellprob import MarketSettings
from ahsync.history.reads import CurrentPrice, ItemInfo
from ahsync.intel.contract import from_json, to_json
from tests.support.fake_clock import FakeClock

M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
NOW = 20_000 * DAY + 12 * HOUR
ITEMS = {2840: ItemInfo(2840, "Copper Bar", 1, 10, 7, None, 10, 20), 6217: ItemInfo(6217, "Copper Rod", 1, 15, 7, None, 50, 20)}
CLOSES = {key: [(NOW - h * HOUR, price + h % 3) for h in range(40)] for key, price in ((2840, 1000), (6217, 4000))}
CURRENT = {2840: (1000, 6, NOW - HOUR), 6217: (4001, 3, NOW - 2 * HOUR)}
VOLUME = {6217: {NOW // DAY - d: {"books": (6.0, 1.0, 7.0)} for d in range(7)}}
BOOK = {6217: (NOW - 3 * HOUR, [(3990, 1, 3, 2), (4100, 5, 4, 1)])}
SUPPLY = {key: [(t, 20 + i % 5) for i, (t, _) in enumerate(rows)] for key, rows in CLOSES.items()}
SALES = {6217: [(NOW - h * HOUR, 0.25 * (h % 4)) for h in range(40)]}


class MemoryHistory:
    """A HistoryReader on plain dicts: what a cloud store would look like to analysis."""

    def items(self) -> dict[int, ItemInfo]:
        """Item metadata."""
        return dict(ITEMS)

    def priced_keys(self, market_id: str, since_ms: int) -> list[int]:
        """Items with data."""
        return sorted(CLOSES)

    def hourly_closes(self, market_id: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """Closes in the window, ascending."""
        return sorted((t, c) for t, c in CLOSES.get(key, []) if from_ms <= t <= to_ms)

    def hourly_supply(self, market_id: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """Units listed in the window, ascending."""
        return sorted((t, q) for t, q in SUPPLY.get(key, []) if from_ms <= t <= to_ms)

    def hourly_sales(self, market_id: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, float]]:
        """Sales rates in the window, ascending."""
        return sorted((t, r) for t, r in SALES.get(key, []) if from_ms <= t <= to_ms)

    def daily_closes(self, market_id: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """No daily closes in this world."""
        return []

    def current_price(self, market_id: str, key: int) -> CurrentPrice | None:
        """The current row."""
        row = CURRENT.get(key)
        return CurrentPrice(key, row[0], row[1], row[2], "ahf") if row else None

    def volume_rows(self, market_id: str, key: int, from_day: int) -> dict:
        """Volume per day and source."""
        return {d: v for d, v in VOLUME.get(key, {}).items() if d >= from_day}

    def newest_book(self, market_id: str, key: int, since_ms: int):
        """The book, if recent enough."""
        book = BOOK.get(key)
        return (book[0], sorted(book[1])) if book and book[0] >= since_ms else None


def sqlite_history(tmp_path) -> Database:
    """The same world in SQLite."""
    db = Database.open(tmp_path / "a.db", FakeClock().now_ms(), None)
    with db.transaction():
        db.conn.executemany("INSERT INTO items(key, item_id, name, quality, ilvl, class, vendor_buy, vendor_sell, stack)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            [(i.key, i.key, i.name, i.quality, i.ilvl, i.item_class, i.vendor_buy, i.vendor_sell, i.stack)
                             for i in ITEMS.values()])
        db.conn.executemany("INSERT INTO candles_h1(market, key, t, close, qty, src) VALUES (?, ?, ?, ?, ?, 'ahf')",
                            [(M, k, t, c, q) for k, rows in CLOSES.items() for (t, c), (_, q) in zip(rows, SUPPLY[k])])
        db.conn.executemany("INSERT INTO sales_h1(market, key, t, sold_per_hour) VALUES (?, ?, ?, ?)",
                            [(M, k, t, r) for k, rows in SALES.items() for t, r in rows])
        db.conn.executemany("INSERT INTO current(market, key, price, qty, at, src) VALUES (?, ?, ?, ?, ?, 'ahf')",
                            [(M, k, *row) for k, row in CURRENT.items()])
        db.conn.executemany("INSERT INTO volume_d1(market, key, day, sold, expired, listed, src) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [(M, k, d, *v, src) for k, days in VOLUME.items() for d, srcs in days.items()
                             for src, v in srcs.items()])
        for key, (at, rows) in BOOK.items():
            db.conn.execute("INSERT INTO scans(scan_id, market, src, at, kind, coverage) VALUES (?, ?, 'local', ?, 'full', 1)",
                            (f"b{key}", M, at))
            db.conn.executemany("INSERT INTO book_entries(scan_id, key, price, stack, band, count) VALUES (?, ?, ?, ?, ?, ?)",
                                [(f"b{key}", key, *row) for row in rows])
    return db


def test_AR1_analysis_gives_identical_intelligence_from_sqlite_and_from_memory(tmp_path):
    db = sqlite_history(tmp_path)
    from_sqlite, failed_sqlite = market.analyze_market(db, M, NOW, MarketSettings())
    from_memory, failed_memory = market.analyze_market(MemoryHistory(), M, NOW, MarketSettings())
    db.close()
    assert failed_sqlite == failed_memory == {}
    assert to_json(from_sqlite) == to_json(from_memory)
    rod = from_memory.items[6217]
    assert (rod.sold_per_day, rod.volume_source, rod.units_on_ah, rod.ref_stack) == (6.0, "books", 7, 1)
    assert all(len(unpack(v, w).values) == 168 for v, w in ((rod.how, rod.how_weeks), (rod.sales_how, rod.sales_weeks),
                                                              (rod.supply_how, rod.supply_weeks)))
    assert rod.how and len(rod.curve) == 24 * 8
    assert from_memory.items[2840].curve == ""                  # no sales data: no curve


WEEK = 7 * DAY
MONDAY = 345_600_000                      # 1970-01-05 00:00 UTC, a Monday
W_NOW = MONDAY + 3000 * WEEK - HOUR       # a Sunday 23:00: 7 whole weeks end here
TUE = 24


class SevenWeeks(MemoryHistory):
    """One item, 7 weeks of hourly data: cheap Mon 04-05, busy Tue 18-20 (AR5)."""

    def hours(self) -> list[int]:
        """Every hour start of the 7 weeks, ascending."""
        first = W_NOW + HOUR - 7 * WEEK
        return [first + h * HOUR for h in range(7 * 168)]

    def priced_keys(self, market_id: str, since_ms: int) -> list[int]:
        """Only Copper Bar."""
        return [2840]

    def hourly_closes(self, market_id: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """100c, except 50c on Mon 04:00 and 05:00."""
        return [(t, 50 if (t - MONDAY) % WEEK // HOUR in (4, 5) else 100) for t in self.hours() if from_ms <= t <= to_ms]

    def hourly_supply(self, market_id: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """80 units listed, every hour."""
        return [(t, 80) for t in self.hours() if from_ms <= t <= to_ms]

    def hourly_sales(self, market_id: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, float]]:
        """2 per hour, 8 per hour on Tue 18-20."""
        return [(t, 8.0 if (t - MONDAY) % WEEK // HOUR in (TUE + 18, TUE + 19, TUE + 20) else 2.0)
                for t in self.hours() if from_ms <= t <= to_ms]

    def current_price(self, market_id: str, key: int) -> CurrentPrice | None:
        """100c now."""
        return CurrentPrice(key, 100, 20, W_NOW, "ahf")

    def volume_rows(self, market_id: str, key: int, from_day: int) -> dict:
        """Book volume: 48 sold a day for the last 7 days."""
        return {W_NOW // DAY - d: {"books": (48.0, 0.0, 48.0)} for d in range(7)}

    def newest_book(self, market_id: str, key: int, since_ms: int):
        """No book."""
        return None


def test_AR5_the_intelligence_holds_the_weekly_patterns_windows_and_curve():
    intel, failed = market.analyze_market(SevenWeeks(), M, W_NOW, MarketSettings())
    bar = intel.items[2840]
    assert failed == {}
    assert bar.how_weeks == bar.sales_weeks == bar.supply_weeks == ",".join(["7"] * 168)
    assert unpack(bar.how, bar.how_weeks).values[4] == 0.8          # Mon 04:00, packed in thousandths
    # Smoothed sales are 5.6 on Tue 18-20 (2 + 8 + 8 + 8 + 2 over 5 slots), 4.4 next to them: under 0.9 × 5.6.
    assert [(w.start_slot, w.hours, w.best_slot, w.weeks) for w in bar.sell_windows] == [(TUE + 18, 3, TUE + 18, 7)]
    # Smoothed price is 0.8 on Mon 03-06 (three 1.0s and two 0.5s), 0.9 next to them: above 0.8 ÷ 0.9.
    assert [(w.start_slot, w.hours) for w in bar.buy_windows] == [(3, 4)]
    assert bar.buy_windows[0].how_avg == pytest.approx(0.8)
    assert bar.supply_how == ",".join(["1000"] * 168)
    # Smoothing spreads the 2-hour dip over 6 hours: the profile misses by 10+20+30+30+20+10 = 120c on them,
    # a flat 100c by 50+50 = 100c, so the profile is no better than flat → strength 0 (W11: predictability).
    assert bar.seasonality == 0
    assert (len(bar.curve), bar.curve_hour) == (24 * 8, W_NOW // HOUR)
    assert from_json(to_json(intel)) == intel


def test_one_failing_item_is_set_aside(monkeypatch):
    original = market.analyze_item

    def flaky(history, market_id, key, info, now_ms, settings):
        """Fail for bars only."""
        if key == 2840:
            raise ValueError("bad close")
        return original(history, market_id, key, info, now_ms, settings)
    monkeypatch.setattr(market, "analyze_item", flaky)
    intel, failed = market.analyze_market(MemoryHistory(), M, NOW, MarketSettings())
    assert failed == {2840: "ValueError: bad close"} and set(intel.items) == {6217}
    assert intel.observations_until_ms == NOW - 2 * HOUR


def test_reference_stack_and_an_empty_market():
    assert market.ref_stack([(10, 20, 3, 2), (11, 5, 3, 2), (12, 1, 3, 1)]) == 5       # ties go to the smaller
    assert market.ref_stack([]) == 1

    class Empty(MemoryHistory):
        """No items at all."""

        def priced_keys(self, market_id: str, since_ms: int) -> list[int]:
            """Nothing."""
            return []
    intel, _ = market.analyze_market(Empty(), M, NOW, MarketSettings())
    assert (intel.items, intel.observations_until_ms) == ({}, None)


def test_an_item_without_metadata_still_gets_intelligence():
    class NoMeta(MemoryHistory):
        """Metadata unknown."""

        def items(self) -> dict[int, ItemInfo]:
            """Nothing."""
            return {}
    intel, _ = market.analyze_market(NoMeta(), M, NOW, MarketSettings())
    rod = intel.items[6217]
    assert rod.sell_value is not None and rod.sell_value <= rod.post_price * 0.95      # no vendor value: fallback 0
