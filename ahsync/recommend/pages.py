"""Desktop recommendations: Disenchant and Craft options and pages (SPEC §3.4A-B, §3.5).

Built from market intelligence plus character state only: they never read history
(AR3). M3 limits, refined in M4: hold value = posting-now sell value, the unsold
fallback is the vendor price only.
"""
from __future__ import annotations

from ahsync.config import Config, market_settings
from ahsync.engine import capacity, craft, disenchant, fees, profile, sellprob, views
from ahsync.engine.de_table import DE_TABLE
from ahsync.engine.sellprob import Supply
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel
from ahsync.state.reads import RecipeRow, StateReader

DE_KIND = {2: "weapon", 4: "armor"}     # WoW item class -> DE table kind
HOUR_MS = 3_600_000


def _market(stats: ItemIntel | None) -> views.MarketActivity:
    """The market activity cells for an item's posting-now intelligence (SPEC §3.5).

    Args:
        stats: The item's intelligence, or None.

    Returns:
        The cells; "—" everywhere when there is no intelligence.
    """
    if stats is None:
        return views.market_activity(None, None, None, None, None)
    return views.market_activity(stats.chance, stats.conf, stats.sold_per_day, stats.volume_source, stats.units_on_ah)


def _mat_caps(mat_keys: set[int], stats: dict[int, ItemIntel], now_ms: int, cfg: Config) -> dict[int, float]:
    """Each mat's liquidity cap for the posting-now window: a share of others' listings (SPEC §3.4 Liquidity).

    Args:
        mat_keys: Mats the disenchant buys would yield.
        stats: Item key -> intelligence (listed supply and its pattern, reference stack).
        now_ms: Now; the window is the next sell_window_hours.
        cfg: Config (sell_window_hours, max_supply_share).

    Returns:
        Mat key -> cap in units; one stack of 1 for a mat with no intelligence.
    """
    hours = sellprob.posting_now_window(now_ms, cfg.sell_window_hours)
    caps = {}
    for key in sorted(mat_keys):
        s = stats.get(key)
        if s is None:
            caps[key] = 1
            continue
        pattern = profile.unpack(s.supply_how, s.supply_weeks).values if s.supply_how else ()
        listed = capacity.window_supply(s.units_on_ah, pattern, now_ms // HOUR_MS, hours)
        caps[key] = capacity.liquidity_cap(listed, cfg.max_supply_share, s.ref_stack)
    return caps


def disenchant_page(state: StateReader, char_ids: list[str], stats: dict[int, ItemIntel], items: dict[int, ItemInfo],
                    total_gold: int, now_ms: int, cfg: Config) -> dict:
    """The Disenchant page payload (SPEC §3.4A, §3.5).

    The mats of all the buys together stay within each mat's cap: buys are taken best
    profit first until a mat's cap is reached (SPEC §3.4 market capacity, R15). A buy that
    would risk more than max_risk_pct of all your gold is left out (SPEC §3.4 Risk).

    Args:
        state: Character state (for the best Enchanting skill).
        char_ids: Your characters on the market.
        stats: Item key -> stats.
        items: Item metadata.
        total_gold: All your characters' gold.
        now_ms: Now (the mats' posting-now window).
        cfg: Config (the liquidity cap's window and supply share).

    Returns:
        {"rows": [...], "hidden": buys left out for capacity or risk, "help": header -> tooltip}, profit highest first.
    """
    listings = [(key, s.cur, items[key].quality, items[key].ilvl, DE_KIND[items[key].item_class])
                for key, s in sorted(stats.items())
                if s.cur and not s.cur_suspect and key in items and items[key].item_class in DE_KIND
                and items[key].ilvl]                                                   # FM5
    sell = {key: s.sell_value for key, s in stats.items() if s.sell_value is not None}
    found, too_risky = [], 0
    risk_limit = total_gold * cfg.max_risk_pct / 100
    for t in disenchant.targets(listings, DE_TABLE, sell, state.best_skill(char_ids, "Enchanting")):
        if t.buy_copper > risk_limit:
            too_risky += 1
            continue
        info = items[t.key]
        _, outcomes = disenchant.outcomes_for(DE_TABLE, info.quality, info.ilvl, DE_KIND[info.item_class])
        found.append((t, info, outcomes, {m: chance * (lo + hi) / 2 for m, chance, lo, hi in outcomes}))
    caps = _mat_caps({m for *_, units in found for m in units}, stats, now_ms, cfg)
    taken, dropped = capacity.take_within_caps([capacity.Candidate(t.key, t.ev - t.buy_copper, units)
                                                for t, _, _, units in found], caps)
    rows = []
    for t, info, outcomes, _ in found:
        if t.key not in taken:
            continue
        mats = tuple((items[m].name if m in items else str(m), chance * (lo + hi) / 2) for m, chance, lo, hi in outcomes)
        main_mat = max(outcomes, key=lambda o: (o[1] * (o[2] + o[3]), -o[0]))[0]
        rows.append(views.disenchant_row(views.DisenchantInput(
            t.key, info.name, info.quality or 0, info.ilvl, t.buy_copper, mats, t.ev, _market(stats.get(main_mat)),
            stats[t.key].cur_age_ms), total_gold))
    return {"rows": rows, "hidden": len(dropped) + too_risky, "help": views.COLUMN_HELP}


def craft_market(state: StateReader, char_ids: list[str], stats: dict[int, ItemIntel], items: dict[int, ItemInfo]
                  ) -> craft.Market:
    """Where reagents can come from: books, vendors, your stock at its hold value (SPEC §3.4B).

    Args:
        state: Character state.
        char_ids: Your characters.
        stats: Item key -> stats (books and sell values).
        items: Item metadata (vendor prices).

    Returns:
        The craft Market, for the mats of the characters' known recipes only: a craft never looks at
        another item's book, and copying all ~15k books per craft tried made a re-plan seconds slower.
    """
    mats = {key for r in state.known_recipes(char_ids) for key, _ in r.reagents}
    books = {}
    for key, s in ((k, stats[k]) for k in sorted(mats) if k in stats and not stats[k].cur_suspect):   # FM5
        levels: dict[int, float] = {}
        for price, stack, _, count in s.book:
            levels[price] = levels.get(price, 0.0) + stack * count
        if not levels and s.cur is not None and s.cur_qty:
            levels = {s.cur: float(s.cur_qty)}          # no book: cur × curQty (SPEC §3.4 "depth unknown")
        books[key] = sorted(levels.items())
    vendor = {k: items[k].vendor_buy for k in state.vendor_reagent_keys() if k in items and items[k].vendor_buy}
    hold = {key: stats[key].sell_value for key in sorted(mats) if key in stats and stats[key].sell_value is not None}
    return craft.vendor_first(craft.Market(books, vendor, {k: float(v) for k, v in state.owned_units(char_ids).items()},
                                           hold))                                       # R22


def _product_net(stats: ItemIntel | None, recipe: RecipeRow, items: dict[int, ItemInfo], now_ms: int, cfg: Config):
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
                p, _ = sellprob.posting_chance(supply, stats.post_price, units, now_ms, market_settings(cfg))
                duration = fees.shortest_duration_covering(cfg.sell_window_hours)
                cache[n] = sellprob.expected_post(p, units, stats.post_price, float(vendor_sell),
                                                  fees.deposit_copper(vendor_sell, units, duration))
        return cache[n]
    return net


def craft_page(state: StateReader, char_ids: list[str], stats: dict[int, ItemIntel], items: dict[int, ItemInfo],
               total_gold: int, now_ms: int, cfg: Config) -> dict:
    """The Craft page payload (SPEC §3.4B, §3.5): every known recipe that makes an item.

    Args:
        state: Character state.
        char_ids: Your characters.
        stats: Item key -> stats.
        items: Item metadata.
        total_gold: All your characters' gold.
        now_ms: Now.
        cfg: Config.

    Returns:
        {"rows": [...], "hidden": 0, "help": header -> tooltip}, profit highest first (ties: spell id).
    """
    recipes = [r for r in state.known_recipes(char_ids) if r.product is not None]
    engine_recipes = {r.spell: craft.Recipe(r.spell, r.product, r.yield_min, r.yield_max, r.reagents, r.crafters,
                                            r.cooldown_s) for r in recipes}
    by_product: dict[int, list[craft.Recipe]] = {}
    for r in engine_recipes.values():
        by_product.setdefault(r.product, []).append(r)
    market = craft_market(state, char_ids, stats, items)
    names = {k: i.name for k, i in items.items()}
    rows = []
    for r in recipes:
        option = craft.craft_option(engine_recipes[r.spell], market, by_product,
                                    _product_net(stats.get(r.product), r, items, now_ms, cfg),
                                    max_depth=cfg.max_craft_depth)
        crafter = option.crafter
        skill = state.skill_of(crafter, r.profession)
        product = items.get(r.product)
        value_each = _product_net(stats.get(r.product), r, items, now_ms, cfg)(1)
        missing = ", ".join(f"{views.round_half_up(u)} {names.get(k, k)}" for k, u in option.missing)
        rows.append((option.profit, r.spell, views.craft_row(views.CraftInput(
            r.spell, product.name if product else str(r.product), (product.quality or 1) if product else 1,
            r.profession,
            f"{crafter.split('-')[0]} ({r.profession} {skill})" if skill is not None else crafter.split("-")[0],
            option.source if option.crafts else "—", option.crafts,
            views.craft_money(option.crafts, option.mat_gold, option.owned_used, names), option.mat_gold,
            value_each, option.profit, _market(stats.get(r.product)), "",
            f"missing: {missing}" if missing else ""), total_gold)))
    rows.sort(key=lambda row: (-row[0], row[1]))
    return {"rows": [row for _, _, row in rows], "hidden": 0, "help": views.COLUMN_HELP}
