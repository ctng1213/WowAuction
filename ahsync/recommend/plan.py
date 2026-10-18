"""The weekly plan's inputs and storage rows: intelligence + character state in, plan out (SPEC §3.4C, §5.0).

Builds engine.week's inputs from the published market intelligence and your
characters (stock, recipes, budgets, rules), never from history (AR3), and turns
the planned actions into `actions` rows. Facts are built only for items the plan
can use: your stock, your recipes' products and mats, and flip candidates.
"""
from __future__ import annotations

from ahsync.config import Config, flip_settings, plan_settings
from ahsync.engine import craft, disenchant, fees, profile
from ahsync.engine.capacity import slot_of
from ahsync.engine.craft_plan import CraftWorld
from ahsync.engine.de_table import DE_TABLE
from ahsync.engine.plan import TREND_CLAMP_PCT, Lot, PlanAction, PlanWindow, SellFacts
from ahsync.engine.week import WeekInputs, week_plan
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel, price_level
from ahsync.recommend.pages import DE_KIND, craft_market
from ahsync.state.reads import StateReader

HOUR_MS = 3_600_000
FLAT = tuple([1.0] * 168)
GEAR_CLASSES = (2, 4)            # weapon, armor: the lenient gear cap (SPEC §3.4 Liquidity)


def _pattern(values: str, weeks: str, empty: tuple[float, ...]) -> tuple[float, ...]:
    """A packed hour-of-week profile as 168 ratios.

    Args:
        values: Packed thousandths (intelligence contract), "" for none.
        weeks: Packed week counts.
        empty: What "no data" means for this profile.

    Returns:
        The ratios.
    """
    return tuple(profile.unpack(values, weeks).values) if values else empty


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


def sell_facts(i: ItemIntel, info: ItemInfo | None, de: tuple[float, str] | None) -> SellFacts:
    """What the plan knows about one item, from its intelligence (SPEC §3.4C).

    Args:
        i: The item's intelligence.
        info: Its metadata (vendor price, class), or None.
        de: (EV per unit, enchanter) when someone can disenchant it.

    Returns:
        The SellFacts; cost basis 0 until tracking records it (M6b).
    """
    return SellFacts(
        i.key, price_level(i), i.trend7, i.post_price, i.sold_per_day, i.listed_per_day, i.ref_stack,
        _pattern(i.how, i.how_weeks, FLAT), _pattern(i.sales_how, i.sales_weeks, FLAT),
        tuple(PlanWindow(w.start_slot, w.hours, w.best_slot) for w in i.sell_windows), i.book or None, i.cur,
        i.cur_qty, (info.vendor_sell if info else None) or 0, de[0] if de else None, de[1] if de else None, 0.0,
        i.units_on_ah, _pattern(i.supply_how, i.supply_weeks, ()),
        bool(info and info.item_class in GEAR_CLASSES), i.conf)


def flip_candidate(i: ItemIntel, min_flip_margin: float) -> bool:
    """Whether an item could flip at all: its best window could beat its cheapest auction by the margin.

    A cheap filter so the flip search runs on a few items, not the whole market.

    Args:
        i: The item's intelligence.
        min_flip_margin: Config min_flip_margin.

    Returns:
        True when the price level × the best peak hour's price ratio × the most a week of trend adds,
        after the cut, ≥ (1 + margin) × cur (SPEC §3.4C.1, R21).
    """
    level = price_level(i)
    if not (i.cur and level and i.sell_windows):
        return False
    how = _pattern(i.how, i.how_weeks, FLAT)
    best = max(how[w.best_slot] for w in i.sell_windows)
    trend = 1 + max(0.0, min(TREND_CLAMP_PCT, i.trend7)) / 100 * 7
    return level * best * trend * (1 - fees.AH_CUT) >= (1 + min_flip_margin) * i.cur


def week_inputs(state: StateReader, characters: list[tuple[str, int]], stats: dict[int, ItemIntel],
                items: dict[int, ItemInfo], cfg: Config) -> WeekInputs:
    """The weekly plan's inputs for one market (SPEC §3.4C).

    Args:
        state: Character state.
        characters: (character id, gold copper) on the market, sorted by id.
        stats: Item key -> intelligence.
        items: Item metadata.
        cfg: Config (budgets, margin).

    Returns:
        WeekInputs; budgets are each character's gold unless config [budgets] overrides it.
    """
    gold = dict(characters)
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
    wanted |= {k for k, i in stats.items() if flip_candidate(i, cfg.min_flip_margin)}
    facts = {k: sell_facts(stats[k], items.get(k), de_offer(items.get(k), stats, enchanters))
             for k in sorted(wanted) if k in stats}
    world = CraftWorld(recipes, by_product, craft_market(state, char_ids, stats, items),
                       {k: f for k, f in facts.items() if k in {r.product for r in recipes}})
    rules = state.item_rules()
    return WeekInputs(lots, facts, world, {c: cfg.budgets.get(c, g) for c, g in gold.items()}, sum(gold.values()),
                      frozenset(k for k, r in rules.items() if r in ("no_buy", "ignore")),
                      frozenset(k for k, r in rules.items() if r == "ignore"))


def week_start_ms(now_ms: int) -> int:
    """Monday 00:00 UTC of the plan week (SPEC §3.4.0).

    Args:
        now_ms: Now.

    Returns:
        ms.
    """
    hour = now_ms // HOUR_MS
    return (hour - slot_of(hour)) * HOUR_MS


def plan_rows(actions: list[PlanAction], market: str, now_ms: int) -> list[tuple]:
    """Planned actions as `actions` rows, status pending (SPEC §3.2 actions, §3.4.1).

    Ids are "<market>:<run ms>:<n>", unique per run. A chain's later steps name its first
    action as their parent, so a done buy or craft keeps its follow-up (§3.4.1). `conf` holds
    the action's planned chance to sell (posts), the P' its expected net assumed.

    Args:
        actions: From plan_market, in plan order.
        market: Market id.
        now_ms: The run time.

    Returns:
        Rows in `actions` column order.
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
                     a.peak_ms, a.source))
    return rows


def plan_market(state: StateReader, characters: list[tuple[str, int]], stats: dict[int, ItemIntel],
                items: dict[int, ItemInfo], now_ms: int, cfg: Config) -> list[PlanAction]:
    """The weekly plan for one market's characters (SPEC §3.4C).

    Args:
        state: Character state.
        characters: (character id, gold copper) on the market, sorted by id.
        stats: Item key -> intelligence.
        items: Item metadata.
        now_ms: Now.
        cfg: Config.

    Returns:
        The planned actions; empty without characters.
    """
    if not characters:
        return []
    return week_plan(week_inputs(state, characters, stats, items, cfg), now_ms, plan_settings(cfg),
                     flip_settings(cfg), cfg.max_craft_depth)
