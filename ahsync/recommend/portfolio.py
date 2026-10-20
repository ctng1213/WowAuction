"""The portfolio plan's inputs and storage rows: intelligence + character state in, funded plan out (SPEC §3.4D).

It replaces the plan's allocation (SPEC §3.4C.4) with the portfolio optimizer. The full refresh and
the incremental re-plan (IU13) both call `plan_portfolio`: it reads your characters (gold, stock,
recipes, professions, cost basis, rules) and the published intelligence, never history (AR3), and
returns the funded plan as actions in the formats every page, the tracker and PortfolioPlan.lua
already read, plus the alternatives (`alternative_rows`) for the `alternatives` table (M9).
"""
from __future__ import annotations

from ahsync.config import Config, flip_settings, plan_settings, portfolio_settings
from ahsync.engine import disenchant
from ahsync.engine.allocation import PortfolioPlan, portfolio_plan, stock_positions
from ahsync.engine.candidates import DeInfo, GenInputs, Run
from ahsync.engine.de_table import DE_TABLE
from ahsync.engine.flips import auctions
from ahsync.engine.portfolio import CharacterGold, candidate_to_json
from ahsync.engine.sell_record import Record
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel
from ahsync.recommend.pages import DE_KIND
from ahsync.recommend.plan import week_inputs
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
        stats: Item key -> intelligence (cur_age_ms is the age at built_ms; 0 for a scan after it, IU6).
        now_ms: Now.
        built_ms: When the intelligence was built.

    Returns:
        Item key -> age ms (a price scanned after the build counts as fresh); None without a price.
    """
    out: dict[int, int | None] = {}
    for key, i in stats.items():
        if i.cur_age_ms is None:
            out[key] = None
        else:
            out[key] = 0 if i.cur_age_ms == 0 else i.cur_age_ms + max(0, now_ms - built_ms)
    return out


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


def held_units(state: StateReader, char_ids: list[str]) -> list[tuple[str, int, int]]:
    """The units committed capital counts until positions exist: held, in the mail or listed (SPEC §3.4D.1).

    Args:
        state: Character state.
        char_ids: The market's characters.

    Returns:
        (character, item key, units): unbound inventory (bags, bank, mail) and your auctions.
    """
    out = [(c, key, qty) for c, _place, key, qty, bound in state.holdings(char_ids) if not bound]
    out += [(c, key, stack * count) for c, key, _p, stack, _b, count, _d, _e in state.own_auctions(char_ids)]
    return out


def plan_portfolio(state: StateReader, characters: list[tuple[str, int]], stats: dict[int, ItemIntel],
                   items: dict[int, ItemInfo], now_ms: int, cfg: Config, records: dict[int, Record] | None = None,
                   built_ms: int | None = None) -> PortfolioPlan | None:
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

    Returns:
        The PortfolioPlan; None without characters.

    Raises:
        engine.selection.SelectionError: The allocation broke a D.5 rule (nothing is published).
    """
    if not characters:
        return None
    char_ids = [c for c, _ in characters]
    w = week_inputs(state, characters, stats, items, cfg, records)
    gi = gen_inputs(state, characters, stats, items, now_ms, now_ms if built_ms is None else built_ms, cfg)
    chars = [CharacterGold(c, g, cfg.budgets.get(c)) for c, g in characters]
    positions = stock_positions(held_units(state, char_ids), state.cost_basis())
    run = Run(now_ms, plan_settings(cfg), flip_settings(cfg), portfolio_settings(cfg), cfg.max_craft_depth)
    return portfolio_plan(w, gi, chars, positions, run)


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
