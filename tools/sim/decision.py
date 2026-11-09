"""Decision quality: one world traded by AHSync's plan and by each benchmark, scored alike (SPEC §8.10; plan §1.6).

`benchmark` runs the same world four ways from the same seed: our characters following the plan (`Trader` with
AHSync's session), the naive trader, doing nothing, and the oracle (computed from the do-nothing run's truth, where
our characters never touch the market). Each run is its own market, so our trades move it and competitors react;
the do-nothing run is the market as it would have been. MS3 has one world, `STARTER` (stock to sell and a dump to
catch); MS4's SD rows add theirs.
"""
from __future__ import annotations

from pathlib import Path

from sim.market import DAY_MS, HOUR_MS, Planted, World
from sim.inference import START_MS, commodity
from sim.runner import simulate
from sim.scorers import Score, oracle_profit, score
from sim.trader import CharacterSpec, NaiveTrader, Trader

GOLD = 10_000
STARTER_CHARACTERS = (CharacterSpec("Amaya", 100 * GOLD, bags=((2589, 200), (4306, 60))),)


def starter(seed: int = 1, days: int = 6) -> World:
    """MS3's decision world: three real-shaped cloths; on day 3, 3,000 Silk dumped at half price just before the
    daily run.

    Args:
        seed: The world's seed.
        days: Its length.

    Returns:
        The World.
    """
    items = (commodity(2589, "Linen Cloth", 50, 400, 120, cancel_share=0.06),
             commodity(4306, "Silk Cloth", 150, 250, 80, cancel_share=0.06, max_units=8),
             commodity(2592, "Wool Cloth", 100, 150, 60, cancel_share=0.06, max_units=6))
    dump = (Planted(START_MS + 3 * DAY_MS + 8 * HOUR_MS + 50 * 60_000, 4306, 3000, 75, 24),)
    return World(seed, START_MS, days, items, planted=dump)


def benchmark(world: World, characters: tuple[CharacterSpec, ...], folder: Path) -> list[Score]:
    """Run a world with the plan, the naive trader and nothing, and score each against the oracle.

    Args:
        world: The scenario.
        characters: Our characters, the same for every benchmark.
        folder: An empty folder for the runs.

    Returns:
        Scores of "plan", "naive", "nothing" and "oracle", in that order.

    Raises:
        AssertionError: A ledger out of balance (SU12).
    """
    stock: dict[int, int] = {}
    for spec in characters:
        for key, units in spec.bags:
            stock[key] = stock.get(key, 0) + units
    plan = Trader(world, characters, folder / "ahsync")
    try:
        simulate(world, folder / "plan", agent=plan)
    finally:
        plan.close()
    naive = NaiveTrader(world, characters)
    simulate(world, folder / "naive", agent=naive)
    nothing = Trader(world, characters)
    quiet = simulate(world, folder / "nothing", agent=nothing)
    oracle = oracle_profit(quiet.truth.events, stock)
    scores = []
    for name, trader in (("plan", plan), ("naive", naive), ("nothing", nothing)):
        assert trader.ledger.balanced(), f"{name}'s ledger is out of balance (seed {world.seed})"
        scores.append(score(name, trader.ledger, trader.samples, oracle))
    return scores + [Score("oracle", oracle, 0.0, 0, 0, 0, 1.0 if oracle else None)]
