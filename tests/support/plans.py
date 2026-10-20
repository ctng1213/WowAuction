"""Run the whole portfolio plan on a hand-built world, for engine tests (SPEC §3.4D)."""
from __future__ import annotations

from ahsync.engine.allocation import PortfolioPlan, portfolio_plan
from ahsync.engine.candidates import DeInfo, GenInputs, Run
from ahsync.engine.flips import FlipSettings
from ahsync.engine.plan import PlanAction, PlanSettings
from ahsync.engine.portfolio import CharacterGold, OpenPosition, PortfolioSettings
from ahsync.engine.week import WeekInputs

RICH = 100_000_000


def characters_of(w: WeekInputs) -> list[str]:
    """Every character a world mentions: lot holders and recipe crafters.

    Args:
        w: The world.

    Returns:
        Character ids, sorted.
    """
    return sorted({lot.char_id for lot in w.lots} | {c for r in w.world.recipes for c in r.crafters})


def plan(w: WeekInputs, now_ms: int, gold: dict[str, int] | None = None, s: PlanSettings | None = None,
         fs: FlipSettings | None = None, ps: PortfolioSettings | None = None, max_depth: int = 3,
         skills: dict[tuple[str, str], int] | None = None, de: dict[int, DeInfo] | None = None,
         positions: tuple[OpenPosition, ...] = (), names: dict[int, str] | None = None,
         recipe_needs: dict[int, tuple[str, int]] | None = None, cost_each: dict[int, float] | None = None,
         price_age: dict[int, int | None] | None = None) -> PortfolioPlan:
    """The portfolio plan of a world.

    Args:
        w: The world.
        now_ms: Now.
        gold: Character -> gold in hand; every character of the world is rich by default.
        s: Plan settings.
        fs: Flip settings.
        ps: Portfolio settings; risk tolerance 100% by default, so only gold binds.
        max_depth: Config max_craft_depth.
        skills: (character, profession) -> skill.
        de: Item key -> disenchant facts.
        positions: Committed capital.
        names: Item names.
        recipe_needs: Spell -> (profession, skill); none by default.
        cost_each: Item key -> cost basis per unit.
        price_age: Item key -> price age ms.

    Returns:
        The PortfolioPlan.
    """
    chars = sorted(gold) if gold is not None else characters_of(w)
    gold = gold if gold is not None else {c: RICH for c in chars}
    gi = GenInputs(tuple(chars), dict(gold), skills or {}, recipe_needs or {}, de or {}, price_age or {}, names or {},
                   cost_each or {})
    run = Run(now_ms, s or PlanSettings(), fs or FlipSettings(), ps or PortfolioSettings(risk_tolerance=1.0), max_depth)
    return portfolio_plan(w, gi, [CharacterGold(c, g) for c, g in sorted(gold.items())], positions, run)


def actions(w: WeekInputs, now_ms: int, **kwargs) -> list[PlanAction]:
    """The portfolio plan's actions of a world.

    Args:
        w: The world.
        now_ms: Now.
        kwargs: plan()'s options.

    Returns:
        The plan's actions.
    """
    return plan(w, now_ms, **kwargs).actions
