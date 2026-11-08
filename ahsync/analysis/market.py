"""Historical analysis: one market's history in, versioned market intelligence out (SPEC §3.3, §5.0).

Market-wide only: no character state (invariant 1). It reads through the
`HistoryReader` protocol, so it runs on SQLite locally or on any other store, for
example in a cloud function (AR1). It imports only the engine, the history contract
and the intelligence contract (AR4). One item that fails is set aside with its error
and the rest carry on (FM8).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace

from ahsync.engine import confidence, fees, patterns, prices, profile, sell_through, sellprob
from ahsync.engine.capacity import GEAR_CLASSES, slot_of
from ahsync.engine.profile import Profile
from ahsync.engine.sellprob import MarketSettings, Supply
from ahsync.engine.volume import (ID_METHOD_VERSION, VOLUME_METHOD_VERSION, Cover, DailyVolume, Volume, daily_volume, median_daily_supply,
                                  observed_turnover)
from ahsync.history.reads import HistoryReader, HistoryWindow, ItemHistory, ItemInfo
from ahsync.intel.contract import IntelWindow, ItemIntel, MarketIntelligence, ages_reference_ms

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
HISTORY_DAYS = 90
BOOK_MAX_AGE_MS = 24 * HOUR_MS          # SPEC §3.3.2: the newest scan, if under 24 h old
SELL_CHANCE_HOURS = 24                  # Sell Chance Now and a selling window's chance: a 24 h post (SPEC §4.3 TT37)


def _volumes(item: ItemHistory) -> dict[int, dict[str, Volume]]:
    """Stored daily volume as engine Volumes.

    Args:
        item: The item's history.

    Returns:
        day -> source -> Volume over the history window.
    """
    return {day: {src: Volume(*v) for src, v in sources.items()} for day, sources in item.volume.items()}


def unseen_per_day(item: ItemHistory, week: range, volume: DailyVolume) -> float | None:
    """Of sold/day, the units a day estimated sold before their listings were first seen (SPEC §2.4, BZ45, BZ49).

    The main id source's unseen share of its sold units over the week, applied to sold/day, so it is averaged
    exactly as sold/day is (coverage, the prior).

    Args:
        item: The item's history (its stored volume and unseen units by day and source).
        week: The day numbers sold/day was taken over.
        volume: The item's sold/day and its main source.

    Returns:
        Units per day; None when sold/day is unknown, the main source isn't auction ids, or nothing was estimated.
    """
    src = volume.main_source
    if volume.sold_per_day is None or volume.main_kind != "ids" or src is None:
        return None
    sold = sum(item.volume.get(day, {}).get(src, (0.0,))[0] for day in week)
    unseen = sum(item.unseen.get(day, {}).get(src, 0.0) for day in week)
    return volume.sold_per_day * unseen / sold if sold > 0 and unseen > 0 else None


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
                  settings: MarketSettings, sales: tuple[Profile, dict], undercut: float | None = None
                  ) -> tuple[dict, tuple[Profile, Profile, Profile]]:
    """An item's weekly patterns, seasonality strength and curve (SPEC §3.3.4, §3.3.6, §3.1.1).

    Args:
        item: The item's history (hourly closes and supply over the history window).
        med30: Its 30-day median, copper.
        volume: Its daily volume over the last 7 days.
        ref: Its reference stack, units.
        now_ms: Now; the curve's first row is this hour.
        settings: Pattern half-life, window threshold and the posting-now window.
        sales: From sales_fields.
        undercut: The item's measured u(p) for the curve (BZ29); None for default_undercut_share.

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
        u = settings.default_undercut_share if undercut is None else undercut
        grid = sellprob.build_curve(volume.sold_per_day, volume.listed_per_day or 0.0, u,
                                    sales_profile.values, now_ms, ref, hours=settings.sell_window_hours)
        curve, curve_hour = sellprob.encode_curve(grid), now_ms // HOUR_MS
    return {
        **fields,
        "seasonality": profile.seasonality_strength(hourly, med30, now_ms, half_life),
        "curve": curve, "curve_hour": curve_hour,
    }, (how, sales_profile, supply)


@dataclass(frozen=True)
class NowBase:
    """What the "now" fields need from an item's baselines (SPEC §3.3; the cloud's hourly mode, §2.4)."""

    med7: int | None                      # copper
    p90_30: int | None                    # copper
    closes_30d: int                       # hourly closes in 30 days (PP4's thin-data rule)
    sold_per_day: float | None            # units
    listed_per_day: float | None          # units
    sales_values: tuple[float, ...]       # the stored sales_how profile's 168 ratios (as decoded, W13)
    vendor_sell: int                      # copper per unit; 0 when unknown
    undercut_share: float | None = None   # measured u(p) at the post-now price (BZ29); None for the default


@dataclass(frozen=True)
class Quote:
    """The market's state of one item at one moment: its lowest price, units at it, and its book."""

    price: int | None                     # lowest buyout, copper; None = no listing
    qty: int | None                       # units at it
    book: tuple[tuple[int, int, int, int], ...] | None   # (price, stack, band, count); None without a book


def now_fields(quote: Quote, base: NowBase, now_ms: int, settings: MarketSettings
               ) -> tuple[int | None, int, float, bool, float | None]:
    """The post price, reference stack, chance and expected value of posting now (SPEC §3.3.2, PP2, PP4, P26).

    One function for the daily analysis and the hourly repricing, so the two can never disagree (BZ32).

    Args:
        quote: The current price, units and book.
        base: The item's baselines.
        now_ms: Now, UTC ms (the chance depends on the clock hour).
        settings: Market-wide defaults.

    Returns:
        (post_price copper or None, ref_stack units, chance, volume_unknown, sell_value float copper per unit or None).
    """
    ref = ref_stack(list(quote.book or ()))
    price = prices.post_price(quote.price, base.med7, base.p90_30, base.closes_30d)
    supply = Supply(base.sold_per_day, base.listed_per_day, quote.book, quote.price, quote.qty)
    chance, unknown = sellprob.posting_chance(supply, price, ref, now_ms, settings, base.sales_values, base.undercut_share)
    sell_value = None
    if price is not None:
        deposit = fees.deposit_copper(base.vendor_sell, ref, fees.shortest_duration_covering(settings.sell_window_hours))
        sell_value = sellprob.expected_post(chance, ref, price, float(base.vendor_sell), deposit) / ref
    return price, ref, chance, unknown, sell_value


def reprice_item(i: ItemIntel, quote: Quote, now_ms: int, settings: MarketSettings, vendor_sell: int,
                 recent_closes: list[int]) -> ItemIntel:
    """An item with a newer complete snapshot's state applied over its daily baselines (SPEC §2.4 hourly mode, BZ32).

    The baselines (medians, rates, profiles, windows, curve, confidence) are copied unchanged; only what the
    snapshot determines changes: cur, cur_qty, their age, the book and units on the AH, and what follows from the
    book against the baselines (post price, reference stack, chance, sell value, the suspect flag).

    Args:
        i: The item's intelligence (the daily document's, or the previous hourly one's).
        quote: Its state in the snapshot (it is listed there).
        now_ms: The snapshot's time, UTC ms.
        settings: The settings the baselines were built with.
        vendor_sell: Copper per unit; 0 when unknown.
        recent_closes: Its closes in the last 24 h, confirming an out-of-range price (FM5).

    Returns:
        The repriced ItemIntel.
    """
    base = NowBase(i.med7, i.p90_30, i.closes_30d or 0, i.sold_per_day, i.listed_per_day,
                   profile.unpack(i.sales_how, i.sales_weeks).values, vendor_sell, i.undercut_share)
    price, ref, chance, unknown, sell_value = now_fields(quote, base, now_ms, settings)
    book = quote.book or ()
    return replace(i, cur=quote.price, cur_qty=quote.qty, cur_age_ms=0, post_price=price, ref_stack=ref,
                   chance=chance, volume_unknown=unknown, sell_value=sell_value, book_age_ms=0, book=tuple(book),
                   sell_chance=sell_chance_now(quote, base, now_ms, settings),
                   sell_windows=sell_window_chances(i.sell_windows, quote, base, now_ms, settings),
                   units_on_ah=sum(stack * count for _, stack, _, count in book),
                   cur_suspect=prices.suspect_price(quote.price, i.p10_30, i.p90_30, recent_closes))


def sell_chance_now(quote: Quote, base: NowBase, now_ms: int, settings: MarketSettings) -> float | None:
    """Sell Chance Now: a 24 h post of the reference stack at the lowest listing sells before it expires (TT37).

    The posting-now simulation (§3.3.2) at the current market price instead of the post price, over 24 h instead of
    sell_window_hours, with the book, the measured sales and u(p) at the post-now position (§6 item 12).

    Args:
        quote: The current price, units and book.
        base: The item's baselines.
        now_ms: Now, UTC ms.
        settings: Market-wide defaults.

    Returns:
        P in [0, 1]; None without a listing or without measured sales ("—").
    """
    if quote.price is None or base.sold_per_day is None:
        return None
    return _chance_24h(quote, base, quote.price, now_ms, settings)


def _chance_24h(quote: Quote, base: NowBase, price: int, at_ms: int, settings: MarketSettings) -> float:
    """The posting simulation for a 24 h post of the reference stack (TT37, TT38).

    Args:
        quote: The current price, units and book.
        base: The item's baselines (sold_per_day not None).
        price: The post's price, copper.
        at_ms: When it is posted, UTC ms (the demand's hours start there).
        settings: Market-wide defaults; sell_window_hours is replaced by SELL_CHANCE_HOURS.

    Returns:
        P in [0, 1].
    """
    supply = Supply(base.sold_per_day, base.listed_per_day, quote.book, quote.price, quote.qty)
    day = replace(settings, sell_window_hours=SELL_CHANCE_HOURS)
    return sellprob.posting_chance(supply, price, ref_stack(list(quote.book or ())), at_ms, day, base.sales_values,
                                   base.undercut_share)[0]


def next_slot_ms(slot: int, now_ms: int) -> int:
    """The start of the next hour (now's included) that falls on an hour of the week.

    Args:
        slot: Hour of the week, 0 = Mon 00:00 UTC.
        now_ms: Now, UTC ms.

    Returns:
        That hour's start, UTC ms.
    """
    hour = now_ms // HOUR_MS
    return (hour + (slot - slot_of(hour)) % 168) * HOUR_MS


def window_lines(i: ItemIntel, quote: Quote, base: NowBase, closes: list[tuple[int, int]], now_ms: int,
                 settings: MarketSettings) -> dict:
    """The best windows' percentages for the tooltip's Best Sell / Best Buy Time (SPEC §4.3 TT38).

    Args:
        i: The item's intelligence, its windows built.
        quote: The current price, units and book.
        base: The item's baselines.
        closes: Its hourly closes, outliers dropped.
        now_ms: Now.
        settings: Market-wide defaults.

    Returns:
        {"sell_windows", "buy_windows"}: each selling window with chance24 (a 24 h post at its expected price at
        its next best hour; None without a price or sales), each buying window with found_share.
    """
    sell = sell_window_chances(i.sell_windows, quote, base, now_ms, settings)
    buy = tuple(replace(w, found_share=patterns.found_share(closes, w.start_slot, w.hours, w.expected_price, now_ms,
                                                            settings.pattern_half_life_weeks))
                for w in i.buy_windows)
    return {"sell_windows": sell, "buy_windows": buy}


def sell_window_chances(windows: tuple[IntelWindow, ...], quote: Quote, base: NowBase, now_ms: int,
                        settings: MarketSettings) -> tuple[IntelWindow, ...]:
    """Each selling window's chance (TT38): a 24 h post at its expected price at its next best hour, on this book.

    The daily analysis, the hourly repricing and your scans all call it, so it follows the book as Sell Chance Now
    does (review, M33b-1).

    Args:
        windows: The item's selling windows.
        quote: The current price, units and book.
        base: The item's baselines.
        now_ms: Now.
        settings: Market-wide defaults.

    Returns:
        The windows with chance24 set; None without an expected price or measured sales.
    """
    return tuple(replace(w, chance24=None if w.expected_price is None or base.sold_per_day is None else
                         _chance_24h(quote, base, w.expected_price, next_slot_ms(w.best_slot, now_ms), settings))
                 for w in windows)


def expired_share(lifetimes: dict[int, tuple[float, ...]], now_ms: int) -> float | None:
    """The share of the listings that ended in the last 30 days that expired (SPEC §2.4, BZ23).

    Args:
        lifetimes: day -> (ended, expired, matched) listings, from the history.
        now_ms: Now.

    Returns:
        expired ÷ ended; None when nothing ended.
    """
    first = (now_ms - 30 * DAY_MS) // DAY_MS + 1
    rows = [row for day, row in lifetimes.items() if first <= day <= now_ms // DAY_MS]
    ended = sum(r[0] for r in rows)
    return sum(r[1] for r in rows) / ended if ended > 0 else None


def measured_undercut(new_listings: dict[int, dict[int, float]], price: int | None, lowest_now: int | None,
                      now_ms: int) -> float | None:
    """u(p) from the last 7 days' new listings, through `sellprob.undercut_share` (SPEC §3.3.2, BZ29).

    Args:
        new_listings: day -> position (thousandths of the lowest other price at first sight) -> units.
        price: The post price, copper; None without one.
        lowest_now: The cheapest competing price now, copper; None without a competitor.
        now_ms: Now; days now−6 to now count, as the 7-day volume window.

    Returns:
        The share of new units at or below the post's position; None without a price, a competitor or history.
    """
    if price is None or lowest_now is None:
        return None
    first = (now_ms - 7 * DAY_MS) // DAY_MS + 1
    rows = [(units, position, 1000) for day, by_position in sorted(new_listings.items()) if first <= day <= now_ms // DAY_MS
            for position, units in sorted(by_position.items())]
    return sellprob.undercut_share(rows, price, lowest_now)


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
    med14 = prices.window_median(hourly, daily, now_ms, 14)       # the tooltip's Price history only (SPEC §4.3, BZ37)
    med30 = prices.window_median(hourly, daily, now_ms, 30)
    last30 = [c for t, c in hourly if t > now_ms - 30 * DAY_MS]
    week = range((now_ms - 7 * DAY_MS) // DAY_MS + 1, now_ms // DAY_MS + 1)
    sales = sales_fields(item, now_ms, settings)
    volume = daily_volume(_volumes(item), week, _covers(item), sales[0].values, settings.volume_prior)   # V30-V32, V41
    newest = item.book
    book = newest[1] if newest else None
    vendor_sell = (info.vendor_sell if info else None) or 0
    quote = Quote(cur.price if cur and cur.price is not None else None, cur.qty if cur else None,
                  tuple(book) if book is not None else None)
    undercut = measured_undercut(item.new_listings, prices.post_price(quote.price, med7, p90, len(last30)),
                                 sellprob.cheapest_other(quote.book, quote.price), now_ms)
    base = NowBase(med7, p90, len(last30), volume.sold_per_day, volume.listed_per_day, sales[0].values, vendor_sell,
                   undercut)
    price, ref, chance, unknown, sell_value = now_fields(quote, base, now_ms, settings)   # PP2, PP4, P26
    book_age = now_ms - newest[0] if newest else None
    recent = [c for t, c in raw if t > now_ms - DAY_MS]
    conf = confidence.percent(confidence.score(len(last30), volume.volume_score, book_age, None, 0))
    trend = prices.trend7(hourly, now_ms, med7)
    units = sum(stack * count for _, stack, _, count in book) if book else (cur.qty if cur else None)
    typical = median_daily_supply(item.hourly_supply, now_ms)
    weekly, (how, sales_profile, supply_profile) = weekly_fields(item, med30, volume, ref, now_ms, settings, sales,
                                                                 undercut)
    level, gear = (med7 if med7 is not None else med30), bool(info and info.item_class in GEAR_CLASSES)
    facts = None if level is None else patterns.ForecastFacts(                     # W15: the window forecasts
        now_ms, level, trend, p10, med7, p90, len(last30), units if units is not None else typical,
        supply_profile.values if any(supply_profile.weeks) else (),
        max(ref, settings.min_gear_units) if gear else ref,
        settings.max_supply_share_gear if gear else settings.max_supply_share,
        volume.volume_score, book_age, settings.max_trend_total)
    intel = ItemIntel(
        key, cur.price if cur else None, cur.qty if cur else None, now_ms - cur.at if cur else None,
        med7, med30, p10, p90,
        trend, volume.sold_per_day, volume.expired_per_day, volume.listed_per_day,
        volume.main_source, volume.days_by_source, price, ref, chance, unknown, sell_value, conf,
        units, book_age, tuple(book or ()),
        **weekly, **window_fields(how, sales_profile, facts, settings),
        cur_suspect=prices.suspect_price(cur.price if cur else None, p10, p90, recent),
        median_daily_supply=typical,
        observed_listing_turnover=observed_turnover(item.hourly_supply, now_ms), med14=med14,
        volume_kind=volume.main_kind, closes_30d=len(last30),
        expired_share=expired_share(item.lifetimes, now_ms), undercut_share=undercut,
        commodity=any(row[sell_through.COMMODITY] for row in item.lifetimes.values()) if item.lifetimes else None,
        supply7=median_daily_supply(item.hourly_supply, now_ms, 7),
        supply14=median_daily_supply(item.hourly_supply, now_ms, 14),
        sell_chance=sell_chance_now(quote, base, now_ms, settings), unseen_per_day=unseen_per_day(item, week, volume))
    return replace(intel, **window_lines(intel, quote, base, hourly, now_ms, settings))


def lifetime_sums(lifetimes: dict[int, tuple[float, ...]], now_ms: int
                  ) -> tuple[tuple[sell_through.Tally, ...], tuple[float, float]]:
    """What the market pass needs from one item's lifetimes, so the rows themselves need not be kept (BZ40, BZ41).

    Args:
        lifetimes: The item's day -> stored lifetimes row.
        now_ms: Now.

    Returns:
        (its Tally per lookback in LOOKBACKS order, its (cancels, left early) for the cancel share).
    """
    return (tuple(sell_through.tally(lifetimes, now_ms, d) for d in sell_through.LOOKBACKS),
            sell_through.cancel_counts(lifetimes, now_ms))


def with_sell_through(items: dict[int, ItemIntel],
                      sums: dict[int, tuple[tuple[sell_through.Tally, ...], tuple[float, float]]],
                      classes: dict[int, int | None]) -> tuple[dict[int, ItemIntel], float | None]:
    """Every item's sell-through per lookback, from the whole market (SPEC §2.4, BZ40, BZ41).

    Market-wide inputs first: the commodity cancel share over 30 days and each class's pooled rate per lookback;
    then each item's own or borrowed rate.

    Args:
        items: Item key -> its intelligence.
        sums: Item key -> `lifetime_sums`, for the items that have lifetimes.
        classes: Item key -> item class, None when unknown.

    Returns:
        (the items with sell_through and sell_through_how set, the market's commodity cancel share or None).
    """
    cancels = sell_through.cancel_share(sums[k][1] for k in sorted(sums))
    none = sell_through.Tally(0.0, 0.0, False)
    tallies = {days: {k: sums[k][0][n] if k in sums else none for k in sorted(items)}
               for n, days in enumerate(sell_through.LOOKBACKS)}
    pooled = {days: sell_through.class_rates(((classes.get(k), t) for k, t in sorted(by_key.items())), cancels)
              for days, by_key in tallies.items()}
    out = {}
    for key, i in sorted(items.items()):
        c = classes.get(key)
        rates = [sell_through.sell_through(tallies[d][key], cancels, None if c is None else pooled[d].get(c))
                 for d in sell_through.LOOKBACKS]
        out[key] = replace(i, sell_through=tuple(r.rate for r in rates),
                           sell_through_how="".join(r.how or "-" for r in rates))
    return out, cancels


def with_scan(intel: MarketIntelligence, prices: dict[int, tuple[int, int, int]],
              books: dict[int, tuple[int, list[tuple[int, int, int, int]]]], settings: MarketSettings,
              vendor_sell: dict[int, int]) -> MarketIntelligence:
    """The intelligence with your newer in-game scans applied to the items they priced (SPEC §3.1.1, IU6).

    A scanned item is repriced through now_fields, the formulas the analysis and the cloud's hourly mode use, so
    the plan's post-now price, chance and expected value follow what you just saw (the user, 2026-11-06).

    Args:
        intel: The published intelligence.
        prices: Item key -> (lowest buyout copper, units at it, seen ms) from your scans after its reference time.
        books: Item key -> (scan ms, [(price, stack, band, count)]) from your newest complete scan after it.
        settings: The settings the document's baselines were built with.
        vendor_sell: Item key -> vendor sell price, copper (the deposit); a missing key counts as 0.

    Returns:
        A copy whose scanned items have the scan's cur, cur_qty, book, current supply, suspect flag and the
        post-now fields recomputed; their ages count back from the document's reference time, so a scan after it
        is negative (intel.contract.age_now_ms ages it); every other item and the build time unchanged.
    """
    reference = ages_reference_ms(intel)
    items = dict(intel.items)
    for key in sorted(set(prices) & set(items)):
        i, (price, qty, at) = items[key], prices[key]
        book = books.get(key)
        rows = tuple(book[1]) if book else i.book
        base = NowBase(i.med7, i.p90_30, i.closes_30d or 0, i.sold_per_day, i.listed_per_day,
                       profile.unpack(i.sales_how, i.sales_weeks).values, vendor_sell.get(key, 0), i.undercut_share)
        quote = Quote(price, qty, rows or None)
        post, ref, chance, unknown, sell_value = now_fields(quote, base, at, settings)
        items[key] = replace(i, cur=price, cur_qty=qty, cur_age_ms=reference - at, book=rows,
                             book_age_ms=reference - book[0] if book else i.book_age_ms,
                             units_on_ah=sum(stack * count for _, stack, _, count in rows) if book else i.units_on_ah,
                             cur_suspect=prices_suspect(price, i.p10_30, i.p90_30), post_price=post, ref_stack=ref,
                             chance=chance, volume_unknown=unknown, sell_value=sell_value,
                             sell_chance=sell_chance_now(quote, base, at, settings),
                             sell_windows=sell_window_chances(i.sell_windows, quote, base, at, settings))
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
    sums: dict[int, tuple[tuple[sell_through.Tally, ...], tuple[float, float]]] = {}
    for item in history.market_history(market, history_window(now_ms, source_rank)):   # one bulk read per market (AR7)
        if item.key < 0:          # some suffix of the item, which one unknown: stored, never valued (SPEC §2.2.1, AR8)
            continue
        try:
            out[item.key] = analyze_item(item, items.get(item.key), now_ms, settings)
        except Exception as error:                 # noqa: BLE001  one bad item never stops the analysis (FM8)
            failed[item.key] = f"{type(error).__name__}: {error}"
            continue
        if item.lifetimes:
            sums[item.key] = lifetime_sums(item.lifetimes, now_ms)     # the rows are not kept (bounded memory)
    classes = {k: items[k].item_class if k in items else None for k in out}
    out, cancels = with_sell_through(out, sums, classes)
    seen = [now_ms - i.cur_age_ms for i in out.values() if i.cur_age_ms is not None]
    method = max(VOLUME_METHOD_VERSION, ID_METHOD_VERSION)       # the newer of the two volume methods (informational)
    return replace(MarketIntelligence(market, now_ms, max(seen) if seen else None, method, out),
                   cancel_share=cancels), failed
