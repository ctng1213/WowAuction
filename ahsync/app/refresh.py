"""The full refresh: stored history in, item statistics and dashboard pages out (SPEC §3.1.1, §3.5).

This is the only place AHSync reads through history. Per market it computes each
item's statistics with the pure engine, builds the Disenchant and Craft pages, writes
everything into staging tables and swaps them in with one transaction, so a failure
halfway leaves the previous numbers live (SPEC §5.2). One item that fails is set aside
with its error and the rest carry on (FM8).

M3 scope (SPEC GAPs, refined in M4): hold value = posting-now sell value (the sell plan
comes in M4); the unsold fallback is the vendor price only; sales_how is flat until
hourly sales are measured from consecutive books.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass, field

from ahsync.config import Config
from ahsync.engine import confidence, craft, disenchant, fees, prices, sellprob, views
from ahsync.engine.de_table import DE_TABLE
from ahsync.engine.volume import Volume, daily_volume
from ahsync.db.sqlite import Database
from ahsync.db.refresh_store import ItemInfo, RecipeRow

log = logging.getLogger(__name__)

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
HISTORY_DAYS = 90
BOOK_MAX_AGE_MS = 24 * HOUR_MS          # SPEC §3.3.2: the newest scan, if under 24 h old
DE_KIND = {2: "weapon", 4: "armor"}     # WoW item class -> DE table kind


@dataclass(frozen=True)
class ItemStats:
    """One item's statistics for a market (SPEC §3.3), stored in item_stats as JSON."""

    key: int
    cur: int | None
    cur_qty: int | None
    cur_age_ms: int | None
    med7: int | None
    med30: int | None
    p10_30: int | None
    p90_30: int | None
    trend7: float
    sold_per_day: float | None
    expired_per_day: float | None
    listed_per_day: float | None
    volume_source: str | None             # the source covering most of the last 7 days
    days_by_source: dict[str, int]
    post_price: int | None
    ref_stack: int
    chance: float                         # P' for posting one reference stack now
    volume_unknown: bool
    sell_value: float | None              # E_post per unit posting now, float copper
    conf: int                             # confidence %
    units_on_ah: int | None
    book_age_ms: int | None
    book: tuple[tuple[int, int, int, int], ...] = field(default=())   # (price, stack, band, count)


@dataclass
class RefreshResult:
    """What a full refresh did."""

    markets: list[str]
    items: int = 0
    failed: dict[str, str] = field(default_factory=dict)    # "<market>:<key>" -> error
    error: str | None = None                                # why nothing was published, if so


def _volumes(db: Database, market: str, key: int, now_ms: int) -> dict[int, dict[str, Volume]]:
    """Stored daily volume as engine Volumes.

    Args:
        db: Database.
        market: Market id.
        key: Item key.
        now_ms: Now.

    Returns:
        day -> source -> Volume over the history window.
    """
    rows = db.volume_rows(market, key, (now_ms - HISTORY_DAYS * DAY_MS) // DAY_MS)
    return {day: {src: Volume(*v) for src, v in sources.items()} for day, sources in rows.items()}


def _ref_stack(book: list[tuple[int, int, int, int]]) -> int:
    """The item's reference stack: the most common stack size in the book, ties to the smaller (SPEC §3.4).

    Args:
        book: (price, stack, band, count) rows.

    Returns:
        The stack size; 1 without a book.
    """
    counts = Counter()
    for _, stack, _, count in book:
        counts[stack] += count
    return min(counts, key=lambda s: (-counts[s], s)) if counts else 1


@dataclass(frozen=True)
class Supply:
    """What a post competes with: market volume and the competing listings."""

    sold_per_day: float | None                        # S; None when unknown
    listed_per_day: float | None                      # L
    book: tuple[tuple[int, int, int, int], ...] | None   # (price, stack, band, count), or None without a book
    cur_price: int | None                             # lowest buyout, used without a book
    cur_qty: int | None


def _chance(supply: Supply, price: int | None, qty: int, now_ms: int, cfg: Config) -> tuple[float, bool]:
    """P' for posting qty units now at price (SPEC §3.3.2), without an own record (M6b).

    Args:
        supply: Volume and competition.
        price: Post price in copper, or None.
        qty: Units posted.
        now_ms: Now.
        cfg: Config.

    Returns:
        (P', volume_unknown).
    """
    if supply.sold_per_day is None or price is None:
        return sellprob.chance(None, None, 0, 0, 0, cfg.default_sell_prob, cfg.own_record_weight)
    competitors = None if supply.book is None else [(p, float(stack * count), band)
                                                    for p, stack, band, count in supply.book]
    ahead = sellprob.ahead_of(competitors, price, supply.cur_price, supply.cur_qty)
    demand = sellprob.demand_for(sellprob.posting_now_window(now_ms, cfg.sell_window_hours), [1.0] * 168)
    queue = sellprob.QueueInputs(supply.sold_per_day, supply.listed_per_day or 0.0, cfg.default_undercut_share,
                                 demand, qty, ahead)
    return sellprob.chance(queue, None, 0, 0, 0, cfg.default_sell_prob, cfg.own_record_weight)


def compute_item(db: Database, market: str, key: int, info: ItemInfo | None, now_ms: int, cfg: Config) -> ItemStats:
    """One item's statistics from its stored history (SPEC §3.3).

    Args:
        db: Database.
        market: Market id.
        key: Item key.
        info: Item metadata, or None when unknown.
        now_ms: Now.
        cfg: Config.

    Returns:
        The ItemStats.
    """
    start = now_ms - HISTORY_DAYS * DAY_MS
    hourly = db.hourly_closes(market, key, start, now_ms)
    daily = db.daily_closes(market, key, start, now_ms)
    cur = db.current_price(market, key)
    med7 = prices.window_median(hourly, daily, now_ms, 7)
    med30 = prices.window_median(hourly, daily, now_ms, 30)
    last30 = [c for t, c in hourly if t > now_ms - 30 * DAY_MS]
    week = range((now_ms - 7 * DAY_MS) // DAY_MS + 1, now_ms // DAY_MS + 1)
    volume = daily_volume(_volumes(db, market, key, now_ms), week)
    newest = db.newest_book(market, key, now_ms - BOOK_MAX_AGE_MS)
    book = newest[1] if newest else None
    ref = _ref_stack(book or [])
    price = prices.post_price(cur.price if cur and cur.price is not None else None, med7)
    supply = Supply(volume.sold_per_day, volume.listed_per_day, tuple(book) if book is not None else None,
                    cur.price if cur else None, cur.qty if cur else None)
    chance, unknown = _chance(supply, price, ref, now_ms, cfg)
    vendor_sell = (info.vendor_sell if info else None) or 0
    sell_value = None
    if price is not None:
        deposit = fees.deposit_copper(vendor_sell, ref, fees.shortest_duration_covering(cfg.sell_window_hours))
        sell_value = sellprob.expected_post(chance, ref, price, float(vendor_sell), deposit) / ref
    book_age = now_ms - newest[0] if newest else None
    conf = confidence.percent(confidence.score(len(last30), volume.days_by_source, book_age, None, 0))
    main_source = max(volume.days_by_source, key=lambda s: (volume.days_by_source[s], s)) \
        if volume.days_by_source else None
    return ItemStats(
        key, cur.price if cur else None, cur.qty if cur else None, now_ms - cur.at if cur else None,
        med7, med30, prices.percentile(last30, 0.1), prices.percentile(last30, 0.9),
        prices.trend7(hourly, now_ms, med7), volume.sold_per_day, volume.expired_per_day, volume.listed_per_day,
        main_source, volume.days_by_source, price, ref, chance, unknown, sell_value, conf,
        sum(stack * count for _, stack, _, count in book) if book else (cur.qty if cur else None),
        book_age, tuple(book or ()))


def _pair(stats: ItemStats | None) -> views.ChanceVolume:
    """The Chance · Volume pair for an item's posting-now stats.

    Args:
        stats: The item's stats, or None.

    Returns:
        The pair; "—" cells when there are no stats.
    """
    if stats is None:
        return views.chance_volume(None, None, None, None, None)
    return views.chance_volume(stats.chance, stats.conf, stats.sold_per_day, stats.volume_source, stats.units_on_ah)


def disenchant_page(db: Database, char_ids: list[str], stats: dict[int, ItemStats], items: dict[int, ItemInfo],
                    total_gold: int) -> dict:
    """The Disenchant page payload (SPEC §3.4A, §3.5).

    Args:
        db: Database (for the best Enchanting skill).
        char_ids: Your characters on the market.
        stats: Item key -> stats.
        items: Item metadata.
        total_gold: All your characters' gold.

    Returns:
        {"rows": [...], "hidden": 0}, profit highest first.
    """
    listings = [(key, s.cur, items[key].quality, items[key].ilvl, DE_KIND[items[key].item_class])
                for key, s in sorted(stats.items())
                if s.cur and key in items and items[key].item_class in DE_KIND and items[key].ilvl]
    sell = {key: s.sell_value for key, s in stats.items() if s.sell_value is not None}
    rows = []
    for t in disenchant.targets(listings, DE_TABLE, sell, db.best_skill(char_ids, "Enchanting")):
        info = items[t.key]
        _, outcomes = disenchant.outcomes_for(DE_TABLE, info.quality, info.ilvl, DE_KIND[info.item_class])
        mats = tuple((items[m].name if m in items else str(m), chance * (lo + hi) / 2) for m, chance, lo, hi in outcomes)
        main_mat = max(outcomes, key=lambda o: (o[1] * (o[2] + o[3]), -o[0]))[0]
        rows.append(views.disenchant_row(views.DisenchantInput(
            t.key, info.name, info.quality or 0, info.ilvl, t.buy_copper, mats, t.ev, _pair(stats.get(main_mat)),
            stats[t.key].cur_age_ms), total_gold))
    return {"rows": rows, "hidden": 0}


def _craft_market(db: Database, char_ids: list[str], stats: dict[int, ItemStats], items: dict[int, ItemInfo]
                  ) -> craft.Market:
    """Where reagents can come from: books, vendors, your stock at its hold value (SPEC §3.4B).

    Args:
        db: Database.
        char_ids: Your characters.
        stats: Item key -> stats (books and sell values).
        items: Item metadata (vendor prices).

    Returns:
        The craft Market.
    """
    books = {}
    for key, s in stats.items():
        levels: dict[int, float] = {}
        for price, stack, _, count in s.book:
            levels[price] = levels.get(price, 0.0) + stack * count
        if not levels and s.cur is not None and s.cur_qty:
            levels = {s.cur: float(s.cur_qty)}          # no book: cur × curQty (SPEC §3.4 "depth unknown")
        books[key] = sorted(levels.items())
    vendor = {k: items[k].vendor_buy for k in db.vendor_reagent_keys() if k in items and items[k].vendor_buy}
    hold = {key: s.sell_value for key, s in stats.items() if s.sell_value is not None}
    return craft.Market(books, vendor, {k: float(v) for k, v in db.owned_units(char_ids).items()}, hold)


def _product_net(stats: ItemStats | None, recipe: RecipeRow, items: dict[int, ItemInfo], now_ms: int, cfg: Config):
    """n -> expected net of n crafts' output posted now; the chance falls as more is posted (SPEC §3.4B).

    Args:
        stats: The product's stats.
        recipe: The recipe.
        items: Item metadata.
        now_ms: Now.
        cfg: Config.

    Returns:
        A function of the craft count, cached per n.
    """
    cache: dict[int, float] = {0: 0.0}
    avg_yield = (recipe.yield_min + recipe.yield_max) / 2
    vendor_sell = (items[recipe.product].vendor_sell if recipe.product in items else None) or 0

    def net(n: int) -> float:
        """Expected net of n crafts' output.

        Args:
            n: Number of crafts.

        Returns:
            Float copper; the vendor value when the product has no price.
        """
        if n not in cache:
            units = max(1, views.round_half_up(n * avg_yield))
            if stats is None or stats.post_price is None:
                cache[n] = units * float(vendor_sell)
            else:
                supply = Supply(stats.sold_per_day, stats.listed_per_day, stats.book or None, stats.cur, stats.cur_qty)
                p, _ = _chance(supply, stats.post_price, units, now_ms, cfg)
                duration = fees.shortest_duration_covering(cfg.sell_window_hours)
                cache[n] = sellprob.expected_post(p, units, stats.post_price, float(vendor_sell),
                                                  fees.deposit_copper(vendor_sell, units, duration))
        return cache[n]
    return net


def craft_page(db: Database, char_ids: list[str], stats: dict[int, ItemStats], items: dict[int, ItemInfo],
               total_gold: int, now_ms: int, cfg: Config) -> dict:
    """The Craft page payload (SPEC §3.4B, §3.5): every known recipe that makes an item.

    Args:
        db: Database.
        char_ids: Your characters.
        stats: Item key -> stats.
        items: Item metadata.
        total_gold: All your characters' gold.
        now_ms: Now.
        cfg: Config.

    Returns:
        {"rows": [...], "hidden": 0}, profit highest first (ties: spell id).
    """
    recipes = [r for r in db.known_recipes(char_ids) if r.product is not None]
    engine_recipes = {r.spell: craft.Recipe(r.spell, r.product, r.yield_min, r.yield_max, r.reagents, r.crafters,
                                            r.cooldown_s) for r in recipes}
    by_product: dict[int, list[craft.Recipe]] = {}
    for r in engine_recipes.values():
        by_product.setdefault(r.product, []).append(r)
    market = _craft_market(db, char_ids, stats, items)
    names = {k: i.name for k, i in items.items()}
    rows = []
    for r in recipes:
        option = craft.craft_option(engine_recipes[r.spell], market, by_product,
                                    _product_net(stats.get(r.product), r, items, now_ms, cfg),
                                    max_depth=cfg.max_craft_depth)
        crafter = option.crafter
        skill = db.skill_of(crafter, r.profession)
        product = items.get(r.product)
        value_each = _product_net(stats.get(r.product), r, items, now_ms, cfg)(1)
        missing = ", ".join(f"{views.round_half_up(u)} {names.get(k, k)}" for k, u in option.missing)
        rows.append((option.profit, r.spell, views.craft_row(views.CraftInput(
            r.spell, product.name if product else str(r.product), (product.quality or 1) if product else 1,
            r.profession,
            f"{crafter.split('-')[0]} ({r.profession} {skill})" if skill is not None else crafter.split("-")[0],
            option.source if option.crafts else "—", option.crafts,
            views.craft_money(option.crafts, option.mat_gold, option.owned_used, names), option.mat_gold,
            value_each, option.profit, _pair(stats.get(r.product)), "",
            f"missing: {missing}" if missing else ""), total_gold)))
    rows.sort(key=lambda row: (-row[0], row[1]))
    return {"rows": [row for _, _, row in rows], "hidden": 0}


def refresh_market(db: Database, market: str, now_ms: int, cfg: Config, result: RefreshResult) -> None:
    """Compute and stage one market's statistics and pages.

    Args:
        db: Database.
        market: Market id.
        now_ms: Now.
        cfg: Config.
        result: Collects the item count and failures.
    """
    items = db.items()
    stats: dict[int, ItemStats] = {}
    for key in db.priced_keys(market, now_ms - HISTORY_DAYS * DAY_MS):
        try:
            stats[key] = compute_item(db, market, key, items.get(key), now_ms, cfg)
        except Exception as error:                     # noqa: BLE001  one bad item never stops the refresh (FM8)
            result.failed[f"{market}:{key}"] = f"{type(error).__name__}: {error}"
            log.warning("refresh: item %s on %s failed: %s", key, market, error)
    characters = db.characters_in(market)
    char_ids = [c for c, _ in characters]
    total_gold = sum(g for _, g in characters)
    with db.transaction():
        for key, s in stats.items():
            db.stage_item_stats(market, key, asdict(s), now_ms)
        db.stage_view("disenchant", market, disenchant_page(db, char_ids, stats, items, total_gold), now_ms)
        db.stage_view("craft", market, craft_page(db, char_ids, stats, items, total_gold, now_ms, cfg), now_ms)
    result.items += len(stats)


def refresh_all(db: Database, now_ms: int, cfg: Config) -> RefreshResult:
    """The full refresh for every configured market (SPEC §3.1.1), published atomically.

    Args:
        db: Database.
        now_ms: Now.
        cfg: Config.

    Returns:
        What it did. Failed items are listed on the Status page. When writing fails
        (e.g. disk full, FM9) nothing is published, the previous numbers stay live, and
        `result.error` and a Status line say why.
    """
    result = RefreshResult(list(cfg.markets))
    try:
        db.drop_staging()
        for market in cfg.markets:
            refresh_market(db, market, now_ms, cfg, result)
        db.publish_refresh(list(cfg.markets))
    except Exception as error:                         # noqa: BLE001  keep the last good numbers (SPEC §5.2 rule 4)
        result.error = f"{type(error).__name__}: {error}"
        log.error("refresh failed: %s", result.error)
        db.drop_staging()
        with db.transaction():
            db.set_status("refresh", "error", "refresh_failed", {"error": result.error}, now_ms)
        return result
    with db.transaction():
        db.clear_status("refresh")
        if result.failed:
            db.set_status("refresh_items", "warn", "refresh_items_failed",
                          {"count": len(result.failed), "items": sorted(result.failed)[:10]}, now_ms)
        else:
            db.clear_status("refresh_items")
    return result
