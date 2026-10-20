"""The Portfolio page's payload, built at the full refresh and at every re-plan (SPEC §3.5 Portfolio, §3.4D).

The view comes from engine.portfolio_view; this module only gathers its inputs from the run that was just
planned (the funded batches, the alternatives, the accounting), the positions, item names and market
activity, and the characters' gold. Nothing is selected again: the page shows what the run stored.
"""
from __future__ import annotations

from ahsync.config import Config, portfolio_settings
from ahsync.engine.allocation import PortfolioPlan
from ahsync.engine.portfolio import CharacterGold
from ahsync.engine.portfolio_view import PortfolioInputs, portfolio_view
from ahsync.engine.selection import STALE_MS
from ahsync.history.reads import ItemInfo
from ahsync.intel.contract import ItemIntel
from ahsync.recommend.plan_pages import item_looks
from ahsync.recommend.portfolio import plan_positions, price_ages
from ahsync.state.reads import StateReader


def prices_stale(stats: dict[int, ItemIntel], now_ms: int, built_ms: int) -> bool:
    """Whether the market's newest price is more than 24 h old (SPEC §3.4D.7 "Market data too old").

    Args:
        stats: Item key -> intelligence.
        now_ms: Now.
        built_ms: The intelligence's built_ms.

    Returns:
        True when no item has a price, or the freshest one is older than 24 h now.
    """
    ages = [age for age in price_ages(stats, now_ms, built_ms).values() if age is not None]
    return not ages or min(ages) > STALE_MS


def portfolio_page(state: StateReader, characters: list[tuple[str, int]], plan: PortfolioPlan | None,
                   action_ids: list[str], stats: dict[int, ItemIntel], items: dict[int, ItemInfo], now_ms: int,
                   offset_ms: int, cfg: Config, built_ms: int) -> dict:
    """The `portfolio` payload of one market (SPEC §3.5 dashboard data).

    Args:
        state: Character state (inventory, auctions and cost basis: the positions).
        characters: (character id, gold copper) on the market, sorted by id.
        plan: The run's plan; None without characters.
        action_ids: The plan's action ids, aligned with plan.actions (recommend.plan.plan_rows).
        stats: Item key -> intelligence.
        items: Item metadata.
        now_ms: The run's time.
        offset_ms: Local time minus UTC.
        cfg: Config (the portfolio settings, [budgets], high_risk_pct).
        built_ms: The intelligence's built_ms (price ages).

    Returns:
        engine.portfolio_view.portfolio_view's payload.
    """
    char_ids = [c for c, _ in characters]
    positions = plan_positions(state, char_ids, plan)
    keys = {p.key for p in positions}
    if plan is not None:
        cands = [*plan.funded, *(a.candidate for a in plan.alternatives)]
        keys |= {c.key for c in cands} | {k for c in cands for k, _ in c.inputs}
    chars = tuple(CharacterGold(c, g, cfg.budgets.get(c)) for c, g in characters)
    return portfolio_view(PortfolioInputs(plan, chars, tuple(positions), tuple(action_ids), item_looks(keys, stats, items),
                                          portfolio_settings(cfg), now_ms, offset_ms,
                                          prices_stale(stats, now_ms, built_ms), cfg.high_risk_pct))
