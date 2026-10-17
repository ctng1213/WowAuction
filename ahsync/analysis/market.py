"""Historical analysis: one market's history in, versioned market intelligence out (SPEC §3.3, §5.0).

Market-wide only: no character state (invariant 1). It reads through the
`HistoryReader` protocol, so it runs on SQLite locally or on any other store, for
example in a cloud function (AR1). It imports only the engine, the history contract
and the intelligence contract (AR4). One item that fails is set aside with its error
and the rest carry on (FM8).
"""
from __future__ import annotations

from collections import Counter

from ahsync.engine import confidence, fees, prices, sellprob
from ahsync.engine.sellprob import MarketSettings, Supply
from ahsync.engine.volume import VOLUME_METHOD_VERSION, Volume, daily_volume
from ahsync.history.reads import HistoryReader, ItemInfo
from ahsync.intel.contract import ItemIntel, MarketIntelligence

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
HISTORY_DAYS = 90
BOOK_MAX_AGE_MS = 24 * HOUR_MS          # SPEC §3.3.2: the newest scan, if under 24 h old


def _volumes(history: HistoryReader, market: str, key: int, now_ms: int) -> dict[int, dict[str, Volume]]:
    """Stored daily volume as engine Volumes.

    Args:
        history: The history.
        market: Market id.
        key: Item key.
        now_ms: Now.

    Returns:
        day -> source -> Volume over the history window.
    """
    rows = history.volume_rows(market, key, (now_ms - HISTORY_DAYS * DAY_MS) // DAY_MS)
    return {day: {src: Volume(*v) for src, v in sources.items()} for day, sources in rows.items()}


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


def analyze_item(history: HistoryReader, market: str, key: int, info: ItemInfo | None, now_ms: int,
                 settings: MarketSettings) -> ItemIntel:
    """One item's intelligence from its stored history (SPEC §3.3).

    Args:
        history: The history.
        market: Market id.
        key: Item key.
        info: Item metadata, or None when unknown.
        now_ms: Now.
        settings: Market-wide defaults.

    Returns:
        The ItemIntel.
    """
    start = now_ms - HISTORY_DAYS * DAY_MS
    hourly = history.hourly_closes(market, key, start, now_ms)
    daily = history.daily_closes(market, key, start, now_ms)
    cur = history.current_price(market, key)
    med7 = prices.window_median(hourly, daily, now_ms, 7)
    med30 = prices.window_median(hourly, daily, now_ms, 30)
    last30 = [c for t, c in hourly if t > now_ms - 30 * DAY_MS]
    week = range((now_ms - 7 * DAY_MS) // DAY_MS + 1, now_ms // DAY_MS + 1)
    volume = daily_volume(_volumes(history, market, key, now_ms), week)
    newest = history.newest_book(market, key, now_ms - BOOK_MAX_AGE_MS)
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
        book_age, tuple(book or ()))


def analyze_market(history: HistoryReader, market: str, now_ms: int,
                   settings: MarketSettings) -> tuple[MarketIntelligence, dict[int, str]]:
    """The market's intelligence for every item with price data (SPEC §3.1.1 step 1).

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
    for key in history.priced_keys(market, now_ms - HISTORY_DAYS * DAY_MS):
        try:
            out[key] = analyze_item(history, market, key, items.get(key), now_ms, settings)
        except Exception as error:                 # noqa: BLE001  one bad item never stops the analysis (FM8)
            failed[key] = f"{type(error).__name__}: {error}"
    seen = [now_ms - i.cur_age_ms for i in out.values() if i.cur_age_ms is not None]
    return MarketIntelligence(market, now_ms, max(seen) if seen else None, VOLUME_METHOD_VERSION, out), failed
