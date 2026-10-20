"""The portfolio optimizer's candidates, stage A: every opportunity as marginal batches (SPEC §3.4D.3, PF1-PF4).

Each strategy reuses the engine that already sizes it one step at a time, so no formula is
written twice (crafts and disenchants are in engine.craft_candidates; engine.allocation.generate
builds the whole universe):

| Strategy | One batch is | From |
|---|---|---|
| Sell stock | one reference stack you hold | plan.stack_choices (the sell plan's per-stack loop) |
| Flip (and Buy for later bought now) | one auction | flips.flip_steps (build_flip's loop) |
| Craft, Multistage craft | one craft | craft.craft_option's steps, crafted by craft.gather's choice |
| Disenchant | one auction | the item's disenchant EV, for every disenchantable item |
| Buy for later | one reference stack bought in a buy window | plan.post_options, post_chance and slot_price |

Generation ignores your gold, your risk tolerance and which professions your characters have
(PF1, PF2): those are selection's checks. Opportunities are generated in the plan's old order
with one generation ledger, so each queues behind the posts generated before it; every unit you
hold is offered to every strategy that could use it, so alternatives compete for the same
resources through the claims they carry, and selection funds only one of them (PF4, PF7). Where a batch could sell now
or at a window, the window is chosen by timing-adjusted value among the windows that pass the
trust gate (SPEC §3.4D.2; trust and time both apply, PF20). Each opportunity can rebuild the plan
actions of its first n batches (`realize`), in the formats the views, tracker and addon read. Pure.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ahsync.engine import fees, sellprob
from ahsync.engine.capacity import slot_of
from ahsync.engine.flips import FlipSettings, auctions, build_flip, flip_actions, flip_steps, pays
from ahsync.engine.plan import (DAY_MS, HOUR_MS, Lot, OptionValue, PlanAction, PlanSettings, PostOption, SellFacts,
                                alternative, merge_stacks, occurrences, post_chance, post_options, slot_price,
                                stack_choices, stack_fallback, thin, window_id)
from ahsync.engine.portfolio import (Candidate, CapacityClaim, OwnedClaim, PortfolioSettings,
                                     candidate_id, route_text, timing_tag)
from ahsync.engine.selection import STALE_MS, Eligibility, Resource
from ahsync.engine.timing import adjusted, premium_micro, wait_hundredths
from ahsync.engine.week import PlanWorld

PATTERN_WEEKS = 3          # a Buy for later window must be at least a "pattern" (SPEC §3.3.4, §3.4D.2)


@dataclass(frozen=True)
class DeInfo:
    """What disenchanting one unit of an item yields (SPEC §3.4A), for every disenchantable item on the AH."""

    skill: int                                         # Enchanting skill it needs
    ev_each: float                                     # disenchant EV per unit, float copper
    mats: tuple[tuple[int, float], ...]                # (mat key, expected units per item disenchanted), by key
    auctions: tuple[tuple[int, int], ...]              # (unit price, units) of other sellers, cheapest first
    conf: int = 0                                      # confidence % of its numbers
    volume_unknown: str | None = None                  # its main mat's name when that rate is unknown (PF30)


@dataclass(frozen=True)
class GenInputs:
    """What generation reads besides the plan's world (SPEC §3.4D.3)."""

    chars: tuple[str, ...]                             # your characters on the market, sorted
    spendable: Mapping[str, int]                       # character -> spendable gold: who buys a flip (most, ties id)
    skills: Mapping[tuple[str, str], int]              # (character, profession) -> skill
    recipe_needs: Mapping[int, tuple[str, int]]        # spell -> (profession, skill it needs)
    de: Mapping[int, DeInfo]                           # item key -> disenchant facts
    price_age: Mapping[int, int | None]                # item key -> how old its newest price is, ms
    names: Mapping[int, str]                           # item key -> name
    cost_each: Mapping[int, float]                     # item key -> what you paid per unit, account-wide (D.8)
    built_ms: int = 0                                  # the intelligence's built_ms


@dataclass(frozen=True)
class Batch:
    """One candidate batch with what selection needs about it (SPEC §3.4D.3)."""

    candidate: Candidate
    eligibility: Eligibility
    claims: tuple[tuple[str, int], ...]                # (resource id, units), by resource id


@dataclass(frozen=True)
class Opportunity:
    """An opportunity's batches, worth less each, and how to build the actions of the first n (SPEC §3.4D.3)."""

    opp: str                                           # strategy:key:char:route-without-sale
    name: str                                          # its row's label
    batches: tuple[Batch, ...]
    realize: Callable[[int], list[PlanAction]]         # n funded batches -> plan actions


@dataclass
class Universe:
    """Every candidate of one run and the resources they claim (SPEC §3.4D.3: bigger than what is shown)."""

    opportunities: list[Opportunity]
    resources: dict[str, Resource]

    def add_resource(self, rid: str, limit: int, name: str) -> None:
        """Register a resource; registered twice, the smaller limit stands.

        Args:
            rid: Resource id.
            limit: Units available.
            name: The item's name.
        """
        old = self.resources.get(rid)
        if old is None or limit < old.limit:
            self.resources[rid] = Resource(rid, limit, name)


@dataclass(frozen=True)
class Run:
    """One generation run's settings."""

    now_ms: int
    s: PlanSettings
    fs: FlipSettings
    ps: PortfolioSettings
    max_depth: int


def sale_ms(o: PostOption, now_ms: int) -> int:
    """A post option's expected sale hour (SPEC §3.4D.2).

    Args:
        o: The option.
        now_ms: Now.

    Returns:
        Its window's start ms; now for posting now.
    """
    return o.window[0] if o.window else now_ms


def timed_value(opts: Sequence[PostOption], run: Run) -> OptionValue:
    """choose_option's value with the timing premium: the option's value ÷ (1 + premium) (SPEC §3.4D.2).

    Args:
        opts: The post options.
        run: The run.

    Returns:
        (option index, value) -> adjusted value, float copper.
    """
    premiums = [premium_micro(wait_hundredths(run.now_ms, sale_ms(o, run.now_ms)), run.ps.premium_base,
                              run.ps.premium_per_day, run.ps.immediate_hours) for o in opts]
    return lambda i, e: adjusted(e, premiums[i])


def cap_rid(key: int, o: PostOption) -> str:
    """The resource id of a window's market capacity (the §3.4C.4 ledger's key).

    Args:
        key: Item key.
        o: The post option.

    Returns:
        "cap:<key>:<window start ms>" or "cap:<key>:now".
    """
    w = window_id(o)
    return f"cap:{key}:{'now' if w is None else w}"


def claims_of(pairs: Sequence[tuple[str, int]]) -> tuple[tuple[str, int], ...]:
    """Claims summed per resource, zeros dropped, by resource id.

    Args:
        pairs: (resource id, units), repeats allowed.

    Returns:
        The claims.
    """
    out: dict[str, int] = {}
    for rid, units in pairs:
        out[rid] = out.get(rid, 0) + units
    return tuple(sorted((rid, u) for rid, u in out.items() if u))


def make_candidate(strategy: str, key: int, char_id: str, route: str, batch: int, gi: GenInputs,
                   **fields: object) -> Candidate:
    """A Candidate with the run's built_ms (its id is checked as it is built).

    Args:
        strategy: A strategy code.
        key: The output item.
        char_id: The acting character.
        route: route_text.
        batch: 1-based.
        gi: Generation inputs.
        fields: The other Candidate fields.

    Returns:
        The Candidate.
    """
    candidate_id(strategy, key, char_id, route, batch)
    return Candidate(strategy, key, char_id, route, batch, built_ms=gi.built_ms, **fields)


def eligibility_of(key: int, units: int, f: SellFacts, gi: GenInputs, o: PostOption, **fields: object) -> Eligibility:
    """The static checks' facts of a batch selling `units` of an item in all (SPEC §3.4D.6).

    The evidence it needs depends on when it sells (PF30): any sale needs a known sales rate, and a sale at a
    window also needs MIN_HOURLY_CLOSES hourly closes in 30 days (an unknown count counts as none).

    Args:
        key: The item whose price and sales rate count.
        units: Units its opportunity plans up to and including this batch.
        f: The item's facts (its sales rate and close count).
        gi: Generation inputs (price ages).
        o: The post option the batch sells by: posting now (no window) or a window.
        fields: Other Eligibility fields (profession, skill, ...).

    Returns:
        The Eligibility.
    """
    return Eligibility(price_age_ms=gi.price_age.get(key), sold_per_day=f.sold_per_day, planned_units=units,
                       window_closes=None if o.window is None else (f.closes_30d or 0),
                       volume_unknown="" if f.sold_per_day is None else None, **fields)


def ungrounded(f: SellFacts, o: PostOption) -> bool:
    """Whether a batch would sell now an item you don't own into an empty book on thin data (R28).

    Args:
        f: The item, as the batch's sale sees its book.
        o: The option it sells by.

    Returns:
        True when it posts now, nothing else is listed and the item has fewer than MIN_HOURLY_CLOSES closes:
        its price is a level no listing grounds. Stock you own is never asked (it may post at the level).
    """
    return o.window is None and thin(f) and sellprob.cheapest_other(f.book, f.cur) is None


def flip_char(gi: GenInputs) -> str:
    """Who buys a flip or a buy for later: the character with the most spendable gold, ties by id (R16).

    Args:
        gi: Generation inputs.

    Returns:
        A character id.
    """
    return min(gi.chars, key=lambda c: (-gi.spendable.get(c, 0), c))


# ---------------------------------------------------------------- Sell stock

def sell_stock(world: PlanWorld, gi: GenInputs, run: Run, u: Universe, ledger: dict) -> None:
    """Sell stock: each reference stack you hold that a post pays for (SPEC §3.4C.1, §3.4D.3).

    Every unit you hold is offered, also those a craft could use: the two compete for the same units
    through their claims (PF7).

    Args:
        world: The plan's world.
        gi: Generation inputs.
        run: The run.
        u: The universe; opportunities and resources are added.
        ledger: The generation ledger (crafts' posts first); the stacks' posts are added.
    """
    by_key: dict[int, list[Lot]] = {}
    for lot in world.lots:
        if lot.key in world.post_facts and lot.qty > 0:
            by_key.setdefault(lot.key, []).append(lot)
    for key in sorted(by_key):
        f = world.post_facts[key]
        opts = post_options(f, run.now_ms, run.s)
        choices = list(stack_choices(by_key[key], f, opts, run.s, ledger, timed_value(opts, run)))
        for lot in sorted(by_key[key], key=lambda lot: lot.char_id):
            u.add_resource(f"own:{lot.char_id}:{key}", lot.qty, gi.names.get(key, str(key)))
            posted = [c for c in choices if c.char_id == lot.char_id and c.option is not None]
            if posted:
                u.opportunities.append(_sell_opportunity(f, opts, lot.char_id, posted, gi, run, u))


def _sell_opportunity(f: SellFacts, opts: Sequence[PostOption], char_id: str, posted: list, gi: GenInputs, run: Run,
                      u: Universe) -> Opportunity:
    """One character's Sell stock opportunity of one item: its posted stacks as batches.

    Args:
        f: The item.
        opts: Its post options.
        char_id: Who holds the stacks.
        posted: Their stack choices that post, in order.
        gi: Generation inputs.
        run: The run.
        u: The universe (capacity resources are added).

    Returns:
        The Opportunity.
    """
    name, batches, units = gi.names.get(f.key, str(f.key)), [], 0
    for n, c in enumerate(posted, 1):
        o = opts[c.option]
        u.add_resource(cap_rid(f.key, o), o.cap, name)
        units += c.qty
        sale = sale_ms(o, run.now_ms)
        cand = make_candidate(
            "sell_stock", f.key, char_id, route_text({}, "-", timing_tag(sale, run.now_ms)), n, gi, out_qty=c.qty,
            inputs=((f.key, c.qty),), gold_deposits=fees.deposit_copper(f.vendor_sell, c.qty, o.duration_h),
            cost_basis=math.floor(gi.cost_each.get(f.key, 0.0) * c.qty + 0.5), expected_net=c.expected - f.paid_each * c.qty,
            chance=c.p, exec_qty=c.qty, conf=f.conf, sale_ms=sale, owned_claims=(OwnedClaim(char_id, "stock", f.key, c.qty),),
            capacity_claims=(CapacityClaim(f.key, window_id(o), c.qty),))
        batches.append(Batch(cand, eligibility_of(f.key, units, f, gi, o),
                             claims_of([(f"own:{char_id}:{f.key}", c.qty), (cap_rid(f.key, o), c.qty)])))
    return Opportunity(f"sell_stock:{f.key}:{char_id}", name, tuple(batches),
                       lambda n: merge_stacks(posted[:n], f, opts, run.now_ms))


def residual(world: PlanWorld, claimed: Mapping[tuple[str, int], int], run: Run) -> list[PlanAction]:
    """What becomes of the units you hold that no funded batch uses: hold, disenchant or vendor (SPEC §3.4C.1).

    Args:
        world: The plan's world.
        claimed: (character, item) -> owned units the funded batches use.
        run: The run.

    Returns:
        One action per lot with units left (a hold of a vendor-sold item is left out, R23), by character then item.
    """
    out = []
    for lot in sorted(world.lots, key=lambda lot: (lot.char_id, lot.key)):
        f = world.post_facts.get(lot.key)
        left = lot.qty - claimed.get((lot.char_id, lot.key), 0)
        if f is None or left <= 0:
            continue
        actions = merge_stacks([stack_fallback(f, lot.char_id, left)], f, (), run.now_ms)
        out.extend(a for a in actions if not (a.key in world.vendor_sold and a.kind == "hold"))
    return out


# ---------------------------------------------------------------- Flip and Buy for later: one pool

def stale(gi: GenInputs, key: int) -> bool:
    """Whether an item's newest price is too old to buy on (SPEC §3.4D.3: generation leaves it out; D.6 stale).

    Args:
        gi: Generation inputs (price ages).
        key: Item key.

    Returns:
        True when its price is more than 24 h old.
    """
    age = gi.price_age.get(key)
    return age is not None and age > STALE_MS


def first_rank(gold: int, net: float, sale: int | None, cand_id: str, run: Run) -> tuple:
    """Where a buy's first batch ranks in the candidate pool: adjusted profit per copper, as the fill orders it.

    Args:
        gold: Its gold needed, copper.
        net: Its expected net, float copper.
        sale: Its expected sale hour, UTC ms.
        cand_id: Its candidate id (the tie-break).
        run: The run (now, the premium settings).

    Returns:
        (0, −adjusted, id) without gold, else (1, −adjusted ÷ gold, id): smaller ranks first.
    """
    ps = run.ps
    adj = adjusted(net, premium_micro(wait_hundredths(run.now_ms, sale), ps.premium_base, ps.premium_per_day,
                                      ps.immediate_hours))
    return (0, -adj, cand_id) if gold <= 0 else (1, -adj / gold, cand_id)


def _flip_first(f: SellFacts, char_id: str, ledger: dict, run: Run) -> tuple | None:
    """A flip's first auction ranked for the pool.

    Args:
        f: The item.
        char_id: Who buys.
        ledger: The generation ledger; read only.
        run: The run.

    Returns:
        first_rank of its first batch; None when not even one auction pays.
    """
    opts = post_options(f, run.now_ms, run.s)
    seen = {(f.key, window_id(o)): ledger[(f.key, window_id(o))] for o in opts if (f.key, window_id(o)) in ledger}
    step = next(flip_steps(f, run.now_ms, run.s, run.fs, seen, timed_value(opts, run), cost_value=True), None)
    if step is None:
        return None
    o = opts[step.option]
    later = f.p10 is not None and f.cur is not None and f.cur < f.p10 and o.window is not None
    sale, cost = sale_ms(o, run.now_ms), step.price * step.stack
    cand = candidate_id("buy_later" if later else "flip", f.key, char_id,
                        route_text({}, "now", timing_tag(sale, run.now_ms)), 1)
    return first_rank(cost + fees.deposit_copper(f.vendor_sell, step.stack, o.duration_h), step.expected - cost,
                      sale, cand, run)


def _flip(f: SellFacts, char_id: str, gi: GenInputs, run: Run, u: Universe, ledger: dict) -> None:
    """One item's whole flip, every auction that pays, added to the universe and the ledger.

    Args:
        f: The item.
        char_id: Who buys.
        gi: Generation inputs.
        run: The run.
        u: The universe.
        ledger: The generation ledger; the flip's posts are added.
    """
    opts = post_options(f, run.now_ms, run.s)
    value = timed_value(opts, run)
    seen = {(f.key, window_id(o)): ledger[(f.key, window_id(o))] for o in opts if (f.key, window_id(o)) in ledger}
    steps = list(flip_steps(f, run.now_ms, run.s, run.fs, seen, value, cost_value=True))
    if steps:
        u.opportunities.append(_flip_opportunity(f, opts, steps, char_id, value, seen, gi, run, u))
        for step in steps:
            window = (f.key, window_id(opts[step.option]))
            ledger[window] = ledger.get(window, 0.0) + step.stack


def _ungrounded_flip(f: SellFacts, char_id: str, gi: GenInputs, run: Run, u: Universe, ledger: dict) -> None:
    """The flip the grounding rule turns down, listed with its reason (SPEC §3.4C.2, §3.4D.6, R28).

    An item whose only listing is the one bought, on thin data, has no price to relist at; when the flip would
    pay at the ungrounded post price, its first auction is added as one excluded batch ("No listing left to
    price against"). Nothing is added to the ledger: it is never funded.

    Args:
        f: The item (no grounded flip).
        char_id: Who would buy.
        gi: Generation inputs.
        run: The run.
        u: The universe.
        ledger: The generation ledger; read only.
    """
    if not thin(f) or len(auctions(f.book, f.cur, f.cur_qty)) != 1:
        return
    opts = post_options(f, run.now_ms, run.s)
    value = timed_value(opts, run)
    seen = {(f.key, window_id(o)): ledger[(f.key, window_id(o))] for o in opts if (f.key, window_id(o)) in ledger}
    steps = list(flip_steps(f, run.now_ms, run.s, run.fs, seen, value, cost_value=True, grounded=False))
    if steps:
        u.opportunities.append(_flip_opportunity(f, opts, steps, char_id, value, seen, gi, run, u, no_listing=True))


def buys(left: PlanWorld, world: PlanWorld, gi: GenInputs, run: Run, u: Universe, ledger: dict) -> None:
    """Flips (flip now included) and buys for later, from one pool of first batches (SPEC §3.4D.3, PF28).

    Generation leaves out an item whose newest price is more than 24 h old, and a Buy for later whose item's best
    selling window has fewer than pattern_full_weeks ÷ 2 weeks. Every other item gets its first batch; only the
    best `candidate_pool` first batches by adjusted profit per copper (ties: candidate id) get their later batches
    and enter the universe, flips by key first, then buys for later. The rest never enter it.

    Args:
        left: The plan's world with the auctions the crafts bought taken out (flips buy from it).
        world: The plan's world.
        gi: Generation inputs.
        run: The run.
        u: The universe; opportunities and resources are added.
        ledger: The generation ledger (crafts' and your stock's posts); the kept flips' posts are added.
    """
    if not gi.chars:
        return
    char_id = flip_char(gi)
    pool, ungrounded = [], []
    for key in sorted(left.facts):
        if key not in left.no_buy and not stale(gi, key):
            rank = _flip_first(left.facts[key], char_id, ledger, run)
            if rank is not None:
                pool.append((rank, "flip", key, 0))
            else:
                ungrounded.append(key)
    for key in sorted(world.facts):
        f = world.facts[key]
        if key in world.no_buy or stale(gi, key) or f.level is None or not f.buy_windows or not f.windows:
            continue
        if f.windows[0].weeks < run.s.pattern_full_weeks / 2:
            continue                                # the trust gate would exclude it anyway (PF28)
        for _start, _end, best, _weeks in occurrences(f.buy_windows, run.now_ms, run.s.horizon_days):
            choice = _later_choice(f, best, ledger, run) if best > run.now_ms else None
            if choice is not None:
                pool.append((_later_rank(f, best, choice, char_id, run), "later", key, best))
    kept = sorted(pool)[:run.ps.candidate_pool]
    for _, _, key, _ in sorted((p for p in kept if p[1] == "flip"), key=lambda p: p[2]):
        _flip(left.facts[key], char_id, gi, run, u, ledger)
    for _, _, key, buy_ms in sorted((p for p in kept if p[1] == "later"), key=lambda p: (p[2], p[3])):
        f = world.facts[key]
        choice = _later_choice(f, buy_ms, ledger, run)
        opp = _later_opportunity(f, buy_ms, choice, char_id, ledger, gi, run, u) if choice is not None else None
        if opp is not None:
            u.opportunities.append(opp)
    for key in ungrounded:                          # outside the pool: excluded, they never take a slot
        _ungrounded_flip(left.facts[key], char_id, gi, run, u, ledger)


def _flip_opportunity(f: SellFacts, opts: Sequence[PostOption], steps: list, char_id: str, value: OptionValue,
                      seen: dict, gi: GenInputs, run: Run, u: Universe, no_listing: bool = False) -> Opportunity:
    """One item's flip as batches, one per auction.

    Args:
        f: The item.
        opts: Its post options.
        steps: flip_steps, in buying order.
        char_id: Who buys.
        value: The timed value the steps were chosen with.
        seen: The ledger the steps were sized against.
        gi: Generation inputs.
        run: The run.
        u: The universe (resources are added).
        no_listing: The steps relist into an empty book on thin data: the batches are excluded (R28).

    Returns:
        The Opportunity.
    """
    name = gi.names.get(f.key, str(f.key))
    later = f.p10 is not None and f.cur is not None and f.cur < f.p10 and opts[steps[0].option].window is not None
    strategy = "buy_later" if later else "flip"
    u.add_resource(f"ah:{f.key}", sum(stack for _, stack, _ in auctions(f.book, f.cur, f.cur_qty)), name)
    batches, units = [], 0
    for n, step in enumerate(steps, 1):
        o = opts[step.option]
        u.add_resource(cap_rid(f.key, o), o.cap, name)
        units += step.stack
        sale, cost = sale_ms(o, run.now_ms), step.price * step.stack
        cand = make_candidate(strategy, f.key, char_id, route_text({}, "now", timing_tag(sale, run.now_ms)), n, gi,
                          out_qty=step.stack, gold_buys=cost,
                          gold_deposits=fees.deposit_copper(f.vendor_sell, step.stack, o.duration_h),
                          expected_net=step.expected - cost, chance=step.p, exec_qty=step.stack, conf=f.conf,
                          buy_ms=run.now_ms, sale_ms=sale, capacity_claims=(CapacityClaim(f.key, window_id(o), step.stack),))
        weeks = o.weeks if later and o.window is not None and o.weeks < PATTERN_WEEKS else None
        batches.append(Batch(cand, eligibility_of(f.key, units, f, gi, o, untrusted_weeks=weeks, no_listing=no_listing),
                             claims_of([(f"ah:{f.key}", step.stack), (cap_rid(f.key, o), step.stack)])))

    def realize(n: int) -> list[PlanAction]:
        """The first n auctions' buy and posts.

        Args:
            n: Funded batches.

        Returns:
            flips.flip_actions of the flip cut to n auctions.
        """
        stage = build_flip(f, run.now_ms, run.s, run.fs, seen, None, n, value, cost_value=True)
        return flip_actions(f, stage, char_id, run.now_ms, run.s)
    return Opportunity(f"{strategy}:{f.key}:{char_id}", name, tuple(batches), realize)


# ---------------------------------------------------------------- Buy for later (in a buy window)

def _later_batches(f: SellFacts, o: PostOption, price: int, ahead: float, run: Run,
                   most: int | None = None) -> list[tuple[int, float, float]]:
    """A buy for later's stacks at one selling window: (units, expected value, chance) while each pays.

    Args:
        f: The item.
        o: The selling window's post option.
        price: The expected buy price, copper.
        ahead: Your units already planned for the window (the ledger).
        run: The run.
        most: The most stacks to size; None for all that pay.

    Returns:
        One entry per reference stack; none when even a sale of every unit couldn't pay min_flip_margin.
    """
    out, used = [], ahead
    alt = alternative(f)[1]
    if max(o.price * (1 - fees.AH_CUT), alt) < (1 + run.fs.min_flip_margin) * price:
        return out                                  # can't pay even if everything sells (no simulation needed)
    while used + f.ref_stack <= o.cap and (most is None or len(out) < most):
        p = post_chance(f, o.price, f.ref_stack, o.hours, used, run.s)
        e = sellprob.expected_post(p, f.ref_stack, o.price, alt,
                                   fees.deposit_copper(f.vendor_sell, f.ref_stack, o.duration_h))
        if e / f.ref_stack <= alt or not pays(e, price * f.ref_stack, run.fs.min_flip_margin):
            break
        out.append((f.ref_stack, e, p))
        used += f.ref_stack
    return out


def _later_choice(f: SellFacts, buy_ms: int, ledger: dict, run: Run) -> tuple | None:
    """Where a buy for later bought in one buy window occurrence sells: the window after it with the best first stack.

    Args:
        f: The item.
        buy_ms: The buy window's cheapest hour, UTC ms.
        ledger: The generation ledger; read only.
        run: The run.

    Returns:
        (the selling window's option, the expected buy price copper, (units, expected value, chance) of the first
        stack); None when no window after the buy pays.
    """
    price = slot_price(f.level, f.how[slot_of(buy_ms // HOUR_MS)], f.trend7, (buy_ms - run.now_ms) / DAY_MS,
                       run.s.max_trend_total, f.p10, f.med7, f.p90, f.closes_30d)
    opts = [o for o in post_options(f, run.now_ms, run.s, thin_windows=True)
            if o.window is not None and o.at_ms >= buy_ms]
    value = timed_value(opts, run)
    best = None
    for i, o in enumerate(opts):                    # the window is chosen by its first stack's adjusted profit
        first = _later_batches(f, o, price, ledger.get((f.key, window_id(o)), 0.0), run, 1)
        if first and (best is None or value(i, first[0][1] - price * first[0][0]) > best[0]):
            best = (value(i, first[0][1] - price * first[0][0]), o, first[0])
    return None if best is None else (best[1], price, best[2])


def _later_rank(f: SellFacts, buy_ms: int, choice: tuple, char_id: str, run: Run) -> tuple:
    """A buy for later's first stack ranked for the pool.

    Args:
        f: The item.
        buy_ms: When it buys.
        choice: From _later_choice.
        char_id: Who buys.
        run: The run.

    Returns:
        first_rank of its first batch.
    """
    o, price, (qty, e, _p) = choice
    route = route_text({}, timing_tag(buy_ms, run.now_ms), timing_tag(o.window[0], run.now_ms))
    return first_rank(price * qty + fees.deposit_copper(f.vendor_sell, qty, o.duration_h), e - price * qty,
                      o.window[0], candidate_id("buy_later", f.key, char_id, route, 1), run)


def _later_opportunity(f: SellFacts, buy_ms: int, choice: tuple, char_id: str, ledger: dict, gi: GenInputs, run: Run,
                       u: Universe) -> Opportunity | None:
    """One buy window occurrence's Buy for later, sold at its chosen window.

    Args:
        f: The item.
        buy_ms: The buy window's cheapest hour, UTC ms.
        choice: From _later_choice.
        char_id: Who buys.
        ledger: The generation ledger; read only.
        gi: Generation inputs.
        run: The run.
        u: The universe (resources are added).

    Returns:
        The Opportunity.
    """
    o, price, _first = choice
    stacks = _later_batches(f, o, price, ledger.get((f.key, window_id(o)), 0.0), run)
    name = gi.names.get(f.key, str(f.key))
    u.add_resource(cap_rid(f.key, o), o.cap, name)
    route = route_text({}, timing_tag(buy_ms, run.now_ms), timing_tag(o.window[0], run.now_ms))
    weeks = o.weeks if o.weeks < PATTERN_WEEKS else None
    batches = []
    for n, (qty, e, p) in enumerate(stacks, 1):
        cand = make_candidate("buy_later", f.key, char_id, route, n, gi, out_qty=qty, gold_buys=price * qty,
                              gold_deposits=fees.deposit_copper(f.vendor_sell, qty, o.duration_h),
                              expected_net=e - price * qty, chance=p, exec_qty=qty, conf=f.conf, buy_ms=buy_ms,
                              sale_ms=o.window[0], capacity_claims=(CapacityClaim(f.key, window_id(o), qty),))
        batches.append(Batch(cand, eligibility_of(f.key, n * qty, f, gi, o, untrusted_weeks=weeks),
                             ((cap_rid(f.key, o), qty),)))
    return Opportunity(f"buy_later:{f.key}:{char_id}:{route}", name, tuple(batches),
                       lambda n: _later_actions(f, o, stacks[:n], price, buy_ms, char_id))


def _later_actions(f: SellFacts, o: PostOption, stacks: list, price: int, buy_ms: int, char_id: str) -> list[PlanAction]:
    """A buy for later's actions: the buy in its window and the post for its selling window.

    Args:
        f: The item.
        o: The selling window's option.
        stacks: The funded (units, expected value, chance).
        price: The expected buy price, copper.
        buy_ms: When to buy.
        char_id: Who buys and posts.

    Returns:
        [buy, post] under chain "later:<key>:<buy hour>"; the buy carries the chain's expected profit.
    """
    units = sum(q for q, _, _ in stacks)
    expected = sum(e for _, e, _ in stacks)
    sold = sum(p * q for q, _, p in stacks)
    chain = f"later:{f.key}:{buy_ms // HOUR_MS}"
    start, end, best = o.window
    return [PlanAction("buy", char_id, f.key, units, price, buy_ms, None, None, None, None, expected,
                       expected - price * units, chain=chain, source="ah"),
            PlanAction("post", char_id, f.key, units, o.price, max(o.at_ms, buy_ms), start, end, best, sold / units,
                       expected, expected - price * units, o.duration_h, chain)]
