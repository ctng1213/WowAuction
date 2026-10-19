"""Historical analysis: one market's history in, versioned market intelligence out (SPEC §3.3, §5.0).

Market-wide only: no character state (invariant 1). It reads through the
`HistoryReader` protocol, so it runs on SQLite locally or on any other store, for
example in a cloud function (AR1). It imports only the engine, the history contract
and the intelligence contract (AR4). One item that fails is set aside with its error
and the rest carry on (FM8).
"""
from __future__ import annotations

from collections import Counter

from ahsync.engine import confidence, fees, patterns, prices, profile, sellprob
from ahsync.engine.profile import Profile
from ahsync.engine.sellprob import MarketSettings, Supply
from ahsync.engine.volume import VOLUME_METHOD_VERSION, DailyVolume, Volume, daily_volume
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


def history_window(now_ms: int) -> HistoryWindow:
    """What analysis reads for a refresh at now_ms (SPEC §3.3): 90 days, books under 24 h old.

    Args:
        now_ms: Now.

    Returns:
        The window.
    """
    start = now_ms - HISTORY_DAYS * DAY_MS
    return HistoryWindow(start, now_ms, start // DAY_MS, now_ms - BOOK_MAX_AGE_MS)


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
    """Engine windows as contract windows.

    Args:
        windows: From patterns.sell_windows or patterns.buy_windows.

    Returns:
        The same windows, in order.
    """
    return tuple(IntelWindow(w.start_slot, w.hours, w.best_slot, w.how_avg, w.weeks) for w in windows)


def weekly_fields(item: ItemHistory, med30: int | None, volume: DailyVolume, ref: int, now_ms: int,
                  settings: MarketSettings) -> dict:
    """An item's weekly patterns, windows, seasonality strength and curve (SPEC §3.3.4, §3.3.6, §3.1.1).

    Args:
        item: The item's history (hourly closes, sales and supply over the history window).
        med30: Its 30-day median, copper.
        volume: Its daily volume over the last 7 days.
        ref: Its reference stack, units.
        now_ms: Now; the curve's first row is this hour.
        settings: Pattern half-life, window threshold and the posting-now window.

    Returns:
        The ItemIntel pattern fields by name.
    """
    hourly = item.hourly_closes
    half_life = settings.pattern_half_life_weeks
    fields: dict = {}
    how = _packed("how", profile.price_profile(hourly, med30, now_ms, half_life), fields)
    sales = _packed("sales", profile.sales_profile(item.hourly_sales, now_ms, half_life), fields)
    _packed("supply", profile.supply_profile(item.hourly_supply, now_ms, half_life), fields)
    curve, curve_hour = "", None
    if volume.sold_per_day is not None:
        grid = sellprob.build_curve(volume.sold_per_day, volume.listed_per_day or 0.0, settings.default_undercut_share,
                                    sales.values, now_ms, ref, hours=settings.sell_window_hours)
        curve, curve_hour = sellprob.encode_curve(grid), now_ms // HOUR_MS
    return {
        **fields,
        "seasonality": profile.seasonality_strength(hourly, med30, now_ms, half_life),
        "sell_windows": _windows(patterns.sell_windows(how.values, sales.values, how.weeks, settings.window_threshold)),
        "buy_windows": _windows(patterns.buy_windows(how.values, how.weeks, settings.window_threshold)),
        "curve": curve, "curve_hour": curve_hour,
    }


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
    key, hourly, daily, cur = item.key, item.hourly_closes, item.daily_closes, item.current
    med7 = prices.window_median(hourly, daily, now_ms, 7)
    med30 = prices.window_median(hourly, daily, now_ms, 30)
    last30 = [c for t, c in hourly if t > now_ms - 30 * DAY_MS]
    week = range((now_ms - 7 * DAY_MS) // DAY_MS + 1, now_ms // DAY_MS + 1)
    volume = daily_volume(_volumes(item), week)
    newest = item.book
    book = newest[1] if newest else None
    ref = ref_stack(book or [])
    price = prices.post_price(cur.price if cur and cur.price is not None else None, med7)
    supply = Supply(volume.sold_per_day, volume.listed_per_day, tuple(book) if book is not None else None,
                    cur.price if cur else None, cur.qty if cur else None)
    chance, unknown = sellprob.posting_chance(supply, price, ref, now_ms, settings)
    vendor_sell = (info.vendor_sell if info else None) or 0
    sell_value = None
    if price is not None:
        deposit = fees.deposit_copper(vendor_sell, ref, fees.shortest_duration_covering(settings.sell_window_hours))
        sell_value = sellprob.expected_post(chance, ref, price, float(vendor_sell), deposit) / ref
    book_age = now_ms - newest[0] if newest else None
    conf = confidence.percent(confidence.score(len(last30), volume.days_by_source, book_age, None, 0))
    main_source = max(volume.days_by_source, key=lambda s: (volume.days_by_source[s], s)) \
        if volume.days_by_source else None
    return ItemIntel(
        key, cur.price if cur else None, cur.qty if cur else None, now_ms - cur.at if cur else None,
        med7, med30, prices.percentile(last30, 0.1), prices.percentile(last30, 0.9),
        prices.trend7(hourly, now_ms, med7), volume.sold_per_day, volume.expired_per_day, volume.listed_per_day,
        main_source, volume.days_by_source, price, ref, chance, unknown, sell_value, conf,
        sum(stack * count for _, stack, _, count in book) if book else (cur.qty if cur else None),
        book_age, tuple(book or ()),
        **weekly_fields(item, med30, volume, ref, now_ms, settings))


def analyze_market(history: HistoryReader, market: str, now_ms: int,
                   settings: MarketSettings) -> tuple[MarketIntelligence, dict[int, str]]:
    """The market's intelligence for every item with price data, except unknown-suffix keys (SPEC §3.1.1 step 1).

    Args:
        history: The history.
        market: Market id.
        now_ms: Now.
        settings: Market-wide defaults.

    Returns:
        (the intelligence, item key -> error for items set aside).
    """
    items = history.items()
    out: dict[int, ItemIntel] = {}
    failed: dict[int, str] = {}
    for item in history.market_history(market, history_window(now_ms)):   # one bulk read per market (AR7)
        if item.key < 0:          # some suffix of the item, which one unknown: stored, never valued (SPEC §2.2.1, AR8)
            continue
        try:
            out[item.key] = analyze_item(item, items.get(item.key), now_ms, settings)
        except Exception as error:                 # noqa: BLE001  one bad item never stops the analysis (FM8)
            failed[item.key] = f"{type(error).__name__}: {error}"
    seen = [now_ms - i.cur_age_ms for i in out.values() if i.cur_age_ms is not None]
    return MarketIntelligence(market, now_ms, max(seen) if seen else None, VOLUME_METHOD_VERSION, out), failed
