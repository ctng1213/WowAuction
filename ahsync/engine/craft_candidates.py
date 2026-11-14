"""The portfolio's Craft, Multistage craft and Disenchant candidates (SPEC §3.4B, §3.4C.3, §3.4A, §3.4D.3).

A craft batch is one craft of craft.craft_option, crafted by craft.gather's choice; recipes are taken
best expected profit × confidence first and each sees the owned mats and AH units the better ones left,
so two chains never plan the same bars. A recipe with crafted reagents gets two routes when they differ,
which share one unit of capacity so only one is funded (PF4); a Multistage candidate carries its mail
steps (PF12). A disenchant batch is one auction of an item, for every disenchantable item, enchanter or
not (PF2). Shared types and helpers are engine.candidates'. Pure.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace

from ahsync.engine import craft, fees
from ahsync.engine.candidates import (Batch, DeInfo, GenInputs, Opportunity, Run, Universe, cap_rid, claims_of,
                                      eligibility_of, make_candidate, sale_ms, stale, timed_value, ungrounded,
                                      window_end)
from ahsync.engine.craft_plan import (best_sale, chain_actions, consume, craft_option_for, intermediate_buys,
                                      units_made, use_held)
from ahsync.engine.plan import OptionValue, PlanAction, PostOption, SellFacts, post_options, window_id
from ahsync.engine.portfolio import CapacityClaim, OwnedClaim, Requirements, route_text, timing_tag
from ahsync.engine.selection import Eligibility
from ahsync.engine.craft_plan import CraftWorld
from ahsync.engine.week import PlanWorld

STEP_MS = 60_000


# ---------------------------------------------------------------- Craft and Multistage craft

def crafts(world: PlanWorld, gi: GenInputs, run: Run, u: Universe, ledger: dict) -> craft.Market:
    """Crafts: each craft of each known recipe, by each reagent route (SPEC §3.4B, §3.4C.3, §3.4D.3, PF4, PF12).

    Recipes are taken best expected profit × confidence first, and each sees the owned mats and AH units the
    better ones left ("a unit claimed by a better candidate is gone", §3.4D.4), as the plan always did, so two
    chains never plan the same bars. A recipe whose reagents can be crafted by you gets two routes when they
    differ: with crafted intermediates (Multistage, its mail steps part of it) and with every mat bought; both
    see the same market, and the better one's use is taken out for the next recipe.

    Args:
        world: The plan's world.
        gi: Generation inputs.
        run: The run.
        u: The universe; opportunities and resources are added.
        ledger: The generation ledger, (item key, window id) -> units: each recipe's first route posts into it,
            so your later posts queue behind them (plan.post_chance's own units ahead).

    Returns:
        The market after the crafts' first routes: what flips may still buy.
    """
    base = world.world
    ranked, ranking = [], {}
    for recipe in sorted(base.recipes, key=lambda r: r.spell):
        f = base.products.get(recipe.product)
        if f is None or recipe.product in world.no_craft or not recipe.crafters:
            continue
        option = craft_option_for(recipe, base, run.now_ms, run.s, run.max_depth,
                                  timed_value(post_options(f, run.now_ms, run.s), run))
        if option.crafts:
            ranked.append((-option.profit * f.conf / 100, recipe.spell, recipe))
            ranking[recipe.spell] = option
    market, held, touched = base.market, base.held, set()
    for _, _, recipe in sorted(ranked, key=lambda r: (r[0], r[1])):
        here = replace(world, world=replace(base, market=market, held=held))
        f = base.products[recipe.product]
        opts = post_options(f, run.now_ms, run.s)
        value = timed_value(opts, run)
        first, seen, routes = None, set(), []
        same = not touched & mat_closure(recipe, base, run.max_depth)       # nothing it uses was taken: reuse
        tries = [(run.max_depth, ranking[recipe.spell] if same else
                  craft_option_for(recipe, here.world, run.now_ms, run.s, run.max_depth, value))]
        if tries[0][1].crafted:                     # crafted intermediates: buying every mat is another route
            tries.append((1, craft_option_for(recipe, here.world, run.now_ms, run.s, 1, value)))
        for depth, option in tries:
            reagents = _reagent_sources(option, recipe)
            if option.crafts and reagents not in seen:
                seen.add(reagents)
                first = first or option
                routes.append(_craft_opportunity(recipe, f, opts, option, depth, value, here, gi, run, u))
        u.opportunities.extend(exclusive(routes, f"cap:{f.key}:route{recipe.spell}", f.key, gi, u))
        if first is not None:
            made = units_made(first.crafts, recipe)
            window = (f.key, window_id(opts[best_sale(f, opts, {}, made, run.s, value)[0]]))
            ledger[window] = ledger.get(window, 0.0) + made
            mail = craft.gather(first.owned_used, held, (first.crafter,))[1]
            held = use_held(held, first.crafter, first.owned_used, mail)
            extra = intermediate_buys(first, recipe, here.world)
            market = consume(market, first, extra)
            touched |= set(first.owned_used) | set(first.bought) | {mat for _, mat, _ in extra}
    return market


def mat_closure(recipe: craft.Recipe, world: CraftWorld, depth: int) -> set[int]:
    """Every item a recipe's crafts can draw on: its reagents and, through recipes you know, theirs (SPEC §3.4B).

    Args:
        recipe: The recipe.
        world: Recipes by product.
        depth: Config max_craft_depth.

    Returns:
        Item keys; a craft option of the recipe reads the market and your stock only for these.
    """
    out, todo = set(), [(key, 1) for key, _ in recipe.reagents]
    while todo:
        key, level = todo.pop()
        if key in out:
            continue
        out.add(key)
        if level < depth:
            todo += [(mat, level + 1) for r in world.by_product.get(key, ()) for mat, _ in r.reagents]
    return out


def exclusive(routes: list[Opportunity], rid: str, key: int, gi: GenInputs, u: Universe) -> list[Opportunity]:
    """Routes to one output that only one may take: each first batch claims one unit of a shared resource (PF4).

    Two routes of a recipe are each sized as if alone, so funding both would post the product twice
    into the same demand; the shared unit lets selection fund the better one only.

    Args:
        routes: The recipe's route opportunities.
        rid: The shared resource's id (a capacity id, so its exclusion reads "<product> capacity").
        key: The product.
        gi: Generation inputs (its name).
        u: The universe (the resource is added).

    Returns:
        The routes, their first batches claiming the resource when there are two or more.
    """
    if len(routes) < 2:
        return routes
    u.add_resource(rid, 1, gi.names.get(key, str(key)))
    out = []
    for opp in routes:
        first = opp.batches[0]
        claimed = replace(first, claims=claims_of(list(first.claims) + [(rid, 1)]))
        out.append(replace(opp, batches=(claimed,) + opp.batches[1:]))
    return out


def _reagent_sources(option: craft.CraftOption, recipe: craft.Recipe) -> str:
    """The reagents part of a craft's route (portfolio.route_text).

    Args:
        option: The craft option.
        recipe: Its recipe.

    Returns:
        E.g. "2840a" or "2840c".
    """
    sources: dict[int, set[str]] = {}
    for key, _ in recipe.reagents:
        kinds = sources.setdefault(key, set())
        if option.owned_used.get(key):
            kinds.add("owned")
        if option.from_vendor.get(key):
            kinds.add("vendor")
        if option.bought.get(key, 0.0) > option.from_vendor.get(key, 0.0):
            kinds.add("ah")
        if key in option.crafted:
            kinds.add("crafted")
        if not kinds:
            kinds.add("owned")
    return route_text(sources, "-", "-").split("/")[0]


def owned_split(units: int, key: int, crafter: str, held: Mapping[tuple[str, int], float]) -> dict[str, int]:
    """Whose owned units a craft uses: the crafter's own first, then the biggest holders' (craft.gather, C22).

    Args:
        units: Owned units used, all characters together.
        key: The mat.
        crafter: The crafter.
        held: (character, key) -> units held.

    Returns:
        Character -> units.
    """
    out, left = {}, units
    givers = [(crafter, int(held.get((crafter, key), 0.0)))]
    givers += sorted(((c, int(n)) for (c, k), n in held.items() if k == key and c != crafter and n > 0),
                     key=lambda g: (-g[1], g[0]))
    for char_id, n in givers:
        take = min(left, n)
        if take > 0:
            out[char_id] = take
            left -= take
    return out


def _craft_opportunity(recipe: craft.Recipe, f: SellFacts, opts: Sequence[PostOption], option: craft.CraftOption,
                       depth: int, value: OptionValue, world: PlanWorld, gi: GenInputs, run: Run,
                       u: Universe) -> Opportunity:
    """One recipe route's crafts as batches.

    Args:
        recipe: The recipe.
        f: The product.
        opts: Its post options.
        option: The full craft option (every craft that pays).
        depth: The max craft depth it was built with.
        value: The timed value its sale was chosen with.
        world: The plan's world.
        gi: Generation inputs.
        run: The run.
        u: The universe (resources are added).

    Returns:
        The Opportunity.
    """
    mail = craft.gather(option.owned_used, world.world.held, (option.crafter,))[1]
    strategy = "multistage" if option.crafted or mail else "craft"
    reagents = _reagent_sources(option, recipe)
    name = gi.names.get(f.key, str(f.key)) + (", crafted" if option.crafted else "")
    profession, skill = gi.recipe_needs.get(recipe.spell, (None, 0))
    ranked = sorted(((c, gi.skills[(c, profession)]) for c in gi.chars if (c, profession) in gi.skills),
                    key=lambda b: (-b[1], b[0]))
    best = ranked[0] if ranked else None
    needs = Requirements(profession, skill, recipe.spell, recipe.cooldown_s or 0)
    elig = dict(profession=profession, skill=skill, actor_skill=gi.skills.get((option.crafter, profession)), best=best)
    batches = _craft_batches(recipe, f, opts, option, value, strategy, reagents, needs, elig, world, gi, run, u)
    chain = f"craft:{recipe.spell}" if depth > 1 or run.max_depth == 1 else f"craft:{recipe.spell}:bought"

    def realize(n: int) -> list[PlanAction]:
        """The first n crafts' chain.

        Args:
            n: Funded batches.

        Returns:
            craft_plan.chain_actions of the option cut to n crafts.
        """
        cut = craft_option_for(recipe, world.world, run.now_ms, run.s, depth, value, n, option.crafter)
        return chain_actions(recipe, cut, world.world, {}, run.now_ms, run.s, value, chain)
    return Opportunity(f"{strategy}:{f.key}:{option.crafter}:{reagents}:{recipe.spell}", name, tuple(batches), realize)


def mats_used(option: craft.CraftOption, crafts: int, world: PlanWorld) -> tuple[dict, dict]:
    """The owned and AH mats the first n crafts of an option use, intermediates' own mats included (C22, PF7).

    Args:
        option: The craft option.
        crafts: n.
        world: The plan's world (recipes, vendors).

    Returns:
        ((first holder, mat) -> owned units, mat -> AH units): the final recipe's owned mats count from the crafter,
        an intermediate's from its own crafter; an intermediate made from your stock claims its mats as owned, one made
        from bought mats claims its AH mats (as craft_plan.intermediate_buys buys them).
    """
    owned: dict[tuple[str, int], float] = {}
    ah: dict[int, float] = {}
    made: dict[int, float] = {}
    for step in option.steps[:crafts]:
        for key, units in step.owned:
            owned[(option.crafter, key)] = owned.get((option.crafter, key), 0.0) + units
        for key, units in step.ah:
            ah[key] = ah.get(key, 0.0) + units
        for key, units in step.crafted:
            made[key] = made.get(key, 0.0) + units
    for key, units in sorted(made.items()):
        c = option.crafted[key]
        inner = next(r for r in world.world.by_product[key] if r.spell == c.spell)
        inner_crafts = math.ceil(units / inner.avg_yield - 1e-9)
        for mat, count in inner.reagents:
            if "owned" in c.raw:
                owned[(c.crafter, mat)] = owned.get((c.crafter, mat), 0.0) + count * inner_crafts
            elif mat not in world.world.market.vendor:
                ah[mat] = ah.get(mat, 0.0) + count * inner_crafts
    return owned, ah


def _owned_by_char(owned: Mapping[tuple[str, int], float], held: Mapping[tuple[str, int], float]) -> dict[tuple[str, int], int]:
    """Owned mats as units per (character, mat): each holder's own first, then the biggest holders' (owned_split).

    Args:
        owned: (first holder, mat) -> units.
        held: (character, key) -> units held.

    Returns:
        (character, mat) -> units.
    """
    out: dict[tuple[str, int], int] = {}
    for (first, mat), units in sorted(owned.items()):
        for char_id, n in owned_split(int(round(units)), mat, first, held).items():
            out[(char_id, mat)] = out.get((char_id, mat), 0) + n
    return out


def _craft_batches(recipe: craft.Recipe, f: SellFacts, opts: Sequence[PostOption], option: craft.CraftOption,
                   value: OptionValue, strategy: str, reagents: str, needs: Requirements, elig: dict,
                   world: PlanWorld, gi: GenInputs, run: Run, u: Universe) -> list[Batch]:
    """A craft option's batches: one craft each, with its gold, deposit, claims and sale.

    Gold, deposits and claims telescope: batch k carries what k crafts need beyond k − 1 (deposits never
    below 0), and its capacity claim moves the output to the window k crafts sell in, so the funded batches'
    claims always sum to what the funded crafts use and post.

    Args:
        recipe: The recipe.
        f: The product.
        opts: Its post options.
        option: The full craft option.
        value: best_sale's value.
        strategy: "craft" or "multistage".
        reagents: The route's reagents part.
        needs: What the crafter needs.
        elig: Eligibility fields (profession, skill, actor_skill, best).
        world: The plan's world.
        gi: Generation inputs.
        run: The run.
        u: The universe (resources are added).

    Returns:
        The batches, in craft order.
    """
    name = gi.names.get(f.key, str(f.key))
    batches, gold, deposit_top = [], 0.0, 0
    prev_owned: dict[tuple[str, int], int] = {}
    prev_ah: dict[int, int] = {}
    prev_cap: tuple[str, int, int | None] | None = None
    for k, step in enumerate(option.steps, 1):
        units = units_made(k, recipe)
        i, _e, p = best_sale(f, list(opts), {}, units, run.s, value)
        o = opts[i]
        u.add_resource(cap_rid(f.key, o), o.cap, name)
        owned_raw, ah_raw = mats_used(option, k, world)
        owned = _owned_by_char(owned_raw, world.world.held)
        ah = {mat: math.ceil(n - 1e-9) for mat, n in ah_raw.items()}
        pairs = [(f"own:{c}:{mat}", n - prev_owned.get((c, mat), 0)) for (c, mat), n in owned.items()]
        pairs += [(f"ah:{mat}", n - prev_ah.get(mat, 0)) for mat, n in ah.items() if f"ah:{mat}" in u.resources]
        caps = [(cap_rid(f.key, o), units, window_id(o))] + ([(prev_cap[0], -prev_cap[1], prev_cap[2])] if prev_cap else [])
        pairs += [(rid, n) for rid, n, _ in caps]
        gold_k = math.ceil(gold + step.gold - 1e-9) - math.ceil(gold - 1e-9)
        gold += step.gold
        deposit = fees.deposit_copper(f.vendor_sell, units, o.duration_h)
        dep_k, deposit_top = max(0, deposit - deposit_top), max(deposit_top, deposit)
        sale, out_k = sale_ms(o, run.now_ms), units - units_made(k - 1, recipe)
        cap_claims: dict[int | None, int] = {}
        for _, n, w in caps:
            cap_claims[w] = cap_claims.get(w, 0) + n
        cand = make_candidate(
            strategy, f.key, option.crafter, f"{reagents}/{'now' if option.bought else '-'}/{timing_tag(sale, run.now_ms)}",
            k, gi, out_qty=out_k, inputs=tuple(sorted(recipe.reagents)), gold_mats=gold_k, gold_deposits=dep_k,
            cost_basis=math.floor(sum(gi.cost_each.get(key, 0.0) * used for key, used in step.owned) + 0.5),
            expected_net=option.profits[k - 1], chance=p, exec_qty=out_k, conf=f.conf, needs=needs,
            buy_ms=run.now_ms if option.bought else None, sale_ms=sale, window_end_ms=window_end(o),
            owned_claims=tuple(OwnedClaim(c, "stock", mat, n - prev_owned.get((c, mat), 0))
                               for (c, mat), n in sorted(owned.items()) if n != prev_owned.get((c, mat), 0)),
            capacity_claims=tuple(CapacityClaim(f.key, w, n) for w, n in
                                  sorted(cap_claims.items(), key=lambda c: (c[0] is None, c[0] or 0)) if n))
        batches.append(Batch(cand, eligibility_of(f.key, units, f, gi, o, no_listing=ungrounded(f, o), **elig),
                             claims_of(pairs)))
        prev_owned, prev_ah, prev_cap = owned, ah, (cap_rid(f.key, o), units, window_id(o))
    return batches


# ---------------------------------------------------------------- Disenchant

def disenchants(world: PlanWorld, gi: GenInputs, run: Run, u: Universe) -> None:
    """Disenchant: each auction of every disenchantable item, enchanter or not (SPEC §3.4A, §3.4D.3, PF2).

    An item whose newest price is more than 24 h old is left out (PF28).

    Args:
        world: The plan's world.
        gi: Generation inputs.
        run: The run.
        u: The universe; opportunities and resources are added.
    """
    if not gi.chars:
        return
    enchanters = sorted(((c, s) for (c, prof), s in gi.skills.items() if prof == "Enchanting"), key=lambda e: (-e[1], e[0]))
    char_id = enchanters[0][0] if enchanters else gi.chars[0]
    for key in sorted(gi.de):
        d = gi.de[key]
        if key in world.no_buy or stale(gi, key) or not d.auctions or d.ev_each <= d.auctions[0][0]:
            continue
        u.opportunities.append(_de_opportunity(key, d, char_id, enchanters, world, gi, run, u))


def _mat_caps(d: DeInfo, world: PlanWorld, gi: GenInputs, run: Run, u: Universe) -> dict[int, str]:
    """The mats' posting-now capacity resources (SPEC §3.4 market capacity: disenchant mats).

    Args:
        d: The item's disenchant facts.
        world: The plan's world (the mats' facts).
        gi: Generation inputs (names).
        run: The run.
        u: The universe (resources are added).

    Returns:
        Mat key -> resource id, for mats with facts and a posting-now option.
    """
    out = {}
    for mat, _ in d.mats:
        f = world.facts.get(mat)
        now = [o for o in post_options(f, run.now_ms, run.s) if o.window is None] if f else []
        if now:
            out[mat] = cap_rid(mat, now[0])
            u.add_resource(out[mat], now[0].cap, gi.names.get(mat, str(mat)))
    return out


def _de_opportunity(key: int, d: DeInfo, char_id: str, enchanters: list, world: PlanWorld, gi: GenInputs, run: Run,
                    u: Universe) -> Opportunity:
    """One item's buy-to-disenchant as batches, one per auction, while each pays.

    Args:
        key: The item.
        d: Its disenchant facts.
        char_id: Who disenchants (the best enchanter, else the first character).
        enchanters: (character, Enchanting skill), best first.
        world: The plan's world.
        gi: Generation inputs.
        run: The run.
        u: The universe (resources are added).

    Returns:
        The Opportunity.
    """
    name = gi.names.get(key, str(key))
    u.add_resource(f"ah:{key}", sum(n for _, n in d.auctions), name)
    caps = _mat_caps(d, world, gi, run, u)
    best = enchanters[0] if enchanters else None
    needs = Requirements("Enchanting", d.skill)
    batches, units = [], 0
    for n, (price, stack) in enumerate(d.auctions, 1):
        net = d.ev_each * stack - price * stack
        if net <= 0:
            break
        units += stack
        pairs = [(f"ah:{key}", stack)] + [(caps[m], math.ceil(per * stack - 1e-9)) for m, per in d.mats if m in caps]
        cand = make_candidate("disenchant", key, char_id, route_text({}, "now", "now"), n, gi, out_qty=stack,
                          inputs=((key, stack),), gold_buys=price * stack, expected_net=net, chance=1.0, exec_qty=stack,
                          conf=d.conf, needs=needs, buy_ms=run.now_ms, sale_ms=run.now_ms,
                          capacity_claims=tuple(CapacityClaim(m, None, math.ceil(per * stack - 1e-9))
                                                for m, per in d.mats if m in caps))
        batches.append(Batch(cand, Eligibility("Enchanting", d.skill, gi.skills.get((char_id, "Enchanting")), best,
                                               price_age_ms=gi.price_age.get(key), planned_units=units,
                                               volume_unknown=d.volume_unknown),
                             claims_of(pairs)))
    taken = d.auctions[:len(batches)]
    return Opportunity(f"disenchant:{key}:{char_id}", name, tuple(batches),
                       lambda n: _de_actions(key, d, taken[:n], char_id, run.now_ms))


def _de_actions(key: int, d: DeInfo, taken: Sequence[tuple[int, int]], char_id: str, now_ms: int) -> list[PlanAction]:
    """A buy-to-disenchant's actions: the buy now and the disenchant a minute later.

    Args:
        key: The item.
        d: Its disenchant facts.
        taken: The funded auctions (price, units).
        char_id: Who buys and disenchants.
        now_ms: Now.

    Returns:
        [buy, de] under chain "de:<key>"; the buy carries the chain's expected profit and the disenchant is a step
        with no net, so every view counts the profit once (VW1) and the buy's row says "then: disenchant it".
    """
    units = sum(n for _, n in taken)
    cost = sum(p * n for p, n in taken)
    ev = d.ev_each * units
    chain = f"de:{key}"
    return [PlanAction("buy", char_id, key, units, taken[-1][0], now_ms, None, None, None, None, ev, ev - cost,
                       chain=chain, source="ah", paid=cost),
            PlanAction("de", char_id, key, units, None, now_ms + STEP_MS, None, None, None, None, ev, 0.0,
                       chain=chain)]
