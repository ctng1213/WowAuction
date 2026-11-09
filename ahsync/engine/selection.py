"""The portfolio optimizer's selection, stage B (SPEC §3.4D.4-D.7, PF5-PF16, PF24-PF27).

Input is language-neutral: each candidate batch is a `Pick` (its gold needed, adjusted profit, list,
character, the resources it claims and, when a static check failed, its exclusion reason), and the
run's `Budget` (remaining capacity, each character's spendable gold, the list limits). The steps:

1. Greedy fill in order of adjusted profit per copper of exposure: batches that need no gold first by
   adjusted profit, ties by candidate id; within an opportunity batch n + 1 never sorts before batch n.
   A batch is funded when D.6's checks pass, in D.6's order: resources still free, gold on the
   account, gold on its character, room in its list, room in the remaining capacity.
2. One swap pass: each funded batch (as funded when the pass began) is tried against each batch
   excluded for a claimed resource (owned units or a window's capacity), gold on its character, a
   list limit or the remaining capacity, in step 1's order: remove it (with its later batches), add
   the excluded one, refill greedily; the best swap is kept when the total adjusted profit rises
   (ties: the earlier candidate id).
3. Every excluded batch gets the first failing D.6 reason against the final allocation.

`validate` is D.5: it raises on any violation, so a bad allocation is never published. Ported to
addon/AHProfit/Engine/Portfolio.lua (shared scenarios: tests/scenarios/portfolio.json); the swap
pass's pruning here is exact (it skips only swaps that provably can't win), so both languages fund
the same ids in the same order. Pure: money is integer copper, adjusted profit float copper.
"""
from __future__ import annotations

import math
from collections import ChainMap
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from ahsync.engine.prices import MIN_HOURLY_CLOSES
from ahsync.engine.views import money

LISTS = ("today", "week")
LIST_WORDS = {"today": "today", "week": "this week"}
SOFT = ("claimed", "gold_char", "limit", "risk")    # reasons a swap may clear (D.4 step 3: capacity, gold, limits)
REFILLABLE = SOFT
EPSILON = 1e-9                                 # relative slack that keeps the swap pruning conservative


class SelectionError(ValueError):
    """An allocation that breaks a D.5 rule: a bug, never published (SPEC §3.4D.5)."""


@dataclass(frozen=True)
class Resource:
    """Something a batch uses up: owned units, a window's market capacity, or units on the AH (SPEC §3.4D.3)."""

    rid: str                   # "own:<char>:<key>", "cap:<key>:<capacity group key|now>" (R32) or "ah:<key>"
    limit: int                 # units available
    name: str                  # the item's name, for exclusion reasons


@dataclass(frozen=True)
class Pick:
    """One candidate batch as selection sees it (SPEC §3.4D.3-D.4)."""

    cand_id: str
    opp: str                                       # its opportunity: batch n + 1 needs batch n funded
    batch: int                                     # 1-based
    char_id: str                                   # the acting character
    lst: str                                       # "today" | "week" (by the immediate window, D.4 step 4)
    gold: int                                      # gold needed, copper (its exposure)
    adjusted: float                                # adjusted profit, float copper
    net: float = 0.0                               # expected net, float copper (for the totals)
    name: str = ""                                 # its row's label, e.g. "Copper Rod"
    reason: str | None = None                      # a failed static check (eligibility), else None
    claims: tuple[tuple[str, int], ...] = ()       # (resource id, units), by resource id


@dataclass(frozen=True)
class Budget:
    """What one selection run may spend (SPEC §3.4D.1)."""

    capacity: int                                  # remaining capacity at the start of the run, copper
    spendable: tuple[tuple[str, int], ...]         # (character, spendable gold copper), by character
    today_limit: int = 20
    week_limit: int = 20
    swap_candidates: int = 50                      # the excluded batches the swap pass tries, best adjusted first


@dataclass(frozen=True)
class Selection:
    """The result of one run (SPEC §3.4D.4)."""

    funded: tuple[str, ...]                        # candidate ids in funding order
    reasons: tuple[tuple[str, str], ...]           # (candidate id, exclusion reason) of the rest, by id
    capacity_left: int                             # copper the funded batches leave of the capacity
    reserved: tuple[tuple[str, int], ...]          # (character, copper its funded batches need), by character
    adjusted: float                                # total adjusted profit of the funded batches
    net: float                                     # total expected net of the funded batches
    gold: int                                      # total gold needed of the funded batches

    def reason_of(self, cand_id: str) -> str | None:
        """An excluded batch's reason.

        Args:
            cand_id: The candidate id.

        Returns:
            Its D.6 status text; None when it is funded.
        """
        return dict(self.reasons).get(cand_id)


@dataclass
class _State:
    """The allocation while it is being filled (mutable; each swap trial starts from a copy, _trial)."""

    capacity_left: int
    spend_left: dict[str, int]
    res_left: dict[str, int] | ChainMap        # resource id -> units left (copy-on-write in a trial)
    holders: dict[str, str] = field(default_factory=dict)          # resource id -> first funded row's name
    counts: dict[str, int] = field(default_factory=dict)           # list -> funded opportunities
    last_batch: dict[str, int] = field(default_factory=dict)       # opportunity -> its last funded batch
    funded: list[Pick] = field(default_factory=list)               # in funding order

    def total(self) -> float:
        """Total adjusted profit, summed in funding order (the same float in Lua).

        Returns:
            Float copper.
        """
        out = 0.0
        for p in self.funded:
            out += p.adjusted
        return out


def short_name(char_id: str) -> str:
    """A character's name without its realm (SPEC §3.4D.6 texts).

    Args:
        char_id: E.g. "Amaya-Grommash".

    Returns:
        E.g. "Amaya".
    """
    return char_id.split("-")[0]


def order_key(p: Pick) -> tuple[int, float]:
    """A batch's place in the greedy fill before tie-breaks (SPEC §3.4D.4 step 2).

    Args:
        p: The batch.

    Returns:
        (0, −adjusted) for a batch that needs no gold, else (1, −adjusted ÷ gold): smaller sorts first.
    """
    if p.gold <= 0:
        return 0, -p.adjusted
    return 1, -p.adjusted / p.gold


def fill_order(picks: Sequence[Pick]) -> list[Pick]:
    """The greedy fill's order (SPEC §3.4D.4 step 2).

    Adjusted profit per copper of exposure, batches needing no gold first by adjusted profit, ties by
    candidate id. Within an opportunity batch n + 1 never comes before batch n: its key is the worse of
    its own and batch n's, and the places an opportunity's batches take are filled in batch order.

    Args:
        picks: The batches, any order.

    Returns:
        The batches in fill order.

    Raises:
        SelectionError: Two batches with one id, or an opportunity whose batches aren't 1..n.
    """
    if len({p.cand_id for p in picks}) != len(picks):
        raise SelectionError("candidate ids must be unique")
    by_opp: dict[str, list[Pick]] = {}
    for p in picks:
        by_opp.setdefault(p.opp, []).append(p)
    keyed = []
    for opp in sorted(by_opp):
        batches = sorted(by_opp[opp], key=lambda p: p.batch)
        if [p.batch for p in batches] != list(range(1, len(batches) + 1)):
            raise SelectionError(f"opportunity {opp} must have batches 1..n")
        worst = None
        for p in batches:
            key = order_key(p)
            worst = key if worst is None or key > worst else worst
            keyed.append((worst, p.cand_id, p))
    ordered = [p for _, _, p in sorted(keyed, key=lambda k: (k[0], k[1]))]
    slots: dict[str, list[int]] = {}
    for i, p in enumerate(ordered):
        slots.setdefault(p.opp, []).append(i)
    out: list[Pick] = list(ordered)
    for opp, places in slots.items():
        for place, p in zip(places, sorted((ordered[i] for i in places), key=lambda p: p.batch)):
            out[place] = p
    return out


def _claim_reason(state: _State, p: Pick, resources: Mapping[str, Resource]) -> str | None:
    """The first resource a batch claims that isn't free any more (D.6 "Resources already claimed").

    Args:
        state: The allocation.
        p: The batch.
        resources: Resource id -> Resource.

    Returns:
        "Resources already claimed: 6 Copper Bar (Copper Rod)" (owned units), "… Copper Rod capacity (…)"
        (market capacity) or "… 20 Linen Cloth on the AH (…)"; None when every claim fits.
    """
    for rid, units in p.claims:
        if units > state.res_left[rid]:
            r = resources[rid]
            what = {"own": f"{units} {r.name}", "cap": f"{r.name} capacity"}.get(rid.split(":")[0],
                                                                                 f"{units} {r.name} on the AH")
            holder = state.holders.get(rid)
            return f"Resources already claimed: {what}" + (f" ({holder})" if holder else "")
    return None


def check(state: _State, p: Pick, resources: Mapping[str, Resource], budget: Budget,
          total_spendable: int, claims: bool = True) -> tuple[str, str] | None:
    """Whether a batch can be funded now, and if not the first failing check in D.6's order (SPEC §3.4D.6).

    Args:
        state: The allocation so far.
        p: The batch.
        resources: Resource id -> Resource.
        budget: The run's budget (list limits).
        total_spendable: All characters' spendable gold, copper.
        claims: False skips the claims check (past the point where the fill stopped, _stop_index).

    Returns:
        None when it fits; else (category, status text). Categories: "static" (its precomputed reason),
        "order" (batch n isn't funded; the text is empty and the caller inherits batch n's), "claimed",
        "gold_all", "gold_char", "limit", "risk".
    """
    if p.reason is not None:
        return "static", p.reason
    if p.batch > 1 and state.last_batch.get(p.opp, 0) != p.batch - 1:
        return "order", ""
    claimed = _claim_reason(state, p, resources) if claims else None
    if claimed is not None:
        return "claimed", claimed
    if p.gold > total_spendable:
        return "gold_all", f"Insufficient gold (needs {money(p.gold)}, all characters hold {money(total_spendable)})"
    left = state.spend_left.get(p.char_id, 0)
    if p.gold > left:
        return "gold_char", f"Insufficient gold on {short_name(p.char_id)} (needs {money(p.gold)}, has {money(max(0, left))})"
    limit = budget.today_limit if p.lst == "today" else budget.week_limit
    if p.opp not in state.last_batch and state.counts.get(p.lst, 0) >= limit:
        return "limit", f"Recommendation limit reached ({limit} of {limit} {LIST_WORDS[p.lst]})"
    if p.gold > state.capacity_left:
        return "risk", f"Exceeds risk tolerance (needs {money(p.gold)}, {money(state.capacity_left)} left)"
    return None


def _fund(state: _State, p: Pick) -> None:
    """Fund a batch: spend its gold, take its resources and its list slot.

    Args:
        state: The allocation; changed in place.
        p: The batch (check passed).
    """
    state.capacity_left -= p.gold
    state.spend_left[p.char_id] = state.spend_left.get(p.char_id, 0) - p.gold
    for rid, units in p.claims:
        state.res_left[rid] -= units
        state.holders.setdefault(rid, p.name)
    if p.opp not in state.last_batch:
        state.counts[p.lst] = state.counts.get(p.lst, 0) + 1
    state.last_batch[p.opp] = p.batch
    state.funded.append(p)


def _unfund(state: _State, gone: Sequence[Pick]) -> None:
    """Take funded batches out again (a swap), giving back their gold, resources and slots.

    Args:
        state: The allocation; changed in place.
        gone: Funded batches: one batch and every later batch of its opportunity.
    """
    ids = {p.cand_id for p in gone}
    for p in gone:
        state.capacity_left += p.gold
        state.spend_left[p.char_id] += p.gold
        for rid, units in p.claims:
            state.res_left[rid] += units
    state.funded = [p for p in state.funded if p.cand_id not in ids]
    opp, first = gone[0].opp, min(p.batch for p in gone)
    if first == 1:
        del state.last_batch[opp]
        state.counts[gone[0].lst] -= 1
    else:
        state.last_batch[opp] = first - 1
    state.holders = {}
    for p in state.funded:
        for rid, _ in p.claims:
            state.holders.setdefault(rid, p.name)


def _fill(state: _State, order: Sequence[Pick], resources: Mapping[str, Resource], budget: Budget,
          total_spendable: int) -> None:
    """The greedy fill (SPEC §3.4D.4 step 2): fund every batch, in order, that passes its checks.

    Args:
        state: The allocation; changed in place.
        order: Batches in fill order.
        resources: Resource id -> Resource.
        budget: The run's budget.
        total_spendable: All characters' spendable gold, copper.
    """
    funded = {p.cand_id for p in state.funded}
    limits = {"today": budget.today_limit, "week": budget.week_limit}
    lowest = _lowest_gold(order)
    for i, p in enumerate(order):
        if _full(state, budget) and state.capacity_left < lowest[i]:
            return                                  # nothing left can enter: stop early (D.4)
        if p.cand_id in funded or (p.opp not in state.last_batch and state.counts.get(p.lst, 0) >= limits[p.lst]):
            continue                                # funded, or a new row in a full list (check would say so)
        if check(state, p, resources, budget, total_spendable) is None:
            _fund(state, p)
            funded.add(p.cand_id)


def _lowest_gold(order: Sequence[Pick]) -> list[int]:
    """For each place in a fill order, the least gold any batch from there on needs.

    Args:
        order: Batches in fill order.

    Returns:
        One value per place, copper.
    """
    out, low = [0] * len(order), None
    for i in range(len(order) - 1, -1, -1):
        low = order[i].gold if low is None else min(low, order[i].gold)
        out[i] = low
    return out


def _full(state: _State, budget: Budget) -> bool:
    """Whether both lists hold their limit of rows.

    Args:
        state: The allocation.
        budget: The run's budget.

    Returns:
        True when today and the week are both full.
    """
    return state.counts.get("today", 0) >= budget.today_limit and state.counts.get("week", 0) >= budget.week_limit


def _stop_index(state: _State, order: Sequence[Pick], budget: Budget) -> int:
    """Where the fill stops against an allocation: both lists full and the capacity left below every later batch's gold.

    Args:
        state: The allocation.
        order: Batches in fill order.
        budget: The run's budget.

    Returns:
        The first such place; len(order) when the fill never stops early. Batches from there on get their
        status without the claims check (D.4).
    """
    if not _full(state, budget):
        return len(order)
    lowest = _lowest_gold(order)
    return next((i for i in range(len(order)) if state.capacity_left < lowest[i]), len(order))


def _suffix_sums(order: Sequence[Pick]) -> dict[str, float]:
    """For each batch, the positive adjusted profit of its opportunity's later batches.

    Args:
        order: All batches.

    Returns:
        Candidate id -> float copper.
    """
    by_opp: dict[str, list[Pick]] = {}
    for p in order:
        by_opp.setdefault(p.opp, []).append(p)
    out = {}
    for batches in by_opp.values():
        tail = 0.0
        for p in sorted(batches, key=lambda p: -p.batch):
            out[p.cand_id] = tail
            tail += max(0.0, p.adjusted)
    return out


def _fits_without(state: _State, gone: Sequence[Pick], x: Pick, budget: Budget) -> bool:
    """Whether a batch could pass its dynamic checks once `gone` is unfunded: check() on the trial, cheaply.

    It is exact for the checks a swap can change (claims, gold on the character, the list, the capacity), so a
    trial it rules out would fail anyway; it only saves copying the allocation.

    Args:
        state: The allocation before the trial.
        gone: The batches the trial removes (one opportunity's).
        x: The excluded batch (soft category, another opportunity).
        budget: The run's budget (list limits).

    Returns:
        False when the trial's check would certainly fail.
    """
    freed: dict[str, int] = {}
    for p in gone:
        for rid, units in p.claims:
            freed[rid] = freed.get(rid, 0) + units
    if any(units > state.res_left[rid] + freed.get(rid, 0) for rid, units in x.claims):
        return False
    gold = sum(p.gold for p in gone)
    mine = sum(p.gold for p in gone if p.char_id == x.char_id)
    if x.gold > state.spend_left.get(x.char_id, 0) + mine or x.gold > state.capacity_left + gold:
        return False
    if x.opp in state.last_batch:
        return True
    limit = budget.today_limit if x.lst == "today" else budget.week_limit
    emptied = min(p.batch for p in gone) == 1 and gone[0].lst == x.lst
    return state.counts.get(x.lst, 0) - (1 if emptied else 0) < limit


@dataclass
class _Survey:
    """What the swap pass knows about the allocation its trials start from (recomputed after a kept swap)."""

    soft: list[Pick]          # unfunded batches excluded for a reason a swap may clear, in fill order
    ratio: float              # best adjusted ÷ gold of a funded or refillable batch that needs gold (0 when none)
    free: float               # Σ positive adjusted of funded or refillable batches that need no gold
    cont: float               # Σ positive adjusted of unfunded batches whose opportunity has a funded batch
    cont_picks: list[Pick]    # those batches, in fill order


def _survey(state: _State, order: Sequence[Pick], resources: Mapping[str, Resource], budget: Budget,
            total_spendable: int) -> _Survey:
    """Classify every unfunded batch once per allocation, for the swap pass's candidates and its bounds.

    Args:
        state: The allocation (maximal: nothing unfunded fits).
        order: Batches in fill order.
        resources: Resource id -> Resource.
        budget: The run's budget.
        total_spendable: All characters' spendable gold, copper.

    Returns:
        The survey; the funded batches count as refillable, since a trial removes some.
    """
    funded = {p.cand_id for p in state.funded}
    soft, cont_picks, ratio, free, cont = [], [], 0.0, 0.0, 0.0
    for p in order:
        category = None
        if p.cand_id not in funded:
            failed = check(state, p, resources, budget, total_spendable)
            category = failed[0] if failed is not None else "fits"
            if category in SOFT:
                soft.append(p)
            if p.opp in state.last_batch:
                cont_picks.append(p)
                cont += max(0.0, p.adjusted)
        if p.cand_id in funded or category in REFILLABLE + ("order",):
            if p.gold > 0:
                ratio = max(ratio, p.adjusted / p.gold)
            elif p.adjusted > 0:
                free += p.adjusted
    return _Survey(soft, ratio, free, cont, cont_picks)


def _lists_full_after(state: _State, gone: Sequence[Pick], x: Pick, budget: Budget) -> bool:
    """Whether both lists are full once `gone` is unfunded and x is funded: then no new row can enter a refill.

    Args:
        state: The allocation before the trial.
        gone: The batches the trial removes (one opportunity's, from one batch on).
        x: The batch the trial adds.
        budget: The run's budget.

    Returns:
        True when today and the week both hold their limit of rows after the swap.
    """
    counts = dict(state.counts)
    if min(p.batch for p in gone) == 1:
        counts[gone[0].lst] = counts.get(gone[0].lst, 0) - 1
    if x.opp not in state.last_batch:
        counts[x.lst] = counts.get(x.lst, 0) + 1
    return counts.get("today", 0) >= budget.today_limit and counts.get("week", 0) >= budget.week_limit


def _refill_bound(survey: _Survey, gone: Sequence[Pick], x: Pick, freed_gold: int, full: bool,
                  suffix: Mapping[str, float]) -> float:
    """An upper bound on what a trial's refill can add (the swap pass's exact pruning).

    Refilled batches that need gold use at most the gold the trial frees beyond x's, each earning at most the
    best ratio; those that need none add at most their sum. With both lists full only batches of rows already
    funded (and the removed ones, and x's later batches) can enter.

    Args:
        survey: The allocation's survey.
        gone: The batches the trial removes.
        x: The batch the trial adds.
        freed_gold: Capacity left plus the removed batches' gold, copper.
        full: _lists_full_after.
        suffix: Candidate id -> Σ positive adjusted of its opportunity's later batches.

    Returns:
        Float copper.
    """
    by_gold = survey.free + survey.ratio * max(0, freed_gold - x.gold)
    if not full:
        return by_gold
    return min(by_gold, survey.cont + sum(max(0.0, p.adjusted) for p in gone) + suffix[x.cand_id])


def _trial(state: _State, gone: Sequence[Pick], x: Pick, refill: Sequence[Pick], resources: Mapping[str, Resource],
           budget: Budget, total_spendable: int) -> _State:
    """One swap: remove `gone`, add x, refill greedily from `refill` (in fill order).

    Args:
        state: The allocation before the trial; not changed (resources are copied on write).
        gone: The batches it removes.
        x: The batch it adds (it fits, _fits_without).
        refill: The batches the refill walks.
        resources: Resource id -> Resource.
        budget: The run's budget.
        total_spendable: All characters' spendable gold, copper.

    Returns:
        The allocation after the swap.
    """
    trial = _State(state.capacity_left, dict(state.spend_left), ChainMap({}, state.res_left), dict(state.holders),
                   dict(state.counts), dict(state.last_batch), list(state.funded))
    _unfund(trial, gone)
    _fund(trial, x)
    _fill(trial, refill, resources, budget, total_spendable)
    return trial


def _swap_pass(state: _State, order: Sequence[Pick], resources: Mapping[str, Resource], budget: Budget,
               total_spendable: int) -> _State:
    """One swap pass with greedy refill (SPEC §3.4D.4 step 3, PF5, PF11).

    Each funded batch (as funded when the pass began) is tried against each batch excluded for a claimed
    resource, gold on its character, a list limit or the capacity, in fill order; the best trial is kept when
    it raises the total adjusted profit (ties: the earlier candidate id). Trials that provably can't win or
    can't fit are skipped (exact: the Lua twin tries them all and gets the same result).

    Args:
        state: The greedy allocation.
        order: Batches in fill order.
        resources: Resource id -> Resource.
        budget: The run's budget.
        total_spendable: All characters' spendable gold, copper.

    Returns:
        The allocation after the pass (the input when no swap raised the total).
    """
    suffix = _suffix_sums(order)
    place = {p.cand_id: i for i, p in enumerate(order)}
    batches: dict[str, list[Pick]] = {}
    for p in order:
        batches.setdefault(p.opp, []).append(p)
    survey = _survey(state, order, resources, budget, total_spendable)
    tried = _tried(survey.soft, budget.swap_candidates, place)
    for f in list(state.funded):
        if f not in state.funded:
            continue
        gone = [p for p in state.funded if p.opp == f.opp and p.batch >= f.batch]
        freed_gold = state.capacity_left + sum(p.gold for p in gone)
        freed_adj = sum(p.adjusted for p in gone)
        current = state.total()
        best: tuple[float, str, _State] | None = None
        for x in tried:
            if x.opp == f.opp or not _fits_without(state, gone, x, budget):
                continue
            best_total = best[0] if best else current
            full = _lists_full_after(state, gone, x, budget)
            bound = current - freed_adj + x.adjusted + _refill_bound(survey, gone, x, freed_gold, full, suffix)
            if bound < best_total - EPSILON * max(1.0, abs(best_total)):
                continue                            # can't beat the best: exact pruning (Python only)
            refill = order
            if full:                                # only batches of funded rows can enter
                later = [p for p in batches[x.opp] if p.batch > x.batch]
                refill = sorted({p.cand_id: p for p in survey.cont_picks + list(gone) + later}.values(),
                                key=lambda p: place[p.cand_id])
            trial = _trial(state, gone, x, refill, resources, budget, total_spendable)
            total = trial.total()
            if total > best_total or (best is not None and total == best[0] and x.cand_id < best[1]):
                best = (total, x.cand_id, trial)
        if best is not None and best[0] > current:
            state = best[2]
            state.res_left = dict(state.res_left)
            survey = _survey(state, order, resources, budget, total_spendable)
            tried = _tried(survey.soft, budget.swap_candidates, place)
    return state


def _tried(soft: Sequence[Pick], most: int, place: Mapping[str, int]) -> list[Pick]:
    """The excluded batches a swap pass tries: the best `most` by adjusted profit (ties: candidate id), in fill order.

    Args:
        soft: Batches excluded for claims, gold on the character, a list limit or the capacity.
        most: Config swap_candidates.
        place: Candidate id -> its place in the fill order.

    Returns:
        At most `most` batches, in fill order.
    """
    best = sorted(soft, key=lambda p: (-p.adjusted, p.cand_id))[:most]
    return sorted(best, key=lambda p: place[p.cand_id])


def _reasons(state: _State, order: Sequence[Pick], resources: Mapping[str, Resource], budget: Budget,
             total_spendable: int) -> dict[str, str]:
    """Every unfunded batch's D.6 status against the final allocation (SPEC §3.4D.6).

    Args:
        state: The final allocation (maximal: nothing unfunded fits).
        order: Batches in fill order.
        resources: Resource id -> Resource.
        budget: The run's budget.
        total_spendable: All characters' spendable gold, copper.

    Returns:
        Candidate id -> status; a batch whose batch n isn't funded takes batch n's status.
    """
    funded = {p.cand_id for p in state.funded}
    by_batch = {(p.opp, p.batch): p for p in order}
    stop = _stop_index(state, order, budget)
    place = {p.cand_id: i for i, p in enumerate(order)}
    out: dict[str, str] = {}
    for p in sorted((p for p in order if p.cand_id not in funded), key=lambda p: (p.opp, p.batch)):
        failed = check(state, p, resources, budget, total_spendable, place[p.cand_id] < stop)
        if failed is None:
            raise SelectionError(f"{p.cand_id} fits but was left out")
        out[p.cand_id] = out[by_batch[(p.opp, p.batch - 1)].cand_id] if failed[0] == "order" else failed[1]
    return out


def select(picks: Sequence[Pick], resources: Iterable[Resource], budget: Budget) -> Selection:
    """Choose the funded batches (SPEC §3.4D.4 steps 1-3): greedy fill, one swap pass, reasons.

    Args:
        picks: Every candidate batch, any order (the result doesn't depend on it, PF27).
        resources: Every resource a batch claims.
        budget: The run's remaining capacity, spendable gold per character and list limits.

    Returns:
        The Selection.

    Raises:
        SelectionError: Duplicate ids, an opportunity without batches 1..n, a list other than today
            or week, or a claim on an unknown resource.
    """
    res = {r.rid: r for r in resources}
    for p in picks:
        if p.lst not in LISTS:
            raise SelectionError(f"{p.cand_id}: list must be today or week, got {p.lst!r}")
        if any(rid not in res for rid, _ in p.claims):
            raise SelectionError(f"{p.cand_id} claims an unknown resource")
    order = fill_order(picks)
    spendable = dict(budget.spendable)
    total_spendable = sum(spendable.values())
    state = _State(budget.capacity, dict(spendable), {rid: r.limit for rid, r in res.items()})
    _fill(state, order, res, budget, total_spendable)
    state = _swap_pass(state, order, res, budget, total_spendable)
    reasons = _reasons(state, order, res, budget, total_spendable)
    reserved: dict[str, int] = {}
    net, gold = 0.0, 0
    for p in state.funded:
        reserved[p.char_id] = reserved.get(p.char_id, 0) + p.gold
        net += p.net
        gold += p.gold
    return Selection(tuple(p.cand_id for p in state.funded), tuple(sorted(reasons.items())), state.capacity_left,
                     tuple(sorted((c, g) for c, g in reserved.items() if g)), state.total(), net, gold)


def validate(picks: Sequence[Pick], resources: Iterable[Resource], budget: Budget, result: Selection) -> None:
    """Check an allocation before it is published (SPEC §3.4D.5, PF25); a failure is a bug.

    Args:
        picks: Every candidate batch.
        resources: Every resource.
        budget: The run's budget.
        result: The selection to check.

    Raises:
        SelectionError: The funded gold is over the capacity or a character's spendable gold; a resource
            (owned units, a window's liquidity cap, AH units) is claimed beyond its limit; a funded batch
            failed a static check; a list holds more rows than its limit; batch n + 1 is funded without
            batch n; a total doesn't equal the sum of the funded batches; or an id is both funded and
            excluded, or neither.
    """
    by_id = {p.cand_id: p for p in picks}
    funded = [by_id[i] for i in result.funded]
    _validate_money(funded, budget, result)
    left = {r.rid: r.limit for r in resources}
    rows: dict[str, set[str]] = {lst: set() for lst in LISTS}
    batches: dict[str, set[int]] = {}
    for p in funded:
        if p.reason is not None:
            raise SelectionError(f"{p.cand_id} is funded but ineligible: {p.reason}")
        for rid, units in p.claims:
            left[rid] -= units
            if left[rid] < 0:
                raise SelectionError(f"{rid} is claimed beyond its limit")
        rows[p.lst].add(p.opp)
        batches.setdefault(p.opp, set()).add(p.batch)
    for opp, got in batches.items():
        if got != set(range(1, max(got) + 1)):
            raise SelectionError(f"{opp}: a later batch is funded without an earlier one")
    if len(rows["today"]) > budget.today_limit or len(rows["week"]) > budget.week_limit:
        raise SelectionError("a list holds more rows than its limit")
    if set(result.funded) & {i for i, _ in result.reasons} or len(result.funded) + len(result.reasons) != len(by_id):
        raise SelectionError("every batch must be funded or excluded, never both")


def _validate_money(funded: Sequence[Pick], budget: Budget, result: Selection) -> None:
    """D.5's gold, exposure and totals rules.

    Args:
        funded: The funded batches.
        budget: The run's budget.
        result: The selection.

    Raises:
        SelectionError: On a violation.
    """
    spendable = dict(budget.spendable)
    by_char: dict[str, int] = {}
    for p in funded:
        by_char[p.char_id] = by_char.get(p.char_id, 0) + p.gold
    gold = sum(p.gold for p in funded)
    if gold > budget.capacity or result.capacity_left != budget.capacity - gold:
        raise SelectionError("the funded gold is over the remaining capacity")
    if any(g > spendable.get(c, 0) for c, g in by_char.items() if g > 0):
        raise SelectionError("a character's funded gold is over its spendable gold")
    adjusted, net = 0.0, 0.0
    for p in funded:
        adjusted += p.adjusted
        net += p.net
    if (result.gold, result.adjusted, result.net) != (gold, adjusted, net) or \
            dict(result.reserved) != {c: g for c, g in by_char.items() if g}:
        raise SelectionError("a total is not the sum of the funded batches")


# ---------------------------------------------------------------- eligibility (§3.4D.6, static checks)

@dataclass(frozen=True)
class Eligibility:
    """What the static checks read about one candidate batch (SPEC §3.4D.6, first six rows)."""

    profession: str | None = None          # the profession it needs; None when it needs none
    skill: int = 0                         # the skill it needs
    actor_skill: int | None = None         # the acting character's skill in it; None without the profession
    best: tuple[str, int] | None = None    # (character, skill) of the best one with the profession, for the detail
    untrusted_weeks: int | None = None     # weeks of data behind a window that failed the trust gate (D.2)
    price_age_ms: int | None = None        # how old the item's newest price is
    sold_per_day: float | None = None      # the item's sales rate; None when unknown (no check)
    planned_units: int = 0                 # units its opportunity plans up to and including this batch
    missing: tuple[int, str] | None = None # (units, item name) of a mat nobody can source
    window_closes: int | None = None       # hourly closes in 30 d of an item it sells at a window; None when it
                                           # sells now (only a window sale needs 24, PF30)
    volume_unknown: str | None = None      # whose sales rate is unknown: "" the item's, else a disenchant's main
                                           # mat's name; None when known (PF30)
    no_listing: bool = False               # it sells now an item you don't own into an empty book on thin data (R28)


STALE_MS = 24 * 3_600_000
MIN_WINDOW_CLOSES = MIN_HOURLY_CLOSES       # hourly closes in 30 days a sale at a window needs (PF30)


def per_day(rate: float) -> str:
    """A sales rate as the liquidity reason writes it: one decimal, half up, without a trailing .0.

    Args:
        rate: Units a day.

    Returns:
        E.g. "3" or "2.7".
    """
    tenths = math.floor(rate * 10 + 0.5)
    return str(tenths // 10) if tenths % 10 == 0 else f"{tenths // 10}.{tenths % 10}"


def age_text(age_ms: int) -> str:
    """A price's age for "Forecast too stale".

    Args:
        age_ms: Age, ms.

    Returns:
        Whole hours under 48 h ("30 h"), else whole days ("3 days").
    """
    hours = age_ms // 3_600_000
    return f"{hours} h" if hours < 48 else f"{hours // 24} days"


def static_reason(e: Eligibility, horizon_days: int) -> str | None:
    """The first static check a batch fails, in D.6's order (SPEC §3.4D.6, PF2, PF9, PF20, PF24, PF30).

    Args:
        e: What the checks read.
        horizon_days: Config horizon_days.

    Returns:
        "Required profession unavailable: Enchanting 150", "Forecast too uncertain (4 closes in 30 d, needs 24)"
        (a window sale on thin data) or "Forecast too uncertain (1 week of data)", "Forecast too stale (prices
        30 h old)", "Sales volume unknown" or "Sales volume unknown (Strange Dust)", "No listing left to price
        against", "Insufficient liquidity: 3/day, 40 planned" or "Missing materials: 2 Iron Bar"; None when
        every static check passes.
    """
    if e.profession is not None and (e.actor_skill is None or e.actor_skill < e.skill):
        return f"Required profession unavailable: {e.profession} {e.skill}"
    if e.window_closes is not None and e.window_closes < MIN_WINDOW_CLOSES:
        n = e.window_closes
        return f"Forecast too uncertain ({n} close{'s' if n != 1 else ''} in 30 d, needs {MIN_WINDOW_CLOSES})"
    if e.untrusted_weeks is not None:
        return f"Forecast too uncertain ({e.untrusted_weeks} week{'s' if e.untrusted_weeks != 1 else ''} of data)"
    if e.price_age_ms is not None and e.price_age_ms > STALE_MS:
        return f"Forecast too stale (prices {age_text(e.price_age_ms)} old)"
    if e.volume_unknown is not None:
        return f"Sales volume unknown ({e.volume_unknown})" if e.volume_unknown else "Sales volume unknown"
    if e.no_listing:
        return "No listing left to price against"
    if e.sold_per_day is not None and e.planned_units > horizon_days * e.sold_per_day:
        return f"Insufficient liquidity: {per_day(e.sold_per_day)}/day, {e.planned_units} planned"
    if e.missing is not None:
        return f"Missing materials: {e.missing[0]} {e.missing[1]}"
    return None


def profession_detail(e: Eligibility) -> str:
    """The detail under a "Required profession unavailable" status (SPEC §3.4D.6, PF2, PF9).

    Args:
        e: The batch's eligibility facts.

    Returns:
        "Amaya has Enchanting 142" for the best character with the profession, else
        "no character has Enchanting"; "" when it needs no profession.
    """
    if e.profession is None:
        return ""
    if e.best is None:
        return f"no character has {e.profession}"
    return f"{short_name(e.best[0])} has {e.profession} {e.best[1]}"


# The export's reason codes (SPEC §3.5 `K` status, §3.4D.6), in D.6's order: a status text's start -> its code.
REASON_CODES: tuple[tuple[str, str], ...] = (
    ("Required profession unavailable", "profession"),
    ("Forecast too uncertain", "uncertain"),
    ("Forecast too stale", "stale"),
    ("Sales volume unknown", "volume"),
    ("No listing left to price against", "no_listing"),
    ("Insufficient liquidity", "liquidity"),
    ("Missing materials", "missing"),
    ("Resources already claimed", "claimed"),
    ("Insufficient gold (", "gold_all"),
    ("Insufficient gold on ", "gold_char"),
    ("Recommendation limit reached", "limit"),
    ("Exceeds risk tolerance", "risk"),
)


def reason_code(status: str | None) -> str:
    """A §3.4D.6 status as the export's short code (SPEC §3.5 `K`, EX17).

    Args:
        status: The exclusion reason's text; None or "" for a funded row.

    Returns:
        "" for a funded row; else its code, e.g. "risk" for "Exceeds risk tolerance (needs 45s, 24s 40c left)".

    Raises:
        ValueError: A status that is none of D.6's.
    """
    if not status:
        return ""
    for start, code in REASON_CODES:
        if status.startswith(start):
            return code
    raise ValueError(f"not a D.6 status: {status!r}")


# ---------------------------------------------------------------- empty states (§3.4D.7)

def empty_state(characters: int, cash_gold: int, committed: int, risk_budget: int, lists_full: bool,
                prices_stale: bool, any_pays: bool) -> str:
    """Why nothing new is funded, the first matching reason (SPEC §3.4D.7, PF14).

    Args:
        characters: How many characters AHSync knows.
        cash_gold: Their gold in hand, copper.
        committed: Committed capital, copper.
        risk_budget: The exposure limit, copper.
        lists_full: Both lists hold their limit of funded rows.
        prices_stale: The newest prices are more than 24 h old.
        any_pays: Some candidate nets above 0.

    Returns:
        The reason's text, e.g. "No new opportunities: risk budget exhausted (10g 67s 40c limit, 10g 88s committed)".
    """
    if characters and not cash_gold:
        return "No new opportunities: no gold in hand"
    if not characters:
        return "No characters yet"
    if committed >= risk_budget:
        return f"No new opportunities: risk budget exhausted ({money(risk_budget)} limit, {money(committed)} committed)"
    if lists_full:
        return "No new opportunities: recommendation limit reached"
    if prices_stale:
        return "No new opportunities: market data too old"
    if not any_pays:
        return "No new opportunities: nothing pays"
    return "No new opportunities: every candidate is excluded"
