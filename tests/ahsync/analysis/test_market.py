"""Tests for ahsync/analysis/market.py: analysis on any HistoryReader (AR1), patterns in the intelligence (AR5), per-item failures (FM8)."""
from __future__ import annotations

import pytest

from ahsync.analysis import market
from ahsync.db.sqlite import Database
from ahsync.engine import patterns, sellprob
from ahsync.engine.profile import unpack
from ahsync.engine.sellprob import MarketSettings
from ahsync.history.reads import CurrentPrice, HistoryWindow, ItemHistory, ItemInfo
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
    """A HistoryReader on plain dicts: what a cloud store would look like to analysis.

    Subclasses shape the world through the per-item helpers; `market_history` is the one
    bulk read analysis makes (AR7), and `calls` counts the reads analysis makes.
    """

    def __init__(self) -> None:
        """No reads yet."""
        self.calls = 0

    def items(self) -> dict[int, ItemInfo]:
        """Item metadata."""
        self.calls += 1
        return dict(ITEMS)

    def market_history(self, market_id: str, window: HistoryWindow):
        """Every priced item's history, in key order, from the per-item helpers."""
        self.calls += 1
        for key in self.priced_keys(market_id, window.from_ms):
            yield ItemHistory(
                key, self.hourly_closes(market_id, key, window.from_ms, window.to_ms),
                self.hourly_supply(market_id, key, window.from_ms, window.to_ms),
                self.hourly_sales(market_id, key, window.from_ms, window.to_ms),
                self.daily_closes(market_id, key, window.from_ms, window.to_ms), self.current_price(market_id, key),
                self.volume_rows(market_id, key, window.volume_from_day),
                self.newest_book(market_id, key, window.book_since_ms))

    def priced_keys(self, market_id: str, since_ms: int) -> list[int]:
        """Items with data."""
        return sorted(CLOSES)

    def hourly_closes(self, market_id: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """Closes in the window, ascending."""
        return sorted((t, c) for t, c in CLOSES.get(key, []) if from_ms <= t <= to_ms)

    def hourly_supply(self, market_id: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
        """Units listed per hour in the window, ascending."""
        return sorted((t, q) for t, q in SUPPLY.get(key, []) if from_ms <= t <= to_ms)

    def hourly_sales(self, market_id: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, float]]:
        """Sales rates per hour in the window, ascending."""
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
            db.conn.executemany("INSERT INTO book_entries(sid, key, price, stack, band, count) "
            "VALUES ((SELECT sid FROM scans WHERE scan_id = ?), ?, ?, ?, ?, ?)",
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
    # 7 whole days at 6 a day with the default prior (V41): (42 + 1 × 1/12) ÷ (7 + 1/12)
    assert (rod.sold_per_day, rod.volume_source, rod.units_on_ah, rod.ref_stack) ==         (pytest.approx((42 + 1 / 12) / (7 + 1 / 12)), "books", 7, 1)
    assert all(len(unpack(v, w).values) == 168 for v, w in ((rod.how, rod.how_weeks), (rod.sales_how, rod.sales_weeks),
                                                              (rod.supply_how, rod.supply_weeks)))
    assert rod.how == "" and rod.sales_how and len(rod.curve) == 24 * 8   # 7 days hold no centred week (W17)
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
    assert bar.sales_weeks == bar.supply_weeks == "7*168"                                  # run-length (BZ47)
    assert bar.how_weeks == "6*168"              # W17: the first and last 84 hours have no centred week
    assert unpack(bar.how, bar.how_weeks).values[4] == 0.8          # Mon 04:00, packed in thousandths
    # P30's rate pools Tue 18-20's rush with every day's 18-20 and shrinks it: smoothed about 2.59 an hour there,
    # 2.41 beside it (above 0.9 × 2.59 = 2.33), 2.23 two away (below it).
    assert [(w.start_slot, w.hours, w.best_slot, w.weeks) for w in bar.sell_windows] == [(TUE + 17, 5, TUE + 18, 6)]   # W17: 6 weeks of centred observations
    # W15: the window's forecast: 100c × 1.0 (within p10..p90 = 100c), floor(0.5 × 20 listed) = 10 units, and the
    # confidence with its 6 weeks of centred observations as the pattern component (W17; still 69).
    assert [(w.expected_price, w.exec_qty, w.conf) for w in bar.sell_windows] == [(100, 10, 69)]
    # Smoothed price is 0.8 on Mon 03-06 (three 1.0s and two 0.5s), 0.9 next to them: above 0.8 ÷ 0.9, so it
    # qualifies; but its forecast is raised to p10 (100c), and 100c can't pay against 95c after the cut: a buy
    # window worth 0 is dropped (W14).
    how = unpack(bar.how, bar.how_weeks)
    assert [(w.start_slot, w.hours) for w in patterns.buy_windows(how.values, how.weeks)] == [(3, 4)]
    assert bar.buy_windows == ()
    assert bar.supply_how == "1000*168"
    # Smoothing spreads the 2-hour dip over 6 hours: the profile misses by 10+20+30+30+20+10 = 120c on them,
    # a flat 100c by 50+50 = 100c, so the profile is no better than flat → strength 0 (W11: predictability).
    assert bar.seasonality == 0
    assert (len(bar.curve), bar.curve_hour) == (24 * 8, W_NOW // HOUR)
    assert from_json(to_json(intel)) == intel


def test_AR7_analysis_reads_history_per_market_not_per_item(monkeypatch):
    class Many(MemoryHistory):
        """n copies of the Copper Rod's history."""

        def __init__(self, n: int) -> None:
            """n items."""
            super().__init__()
            self.n = n

        def priced_keys(self, market_id: str, since_ms: int) -> list[int]:
            """n items."""
            return list(range(1, self.n + 1))

        def hourly_closes(self, market_id: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
            """Each item has the Copper Rod's closes."""
            return super().hourly_closes(market_id, 6217, from_ms, to_ms)
    counts = []
    for n in (50, 500):
        history = Many(n)
        intel, _ = market.analyze_market(history, M, NOW, MarketSettings())
        counts.append((len(intel.items), history.calls))
    assert counts == [(50, 2), (500, 2)]          # items() and market_history(): the same for 50 and 500 items


def test_one_failing_item_is_set_aside(monkeypatch):
    original = market.analyze_item

    def flaky(item, info, now_ms, settings):
        """Fail for bars only."""
        if item.key == 2840:
            raise ValueError("bad close")
        return original(item, info, now_ms, settings)
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


def test_AR8_an_unknown_suffix_key_gets_no_intelligence():
    class WithUnknownSuffix(MemoryHistory):
        """The Copper Rod, and the same closes under the unknown-suffix key -2644."""

        def priced_keys(self, market_id: str, since_ms: int) -> list[int]:
            """Both keys."""
            return [-2644, 6217]

        def hourly_closes(self, market_id: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
            """The Copper Rod's closes for either key."""
            return super().hourly_closes(market_id, 6217, from_ms, to_ms)
    intel, failed = market.analyze_market(WithUnknownSuffix(), M, NOW, MarketSettings())
    assert (set(intel.items), failed) == ({6217}, {})


def test_IU6_a_newer_in_game_scan_updates_only_the_items_it_priced():
    from ahsync.intel.contract import ItemIntel, MarketIntelligence, age_now_ms
    silk, linen = 4306, 2589

    def item(key: int, cur: int) -> ItemIntel:
        """An item priced at cur, with 5 listed."""
        return ItemIntel(key, cur, 5, HOUR, cur, cur, 80, 120, 0.0, 10.0, 0.0, 10.0, "books", {"books": 7}, cur, 1, 0.5,
                         False, float(cur), 60, 5, None, (), closes_30d=200)
    intel = MarketIntelligence(M, NOW, NOW, 1, {silk: item(silk, 100), linen: item(linen, 45)})
    book = [(90, 12, 4, 1), (95, 12, 4, 2)]
    settings, vendor = MarketSettings(), {silk: 8}
    after = market.with_scan(intel, {silk: (90, 12, NOW + HOUR), 999: (5, 1, NOW + HOUR)}, {silk: (NOW + HOUR, book)},
                             settings, vendor)
    s = after.items[silk]
    assert (s.cur, s.cur_qty, s.book, s.units_on_ah, s.cur_suspect) == (90, 12, tuple(book), 36, False)
    assert s.cur_age_ms == -HOUR and age_now_ms(s.cur_age_ms, NOW + 3 * HOUR, NOW) == 2 * HOUR   # aged from the scan
    base = market.NowBase(100, 80, 100, 1.0, 10.0, 10.0, tuple([1.0] * 168), 8)      # the ask level of its 100 c medians
    expected = market.now_fields(market.Quote(90, 12, tuple(book)), base, NOW + HOUR, settings)
    assert (s.post_price, s.ref_stack, s.chance, s.volume_unknown, s.sell_value) == expected   # repriced (decision 2)
    assert s.post_price != intel.items[silk].post_price
    assert after.items[linen] == intel.items[linen] and after.built_ms == NOW and 999 not in after.items
    lone = market.with_scan(intel, {silk: (5, 1, NOW + HOUR)}, {}, settings, vendor).items[silk]   # no book in this scan
    assert (lone.cur, lone.book, lone.units_on_ah, lone.cur_suspect) == (5, (), 5, True)   # 5c < 10% of 80c

def test_FM31_a_lone_outlier_close_moves_no_median_but_still_sets_the_range():
    base, _ = market.analyze_market(MemoryHistory(), M, NOW, MarketSettings())

    class WithOutlier(MemoryHistory):
        """The Copper Rod with one 1c hour yesterday."""

        def hourly_closes(self, market_id: str, key: int, from_ms: int, to_ms: int) -> list[tuple[int, int]]:
            """The usual closes plus one far below them."""
            closes = super().hourly_closes(market_id, key, from_ms, to_ms)
            if key != 6217 or not closes:
                return closes
            t = closes[-1][0] - 24 * HOUR
            return sorted([c for c in closes if c[0] != t] + [(t, 1)])
    odd, _ = market.analyze_market(WithOutlier(), M, NOW, MarketSettings())
    a, b = base.items[6217], odd.items[6217]
    assert (b.med7, b.med30, b.trend7, b.how) == (a.med7, a.med30, a.trend7, a.how)
    assert b.p10_30 == a.p10_30 or b.p10_30 <= a.p10_30                      # the range uses every close


def test_FM31_the_statistics_are_computed_from_the_filtered_closes(monkeypatch):
    from ahsync.engine import prices
    seen = []

    def filtered(closes, p10, p90, **_k):
        """Record the call and pretend every close was 7c."""
        seen.append((len(closes), p10, p90))
        return [(t, 7) for t, _ in closes]
    monkeypatch.setattr(prices, "drop_suspect", filtered)
    intel, _ = market.analyze_market(MemoryHistory(), M, NOW, MarketSettings())
    rod = intel.items[6217]
    assert (rod.med7, rod.med14, rod.med30) == (7, 7, 7) and rod.p10_30 == 4000   # medians from the filtered closes
    assert seen and all(n > 0 for n, *_ in seen if n)


def test_SRC11_ahledgers_aggregates_are_stored_never_used(tmp_path):
    from pathlib import Path
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain, odd = sqlite_history(tmp_path / "a"), sqlite_history(tmp_path / "b")
    with plain.transaction():
        plain.upsert_ahledger_rows(M, NOW // 1000, [(k, 50, 52, 49, 40, 60, 3600) for k in ITEMS])
    with odd.transaction():
        odd.upsert_ahledger_rows(M, NOW // 1000, [(k, 999_999, 999_999, 999_999, 999_999, 999_999, 0) for k in ITEMS])
    first, _ = market.analyze_market(plain, M, NOW, MarketSettings())
    second, _ = market.analyze_market(odd, M, NOW, MarketSettings())
    plain.close()
    odd.close()
    assert to_json(first) == to_json(second)
    # no reader anywhere: only the writer, its schema, migration and pruning name the table (the W1 half is WP1)
    root = Path(__file__).resolve().parents[3] / "ahsync"
    readers = sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*.py")
                     if "ahledger_rows" in p.read_text(encoding="utf-8"))
    assert readers == ["collect/ahledger.py", "collect/ahledger_backfill.py",   # the backfill reads only its keys (§2.3)
                       "db/migrations.py", "db/sqlite.py", "history/prune.py", "history/writes.py"]


def test_BZ32_repricing_an_item_with_its_own_state_reproduces_the_daily_numbers():
    intel, _ = market.analyze_market(MemoryHistory(), M, NOW, MarketSettings())
    i = intel.items[6217]
    quote = market.Quote(i.cur, i.cur_qty, i.book)
    again = market.reprice_item(i, quote, NOW, MarketSettings(), ITEMS[6217].vendor_sell, [])
    now = ("post_price", "ref_stack", "chance", "volume_unknown", "sell_value", "cur", "cur_qty", "book", "units_on_ah")
    assert {f: getattr(again, f) for f in now} == {f: getattr(i, f) for f in now}   # one formula, daily and hourly


def test_BZ32_a_newer_book_moves_the_now_fields_and_no_baseline():
    intel, _ = market.analyze_market(MemoryHistory(), M, NOW, MarketSettings())
    i = intel.items[6217]
    book = ((3800, 1, 4, 3), (3990, 1, 3, 2))                         # a cheaper level appeared
    later = market.reprice_item(i, market.Quote(3800, 3, book), NOW + 2 * HOUR, MarketSettings(), 50, [3800])
    assert (later.cur, later.cur_qty, later.cur_age_ms, later.book_age_ms, later.units_on_ah) == (3800, 3, 0, 0, 5)
    assert later.post_price is not None and later.post_price <= i.post_price
    baselines = ("med7", "med30", "p10_30", "p90_30", "trend7", "sold_per_day", "expired_per_day", "listed_per_day",
                 "how", "how_weeks", "sales_how", "sales_weeks", "supply_how", "supply_weeks", "seasonality",
                 "buy_windows", "curve", "curve_hour", "conf", "median_daily_supply", "closes_30d")
    assert all(getattr(later, f) == getattr(i, f) for f in baselines)
    plain = lambda ws: [market.replace(w, chance24=None) for w in ws]       # a window's 24 h chance follows the book
    assert plain(later.sell_windows) == plain(i.sell_windows)
    assert to_json(intel) != to_json(market.replace(intel, items={**intel.items, 6217: later}))


def test_BZ23_expired_share_is_the_share_of_ended_listings_that_expired():
    now = 20_030 * 86_400_000 + 12 * 3_600_000
    day = now // 86_400_000
    ended = {day: (25.0, 6.0, 2.0), day - 29: (15.0, 4.0, 0.0), day - 30: (100.0, 100.0, 0.0)}
    assert market.expired_share(ended, now) == 0.25                     # 40 ended in 30 days, 10 expired; day −30 is out
    assert market.expired_share({}, now) is None


def test_BZ29_the_undercut_share_is_measured_from_the_weeks_new_listings():
    now = 20_030 * 86_400_000 + 12 * 3_600_000
    day = now // 86_400_000
    listings = {day: {960: 200.0, 1000: 500.0}, day - 6: {950: 100.0, 1100: 200.0}, day - 7: {500: 9_999.0}}
    assert market.measured_undercut(listings, 120, 125, now) == 0.3     # 300 of 1,000 at or below 120/125; day −7 is out
    assert market.measured_undercut(listings, None, 125, now) is None and market.measured_undercut({}, 120, 125, now) is None
    assert market.measured_undercut(listings, 120, None, now) is None


def test_BZ29_the_simulation_uses_the_measured_share_not_the_default():
    settings = MarketSettings()
    supply = sellprob.Supply(240.0, 480.0, ((125, 20, 4, 5),), 125, 100)
    measured, _ = sellprob.posting_chance(supply, 120, 20, 0, settings, None, 0.3)
    default, _ = sellprob.posting_chance(supply, 120, 20, 0, settings)
    assert measured > default
    assert default == sellprob.posting_chance(supply, 120, 20, 0, settings, None, settings.default_undercut_share)[0]



def test_sell_chance_now_is_a_24h_post_at_the_lowest_listing():
    book = ((90, 12, 4, 1), (95, 20, 4, 2))
    base = market.NowBase(100, 80, 100, 1.0, 10.0, 10.0, tuple([1.0] * 168), 8)
    quote = market.Quote(90, 12, book)
    day = MarketSettings(sell_window_hours=24)
    supply = sellprob.Supply(10.0, 10.0, book, 90, 12)
    expected = sellprob.posting_chance(supply, 90, market.ref_stack(list(book)), NOW, day, base.sales_values, None)[0]
    assert market.sell_chance_now(quote, base, NOW, MarketSettings()) == expected      # 24 h, whatever the post window
    assert market.sell_chance_now(quote, market.NowBase(100, 80, 100, 1.0, None, None, (), 8), NOW, MarketSettings()) is None
    assert market.sell_chance_now(market.Quote(None, None, None), base, NOW, MarketSettings()) is None


def test_next_slot_is_this_hour_or_the_next_one_on_that_hour_of_the_week():
    hour = NOW // HOUR
    slot = (hour // 24 + 3) % 7 * 24 + hour % 24
    assert market.next_slot_ms(slot, NOW) == hour * HOUR
    assert market.next_slot_ms((slot + 167) % 168, NOW) == (hour + 167) * HOUR


def test_BZ41_the_market_pass_borrows_from_the_class_and_reports_the_cancel_share():
    day = NOW // 86_400_000
    linen = {day: (100.0, 10.0, 0.0, 9.0, 180.0, 94.0, 74.0, 1)}                # 90 left early, 9 cancels
    thin = {day: (12.0, 6.0, 0.0, 0.0, 0.0, 12.0, 3.0, 1)}
    items = {2589: bare_intel(2589), 2592: bare_intel(2592), 999: bare_intel(999)}
    sums = {2589: market.lifetime_sums(linen, NOW), 2592: market.lifetime_sums(thin, NOW)}
    out, cancels = market.with_sell_through(items, sums, {2589: 7, 2592: 7, 999: None})
    assert cancels == pytest.approx(9 / (90 + 6))                                # both are commodities
    assert out[2589].sell_through[0] == pytest.approx(74 / 94) and out[2589].sell_through_how == "mmm"
    class_rate = (74 + 3) / (94 + 12)
    assert out[2592].sell_through[0] == pytest.approx((3 + 30 * class_rate) / 42) and out[2592].sell_through_how == "ccc"
    assert out[999].sell_through == (None, None, None) and out[999].sell_through_how == "---"


def bare_intel(key: int):
    """A bare ItemIntel for the market pass."""
    from ahsync.intel.contract import ItemIntel
    return ItemIntel(key, None, None, None, None, None, None, None, 0.0, None, None, None, None, {}, None, 1, 0.5, True,
                     None, 0, None, None)


def test_the_analysis_publishes_the_level_and_divides_by_the_pattern_only_when_switched_on():
    from dataclasses import replace

    from ahsync.engine.lifetime_prices import SoldHour
    monday_four = W_NOW - 6 * DAY - 19 * HOUR                    # the last Mon 04:00: `how` 0.8 there (AR5)

    class Sold(SevenWeeks):
        """AR5's bar, with 3 units sold at 80 c on the last Mon 04:00."""

        def market_history(self, market_id, window):
            """The bar with its sold prices by hour."""
            for item in super().market_history(market_id, window):
                yield replace(item, sold_hours=[SoldHour(monday_four, 80, 3.0, 3.0)])
    off = market.analyze_market(Sold(), M, W_NOW, MarketSettings())[0].items[2840]
    assert (off.level, off.level_source, off.level_units, off.sold_curve) == (80, "sold", 3.0, None)
    on = market.analyze_market(Sold(), M, W_NOW, MarketSettings(level_divides_by_pattern=True))[0].items[2840]
    assert on.level == 100                                       # 80 ÷ 0.8
    plain = market.analyze_market(SevenWeeks(), M, W_NOW, MarketSettings())[0].items[2840]
    assert (plain.level, plain.level_source) == (100, "asks")    # no sales: the ask blend of 100 c medians


def test_BZ53_sold_med7_weighs_the_weeks_sold_prices_by_units_and_leaves_older_days_out():
    from types import SimpleNamespace

    from ahsync.analysis.market import sold_median7
    week = range(20_394, 20_401)
    days = {20_394: {100: 60.0}, 20_399: {200: 40.0}, 20_393: {500: 500.0}}        # the 8th day back is outside it
    assert sold_median7(SimpleNamespace(sold_prices=days), week) == (100, 100.0)
    halves = {20_394: {100: 50.0}, 20_400: {200: 50.0}}
    assert sold_median7(SimpleNamespace(sold_prices=halves), week) == (150, 100.0)
    assert sold_median7(SimpleNamespace(sold_prices={}), week) == (None, 0.0)


def test_BZ49_unseen_per_day_is_sold_per_days_unseen_share_over_the_week_of_the_id_source():
    from types import SimpleNamespace

    from ahsync.analysis.market import unseen_per_day
    from ahsync.engine.volume import DailyVolume
    week = range(20_394, 20_401)
    item = SimpleNamespace(volume={d: {"blizzard": (100.0, 10.0, 50.0), "books": (130.0, 0.0, 0.0)} for d in week},
                           unseen={d: {"blizzard": 12.0} for d in week})
    ids = DailyVolume(98.0, 10.0, 50.0, {"blizzard": 7}, 0.8, "blizzard", "ids")
    assert unseen_per_day(item, week, ids) == 98.0 * 84 / 700                    # 11.76: 12% of the week's sold, unseen
    assert unseen_per_day(item, week, DailyVolume(98.0, 10.0, 50.0, {"books": 7}, 0.6, "books", "groups")) is None
    assert unseen_per_day(SimpleNamespace(volume=item.volume, unseen={}), week, ids) is None   # nothing estimated
    assert unseen_per_day(item, week, DailyVolume(None, None, None)) is None


def test_P28_the_now_fields_and_the_window_chances_read_the_price_acceptance(monkeypatch):
    monkeypatch.setattr(sellprob, "DEMAND_SPREAD", sellprob.NO_SPREAD)   # P28's numbers alone; P31 is tested apart
    from dataclasses import replace

    from ahsync.intel.contract import IntelWindow
    curve = (60, 80, 90, 100, 120, 150, 200)
    cheap = tuple([0.7] * 168)                                   # every hour's level is 70 c; the scaled curve 42..140 c
    plain = market.NowBase(100, 80, 150, 0.7, 0.75, 0.0, tuple([1.0] * 168), 8, None, None, cheap)   # 0.25 units in 8 h
    sold = replace(plain, sold_curve=curve)
    quote = market.Quote(None, None, None)                       # nothing listed: the post price is the ceiling
    settings = MarketSettings(default_undercut_share=0.0)
    price, ref, chance, _, _ = market.now_fields(quote, sold, NOW, settings)
    # PP5's low-bound edge: the 70 c forecast is raised to the low bound, 80 c, above the hour's level, where the
    # scaled curve (70 c its 50th, 84 c its 75th) puts 32.14% of the units at or above it: a = 0.3214 / 0.5.
    assert (price, ref) == (80, 1)
    assert chance == pytest.approx(market.now_fields(quote, plain, NOW, settings)[2] * (1 - 0.678571) / 0.5, rel=1e-5)
    window = IntelWindow(0, 4, 2, 1.0, 6, expected_price=105)         # a 24 h post at 105 c, the scaled curve's 90th: a = 0.2
    with_curve = market.sell_window_chances((window,), quote, sold, NOW, settings)[0].chance24
    without = market.sell_window_chances((window,), quote, plain, NOW, settings)[0].chance24
    assert with_curve == pytest.approx(without * 0.2)
    assert market.sell_chance_now(market.Quote(60, 1, ((60, 1, 4, 1),)), sold, NOW, settings) ==         market.sell_chance_now(market.Quote(60, 1, ((60, 1, 4, 1),)), plain, NOW, settings)     # cur under the level


def test_P28_now_base_carries_the_documents_curve_and_pattern_and_the_exported_curve_is_built_at_the_post_price(monkeypatch):
    monkeypatch.setattr(sellprob, "DEMAND_SPREAD", sellprob.NO_SPREAD)   # P28's numbers alone; P31 is tested apart
    from dataclasses import replace

    from ahsync.engine import profile
    from ahsync.engine.sellprob import Acceptance
    from ahsync.intel.contract import ItemIntel
    how = profile.pack(profile.Profile(tuple([1.0] * 167 + [1.5]), tuple([6] * 168), tuple([True] * 168)))[0]
    curve = (60, 80, 90, 100, 120, 150, 200)
    i = ItemIntel(2840, 100, 5, HOUR, 100, 100, 80, 120, 0.0, 10.0, 0.0, 10.0, "books", {"books": 7}, 100, 1, 0.5,
                  False, 100.0, 60, 5, None, (), how=how, how_weeks="6*168", level=100, level_source="sold",
                  level_units=30.0, sold_curve=curve)
    base = market.now_base(i, NOW, 8)
    assert (base.level, base.low, base.high, base.sold_curve) == (100, 80, 150, curve)
    assert base.how_values == profile.unpack_values(how) and base.acceptance() == Acceptance(100, curve, base.how_values)
    older = market.now_base(replace(i, how="", level=None, level_source=None, sold_curve=None), NOW, 8)
    assert (older.sold_curve, older.how_values, older.acceptance()) == (None, sellprob.FLAT_HOW, Acceptance(100, None, sellprob.FLAT_HOW))
    # The exported curve (weekly_fields) is built at the post price with the acceptance: a post at 150 c reads
    # a = 0.2 in every row where the hour's level is 100 c, so its rows sit under the plain curve's.
    item = next(it for it in SevenWeeks().market_history(M, HistoryWindow(0, W_NOW, 0, 0)) if it.key == 2840)
    sales = market.sales_fields(item, W_NOW, MarketSettings())
    pattern = profile.price_profile(item.hourly_closes, 100, W_NOW, 2)
    plain = market.NowBase(100, 80, 150, 1.0, 10.0, 10.0, sales[0].values, 8, None, None, sellprob.FLAT_HOW)
    priced, _ = market.weekly_fields(item, 100, replace(plain, sold_curve=curve), 150, 1, W_NOW, MarketSettings(), sales, pattern)
    at_level, _ = market.weekly_fields(item, 100, plain, 150, 1, W_NOW, MarketSettings(), sales, pattern)
    rows, plain_rows = sellprob.decode_curve(priced["curve"], 8), sellprob.decode_curve(at_level["curve"], 8)
    assert rows != plain_rows and all(a <= b for r, s in zip(rows, plain_rows) for a, b in zip(r, s))
    assert priced["curve"] == sellprob.encode_curve(sellprob.build_curve(10.0, 10.0, 0.5, sales[0].values, W_NOW, 1, price=150,
                                                                         acc=Acceptance(100, curve, sellprob.FLAT_HOW)))
