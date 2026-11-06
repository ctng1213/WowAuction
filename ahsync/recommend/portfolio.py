"""The portfolio plan's inputs and storage rows: intelligence + character state in, funded plan out (SPEC §3.4D).

It replaces the plan's allocation (SPEC §3.4C.4) with the portfolio optimizer. The full refresh and
the incremental re-plan (IU13) both call `plan_portfolio`: it reads your characters (gold, stock,
recipes, professions, cost basis, rules) and the published intelligence, never history (AR3), and
returns the funded plan as actions in the formats every page, the tracker and PortfolioPlan.lua
already read, plus the alternatives (`alternative_rows`) for the `alternatives` table (M9).
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from ahsync.config import Config, flip_settings, plan_settings, portfolio_settings
from ahsync.engine import disenchant
from ahsync.engine.allocation import PortfolioPlan, portfolio_plan
from ahsync.engine.candidates import DeInfo, GenInputs, Run
from ahsync.engine.de_table import DE_TABLE
from ahsync.engine.flips import auctions
from ahsync.engine.portfolio import (CharacterGold, OpenPosition, PortfolioSettings, candidate_to_json, config_version,
                                     export_subset)
from ahsync.engine.position_views import PositionView
from ahsync.engine.sell_record import Record
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel, age_now_ms
from ahsync.recommend.pages import DE_KIND
from ahsync.recommend.plan import week_inputs
from ahsync.recommend.positions_pass import position_json
from ahsync.state.reads import StateReader


def de_infos(stats: dict[int, ItemIntel], items: dict[int, ItemInfo]) -> dict[int, DeInfo]:
    """Disenchant facts of every disenchantable item on the AH, enchanter or not (SPEC §3.4A, §3.4D.3, PF2).

    Args:
        stats: Item key -> intelligence (the item's book, the mats' sell values).
        items: Item metadata (class, quality, item level).

    Returns:
        Item key -> DeInfo, for items with a listing, an unsuspect price and a disenchant row; by key.
    """
    out = {}
    for key in sorted(stats):
        i, info = stats[key], items.get(key)
        if info is None or info.item_class not in DE_KIND or not info.ilvl or info.quality is None:
            continue
        if not i.cur or i.cur_suspect:                                                          # FM5
            continue
        row = disenchant.outcomes_for(DE_TABLE, info.quality, info.ilvl, DE_KIND[info.item_class])
        if row is None:
            continue
        values = [(stats[m].sell_value or 0.0) if m in stats else 0.0 for m, _, _, _ in row[1]]
        listed = tuple((price, stack) for price, stack, _ in auctions(i.book or None, i.cur, i.cur_qty))
        mats = tuple(sorted((m, chance * (lo + hi) / 2) for m, chance, lo, hi in row[1]))
        out[key] = DeInfo(row[0], disenchant.expected_value(row[1], values), mats, listed, i.conf,
                          main_mat_unknown(row[1], values, stats, items))
    return out


def main_mat_unknown(outcomes: tuple, values: list[float], stats: dict[int, ItemIntel],
                     items: dict[int, ItemInfo]) -> str | None:
    """Whether a disenchant's EV rests on a mat nobody knows the sales rate of (SPEC §3.4D.6, PF30).

    The green is never resold, so its own history doesn't count; the main mat's sell value is what the EV rests on.

    Args:
        outcomes: The disenchant row's (mat key, chance, min units, max units).
        values: Each outcome's sell value per unit, copper, in the same order.
        stats: Item key -> intelligence (the mats' sales rates).
        items: Item metadata (the mats' names).

    Returns:
        The main mat's name (the largest expected value, ties by key) when its sales rate is unknown, else None.
    """
    worth = [(-(chance * (lo + hi) / 2 * value), m) for (m, chance, lo, hi), value in zip(outcomes, values)]
    _, main = min(worth)
    if main in stats and stats[main].sold_per_day is not None:
        return None
    return items[main].name if main in items else str(main)


def price_ages(stats: dict[int, ItemIntel], now_ms: int, built_ms: int) -> dict[int, int | None]:
    """How old each item's newest price is now (SPEC §3.4D.6 "Forecast too stale").

    Args:
        stats: Item key -> intelligence (cur_age_ms counts back from the document's reference time; a scan after
            it is negative, IU6).
        now_ms: Now.
        built_ms: The document's reference time (intel.contract.ages_reference_ms: built_ms for a desktop-built
            document, the prices' time for a cloud one).

    Returns:
        Item key -> age ms now (intel.contract.age_now_ms); None without a price.
    """
    return {key: age_now_ms(i.cur_age_ms, now_ms, built_ms) for key, i in stats.items()}


def gen_inputs(state: StateReader, characters: list[tuple[str, int]], stats: dict[int, ItemIntel],
               items: dict[int, ItemInfo], now_ms: int, built_ms: int, cfg: Config) -> GenInputs:
    """Generation's inputs beyond the plan's world (SPEC §3.4D.3).

    Args:
        state: Character state.
        characters: (character id, gold copper) on the market, sorted by id.
        stats: Item key -> intelligence.
        items: Item metadata.
        now_ms: Now.
        built_ms: The intelligence's built_ms.
        cfg: Config ([budgets]).

    Returns:
        GenInputs: skills in Enchanting and every known recipe's profession, disenchant facts, price ages,
        names and the account-wide cost basis per unit.
    """
    char_ids = [c for c, _ in characters]
    recipes = state.known_recipes(char_ids)
    professions = sorted({"Enchanting"} | {r.profession for r in recipes})
    skills = {(c, p): s for c in char_ids for p in professions if (s := state.skill_of(c, p)) is not None}
    cost = {k: paid / bought for k, (bought, paid) in state.cost_basis().items() if bought > 0}
    return GenInputs(tuple(char_ids), {c: cfg.budgets.get(c, g) for c, g in characters}, skills,
                     {r.spell: (r.profession, 0) for r in recipes}, de_infos(stats, items),
                     price_ages(stats, now_ms, built_ms), {k: i.name for k, i in items.items()}, cost, built_ms)


def plan_portfolio(state: StateReader, characters: list[tuple[str, int]], stats: dict[int, ItemIntel],
                   items: dict[int, ItemInfo], now_ms: int, cfg: Config, records: dict[int, Record] | None = None,
                   built_ms: int | None = None, committed: Sequence[OpenPosition] = ()) -> PortfolioPlan | None:
    """The funded plan for one market's characters (SPEC §3.4D).

    Args:
        state: Character state.
        characters: (character id, gold copper) on the market, sorted by id.
        stats: Item key -> intelligence.
        items: Item metadata.
        now_ms: Now.
        cfg: Config.
        records: Item key -> your sell record (SPEC §3.3.3); None for none.
        built_ms: The intelligence's built_ms; now when None.
        committed: The committed capital: the open positions' cost left (engine.positions.committed, §3.4D.1).

    Returns:
        The PortfolioPlan; None without characters.

    Raises:
        engine.selection.SelectionError: The allocation broke a D.5 rule (nothing is published).
    """
    if not characters:
        return None
    w = week_inputs(state, characters, stats, items, cfg, records)
    gi = gen_inputs(state, characters, stats, items, now_ms, now_ms if built_ms is None else built_ms, cfg)
    chars = [CharacterGold(c, g, cfg.budgets.get(c)) for c, g in characters]
    run = Run(now_ms, plan_settings(cfg), flip_settings(cfg), portfolio_settings(cfg), cfg.max_craft_depth)
    return portfolio_plan(w, gi, chars, committed, run)


def export_rows(plan: PortfolioPlan | None, limit: int) -> list[tuple[str, str, float, str]]:
    """The candidates the addon file exports, in export order (SPEC §3.4D.3, §3.5 `K`, EX17).

    Args:
        plan: The run's plan, or None.
        limit: Config candidate_export.

    Returns:
        (cand_id, status = "" funded or the D.6 status, adjusted profit float copper, candidate JSON): the funded
        in funding order, then the best alternatives by adjusted profit (portfolio.export_subset), `limit` in all.
    """
    if plan is None:
        return []
    alternatives = {a.candidate.cand_id: a for a in plan.alternatives}
    adjusted = {i: a.adjusted for i, a in alternatives.items()}
    funded = dict(zip(plan.selection.funded, plan.funded))
    picked = export_subset([*plan.funded, *(a.candidate for a in plan.alternatives)], plan.selection.funded,
                           adjusted, limit)
    funded_adjusted = dict(plan.funded_adjusted)
    return [(c.cand_id, "" if c.cand_id in funded else alternatives[c.cand_id].reason,
             funded_adjusted[c.cand_id] if c.cand_id in funded else adjusted[c.cand_id], candidate_to_json(c))
            for c in picked]


def snapshot_data(plan: PortfolioPlan, ps: PortfolioSettings, positions: Sequence[PositionView], now_ms: int) -> dict:
    """The portfolio snapshot PortfolioPlan.lua carries, as stored between runs (SPEC §3.5 `portfolio`, EX18).

    Args:
        plan: The run's plan.
        ps: The portfolio settings it ran with.
        positions: The market's evaluated positions and found stock (recommend.positions_pass.views).
        now_ms: The run's time.

    Returns:
        JSON-serializable: snapshot_ms, the settings the addon scores with (risk tolerance and premiums in whole
        thousandths), config_version, capital_base, committed, capacity (the remaining capacity), allocated (gold
        needed of the funded rows), reserved (character -> copper) and positions (positions_pass.position_json rows:
        position id, character, key, state, units, units left, cost left, net now float copper or None, window ms
        or None, next action).
    """
    a, sel = plan.accounting, plan.selection
    return {
        "snapshot_ms": now_ms,
        "settings": [math.floor(ps.risk_tolerance * 1000 + 0.5), math.floor(ps.premium_base * 1000 + 0.5),
                     math.floor(ps.premium_per_day * 1000 + 0.5), ps.immediate_hours, ps.today_limit, ps.week_limit,
                     ps.horizon_days],
        "config_version": config_version(ps),
        "capital_base": a.capital_base, "committed": a.committed_capital, "capacity": a.remaining_capacity,
        "allocated": sel.gold, "reserved": {c: n for c, n in sel.reserved if n},
        "positions": [position_json(v) for v in positions],
    }


def portfolio_export(plan: PortfolioPlan | None, cfg: Config, now_ms: int,
                     positions: Sequence[PositionView]) -> tuple[list[tuple[str, str, float, str]], dict | None]:
    """What the addon files carry of one market's plan run, for RecommendStore.stage_export (SPEC §3.5, M11).

    Args:
        plan: The run's plan, or None without characters.
        cfg: Config (candidate_export and the portfolio settings).
        now_ms: The run's time.
        positions: The market's evaluated positions and found stock.

    Returns:
        (export_rows, snapshot_data); ([], None) without a plan.
    """
    if plan is None:
        return [], None
    return export_rows(plan, cfg.candidate_export), snapshot_data(plan, portfolio_settings(cfg), positions, now_ms)


def alternative_rows(plan: PortfolioPlan | None) -> list[tuple[str, str, str, str, str]]:
    """The unfunded candidates as `alternatives` rows (SPEC §3.4D, M9).

    Args:
        plan: The run's plan, or None.

    Returns:
        (cand_id, char_id, list, reason, candidate JSON), by candidate id.
    """
    if plan is None:
        return []
    return [(a.candidate.cand_id, a.candidate.char_id, a.lst, a.reason, candidate_to_json(a.candidate))
            for a in plan.alternatives]
