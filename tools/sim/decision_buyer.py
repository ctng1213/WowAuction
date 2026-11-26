"""SM22, the buy side (SPEC §8.10; `docs/plans/sold-price.md` §1d item 30(f), built with SP2d).

SP2a's prototype bought under the low bound and resold at the post price over SM20's worlds: +0.11 to +0.16 per gold
spent under SP2's level and bounds against +0.02 to +0.11 under the level of the day, spending about 35% less.
"Today's level" no longer exists in the engine (SP2c), so the built row reports SP2's numbers against those recorded
ones. A buyer who takes every flip and buy for later the plan offers is the real plan itself, traded through the
real addon import (`trader.Trader`, as the SD rows are run), with enough gold that no offer is left unfunded. The
world is SM20's, run past the plan's 3-week trust gate (SPEC §3.4D.2, PF28: a buy for later needs 3 weeks of
pattern data) before the worth changes, so each regime's buys are made with the plan buying, and LATE_DAYS past the
last counted buy, so a unit bought has a week to resell before the world ends (a buy later than that is reported
apart, not scored). Measured only: every line with a buy is a report, not a pass.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ahsync.engine.fees import AH_CUT

from sim.decision import Line, plan_run
from sim.inference import COMMODITY_DEMAND_NOISE, GOLD, START_MS
from sim.market import DAY_MS
from sim.price_rules import GROUPS, REGIMES, Kind, group_of, rules_world
from sim.trader import OUR_PREFIX, CharacterSpec
from sim.truth import Event

DAYS, CHANGE_DAY = 36, 22                        # past the 3-week trust gate (decision.MATURE_DAYS) before the change
LATE_DAYS = 7                                    # a buy within this of the end is not scored: no time to resell
FUNDED = 5_000 * GOLD                            # our character's gold: no flip or buy for later goes unfunded
PROTOTYPE = "reported (the prototype: +0.11 to +0.16, plan §1d item 30(f))"
BUYER = (CharacterSpec("Amaya", FUNDED),)


@dataclass(frozen=True)
class Trades:
    """What our buys of one regime's items came to."""

    buys: int                         # buy events
    spent: int                        # copper paid for the units
    bought: int                       # units bought
    proceeds: float                   # copper received for them after the AH cut, float
    sold: int                         # units of them resold

    @property
    def earned_per_gold(self) -> float | None:
        """(proceeds − spent) ÷ spent; None without a buy.

        Returns:
            Earned per gold spent; the unsold units count for nothing (stranded).
        """
        return (self.proceeds - self.spent) / self.spent if self.spent else None


def trades(events: Sequence[Event], kinds: dict[int, Kind], until_ms: int | None = None,
           by: Callable[[Kind], str] = lambda kind: kind.regime) -> dict[str, Trades]:
    """Our buys and the resales of what we bought, grouped by the items' Kind.

    Args:
        events: The run's truth log.
        kinds: Item key -> its Kind.
        until_ms: Buys after this are left out (they had no time to resell), and so are the resales of our auctions
            posted after it (they would be those late buys'); None counts every buy and resale.
        by: The group of a Kind: its regime (the default), or price_rules.group_of for its shape.

    Returns:
        Group -> its Trades; every group of `kinds` present.
    """
    ours = {e.auction for e in events if e.kind == "post" and e.actor.startswith(OUR_PREFIX)
            and (until_ms is None or e.at_ms <= until_ms)}
    sums = {by(kind): [0, 0, 0, 0.0, 0] for kind in kinds.values()}
    for e in events:
        if e.kind != "buy" or e.key not in kinds:
            continue
        row = sums[by(kinds[e.key])]
        if e.actor.startswith(OUR_PREFIX):
            if until_ms is not None and e.at_ms > until_ms:
                continue
            row[0] += 1
            row[1] += e.price * e.units
            row[2] += e.units
        elif e.auction in ours:
            row[3] += e.price * e.units * (1 - AH_CUT)
            row[4] += e.units
    return {regime: Trades(*row) for regime, row in sums.items()}


def sm22(folder: Path, seed: int = 1, replicates: int = 2, days: int = DAYS, change_day: int = CHANGE_DAY
         ) -> list[Line]:
    """SM22: earned per gold spent by the plan's flips and buys for later, per regime, against the prototype's.

    Args:
        folder: An empty folder.
        seed: The world's seed.
        replicates: Items per shape and regime of SM20's world.
        days: The world's length.
        change_day: The day the worth steps.

    Returns:
        Per regime, per group (SM20's GROUPS) and over all: earned per gold spent on the buys made LATE_DAYS or more
        before the end, with the buys, the units bought and the units unsold at the end in the measure's words
        ("not exercised" without a buy); then the late buys, reported apart.
    """
    world, kinds = rules_world(seed, replicates, days=days, change_day=change_day, demand_noise=COMMODITY_DEMAND_NOISE)
    plan = plan_run(world, folder, BUYER)
    until = START_MS + (days - LATE_DAYS) * DAY_MS
    by_regime = trades(plan.truth.events, kinds, until)
    by_group = trades(plan.truth.events, kinds, until, group_of)
    rows = [(regime, by_regime[regime]) for regime in REGIMES] + [(group, by_group[group]) for group in GROUPS]
    rows.append(("all regimes", Trades(sum(t.buys for t in by_regime.values()), sum(t.spent for t in by_regime.values()),
                                       sum(t.bought for t in by_regime.values()),
                                       sum(t.proceeds for t in by_regime.values()), sum(t.sold for t in by_regime.values()))))
    lines = []
    for name, t in rows:
        ratio = t.earned_per_gold
        what = f"{name}: earned per gold spent ({t.buys} buys, {t.bought} units for {t.spent / GOLD:.1f} g, " \
               f"{t.bought - t.sold} unsold)"
        lines.append(Line("SM22", what, "no buys" if ratio is None else f"{ratio:+.2f}", PROTOTYPE,
                          None if ratio is None else True))
    late = [e for e in plan.truth.events if e.kind == "buy" and e.actor.startswith(OUR_PREFIX) and e.at_ms > until
            and e.key in kinds]
    lines.append(Line("SM22", f"late buys, within {LATE_DAYS} days of the end, not scored",
                      f"{len(late)} buys, {sum(e.units for e in late)} units for {sum(e.price * e.units for e in late) / GOLD:.1f} g",
                      "reported", True))
    return lines


BUYER_ROWS = {"SM22": sm22}          # tools/sim_report merges them with its SD rows
