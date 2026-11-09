"""View models for the plan's tabs: Today, Week and Flip (SPEC §3.4, §3.5, §8.5).

Every number these tabs show is made here; the dashboard only displays it. Today and Week show one row
per chain, its same-character steps as lines under it (plan_chains, D62, D63), and no holds or vendor
sales (D64). A chain's profit is counted once in any view (§3.4 expected net, VW1):
- a sale of stock you hold carries its own net;
- mat buys, intermediate crafts and mail carry none ("for Copper Rod");
- the chain's profit step (a flip's buy, a craft chain's final craft) carries the profit;
- a chain's follow-up post says "profit counted on …" while that step is in the view,
  and carries its own net once the step has left it (D19, D20).
Times are labelled in local time: the caller passes the UTC offset, so the engine has no clock.
"""
from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace

from ahsync.engine.plan_chains import (LEFT_OUT, Arriving, ViewAction, by_chain, carrier_of, chain_id, chain_lines,
                                       clock, folded, role, row_money, short_name)
from ahsync.engine.views import MarketActivity, market_activity, money, risk, round_half_up, signed_money
from ahsync.engine.sell_record import Record

__all__ = ["ViewAction", "clock", "role", "chain_id"]   # re-exported: the views' callers import them from here

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def counted(actions: Sequence[ViewAction], start_ms: int, end_ms: int, offset_ms: int) -> dict[str, tuple[float | None, str]]:
    """What each action in a view counts, and the note that explains it (SPEC §3.4 expected net, VW1).

    Args:
        actions: Everything that might be in the view.
        start_ms: View start, inclusive.
        end_ms: View end, exclusive.
        offset_ms: Local time minus UTC, for the notes.

    Returns:
        action id -> (net counted, or None for "no net"; note), for actions in the view.
    """
    shown = [a for a in actions if start_ms <= a.at_ms < end_ms]
    carriers = {}
    for a in sorted(shown, key=lambda a: (a.at_ms, a.action_id)):
        if role(a) == "profit":
            carriers.setdefault(chain_id(a), a)
    out = {}
    for a in shown:
        r = role(a)
        if r == "step":                           # nothing sold now: no net (D39); holds are no rows (D64)
            out[a.action_id] = (None, "")
        elif r == "follow" and chain_id(a) in carriers:
            day, hour = clock(carriers[chain_id(a)].at_ms, offset_ms)
            out[a.action_id] = (0.0, f"profit counted on the {day} {hour} {carriers[chain_id(a)].kind}")
        else:
            out[a.action_id] = (a.net, "")
    return out


def chain_chances(actions: Sequence[ViewAction]) -> dict[str, float]:
    """Each chain's chance to sell: its post's (SPEC §3.5: a craft or flip shows its product's chance).

    Args:
        actions: Plan actions.

    Returns:
        chain id -> P of its first post.
    """
    out: dict[str, float] = {}
    for a in sorted(actions, key=lambda a: (a.at_ms, a.action_id)):
        if a.kind == "post" and a.p is not None and a.in_chain:
            out.setdefault(chain_id(a), a.p)
    return out


def chance_of(a: ViewAction, chains: dict[str, float]) -> float | None:
    """The chance a row shows: its own for a post, its chain's for a craft or a flip's buy; none for steps.

    Args:
        a: The action.
        chains: From chain_chances.

    Returns:
        P, or None when nothing is sold.
    """
    if a.kind == "post":
        return a.p
    if a.kind == "de" or role(a) == "step":
        return None
    return chains.get(chain_id(a))


@dataclass(frozen=True)
class ItemLook:
    """What the plan views show about an item besides the plan: its name and market activity."""

    name: str
    quality: int
    conf: int | None = None
    sold_per_day: float | None = None
    source: str | None = None
    units_on_ah: int | None = None
    level: int | None = None                # price level: med7, else med30 (SPEC §3.4C.1)
    seasonality: int | None = None
    paid_each: int | None = None            # what you paid per unit, copper; None before tracking (T7)
    record: Record | None = None            # your own sell record (SPEC §3.3.3)
    kind: str | None = None                 # the main volume source's kind: "ids" | "groups" | "own" (D57)
    volume_unknown: bool = False            # the intelligence's chance is the default: never hidden by Min chance (D58)


def _activity(look: ItemLook, p: float | None) -> MarketActivity:
    """The market activity cells for a row with its own chance.

    Args:
        look: The item.
        p: The row's chance, or None.

    Returns:
        views.market_activity's cells; the chance carries "volume unknown" when the intelligence says so (D58).
    """
    m = market_activity(p, look.conf, look.sold_per_day, look.source, look.units_on_ah, look.kind)
    return replace(m, chance_note="volume unknown") if look.volume_unknown and p is not None else m


QTY_FRACTIONS = {1: "½", 2: "¼"}            # Latin-1 only: WoW's fonts lack ⅛ (shown "1/8")
RECORD_WEIGHT, REPRICE_STEP = 4, 0.05       # config own_record_weight and reprice_step defaults (SPEC §3.6)


def _posts(count: float) -> str:
    """A record's count of posts' worth: whole as is, else one decimal (O19: a post counts its share sold).

    Args:
        count: Posts' worth.

    Returns:
        "2" or "1.4".
    """
    tenths = math.floor(count * 10 + 0.5)
    return str(tenths // 10) if tenths % 10 == 0 else f"{tenths / 10:.1f}"


def record_line(r: Record, p_after: float | None = None, weight: float = RECORD_WEIGHT, step: float = REPRICE_STEP) -> str:
    """The record line an item's rows show (SPEC §3.3.3, D42).

    Args:
        r: The item's record.
        p_after: The row's P' when it is a post, else None.
        weight: own_record_weight k.
        step: reprice_step.

    Returns:
        E.g. "Your record (30 d): 2 of 5 sold · expired 2× while cheapest · P 80% → 58% · price −10% · qty ¼".
        P before the blend is worked back from P' = (k·P + s) ÷ (k + s + x).
    """
    parts = [f"Your record (30 d): {_posts(r.sold)} of {_posts(r.sold + r.expired)} sold"]
    if r.lowest_expiries:
        parts.append(f"expired {r.lowest_expiries}× while cheapest")
    if p_after is not None:
        p_model = min(1.0, max(0.0, (p_after * (weight + r.sold + r.expired) - r.sold) / weight))
        parts.append(f"P {round_half_up(p_model * 100)}% → {round_half_up(p_after * 100)}%")
    if r.streak:
        parts.append(f"price −{round_half_up((1 - (1 - step) ** r.streak) * 100)}%")
        parts.append(f"qty {QTY_FRACTIONS.get(r.streak, '1/' + str(2 ** r.streak))}")
    return " · ".join(parts)


@dataclass(frozen=True)
class Cells:
    """What a row shows besides its action: what it counts, why, its chance and its chain's lines."""

    net: float | None                     # what it counts; None: no net
    note: str                             # why, or what it is for
    p: float | None                       # the chance it shows
    lines: tuple[str, ...] = ()           # a chain row's steps and sale (D62)
    chain: tuple[ViewAction, ...] = ()    # its chain's actions when it carries the chain's row, for its money


def _row(a: ViewAction, c: Cells, look: ItemLook, total_gold: int, offset_ms: int) -> dict:
    """One action's display cells, shared by Today and Week.

    Args:
        a: The action.
        c: What it counts, its note, chance and lines.
        look: Its item.
        total_gold: All your gold, for risk.
        offset_ms: Local time minus UTC.

    Returns:
        Row dict with display cells, the chain's lines and numeric sort keys; a craft row's Each and Total are its
        chain's mat gold per craft and in all, its risk the chain's gold at risk (plan_chains.row_money, D62).
    """
    each, total, spend = row_money(a, c.chain)
    risk_text, amber = risk(spend, total_gold)
    m = _activity(look, c.p)
    day, hour = clock(a.at_ms, offset_ms)
    net = c.net
    return {
        "id": a.action_id, "action": a.kind, "item": look.name, "quality": look.quality, "qty": a.qty,
        "each": each, "total": total, "lines": list(c.lines),
        "net": "—" if net is None else signed_money(net), "net_class": "pos" if (net or 0) > 0 else "neg" if (net or 0) < 0 else "",
        "chance": m.chance, "chance_note": m.chance_note, "confidence": m.confidence, "dot": m.dot, "sales": m.sales,
        "supply": m.supply, "days": m.days, "risk": risk_text, "risk_amber": amber,
        "character": short_name(a.char_id), "day": day, "time": hour, "status": a.status, "note": c.note,
        "record": record_line(look.record, c.p if a.kind == "post" else None) if look.record else "",
        "sort": {"net": round_half_up(net or 0.0), "chance": m.percent if m.percent is not None else -1,
                 "sales": m.sales_tenths, "supply": m.supply_units, "days": m.days_tenths,
                 "risk": round_half_up(spend / total_gold * 1000) if total_gold > 0 else -1, "at": a.at_ms},
    }


@dataclass(frozen=True)
class ViewContext:
    """What every row of one view is built from besides its action."""

    chains: dict[str, list[ViewAction]]       # chain id -> its actions (plan_chains.by_chain)
    folds: dict[str, str]                     # step id -> the row it folds into
    nets: dict[str, tuple[float | None, str]] # from counted
    looks: dict[int, ItemLook]
    offset_ms: int
    arriving: Arriving = Arriving()           # mail on its way, for the craft rows' lines (C23)


def collapse(candidates: Sequence[ViewAction], actions: Sequence[ViewAction]) -> tuple[list[ViewAction], dict, dict]:
    """A view's rows: its candidates less holds, vendor sales and the steps that fold into their chain's row (D62-D64).

    Args:
        candidates: The actions the view would show.
        actions: The plan, with history (for the chains).

    Returns:
        (the row actions in candidate order, chain id -> its actions, step id -> the row it folds into).
    """
    shown = [a for a in candidates if a.kind not in LEFT_OUT]
    chains = by_chain(actions)
    folds = folded(shown, chains)
    return [a for a in shown if a.action_id not in folds], chains, folds


def _cells(a: ViewAction, ctx: ViewContext, chances: dict[str, float], crafts: dict[str, str]) -> Cells:
    """A row's net, note, chance and, when it carries its chain, the chain's lines (D62, D66).

    Args:
        a: The row's action.
        ctx: The view.
        chances: From chain_chances.
        crafts: From chain_crafts.

    Returns:
        The row's Cells.
    """
    net, note = ctx.nets[a.action_id]
    members = ctx.chains.get(chain_id(a), []) if a.in_chain else []
    carrier = carrier_of(members) if members else None
    lines, chain = (), ()
    if carrier is not None and carrier.action_id == a.action_id:
        names = {k: look.name for k, look in ctx.looks.items()}
        incoming = ctx.arriving.for_craft(a) if a.kind == "craft" else []
        lines, chain = tuple(chain_lines(a, members, ctx.folds, names, ctx.offset_ms, incoming)), tuple(members)
    return Cells(net, note_of(a, note, crafts, ctx.offset_ms, ctx.looks[a.key].paid_each), chance_of(a, chances),
                 lines, chain)


def today_view(actions: Sequence[ViewAction], now_ms: int, offset_ms: int, min_sell_prob: float,
               looks: dict[int, ItemLook], total_gold: int, arriving: Arriving = Arriving()) -> dict:
    """The Today tab: what to do now, best expected net first (SPEC §3.4 Today list, D1, D2, D16, D62, D64).

    "Now" is any open action due within the next hour; a chain is one row (its steps are lines), and holds and vendor
    sales are no rows. Ties go to the higher chance, then the item name, then the character, so the order is always
    the same (§8.6). Rows whose chance is below min_sell_prob are hidden and counted; rows with nothing to sell, and
    rows of items whose volume is unknown (their chance is a default, D58), never are.

    Args:
        actions: The plan, with history.
        now_ms: Now.
        offset_ms: Local time minus UTC.
        min_sell_prob: The filter (config min_sell_prob, or the page's dropdown).
        looks: Item key -> name and market activity.
        total_gold: All your gold.
        arriving: Mail on its way, for the lines under the crafts that use it (C23).

    Returns:
        {"rows": [...], "hidden": n, "total": money of the shown rows' nets, "decisions": the Needs a decision rows}.
    """
    due = [a for a in actions if a.status in ("pending", "late") and a.at_ms < now_ms + HOUR_MS]
    shown, chains, folds = collapse(due, actions)
    nets = counted(shown, min(a.at_ms for a in shown) if shown else now_ms, now_ms + HOUR_MS, offset_ms)
    ctx = ViewContext(chains, folds, nets, looks, offset_ms, arriving)
    chances, crafts = chain_chances(actions), chain_crafts(actions, looks)
    rows, hidden = [], 0
    for a in shown:
        c = _cells(a, ctx, chances, crafts)
        if c.p is not None and c.p < min_sell_prob and not looks[a.key].volume_unknown:
            hidden += 1
            continue
        rows.append((-(c.net or 0.0), -(c.p if c.p is not None else -1.0), looks[a.key].name, a.char_id,
                     _row(a, c, looks[a.key], total_gold, offset_ms)))
    rows.sort(key=lambda r: r[:4])
    total = sum(r[4]["sort"]["net"] for r in rows)
    return {"rows": [r[4] for r in rows], "hidden": hidden, "total": signed_money(total), "decisions": decisions(looks)}


def decisions(looks: dict[int, ItemLook]) -> list[dict]:
    """Today's Needs a decision section: the items flagged not selling (SPEC §3.3.3, D43).

    Args:
        looks: Item key -> look, with its record.

    Returns:
        {"key", "item", "quality", "record"} per flagged item, by name then key.
    """
    flagged = [(look.name, key, look) for key, look in looks.items() if look.record is not None and look.record.not_selling]
    return [{"key": str(key), "item": look.name, "quality": look.quality, "record": record_line(look.record)}
            for _, key, look in sorted(flagged, key=lambda f: (f[0], f[1]))]


def category(a: ViewAction, chains: dict[str, set[str]]) -> str:
    """Which week total an action counts toward (SPEC §3.4C output, D17).

    Args:
        a: The action.
        chains: chain id -> the kinds of its actions.

    Returns:
        "Crafts", "Flips", "Disenchant" (a disenchant, or a buy to disenchant, SPEC §3.4D.3) or "Sell owned".
    """
    kinds = chains.get(chain_id(a), set()) if a.in_chain else set()
    if "craft" in kinds:
        return "Crafts"
    if "de" in kinds or a.kind == "de":
        return "Disenchant"
    return "Flips" if "buy" in kinds else "Sell owned"


def _post_note(a: ViewAction, offset_ms: int) -> str:
    """A post's note: the peak it is for.

    Args:
        a: The action.
        offset_ms: Local time minus UTC.

    Returns:
        "for the 18:00 peak", or "" for posting now.
    """
    return f"for the {clock(a.peak_ms, offset_ms)[1]} peak" if a.kind == "post" and a.peak_ms is not None else ""


def chain_crafts(actions: Sequence[ViewAction], looks: dict[int, "ItemLook"]) -> dict[str, str]:
    """What each crafting chain makes: the name of its last craft's product (D39).

    Args:
        actions: Plan actions.
        looks: Item key -> name.

    Returns:
        chain id -> product name.
    """
    out: dict[str, str] = {}
    for a in sorted(actions, key=lambda a: (a.at_ms, a.action_id)):
        if a.kind == "craft" and a.in_chain and a.key in looks:
            out[chain_id(a)] = looks[a.key].name
    return out


def note_of(a: ViewAction, counted_note: str, crafts: dict[str, str], offset_ms: int,
            paid_each: int | None = None) -> str:
    """A row's note: what it is for and why it counts what it counts (D37-D39, D66).

    Args:
        a: The action.
        counted_note: From counted ("profit counted on …" or "").
        crafts: From chain_crafts.
        offset_ms: Local time minus UTC.
        paid_each: What you paid per unit for the item, copper; None before tracking.

    Returns:
        The parts joined by " · ", e.g. "for the 18:00 peak · profit counted on the Fri 14:10 buy",
        "from a vendor · for crafting Basic Campfire Kit" or "to Amaya · for crafting Copper Rod"; "" when there is
        nothing to say. When a chain posts is its row's last line now, not its note (D62).
    """
    parts = [_post_note(a, offset_ms), "from a vendor" if a.kind == "buy" and a.vendor else ""]   # D41
    if a.kind == "mail" and a.to_char:
        parts.append(f"to {short_name(a.to_char)}")                                                  # C22, D66
    if role(a) == "step" and a.kind != "craft" and chain_id(a) in crafts:
        parts.append(f"for crafting {crafts[chain_id(a)]}")
    if a.kind == "post" and paid_each and a.price is not None and a.price < paid_each:
        parts.append(f"below what you paid ({money(paid_each)})")      # still the best option: it is spent (T7)
    parts.append(counted_note)
    return " · ".join(p for p in parts if p)


def week_view(actions: Sequence[ViewAction], now_ms: int, offset_ms: int, looks: dict[int, ItemLook],
              total_gold: int, arriving: Arriving = Arriving()) -> dict:
    """The Week tab: a rolling 7 days from today's local midnight, by day (SPEC §3.4C, D11, D17, D19-D21, D63, D64).

    A chain is one row on its carrier's day, its steps lines under it; holds and vendor sales are no rows and count
    toward no total.

    Args:
        actions: The plan, with history (done actions earlier today show ticked).
        now_ms: Now.
        offset_ms: Local time minus UTC.
        looks: Item key -> name and market activity.
        total_gold: All your gold.
        arriving: Mail on its way (C23).

    Returns:
        {"days": [{"label", "today", "total", "cents", "rows"}], "categories", "characters", "total", and
        "sums": the same totals in copper}; every total is the sum of the nets its rows count (VW1).
    """
    start = (now_ms + offset_ms) // DAY_MS * DAY_MS - offset_ms
    end = start + 7 * DAY_MS
    shown, chains, folds = collapse(sorted((a for a in actions if start <= a.at_ms < end),
                                           key=lambda a: (a.at_ms, a.action_id)), actions)
    ctx = ViewContext(chains, folds, counted(shown, start, end, offset_ms), looks, offset_ms, arriving)
    chain_kinds: dict[str, set[str]] = {}
    for a in actions:
        chain_kinds.setdefault(chain_id(a), set()).add(a.kind)
    chances, crafts = chain_chances(actions), chain_crafts(actions, looks)
    days = [{"label": "", "today": d == 0, "total": 0, "rows": []} for d in range(7)]
    by_category: dict[str, int] = {}
    by_character: dict[str, int] = {}
    for a in shown:
        d = (a.at_ms - start) // DAY_MS
        row = _row(a, _cells(a, ctx, chances, crafts), looks[a.key], total_gold, offset_ms)
        days[d]["rows"].append(row)
        cents = row["sort"]["net"]
        days[d]["total"] += cents
        cat, who = category(a, chain_kinds), row["character"]
        by_category[cat] = by_category.get(cat, 0) + cents
        by_character[who] = by_character.get(who, 0) + cents
    for d, day in enumerate(days):
        weekday, _ = clock(start + d * DAY_MS + HOUR_MS, offset_ms)
        day["label"] = f"{weekday} {time.gmtime((start + d * DAY_MS + offset_ms) // 1000).tm_mday}"
        day["cents"] = day["total"]
        day["total"] = signed_money(day["total"])
    total = sum(by_category.values())
    return {"days": days, "total": signed_money(total),
            "categories": {k: signed_money(v) for k, v in sorted(by_category.items())},
            "characters": {k: signed_money(v) for k, v in sorted(by_character.items())},
            "sums": {"total": total, "categories": dict(sorted(by_category.items())),
                     "characters": dict(sorted(by_character.items()))}}


def flip_view(actions: Sequence[ViewAction], offset_ms: int, looks: dict[int, ItemLook], total_gold: int) -> dict:
    """The Flip tab: each planned flip, best expected profit first (SPEC §3.4C.2, §3.5 Flip columns).

    Args:
        actions: The plan.
        offset_ms: Local time minus UTC.
        looks: Item key -> name, market activity, price level and seasonality strength.
        total_gold: All your gold.

    Returns:
        {"rows": [...], "hidden": 0}.
    """
    chain_kinds: dict[str, set[str]] = {}
    for a in actions:
        chain_kinds.setdefault(chain_id(a), set()).add(a.kind)
    rows = []
    for buy in (a for a in actions if a.kind == "buy" and a.in_chain and chain_kinds[chain_id(a)] == {"buy", "post"}):
        posts = sorted((a for a in actions if a.kind == "post" and chain_id(a) == chain_id(buy)), key=lambda a: a.at_ms)
        rows.append(_flip_row(buy, posts, looks[buy.key], total_gold, offset_ms))
    rows.sort(key=lambda r: (-r["sort"]["profit"], r["item"]))
    return {"rows": rows, "hidden": 0}


def _flip_row(buy: ViewAction, posts: list[ViewAction], look: ItemLook, total_gold: int, offset_ms: int) -> dict:
    """One flip's cells.

    Args:
        buy: The flip's buy.
        posts: Its posts, earliest first.
        look: The item.
        total_gold: All your gold.
        offset_ms: Local time minus UTC.

    Returns:
        Row dict with display cells and numeric sort keys.
    """
    cost = buy.expected - buy.net
    first = posts[0]
    sold = sum((p.p or 0.0) * p.qty for p in posts) / max(1, sum(p.qty for p in posts))
    m = _activity(look, sold)
    risk_text, amber = risk(cost, total_gold)
    roi = round_half_up(buy.net / cost * 100) if cost else 0
    uplift = round_half_up((first.price / look.level - 1) * 100) if look.level and first.price else None
    day, hour = clock(first.peak_ms if first.peak_ms is not None else first.at_ms, offset_ms)
    return {
        "id": buy.action_id, "item": look.name, "quality": look.quality, "qty": buy.qty, "buy_each": money(buy.price),
        "buy_total": money(cost), "sell_each": money(first.price), "sell_slot": f"{day} {hour}",
        "why": "relist now, under the listings left" if first.peak_ms is None else
               f"{day} peak {uplift:+d}% over its recent median" if uplift is not None else "",
        "profit": signed_money(buy.net), "seasonality": "—" if look.seasonality is None else str(look.seasonality),
        "roi": f"{roi}%", "chance": m.chance, "chance_note": m.chance_note, "confidence": m.confidence, "dot": m.dot,
        "sales": m.sales, "supply": m.supply, "days": m.days, "risk": risk_text, "risk_amber": amber,
        "character": short_name(buy.char_id),
        "sort": {"qty": buy.qty, "buy_total": round_half_up(cost), "profit": round_half_up(buy.net),
                 "seasonality": look.seasonality if look.seasonality is not None else -1, "roi": roi,
                 "chance": m.percent if m.percent is not None else -1, "sales": m.sales_tenths,
                 "supply": m.supply_units, "days": m.days_tenths, "risk": round_half_up(cost / total_gold * 1000) if total_gold > 0 else -1},
    }
