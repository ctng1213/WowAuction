"""Historical analysis: one market's history in, versioned market intelligence out (SPEC §3.3, §5.0).

Market-wide only: no character state (invariant 1). It reads through the
`HistoryReader` protocol, so it runs on SQLite locally or on any other store, for
example in a cloud function (AR1). It imports only the engine, the history contract
and the intelligence contract (AR4). One item that fails is set aside with its error
and the rest carry on (FM8).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace

from ahsync.engine import confidence, fees, patterns, prices, profile, sellprob
from ahsync.engine.capacity import GEAR_CLASSES
from ahsync.engine.profile import Profile
from ahsync.engine.sellprob import MarketSettings, Supply
from ahsync.engine.volume import (VOLUME_METHOD_VERSION, Cover, DailyVolume, Volume, daily_volume, median_daily_supply,
                                  observed_turnover)
from ahsync.history.reads import HistoryReader, HistoryWindow, ItemHistory, ItemInfo
from ahsync.intel.contract import IntelWindow, ItemIntel, MarketIntelligence

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
HISTORY_DAYS = 90
BOOK_MAX_AGE_MS = 24 * HOUR_MS          # SPEC §3.3.2: the newest scan, if under 24 h old


def _volumes(item: ItemHistory) -> dict[int, dict[str, Volume]]:
    """Stored daily volume as engine Volumes.

    Args:
        item: The item's history.

    Returns:
        day -> source -> Volume over the history window.
    """
    return {day: {src: Volume(*v) for src, v in sources.items()} for day, sources in item.volume.items()}


def _covers(item: ItemHistory) -> dict[int, dict[str, Cover]]:
    """Stored volume covers as engine Covers.

    Args:
        item: The item's history.

    Returns:
        day -> source -> Cover.
    """
    return {day: {src: Cover(kind, covered) for src, (kind, covered) in sources.items()}
            for day, sources in item.volume_cover.items()}


def history_window(now_ms: int, source_rank: tuple[str, ...] = ("local", "ahf")) -> HistoryWindow:
    """What analysis reads for a refresh at now_ms (SPEC §3.3): 90 days, books under 24 h old.

    Args:
        now_ms: Now.
        source_rank: Price sources, most trusted first (SRC3).

    Returns:
        The window.
    """
    start = now_ms - HISTORY_DAYS * DAY_MS
    return HistoryWindow(start, now_ms, start // DAY_MS, now_ms - BOOK_MAX_AGE_MS, source_rank)


def ref_stack(book: list[tuple[int, int, int, int]]) -> int:
    """The item's reference stack: the most common stack size in the book, ties to the smaller (SPEC §3.4).

    Args:
        book: (price, stack, band, count) rows.

    Returns:
        The stack size; 1 without a book.
    """
    counts: Counter = Counter()
    for _, stack, _, count in book:
        counts[stack] += count
    return min(counts, key=lambda s: (-counts[s], s)) if counts else 1


def _packed(name: str, prof: Profile, fields: dict) -> Profile:
    """Pack a profile into contract fields and return it as stored (SPEC §5.0 contents, W13).

    Args:
        name: Contract field prefix: "how", "sales" or "supply".
        prof: The profile.
        fields: Receives the value and weeks fields (side effect).

    Returns:
        The profile as consumers will decode it, so windows and the curve use the stored values.
    """
    values, weeks = profile.pack(prof)
    fields["how" if name == "how" else f"{name}_how"] = values
    fields[f"{name}_weeks"] = weeks
    return profile.unpack(values, weeks)


def _windows(windows: list[patterns.Window]) -> tuple[IntelWindow, ...]:
    """Engine windows as contract windows, with their forecasts (W15).

    Args:
        windows: From patterns.sell_windows or patterns.buy_windows.

    Returns:
        The same windows, in order; the forecast fields None for a window without a forecast.
    """
    return tuple(IntelWindow(w.start_slot, w.hours, w.best_slot, w.how_avg, w.weeks,
                             *((w.forecast.price, w.forecast.exec_qty, w.forecast.conf) if w.forecast else ()))
                 for w in windows)


def window_fields(how: Profile, sales: Profile, facts: patterns.ForecastFacts | None, settings: MarketSettings) -> dict:
    """An item's selling and buying windows with their forecasts (SPEC §3.3.4, §3.3.6, W14, W15).

    Args:
        how: The item's price profile, as stored.
        sales: Its sales profile, as stored.
        facts: What forecasting needs (level, supply, ...); None when the item has no price level.
        settings: Window threshold and how many windows to keep.

    Returns:
        {"sell_windows", "buy_windows"} as contract windows: selling by sell_score, buying by actionable value
        (by price ratio without a level).
    """
    forecast = None if facts is None else (lambda w: patterns.window_forecast(w, facts))
    sell = patterns.sell_windows(how.values, sales.values, how.weeks, settings.window_threshold,
                                 settings.top_sell_windows, forecast)
    net = None if facts is None else patterns.sell_net_price(sell, facts.level)
    buy = patterns.buy_windows(how.values, how.weeks, settings.window_threshold, settings.top_buy_windows, forecast,
                               net)
    return {"sell_windows": _windows(sell), "buy_windows": _windows(buy)}


def sales_fields(item: ItemHistory, now_ms: int, settings: MarketSettings) -> tuple[Profile, dict]:
    """An item's hourly sales profile, packed: volume weights partly observed days by it (SPEC §3.3.1, V31).

    Args:
        item: The item's history (hourly sales).
        now_ms: Now.
        settings: Pattern half-life.

    Returns:
        (the profile as stored, its contract fields by name).
    """
    fields: dict = {}
    sales = _packed("sales", profile.sales_profile(item.hourly_sales, now_ms, settings.pattern_half_life_weeks), fields)
    return sales, fields


def weekly_fields(item: ItemHistory, med30: int | None, volume: DailyVolume, ref: int, now_ms: int,
                  settings: MarketSettings, sales: tuple[Profile, dict]) -> tuple[dict, tuple[Profile, Profile, Profile]]:
    """An item's weekly patterns, seasonality strength and curve (SPEC §3.3.4, §3.3.6, §3.1.1).

    Args:
        item: The item's history (hourly closes and supply over the history window).
        med30: Its 30-day median, copper.
        volume: Its daily volume over the last 7 days.
        ref: Its reference stack, units.
        now_ms: Now; the curve's first row is this hour.
        settings: Pattern half-life, window threshold and the posting-now window.
        sales: From sales_fields.

    Returns:
        (the ItemIntel pattern fields by name, (price, sales, supply) profiles as stored for window_fields).
    """
    hourly = item.hourly_closes
    half_life = settings.pattern_half_life_weeks
    fields: dict = dict(sales[1])
    sales_profile = sales[0]
    how = _packed("how", profile.price_profile(hourly, med30, now_ms, half_life), fields)
    supply = _packed("supply", profile.supply_profile(item.hourly_supply, now_ms, half_life), fields)
    curve, curve_hour = "", None
    if volume.sold_per_day is not None:
        grid = sellprob.build_curve(volume.sold_per_day, volume.listed_per_day or 0.0, settings.default_undercut_share,
                                    sales_profile.values, now_ms, ref, hours=settings.sell_window_hours)
        curve, curve_hour = sellprob.encode_curve(grid), now_ms // HOUR_MS
    return {
        **fields,
        "seasonality": profile.seasonality_strength(hourly, med30, now_ms, half_life),
        "curve": curve, "curve_hour": curve_hour,
    }, (how, sales_profile, supply)


def analyze_item(item: ItemHistory, info: ItemInfo | None, now_ms: int, settings: MarketSettings) -> ItemIntel:
    """One item's intelligence from its stored history (SPEC §3.3).

    Args:
        item: The item's history, from the market read.
        info: Item metadata, or None when unknown.
        now_ms: Now.
        settings: Market-wide defaults.

    Returns:
        The ItemIntel.
    """
    key, raw, daily, cur = item.key, item.hourly_closes, item.daily_closes, item.current
    raw30 = [c for t, c in raw if t > now_ms - 30 * DAY_MS]
    p10, p90 = prices.percentile(raw30, 0.1), prices.percentile(raw30, 0.9)
    hourly = prices.drop_suspect(raw, p10, p90)            # unconfirmed outliers count in no statistic (SPEC §5.2, FM31)
    item = replace(item, hourly_closes=hourly)
    med7 = prices.window_median(hourly, daily, now_ms, 7)
    med30 = prices.window_median(hourly, daily, now_ms, 30)
    last30 = [c for t, c in hourly if t > now_ms - 30 * DAY_MS]
    week = range((now_ms - 7 * DAY_MS) // DAY_MS + 1, now_ms // DAY_MS + 1)
    sales = sales_fields(item, now_ms, settings)
    volume = daily_volume(_volumes(item), week, _covers(item), sales[0].values, settings.volume_prior)   # V30-V32, V41
    newest = item.book
    book = newest[1] if newest else None
    ref = ref_stack(book or [])
    price = prices.post_price(cur.price if cur and cur.price is not None else None, med7, p90, len(last30))  # PP2, PP4
    supply = Supply(volume.sold_per_day, volume.listed_per_day, tuple(book) if book is not None else None,
                    cur.price if cur else None, cur.qty if cur else None)
    chance, unknown = sellprob.posting_chance(supply, price, ref, now_ms, settings, sales[0].values)   # P26
    vendor_sell = (info.vendor_sell if info else None) or 0
    sell_value = None
    if price is not None:
        deposit = fees.deposit_copper(vendor_sell, ref, fees.shortest_duration_covering(settings.sell_window_hours))
        sell_value = sellprob.expected_post(chance, ref, price, float(vendor_sell), deposit) / ref
    book_age = now_ms - newest[0] if newest else None
    recent = [c for t, c in raw if t > now_ms - DAY_MS]
    conf = confidence.percent(confidence.score(len(last30), volume.volume_score, book_age, None, 0))
    trend = prices.trend7(hourly, now_ms, med7)
    units = sum(stack * count for _, stack, _, count in book) if book else (cur.qty if cur else None)
    typical = median_daily_supply(item.hourly_supply, now_ms)
    weekly, (how, sales_profile, supply_profile) = weekly_fields(item, med30, volume, ref, now_ms, settings, sales)
    level, gear = (med7 if med7 is not None else med30), bool(info and info.item_class in GEAR_CLASSES)
    facts = None if level is None else patterns.ForecastFacts(                     # W15: the window forecasts
        now_ms, level, trend, p10, med7, p90, len(last30), units if units is not None else typical,
        supply_profile.values if any(supply_profile.weeks) else (),
        max(ref, settings.min_gear_units) if gear else ref,
        settings.max_supply_share_gear if gear else settings.max_supply_share,
        volume.volume_score, book_age, settings.max_trend_total)
    return ItemIntel(
        key, cur.price if cur else None, cur.qty if cur else None, now_ms - cur.at if cur else None,
        med7, med30, p10, p90,
        trend, volume.sold_per_day, volume.expired_per_day, volume.listed_per_day,
        volume.main_source, volume.days_by_source, price, ref, chance, unknown, sell_value, conf,
        units, book_age, tuple(book or ()),
        **weekly, **window_fields(how, sales_profile, facts, settings),
        cur_suspect=prices.suspect_price(cur.price if cur else None, p10, p90, recent),
        median_daily_supply=typical,
        observed_listing_turnover=observed_turnover(item.hourly_supply, now_ms),
        volume_kind=volume.main_kind, closes_30d=len(last30))


def with_scan(intel: MarketIntelligence, prices: dict[int, tuple[int, int, int]],
              books: dict[int, tuple[int, list[tuple[int, int, int, int]]]]) -> MarketIntelligence:
    """The intelligence with your newer in-game scans applied to the items they priced (SPEC §3.1.1, IU6).

    Args:
        intel: The published intelligence.
        prices: Item key -> (lowest buyout copper, units at it, seen ms) from your scans after intel.built_ms.
        books: Item key -> (scan ms, [(price, stack, band, count)]) from your newest complete scan after it.

    Returns:
        A copy whose scanned items have the scan's cur, cur_qty, price age (0 when seen after the build), book
        and current supply, and cur_suspect re-checked; every other item and the build time unchanged.
    """
    items = dict(intel.items)
    for key in sorted(set(prices) & set(items)):
        i, (price, qty, _at) = items[key], prices[key]
        book = books.get(key)
        rows = tuple(book[1]) if book else i.book
        items[key] = replace(i, cur=price, cur_qty=qty, cur_age_ms=0, book=rows,
                             units_on_ah=sum(stack * count for _, stack, _, count in rows) if book else i.units_on_ah,
                             cur_suspect=prices_suspect(price, i.p10_30, i.p90_30))
    return replace(intel, items=items)


def prices_suspect(price: int, p10: int | None, p90: int | None) -> bool:
    """Whether one fresh scan price is out of the item's usual range (FM5), with no second hour to confirm it.

    Args:
        price: Copper.
        p10: The 30-day 10th percentile.
        p90: The 30-day 90th percentile.

    Returns:
        As engine.prices.suspect_price with no recent closes.
    """
    return prices.suspect_price(price, p10, p90, [])


def analyze_market(history: HistoryReader, market: str, now_ms: int, settings: MarketSettings,
                   source_rank: tuple[str, ...] = ("local", "ahf")) -> tuple[MarketIntelligence, dict[int, str]]:
    """The market's intelligence for every item with price data, except unknown-suffix keys (SPEC §3.1.1 step 1).

    Args:
        history: The history.
        market: Market id.
        now_ms: Now.
        settings: Market-wide defaults.
        source_rank: Price sources, most trusted first: your scans, then the configured remote ranking (SRC3).

    Returns:
        (the intelligence, item key -> error for items set aside).
    """
    items = history.items()
    out: dict[int, ItemIntel] = {}
    failed: dict[int, str] = {}
    for item in history.market_history(market, history_window(now_ms, source_rank)):   # one bulk read per market (AR7)
        if item.key < 0:          # some suffix of the item, which one unknown: stored, never valued (SPEC §2.2.1, AR8)
            continue
        try:
            out[item.key] = analyze_item(item, items.get(item.key), now_ms, settings)
        except Exception as error:                 # noqa: BLE001  one bad item never stops the analysis (FM8)
            failed[item.key] = f"{type(error).__name__}: {error}"
    seen = [now_ms - i.cur_age_ms for i in out.values() if i.cur_age_ms is not None]
    return MarketIntelligence(market, now_ms, max(seen) if seen else None, VOLUME_METHOD_VERSION, out), failed
