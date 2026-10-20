"""The portfolio plan: candidates, selection and validation, then the funded batches as plan actions (SPEC §3.4D).

1. engine.week.prepare applies the rules and fills in hold values.
2. generate builds every opportunity as marginal batches (stage A, engine.candidates and
   engine.craft_candidates).
3. Each batch becomes a selection `Pick`: its adjusted profit (engine.timing), its list (Today when its
   sale is inside the immediate window, else Next 7 days; an opportunity's row takes its first batch's),
   its static exclusion reason (engine.selection.static_reason) and its claims.
4. engine.selection.select funds batches within the remaining capacity, each character's spendable gold
   and the list limits; engine.selection.validate checks the result (D.5) and raises on any violation.
5. The funded batches' opportunities rebuild their actions (the same PlanAction kinds and chain ids as
   before), and the units you hold that no funded batch uses become a hold, a disenchant or a vendor sale.
   Every unfunded batch is an alternative with its reason; none is ever an action.

Committed capital is the open positions' cost left (engine.positions.committed, SPEC §3.4D.1, M14), passed in.
Pure: data in, numbers out.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from ahsync.engine.candidates import GenInputs, Run, Universe, buys, residual, sell_stock
from ahsync.engine.craft_candidates import crafts, disenchants
from ahsync.engine.flips import auctions
from ahsync.engine.plan import PlanAction
from ahsync.engine.portfolio import Accounting, Candidate, CharacterGold, OpenPosition, accounting
from ahsync.engine.selection import Budget, Eligibility, Pick, Selection, select, static_reason, validate
from ahsync.engine.timing import adjusted, immediate, premium_micro, wait_hundredths
from ahsync.engine.week import PlanWorld, WeekInputs, bought_from, prepare, without_bought


@dataclass(frozen=True)
class Alternative:
    """An unfunded candidate batch and why (SPEC §3.4D.6): stored in `alternatives`, never an action."""

    candidate: Candidate
    lst: str                                   # "today" | "week"
    reason: str                                # the §3.4D.6 status
    adjusted: float                            # its adjusted profit, float copper
    eligibility: Eligibility | None = None     # what its static checks read, for the reason's detail line (M13)


@dataclass(frozen=True)
class PortfolioPlan:
    """One run's result (SPEC §3.4D)."""

    actions: list[PlanAction]                  # the plan: funded opportunities in funding order, then your stock's rest
    funded: tuple[Candidate, ...]              # the funded batches in funding order
    alternatives: tuple[Alternative, ...]      # the rest, by candidate id
    selection: Selection
    accounting: Accounting
    funded_adjusted: tuple[tuple[str, float], ...] = ()   # (candidate id, adjusted profit) of the funded, in funding order
    picks: tuple[Pick, ...] = ()               # every batch as selection saw it (opportunity, list, name), for the page
    opp_actions: tuple[tuple[str, int, int], ...] = ()    # (opportunity, first, end) slices of `actions` it realized
    action_cands: tuple[str | None, ...] = ()  # each action's opportunity's first funded candidate id; None: stock's rest


def generate(world: PlanWorld, gi: GenInputs, run: Run) -> Universe:
    """Every candidate of one run (SPEC §3.4D.3): independent of gold, risk tolerance and professions (PF1, PF2).

    Args:
        world: The plan's world.
        gi: Generation inputs.
        run: The run.

    Returns:
        The universe: opportunities in a fixed order (crafts, sell stock, flips, buys for later, disenchants;
        each by key) and every resource they claim.
    """
    u = Universe([], {})
    for lot in world.lots:
        u.add_resource(f"own:{lot.char_id}:{lot.key}", lot.qty, gi.names.get(lot.key, str(lot.key)))
    for key, f in sorted(world.facts.items()):
        if f.book is not None or f.cur_qty:
            u.add_resource(f"ah:{key}", sum(stack for _, stack, _ in auctions(f.book, f.cur, f.cur_qty)),
                           gi.names.get(key, str(key)))
    ledger: dict = {}                                 # your posts in generation order: later ones queue behind
    after = crafts(world, gi, run, u, ledger)
    bought = bought_from(world.world.market, after)
    left = replace(world, facts={k: without_bought(f, bought.get(k, 0.0)) for k, f in world.facts.items()})
    sell_stock(world, gi, run, u, ledger)
    buys(left, world, gi, run, u, ledger)             # flips buy from the auctions the crafts left
    disenchants(world, gi, run, u)
    seen: set[str] = set()
    unique = []
    for opp in u.opportunities:           # two recipes with one product and one route: the first (lower spell) stays
        ids = {b.candidate.cand_id for b in opp.batches}
        if opp.opp not in seen and not ids & seen:
            unique.append(opp)
            seen |= ids | {opp.opp}
    u.opportunities = unique
    return u


def picks(u: Universe, run: Run) -> list[Pick]:
    """The universe's batches as selection sees them (SPEC §3.4D.4).

    Args:
        u: The universe.
        run: The run (now, the premium settings, horizon).

    Returns:
        One Pick per batch: adjusted profit by its sale time, its opportunity's list (its first batch's),
        its static reason and its claims.
    """
    ps = run.ps
    out = []
    for opp in u.opportunities:
        first = wait_hundredths(run.now_ms, opp.batches[0].candidate.sale_ms)
        lst = "today" if immediate(first, ps.immediate_hours) else "week"
        for b in opp.batches:
            c = b.candidate
            premium = premium_micro(wait_hundredths(run.now_ms, c.sale_ms), ps.premium_base, ps.premium_per_day,
                                    ps.immediate_hours)
            out.append(Pick(c.cand_id, opp.opp, c.batch, c.char_id, lst, c.gold_needed,
                            adjusted(c.expected_net, premium), c.expected_net, opp.name,
                            static_reason(b.eligibility, ps.horizon_days), b.claims))
    return out


def portfolio_plan(w: WeekInputs, gi: GenInputs, chars: Sequence[CharacterGold], positions: Sequence[OpenPosition],
                   run: Run) -> PortfolioPlan:
    """The plan: every candidate generated, the best affordable set funded, validated (SPEC §3.4D.3-D.5).

    Args:
        w: The plan's inputs (stock, facts, recipes and market, rules).
        gi: Generation inputs (skills, disenchant facts, price ages, names, cost basis).
        chars: Your characters' gold and [budgets] values.
        positions: Committed capital: the open positions' cost left (engine.positions.committed).
        run: The run's settings.

    Returns:
        The PortfolioPlan.

    Raises:
        engine.selection.SelectionError: The allocation broke a D.5 rule (a bug: nothing is published).
    """
    world = prepare(w, run.now_ms, run.s)
    u = generate(world, gi, run)
    batch_picks = picks(u, run)
    acct = accounting(chars, positions, run.ps.risk_tolerance)
    budget = Budget(acct.capacity_left, acct.spendable, run.ps.today_limit, run.ps.week_limit)
    result = select(batch_picks, u.resources.values(), budget)
    validate(batch_picks, u.resources.values(), budget, result)
    funded_ids = set(result.funded)
    counts: dict[str, int] = {}
    claimed: dict[tuple[str, int], int] = {}
    by_id = {}
    for opp in u.opportunities:
        for b in opp.batches:
            by_id[b.candidate.cand_id] = (opp, b)
            if b.candidate.cand_id in funded_ids:
                counts[opp.opp] = counts.get(opp.opp, 0) + 1
                for oc in b.candidate.owned_claims:
                    claimed[(oc.char_id, oc.key)] = claimed.get((oc.char_id, oc.key), 0) + oc.units
    actions: list[PlanAction] = []
    done: set[str] = set()
    spans, cands = [], []
    for cand_id in result.funded:
        opp = by_id[cand_id][0]
        if opp.opp not in done:
            done.add(opp.opp)
            first = len(actions)
            actions.extend(opp.realize(counts[opp.opp]))
            spans.append((opp.opp, first, len(actions)))
            cands += [cand_id] * (len(actions) - first)
    actions.extend(residual(world, claimed, run))
    cands += [None] * (len(actions) - len(cands))
    pick_by_id = {p.cand_id: p for p in batch_picks}
    alternatives = tuple(Alternative(by_id[i][1].candidate, pick_by_id[i].lst, reason, pick_by_id[i].adjusted,
                                     by_id[i][1].eligibility) for i, reason in result.reasons)
    return PortfolioPlan(actions, tuple(by_id[i][1].candidate for i in result.funded), alternatives, result, acct,
                         tuple((i, pick_by_id[i].adjusted) for i in result.funded), tuple(batch_picks), tuple(spans),
                         tuple(cands))
