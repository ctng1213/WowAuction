"""The weekly plan's inputs and storage rows: intelligence + character state in, plan out (SPEC §3.4C, §5.0).

Builds engine.week's inputs from the published market intelligence and your
characters (stock, recipes, budgets, rules), never from history (AR3), and turns
the planned actions into `actions` rows. Facts are built only for items the plan
can use: your stock, your recipes' products and mats, and flip candidates.
"""
from __future__ import annotations

from collections.abc import Sequence

from ahsync.config import Config, record_settings
from ahsync.db.sqlite import Database
from ahsync.engine import capacity, craft, disenchant, fees, profile
from ahsync.engine.capacity import slot_of
from ahsync.engine.craft_plan import CraftWorld
from ahsync.engine.flips import flip_offer
from ahsync.engine.de_table import DE_TABLE
from ahsync.engine.plan import TREND_CLAMP_PCT, Lot, PlanAction, PlanWindow, SellFacts
from ahsync.engine.prices import MIN_HOURLY_CLOSES
from ahsync.engine.sell_record import Record, ResolvedPost, sell_record
from ahsync.engine.views import round_half_up
from ahsync.engine.week import WeekInputs
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel, others_supply, price_level
from ahsync.recommend.listings import own_listed
from ahsync.recommend.pages import DE_KIND, craft_market
from ahsync.state.reads import StateReader

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
FLAT = tuple([1.0] * 168)
GEAR_CLASSES = capacity.GEAR_CLASSES
DE_MATS = frozenset(m for *_, outcomes in DE_TABLE for m, _, _, _ in outcomes)   # what disenchanting yields


def _pattern(values: str, weeks: str, empty: tuple[float, ...]) -> tuple[float, ...]:
    """A packed hour-of-week profile as 168 ratios.

    Args:
        values: Packed thousandths (intelligence contract), "" for none.
        weeks: Packed week counts (not read: the plan uses the ratios only).
        empty: What "no data" means for this profile.

    Returns:
        The ratios.
    """
    return profile.unpack_values(values) or empty


def de_offer(info: ItemInfo | None, stats: dict[int, ItemIntel], enchanters: list[tuple[str, int]]
             ) -> tuple[float, str] | None:
    """What disenchanting one unit is worth, and who does it (SPEC §3.4A, R7).

    Args:
        info: Item metadata.
        stats: Item key -> intelligence (the mats' sell values).
        enchanters: (character id, Enchanting skill), best first.

    Returns:
        (EV per unit, the best enchanter who can), or None when nobody can or it can't be disenchanted.
    """
    if info is None or info.item_class not in DE_KIND or not info.ilvl or info.quality is None:
        return None
    row = disenchant.outcomes_for(DE_TABLE, info.quality, info.ilvl, DE_KIND[info.item_class])
    able = [char for char, skill in enchanters if row and disenchant.can_disenchant(row[0], skill)]
    if not able:
        return None
    values = [(stats[m].sell_value or 0.0) if m in stats else 0.0 for m, _, _, _ in row[1]]
    return disenchant.expected_value(row[1], values), able[0]


def sell_records(db: Database, char_ids: list[str], market: str, now_ms: int, cfg: Config) -> dict[int, Record]:
    """Your sell record of every item you posted lately on a market (SPEC §3.3.3).

    Args:
        db: Database (posts, Keep rules, hourly closes).
        char_ids: The market's characters.
        market: Market id, for the closes during each post.
        now_ms: Now.
        cfg: Config.

    Returns:
        Item key -> Record, for items with a sold or expired post that counts.
    """
    s = record_settings(cfg)
    since = now_ms - s.days * DAY_MS
    posts: dict[int, list[ResolvedPost]] = {}
    for key, post_id, price, at, resolved, outcome, stack, sold_units in db.post_outcomes(char_ids, since):
        units = sold_units if outcome != "sold" else stack             # O19: a sold post sold its stack
        posts.setdefault(key, []).append(ResolvedPost(post_id, price, at, resolved, outcome, stack or 1, units))
    keeps = db.keep_times()
    out = {}
    for key, rows in sorted(posts.items()):
        closes = db.hourly_closes(market, key, min(p.at_ms for p in rows), max(p.resolved_at_ms for p in rows))
        record = sell_record(rows, closes, now_ms, keeps.get(key), s)
        if record is not None:
            out[key] = record
    return out


def sell_facts(i: ItemIntel, info: ItemInfo | None, de: tuple[float, str] | None,
               record: Record | None = None, own: tuple[tuple[int, int | None], ...] = ()) -> SellFacts:
    """What the plan knows about one item, from its intelligence (SPEC §3.4C).

    Args:
        i: The item's intelligence.
        info: Its metadata (vendor price, class), or None.
        de: (EV per unit, enchanter) when someone can disenchant it.
        record: Your sell record of it (SPEC §3.3.3), or None.
        own: Your auctions of it listed now, (units, end ms or None), as own_listed returns them (R31).

    Returns:
        The SellFacts; cost basis 0 until tracking records it (M6b). Others' supply is the listed supply less your
        own units (a book names no seller, so it counts yours), never below 0; the 30-day median, when nothing is
        listed now, is taken as it is.
    """
    supply = others_supply(i, sum(units for units, _ in own))                              # V23, R31
    return SellFacts(
        i.key, price_level(i), i.trend7, i.post_price, i.sold_per_day, i.listed_per_day, i.ref_stack,
        _pattern(i.how, i.how_weeks, FLAT), _pattern(i.sales_how, i.sales_weeks, FLAT),
        tuple(PlanWindow(w.start_slot, w.hours, w.best_slot, w.weeks) for w in i.sell_windows), i.book or None, i.cur,
        i.cur_qty, (info.vendor_sell if info else None) or 0, de[0] if de else None, de[1] if de else None, 0.0,
        supply, _pattern(i.supply_how, i.supply_weeks, ()),
        bool(info and info.item_class in GEAR_CLASSES), i.conf, record, i.med7, i.p90_30, i.p10_30,
        tuple(PlanWindow(w.start_slot, w.hours, w.best_slot, w.weeks) for w in i.buy_windows), i.closes_30d,
        i.undercut_share, bool(i.commodity), own)


def window_candidate(i: ItemIntel, min_flip_margin: float, max_trend_total: float = 0.15) -> bool:
    """Whether an item's best window could beat its cheapest auction by the margin (SPEC §3.4C.2).

    Args:
        i: The item's intelligence.
        min_flip_margin: Config min_flip_margin.
        max_trend_total: Config max_trend_total: the most the trend may add (slot_price, TT7).

    Returns:
        True when the price level × the best peak hour's price ratio × the most a week of trend adds
        (at most 1 + max_trend_total), after the cut, ≥ (1 + margin) × cur (SPEC §3.4C.1, R21); never for a
        suspect cur (FM5).
    """
    level = price_level(i)
    if not (i.cur and level and i.sell_windows) or i.cur_suspect:
        return False
    slots = profile.expand(i.how) if i.how else None        # only the windows' best hours are read (§3.1.2 re-plan)
    best = max((int(slots[w.best_slot]) / profile.PACK_SCALE if slots else 1.0) for w in i.sell_windows)
    trend = 1 + min(max_trend_total, max(0.0, min(TREND_CLAMP_PCT, i.trend7)) / 100 * 7)
    peak = level * best * trend
    thin = i.closes_30d is not None and i.closes_30d < MIN_HOURLY_CLOSES          # slot_price's ceiling (TT22, TT23)
    ceiling = [level] if thin else [x for x in (i.med7, i.p90_30) if x is not None]
    peak = min(peak, max(ceiling)) if ceiling else peak
    return peak * (1 - fees.AH_CUT) >= (1 + min_flip_margin) * i.cur


def now_candidate(i: ItemIntel, min_flip_margin: float) -> bool:
    """Whether relisting now just under the book's second price level could beat its cheapest auction (R24, R25).

    Args:
        i: The item's intelligence (its book, cur and posting-now chance).
        min_flip_margin: Config min_flip_margin.

    Returns:
        True when (p2 − 1) × (1 − cut) × chance ≥ (1 + margin) × cur, p2 being the book's second-lowest price;
        never without two price levels or for a suspect cur (FM5).
    """
    levels = sorted({price for price, _, _, _ in i.book})
    if not i.cur or i.cur_suspect or len(levels) < 2:
        return False
    return (levels[1] - 1) * (1 - fees.AH_CUT) * i.chance >= (1 + min_flip_margin) * i.cur


def flip_candidate(i: ItemIntel, min_flip_margin: float, max_trend_total: float = 0.15) -> bool:
    """Whether an item could flip at all: a cheap filter so the flip search runs on a few items, not the market.

    Args:
        i: The item's intelligence.
        min_flip_margin: Config min_flip_margin.
        max_trend_total: Config max_trend_total.

    Returns:
        True when its best window (window_candidate) or relisting now (now_candidate) could pay the margin.
    """
    return window_candidate(i, min_flip_margin, max_trend_total) or now_candidate(i, min_flip_margin)


def flip_offers(stats: dict[int, ItemIntel], items: dict[int, ItemInfo], vendor_sold: set[int], now_ms: int,
                cfg: Config) -> dict[int, str]:
    """Market-wide flip offers for the addon's own one-stack flips (SPEC §4.3, EX11).

    Args:
        stats: Item key -> intelligence.
        items: Item metadata (what a merchant pays).
        vendor_sold: Items a vendor sells in unlimited supply (never flipped, R22).
        now_ms: Now.
        cfg: Config (min_flip_margin, sell_window_hours).

    Returns:
        Item key -> "expected per unit,window price,best slot" for flip candidates, by key; none for an item with
        fewer than MIN_HOURLY_CLOSES hourly closes in 30 days, which has no later window to sell in (R28, R29).
    """
    out = {}
    duration = fees.shortest_duration_covering(cfg.sell_window_hours)
    for key in sorted(stats):
        i = stats[key]
        thin = i.closes_30d is not None and i.closes_30d < MIN_HOURLY_CLOSES      # no later window, so no flip (R28)
        if (key in vendor_sold or i.cur_suspect or thin
                or not window_candidate(i, cfg.min_flip_margin, cfg.max_trend_total)):
            continue
        how = _pattern(i.how, i.how_weeks, FLAT)
        best = max(i.sell_windows, key=lambda w: (how[w.best_slot], -w.best_slot)).best_slot
        days = ((best - slot_of(now_ms // HOUR_MS)) % 168 * HOUR_MS - now_ms % HOUR_MS) / DAY_MS
        vendor_sell = (items[key].vendor_sell if key in items else None) or 0
        expected, price = flip_offer(price_level(i), how[best], i.trend7, max(0.0, days), i.chance, float(vendor_sell),
                                     fees.deposit_copper(vendor_sell, i.ref_stack, duration), i.ref_stack,
                                     cfg.max_trend_total, i.p10_30, i.med7, i.p90_30, i.closes_30d)
        if expected > 0:
            out[key] = f"{round_half_up(expected)},{price},{best}"
    return out


def week_inputs(state: StateReader, characters: list[tuple[str, int]], stats: dict[int, ItemIntel],
                items: dict[int, ItemInfo], cfg: Config, records: dict[int, Record] | None = None,
                now_ms: int | None = None) -> WeekInputs:
    """The weekly plan's inputs for one market (SPEC §3.4C, §3.4D.3).

    Facts are built only for items the plan can use: your stock, your recipes' products and mats, flip
    candidates and the mats disenchanting yields (their capacity, SPEC §3.4 market capacity).

    Args:
        state: Character state.
        characters: (character id, gold copper) on the market, sorted by id.
        stats: Item key -> intelligence.
        items: Item metadata.
        cfg: Config (margin, trend clamp).
        records: Item key -> your sell record (SPEC §3.3.3); None for none.
        now_ms: Now: your auctions that ended before it don't count against a cap (R31); None counts them all.

    Returns:
        WeekInputs (gold is the portfolio's accounting, SPEC §3.4D.1).
    """
    char_ids = [c for c, _ in characters]
    lots = tuple(Lot(c, k, q) for c, k, q in state.owned_lots(char_ids))
    recipes = tuple(craft.Recipe(r.spell, r.product, r.yield_min, r.yield_max, r.reagents, r.crafters, r.cooldown_s)
                    for r in state.known_recipes(char_ids) if r.product is not None)
    by_product: dict[int, list[craft.Recipe]] = {}
    for r in recipes:
        by_product.setdefault(r.product, []).append(r)
    enchanters = sorted(((c, state.skill_of(c, "Enchanting")) for c in char_ids), key=lambda e: (-(e[1] or -1), e[0]))
    enchanters = [(c, s) for c, s in enchanters if s is not None]
    wanted = {lot.key for lot in lots} | {r.product for r in recipes} | {k for r in recipes for k, _ in r.reagents}
    wanted |= {k for k, i in stats.items() if flip_candidate(i, cfg.min_flip_margin, cfg.max_trend_total)}
    wanted |= DE_MATS
    mine = own_listed(state, char_ids, now_ms)                                                  # R31
    facts = {k: sell_facts(stats[k], items.get(k), de_offer(items.get(k), stats, enchanters), (records or {}).get(k),
                           mine.get(k, ())) for k in sorted(wanted) if k in stats}
    world = CraftWorld(recipes, by_product, craft_market(state, char_ids, stats, items),
                       {k: f for k, f in facts.items() if k in {r.product for r in recipes}})
    rules = state.item_rules()
    return WeekInputs(lots, facts, world, frozenset(k for k, r in rules.items() if r in ("no_buy", "ignore")),
                      frozenset(k for k, r in rules.items() if r == "ignore"),
                      frozenset(facts_suspect(stats, facts)))


def facts_suspect(stats: dict[int, ItemIntel], facts: dict) -> list[int]:
    """The planned items whose lowest price is suspect (FM5).

    Args:
        stats: Item key -> intelligence.
        facts: The plan's items.

    Returns:
        Their keys, ascending.
    """
    return [k for k in sorted(facts) if stats[k].cur_suspect]


def week_start_ms(now_ms: int) -> int:
    """Monday 00:00 UTC of the plan week (SPEC §3.4.0).

    Args:
        now_ms: Now.

    Returns:
        ms.
    """
    hour = now_ms // HOUR_MS
    return (hour - slot_of(hour)) * HOUR_MS


def plan_rows(actions: list[PlanAction], market: str, now_ms: int,
              cand_ids: Sequence[str | None] = ()) -> list[tuple]:
    """Planned actions as `actions` rows, status pending (SPEC §3.2 actions, §3.4.1).

    Ids are "<market>:<run ms>:<n>", unique per run. A chain's later steps name its first
    action as their parent, so a done buy or craft keeps its follow-up (§3.4.1). `conf` holds
    the action's planned chance to sell (posts), the P' its expected net assumed. `cand_id` names the funded
    candidate whose opportunity made the action, so a matched buy or craft opens a position (§3.4D.9).

    Args:
        actions: The plan's actions (recommend.portfolio.plan_portfolio), in plan order.
        market: Market id.
        now_ms: The run time.
        cand_ids: Each action's funded candidate id, aligned with actions (PortfolioPlan.action_cands); missing or
            None for your stock's rest.

    Returns:
        Rows in `actions` column order, cand_id last.
    """
    rows, first_of_chain = [], {}
    week = week_start_ms(now_ms)
    for n, a in enumerate(actions):
        action_id = f"{market}:{now_ms}:{n}"
        parent = first_of_chain.get(a.chain) if a.chain else None
        if a.chain and parent is None:
            first_of_chain[a.chain] = action_id
        rows.append((action_id, now_ms, a.at_ms, a.char_id, a.kind, a.key, a.qty, a.price, a.net, a.p, "pending",
                     0, None, parent, a.window_start_ms, a.window_end_ms,
                     a.net if a.kind == "post" else None, a.window_start_ms if a.kind == "post" else None, None, week,
                     a.peak_ms, a.source, a.duration_h if a.kind == "post" else None, a.to_char,
                     cand_ids[n] if n < len(cand_ids) else None))
    return rows
