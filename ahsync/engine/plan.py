"""The weekly plan, part 1: the sell plan for what you own (SPEC §3.4C.1).

For each item you hold it compares posting now, posting for each upcoming selling
window in the next 7 days, disenchanting and vendoring, and picks the best option
stack by stack. Posts are timed post_lead_hours before their window's best hour (or
now, when that has passed) and stay within the window's liquidity cap, a share of what
others list then (SPEC §3.4 Liquidity). Units no option can take within 7 days are a hold, flagged
and never planned. Pure: data in, numbers out; times are UTC ms, money copper.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ahsync.engine import fees, sellprob
from ahsync.engine.capacity import liquidity_cap, slot_of, window_supply
from ahsync.engine.sell_record import Record, repriced, stacks_to_post

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
WEEK_SLOTS = 168
TREND_CLAMP_PCT = 5.0          # SPEC §3.4C.1: trend7 is clamped to ±5% per day


@dataclass(frozen=True)
class PlanSettings:
    """The config values the plan needs (passed in; no config file in the engine)."""

    post_lead_hours: int = 8           # config post_lead_hours
    max_supply_share: float = 0.5      # config max_supply_share
    max_supply_share_gear: float = 1.0 # config max_supply_share_gear: gear may double the market
    min_gear_units: int = 2            # config min_gear_units: gear always gets this many per window
    sell_window_hours: int = 8         # config sell_window_hours: the posting-now window
    horizon_days: int = 7
    default_undercut_share: float = 0.5
    default_sell_prob: float = 0.25
    reprice_step: float = 0.05         # config reprice_step: price cut per lowest expiry (SPEC §3.3.3)
    own_record_weight: float = 4       # config own_record_weight: the model's worth in posts


@dataclass(frozen=True)
class PlanWindow:
    """A selling window as hours of the week (UTC, slot 0 = Mon 00:00)."""

    start_slot: int
    hours: int
    best_slot: int


@dataclass(frozen=True)
class Lot:
    """Unbound units of one item on one character (bags, bank and mail together)."""

    char_id: str
    key: int
    qty: int


@dataclass(frozen=True)
class SellFacts:
    """What the sell plan knows about one item: market intelligence plus your values for it."""

    key: int
    level: int | None                              # price level: med7, else med30 (SPEC §3.4C.1, R21)
    trend7: float                                  # % per day
    post_price: int | None                         # the posting-now price
    sold_per_day: float | None
    listed_per_day: float | None
    ref_stack: int
    how: tuple[float, ...]                         # 168 price ratios
    sales_how: tuple[float, ...]                   # 168 sales ratios
    windows: tuple[PlanWindow, ...]                # best first
    book: tuple[tuple[int, int, int, int], ...] | None   # (price, stack, band, count) of other sellers
    cur: int | None
    cur_qty: int | None
    vendor_sell: int                               # copper per unit
    de_value: float | None = None                  # disenchant EV per unit when a character can, float copper
    de_char: str | None = None                     # who disenchants it
    paid_each: float = 0.0                         # your cost basis per unit; 0 for found items
    supply_now: int | None = None                  # others' listed units now; None when never seen
    supply_how: tuple[float, ...] = ()             # 168 listed-supply ratios; () when there is no pattern
    gear: bool = False                             # a weapon or armor: the lenient gear cap (SPEC §3.4 Liquidity)
    conf: int = 0                                  # confidence % of the item's numbers (SPEC §3.3.5)
    record: Record | None = None                   # your own sell record (SPEC §3.3.3); None without one


@dataclass(frozen=True)
class PlanAction:
    """One planned action: a post, a disenchant, a vendor sale, or a hold (flagged, never planned)."""

    kind: str                     # "post" | "de" | "vendor" | "hold"
    char_id: str
    key: int
    qty: int
    price: int | None             # each, copper (posts and vendor sales)
    at_ms: int                    # when to act
    window_start_ms: int | None   # posts: the target selling window
    window_end_ms: int | None
    peak_ms: int | None           # posts for a window: its best hour
    p: float | None               # chance it sells over its window (posts)
    expected: float               # expected value of the action, float copper
    net: float                    # expected − what you paid
    duration_h: int | None = None # posts: auction duration
    chain: str | None = None      # links a buy to its follow-up post, e.g. "flip:2589" (SPEC §3.4 expected net)
    source: str | None = None     # buys: "vendor" or "ah" (D41)


@dataclass(frozen=True)
class PostOption:
    """One way to post: now, or for one occurrence of a selling window."""

    at_ms: int                     # post time
    hours: tuple[int, ...]         # clock hours its chance runs over
    price: int
    window: tuple[int, int, int] | None   # (start ms, end ms, best ms), or None for posting now
    cap: int                       # liquidity cap in units
    duration_h: int



def occurrences(windows: Sequence[PlanWindow], now_ms: int, horizon_days: int) -> list[tuple[int, int, int]]:
    """The windows' occurrences that haven't ended and start within the horizon.

    Args:
        windows: Selling windows.
        now_ms: Now.
        horizon_days: Plan length in days.

    Returns:
        (start ms, end ms, best-hour ms) per occurrence, by start (ties: best hour).
    """
    now_h, end_h = now_ms // HOUR_MS, now_ms // HOUR_MS + horizon_days * 24
    found = []
    for w in windows:
        start = now_h + (w.start_slot - slot_of(now_h)) % WEEK_SLOTS
        if start - WEEK_SLOTS + w.hours > now_h:          # the previous occurrence is still running
            start -= WEEK_SLOTS
        while start < end_h:
            best = start + (w.best_slot - w.start_slot) % WEEK_SLOTS
            found.append((start * HOUR_MS, (start + w.hours) * HOUR_MS, best * HOUR_MS))
            start += WEEK_SLOTS
    return sorted(found, key=lambda o: (o[0], o[2]))


def slot_price(level: int, how_best: float, trend7: float, days_ahead: float) -> int:
    """Expected price at a window's best hour: level × how × (1 + trend7 ÷ 100 × days ahead) (SPEC §3.4C.1).

    Args:
        level: The price level (med7, else med30), copper.
        how_best: Price ratio of the best hour.
        trend7: % per day, clamped to ±5.
        days_ahead: Days from now to the best hour.

    Returns:
        Copper, rounded half up.
    """
    trend = max(-TREND_CLAMP_PCT, min(TREND_CLAMP_PCT, trend7))
    return math.floor(level * how_best * (1 + trend / 100 * days_ahead) + 0.5)


def post_options(f: SellFacts, now_ms: int, s: PlanSettings) -> list[PostOption]:
    """Posting now and posting for each upcoming window occurrence, with their caps.

    Args:
        f: The item.
        now_ms: Now.
        s: Settings.

    Returns:
        The options that have hours left to sell in.
    """
    out = []
    if f.post_price is not None:
        hours = tuple(sellprob.posting_now_window(now_ms, s.sell_window_hours))
        out.append(PostOption(now_ms, hours, _record_price(f, f.post_price, s), None, _cap(f, hours, now_ms, s),
                           fees.shortest_duration_covering(s.sell_window_hours)))
    if f.level is None:
        return out
    for start, end, best in occurrences(f.windows, now_ms, s.horizon_days):
        post = max(best - s.post_lead_hours * HOUR_MS, now_ms)
        hours = tuple(sellprob.planned_window(post, end, now_ms))    # never empty: occurrences haven't ended
        price = _record_price(f, slot_price(f.level, f.how[slot_of(best // HOUR_MS)], f.trend7, (best - now_ms) / DAY_MS),
                              s)
        covering = -(-(best + HOUR_MS - post) // HOUR_MS)      # the listing stays up through the best hour
        out.append(PostOption(post, hours, price, (start, end, best), _cap(f, hours, now_ms, s),
                           fees.shortest_duration_covering(covering)))
    return out


def _cap(f: SellFacts, hours: Sequence[int], now_ms: int, s: PlanSettings) -> int:
    """The liquidity cap of one window: a share of others' listed units in it (SPEC §3.4 Liquidity, R14, R17, R18).

    Gear may double the market and always gets min_gear_units; materials keep max_supply_share and one stack.

    Args:
        f: The item.
        hours: The window's clock hours.
        now_ms: Now.
        s: Settings.

    Returns:
        Units.
    """
    listed = window_supply(f.supply_now, f.supply_how, now_ms // HOUR_MS, hours)
    if f.gear:
        return liquidity_cap(listed, s.max_supply_share_gear, max(f.ref_stack, s.min_gear_units))
    return liquidity_cap(listed, s.max_supply_share, f.ref_stack)


def post_chance(f: SellFacts, price: int, qty: int, hours: Sequence[int], own_ahead: float,
                s: PlanSettings) -> float:
    """P that qty units posted at price sell over the window's hours (SPEC §3.3.2).

    Your own units already planned for the same window are ahead of you in the queue.

    Args:
        f: The item.
        price: Unit price.
        qty: Units posted.
        hours: The window's clock hours.
        own_ahead: Your units already planned for this window.
        s: Settings.

    Returns:
        P' in [0, 1]: the simulation (default_sell_prob when sales are unknown) blended with your
        own record when there is one (SPEC §3.3.3, O1, O2).
    """
    if f.sold_per_day is None:
        return _with_record(f, s.default_sell_prob, s)
    competitors = None if f.book is None else [(p, float(stack * count), band) for p, stack, band, count in f.book]
    ahead = sellprob.ahead_of(competitors, price, f.cur, f.cur_qty)
    if own_ahead:
        ahead = ahead + (sellprob.Competitor(price, own_ahead, 0),)
    queue = sellprob.QueueInputs(f.sold_per_day, f.listed_per_day or 0.0, s.default_undercut_share,
                                 sellprob.demand_for(hours, f.sales_how), qty, ahead)
    return _with_record(f, sellprob.simulate(queue), s)


def _with_record(f: SellFacts, p: float, s: PlanSettings) -> float:
    """P blended with the item's own record (SPEC §3.3.3).

    Args:
        f: The item.
        p: The model's P.
        s: Settings.

    Returns:
        P' = (k·P + s) ÷ (k + s + x); P without a record.
    """
    if f.record is None:
        return p
    return sellprob.with_record(p, f.record.sold, f.record.expired, s.own_record_weight)


def _record_price(f: SellFacts, price: int, s: PlanSettings) -> int:
    """A planned price after the item's record (SPEC §3.3.3 price, O11-O13).

    Args:
        f: The item.
        price: The plan's price, copper.
        s: Settings.

    Returns:
        The price unchanged without a streak or an undercut expiry; else lowered 5% per streak step,
        capped below the cheapest other listing after an undercut, never below what vendoring or
        disenchanting pays after the cut.
    """
    r = f.record
    if r is None or (r.streak == 0 and not r.undercut_last):
        return price
    floor = math.ceil(alternative(f)[1] / (1 - fees.AH_CUT))
    cap = f.cur - 1 if r.undercut_last and f.cur else None
    return repriced(price, r.streak, s.reprice_step, floor, cap)


def alternative(f: SellFacts) -> tuple[str, float]:
    """The best way to get value without posting: disenchant or vendor (SPEC §3.4C.1).

    Args:
        f: The item.

    Returns:
        ("de" | "vendor", value per unit, float copper); ("hold", 0.0) when neither pays anything.
    """
    if f.de_value is not None and f.de_char is not None and f.de_value > f.vendor_sell:
        return "de", f.de_value
    if f.vendor_sell > 0:
        return "vendor", float(f.vendor_sell)
    return "hold", 0.0


def _best_post(f: SellFacts, opts: list[PostOption], used: list[float], qty: int,
               s: PlanSettings) -> tuple[int, float, float] | None:
    """The best option for one more stack, within its window's cap.

    Args:
        f: The item.
        opts: The post options.
        used: Units already planned per option.
        qty: The stack.
        s: Settings.

    Returns:
        (option index, expected value, P), best value per unit first (ties: earlier post); None when no option has room.
    """
    best = None
    for i, o in enumerate(opts):
        if used[i] + qty > o.cap:
            continue
        p = post_chance(f, o.price, qty, o.hours, used[i], s)
        deposit = fees.deposit_copper(f.vendor_sell, qty, o.duration_h)
        e = sellprob.expected_post(p, qty, o.price, max(alternative(f)[1], 0.0), deposit)
        if best is None or e > best[1]:
            best = (i, e, p)
    return best


def window_id(opt: PostOption) -> int | None:
    """The key an option's units are counted under in the capacity ledger.

    Args:
        opt: A post option.

    Returns:
        Its window's start ms; None for posting now.
    """
    return opt.window[0] if opt.window else None


def plan_item(lots: Sequence[Lot], f: SellFacts, now_ms: int, s: PlanSettings,
              ledger: dict[tuple[int, int | None], float] | None = None) -> list[PlanAction]:
    """The sell plan for one item across your characters (SPEC §3.4C.1, R6, R7, R14).

    Lots are taken in character order and split into reference stacks. Each stack goes to
    the post option with the highest expected value that still has room in its window, when that pays more per
    unit than disenchanting or vendoring. Otherwise it is a hold while the AH still pays more than either (it waits
    for later weeks), else a disenchant or vendor sale.

    Args:
        lots: Your lots of this item.
        f: The item.
        now_ms: Now.
        s: Settings.
        ledger: (item key, window id) -> units already planned there by anyone (SPEC §3.4 market
            capacity, PL4); updated with this item's posts. None for a private one.

    Returns:
        Actions, one per (kind, character, option), in time order (ties: character, kind).
    """
    opts = post_options(f, now_ms, s)
    ledger = {} if ledger is None else ledger
    used = [ledger.get((f.key, window_id(o)), 0.0) for o in opts]
    merged: dict[tuple, list] = {}            # (kind, char, option) -> [qty, expected, expected sold units]
    alt_kind, alt_value = alternative(f)
    # SPEC §3.3.3 quantity: a losing streak halves the stacks posted per step (O3, O4).
    stacks = sum(-(-lot.qty // f.ref_stack) for lot in lots)
    postable = stacks_to_post(stacks, f.record.streak) if f.record else stacks
    for lot in sorted(lots, key=lambda lot: lot.char_id):
        left = lot.qty
        while left > 0:
            qty = min(f.ref_stack, left)
            best = _best_post(f, opts, used, qty, s) if postable > 0 else None
            if best is not None and best[1] / qty > alt_value:
                postable -= 1
                i, e, p = best
                used[i] += qty
                slot = merged.setdefault(("post", lot.char_id, i), [0, 0.0, 0.0])
                slot[0], slot[1], slot[2] = slot[0] + qty, slot[1] + e, slot[2] + p * qty
            else:
                # This week has no room that pays: keep it for later weeks when the AH still pays more.
                held = f.post_price is not None and f.post_price * (1 - fees.AH_CUT) > alt_value
                kind = "hold" if held or alt_kind == "hold" else alt_kind
                slot = merged.setdefault((kind, lot.char_id, -1), [0, 0.0, 0.0])
                slot[0], slot[1] = slot[0] + qty, slot[1] + (0.0 if kind == "hold" else alt_value * qty)
            left -= qty
    for i, o in enumerate(opts):
        ledger[(f.key, window_id(o))] = used[i]
    return sorted((_action(kind, char, opts[i] if i >= 0 else None, totals, f, now_ms)
                   for (kind, char, i), totals in merged.items()),
                  key=lambda a: (a.at_ms, a.char_id, a.kind))


def _action(kind: str, char_id: str, opt: PostOption | None, totals: list, f: SellFacts, now_ms: int) -> PlanAction:
    """Build one action from the stacks merged into it.

    Args:
        kind: "post", "de", "vendor" or "hold".
        char_id: Who acts; a disenchant names the character who can.
        opt: The post option, or None.
        totals: [units, expected value, expected units sold].
        f: The item.
        now_ms: Now.

    Returns:
        The action; P for a post is the share of its units expected to sell.
    """
    qty, expected, sold = totals
    net = expected - f.paid_each * qty
    if kind != "post":
        actor = f.de_char if kind == "de" else char_id
        price = f.vendor_sell if kind == "vendor" else None
        return PlanAction(kind, actor, f.key, qty, price, now_ms, None, None, None, None, expected,
                          net if kind != "hold" else 0.0)
    start, end, best = opt.window if opt.window else (None, None, None)
    return PlanAction("post", char_id, f.key, qty, opt.price, opt.at_ms, start, end, best, sold / qty, expected, net,
                      opt.duration_h)


def sell_plan(lots: Sequence[Lot], facts: dict[int, SellFacts], now_ms: int, s: PlanSettings,
              ledger: dict[tuple[int, int | None], float] | None = None) -> list[PlanAction]:
    """The sell plan for everything you own (SPEC §3.4C.1).

    Args:
        lots: Your unbound lots, every character (bound items are never lots, R10).
        facts: Item key -> what the plan knows; items without facts are left out.
        now_ms: Now.
        s: Settings.
        ledger: The shared capacity ledger (see plan_item), filled with these posts.

    Returns:
        Actions in time order (ties: character, item, kind).
    """
    by_key: dict[int, list[Lot]] = {}
    for lot in lots:
        if lot.key in facts and lot.qty > 0:
            by_key.setdefault(lot.key, []).append(lot)
    actions = [a for key in sorted(by_key) for a in plan_item(by_key[key], facts[key], now_ms, s, ledger)]
    return sorted(actions, key=lambda a: (a.at_ms, a.char_id, a.key, a.kind))
