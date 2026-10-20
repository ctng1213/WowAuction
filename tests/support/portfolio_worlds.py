"""Small hand-built worlds for the portfolio candidate tests (SPEC §3.4D.3)."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine import craft
from ahsync.engine.allocation import generate
from ahsync.engine.candidates import GenInputs, Opportunity, Run, Universe
from ahsync.engine.craft_plan import CraftWorld
from ahsync.engine.flips import FlipSettings
from ahsync.engine.plan import PlanSettings, PlanWindow, SellFacts
from ahsync.engine.portfolio import PortfolioSettings
from ahsync.engine.week import WeekInputs, prepare
from tests.support import plans

HOUR, DAY = 3_600_000, 86_400_000
MONDAY = 345_600_000 + 3000 * 7 * DAY
SAT, SUN, WED = 5, 6, 2
NOW = MONDAY + 10 * HOUR + 38 * 60_000              # Mon 10:38
BAR, ROD, RUNED, DUST, LINEN, GLOVES, ESSENCE = 2840, 6217, 6218, 10940, 2589, 7005, 10939
S, FS, PS = PlanSettings(), FlipSettings(), PortfolioSettings()
RUN = Run(NOW, S, FS, PS, 3)
NAMES = {BAR: "Copper Bar", ROD: "Copper Rod", RUNED: "Runed Copper Rod", LINEN: "Linen Cloth",
         GLOVES: "Scouting Gloves of the Monkey", ESSENCE: "Greater Magic Essence", DUST: "Strange Dust"}
ROD_RECIPE = craft.Recipe(2663, ROD, 1, 1, ((BAR, 2),), ("Brugan-G",))


def facts(key: int, level: int, **changes) -> SellFacts:
    """An item with a Sunday 18:00 peak (1.2×) seen 6 weeks, 60 sold a day, 20 listed by others.

    Args:
        key: Item key.
        level: Price level, copper.
        changes: Field overrides.

    Returns:
        The SellFacts.
    """
    how = [1.0] * 168
    how[SUN * 24 + 18] = 1.2
    base = SellFacts(key, level=level, trend7=0.0, closes_30d=720, post_price=level, sold_per_day=60.0, listed_per_day=20.0,
                     ref_stack=1, how=tuple(how), sales_how=tuple([1.0] * 168),
                     windows=(PlanWindow(SUN * 24 + 17, 3, SUN * 24 + 18, 6),), book=(), cur=level, cur_qty=2,
                     vendor_sell=50, supply_now=20, conf=70)
    return replace(base, **changes)


def gen_inputs(chars=("Amaya-G", "Brugan-G"), skills=None, de=None, gold=plans.RICH) -> GenInputs:
    """Generation inputs for the test worlds.

    Args:
        chars: Characters.
        skills: (character, profession) -> skill.
        de: Disenchant facts.
        gold: Everyone's spendable gold.

    Returns:
        GenInputs.
    """
    return GenInputs(tuple(chars), {c: gold for c in chars}, skills or {}, {}, de or {}, {}, NAMES, {})


def universe(w: WeekInputs, gi: GenInputs | None = None, run: Run = RUN) -> Universe:
    """The candidates of a world.

    Args:
        w: The world.
        gi: Generation inputs.
        run: The run.

    Returns:
        The universe.
    """
    return generate(prepare(w, run.now_ms, run.s), gi or gen_inputs(), run)


def empty_world(**changes) -> WeekInputs:
    """A world without recipes.

    Args:
        changes: WeekInputs overrides.

    Returns:
        WeekInputs.
    """
    return replace(WeekInputs((), {}, CraftWorld((), {}, craft.Market({}, {}, {}, {}), {})), **changes)


def opportunity(u: Universe, prefix: str) -> Opportunity:
    """The one opportunity whose id starts with a prefix.

    Args:
        u: The universe.
        prefix: Id prefix.

    Returns:
        It.
    """
    [opp] = [o for o in u.opportunities if o.opp.startswith(prefix)]
    return opp


def linen_book(**changes) -> SellFacts:
    """Linen: stacks of 20 at 5c, 8c and 12c below a 14c market, posting now only.

    Args:
        changes: Field overrides.

    Returns:
        The SellFacts.
    """
    base = facts(LINEN, 14, ref_stack=20, windows=(), post_price=13, med7=14, p90=14, vendor_sell=1,
                 book=((5, 20, 4, 1), (8, 20, 4, 1), (12, 20, 4, 1), (14, 20, 4, 5)), cur=5, cur_qty=20,
                 sold_per_day=400.0, supply_now=400)
    return replace(base, **changes)
