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
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

from ahsync.engine import fees, sellprob
from ahsync.engine.capacity import liquidity_cap, slot_of, window_supply
from ahsync.engine.prices import MIN_HOURLY_CLOSES
from ahsync.engine.sell_record import Record, repriced, stacks_to_post

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
WEEK_SLOTS = 168
TREND_CLAMP_PCT = 5.0          # SPEC §3.4C.1: trend7 is clamped to ±5% per day
OptionValue = Callable[[int, float], float]   # (option index, expected value) -> what choose_option maximizes


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
    default_sell_prob_cheapest: float = 0.5   # config default_sell_prob_cheapest: P without volume when cheapest
    window_margin: float = 0.15        # config window_margin: what an untrusted window must beat posting now by (R26)
    pattern_full_weeks: int = 6        # config pattern_full_weeks: weeks of data at which a window is fully trusted
    max_trend_total: float = 0.15      # config max_trend_total: the most the trend may move a forecast (TT7)


@dataclass(frozen=True)
class PlanWindow:
    """A selling window as hours of the week (UTC, slot 0 = Mon 00:00)."""

    start_slot: int
    hours: int
    best_slot: int
    weeks: int                     # weeks of data behind it (IntelWindow.weeks): how far it is trusted (R26)


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
    med7: int | None = None                        # 7-day median, copper: the post price's cap (SPEC §3.4, R24)
    p90: int | None = None                         # 30-day 90th percentile, copper: likewise
    p10: int | None = None                         # 30-day 10th percentile, copper: buying below it is cheap (§3.4D.3)
    buy_windows: tuple[PlanWindow, ...] = ()       # buying windows, cheapest first (Buy for later, §3.4D.3)
    closes_30d: int | None = None                  # hourly closes in 30 days; thin data caps forecasts at the level (TT23)
    undercut_share: float | None = None            # the document's measured u(p) (BZ29); None for the default
    commodity: bool = False                        # seen in Blizzard's commodities list: a buyer takes any number of
                                                   # an auction's units, so a flip buys lots, not whole auctions (R30)


@dataclass(frozen=True)
class PlanAction:
    """One planned action: a post, a disenchant, a vendor sale, a hold (flagged, never planned), or a chain step
    (a buy, a craft, or mail between your characters)."""

    kind: str                     # "post" | "de" | "vendor" | "hold" | "buy" | "craft" | "mail"
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
    to_char: str | None = None    # mail: the character it goes to (C22); char_id is the sender


@dataclass(frozen=True)
class PostOption:
    """One way to post: now, or for one occurrence of a selling window."""

    at_ms: int                     # post time
    hours: tuple[int, ...]         # clock hours its chance runs over
    price: int
    window: tuple[int, int, int] | None   # (start ms, end ms, best ms), or None for posting now
    cap: int                       # liquidity cap in units
    duration_h: int
    weeks: int = 0                 # the window's weeks of data; 0 for posting now (R26)



def occurrences(windows: Sequence[PlanWindow], now_ms: int, horizon_days: int) -> list[tuple[int, int, int, int]]:
    """The windows' occurrences that haven't ended and start within the horizon.

    Args:
        windows: Selling windows.
        now_ms: Now.
        horizon_days: Plan length in days.

    Returns:
        (start ms, end ms, best-hour ms, the window's weeks of data) per occurrence, by start (ties: best hour,
        then weeks).
    """
    now_h, end_h = now_ms // HOUR_MS, now_ms // HOUR_MS + horizon_days * 24
    found = []
    for w in windows:
        start = now_h + (w.start_slot - slot_of(now_h)) % WEEK_SLOTS
        if start - WEEK_SLOTS + w.hours > now_h:          # the previous occurrence is still running
            start -= WEEK_SLOTS
        while start < end_h:
            best = start + (w.best_slot - w.start_slot) % WEEK_SLOTS
            found.append((start * HOUR_MS, (start + w.hours) * HOUR_MS, best * HOUR_MS, w.weeks))
            start += WEEK_SLOTS
    return sorted(found, key=lambda o: (o[0], o[2], o[3]))


def slot_price(level: int, how_best: float, trend7: float, days_ahead: float, max_trend_total: float,
               p10: int | None = None, med7: int | None = None, p90: int | None = None,
               closes: int | None = None) -> int:
    """Expected price at a window's best hour: level × how × trend factor, clamped (SPEC §3.4C.1, TT6, TT7, TT22).

    The trend factor is 1 + trend7 ÷ 100 × days ahead, with trend7 clamped to ±5% a day and the factor
    clamped to 1 ± max_trend_total, so a week of thin data can't move a forecast 35%. The result is then kept
    within [p10_30, max(med7, p90_30)], as far as they are known: a window can't promise a price the last 30
    days never traded at (the post-now price's cap, PP2). With fewer than MIN_HOURLY_CLOSES hourly closes in
    30 days the ceiling is the level itself (TT23): a p90 of a few closes is the outlier, not the market.

    Args:
        level: The price level (med7, else med30), copper.
        how_best: Price ratio of the best hour.
        trend7: % per day, clamped to ±5.
        days_ahead: Days from now to the best hour.
        max_trend_total: Config max_trend_total, a fraction (0.15).
        p10: The 30-day 10th percentile, copper, the floor; None for none.
        med7: The 7-day median, copper; with p90 the ceiling is the larger known one.
        p90: The 30-day 90th percentile, copper.
        closes: Hourly closes in the last 30 days; None when unknown (the TT22 clamp).

    Returns:
        Copper, rounded half up, then raised to p10 and lowered to max(med7, p90) where known, or to the level
        when closes < MIN_HOURLY_CLOSES.
    """
    trend = max(-TREND_CLAMP_PCT, min(TREND_CLAMP_PCT, trend7))
    factor = max(1 - max_trend_total, min(1 + max_trend_total, 1 + trend / 100 * days_ahead))
    price = math.floor(level * how_best * factor + 0.5)
    if p10 is not None:
        price = max(price, p10)
    ceiling = [level] if closes is not None and closes < MIN_HOURLY_CLOSES else [x for x in (med7, p90) if x is not None]
    return min(price, max(ceiling)) if ceiling else price


def thin(f: SellFacts) -> bool:
    """Whether an item has too few hourly closes to forecast a window (SPEC §3.3.4, PF30).

    Args:
        f: The item.

    Returns:
        True when its count is known and below MIN_HOURLY_CLOSES; an unknown count is not thin.
    """
    return f.closes_30d is not None and f.closes_30d < MIN_HOURLY_CLOSES


def post_options(f: SellFacts, now_ms: int, s: PlanSettings, thin_windows: bool = False) -> list[PostOption]:
    """Posting now and posting for each upcoming window occurrence, with their caps.

    Thin data earns no deferral (PF30): an item with fewer than MIN_HOURLY_CLOSES closes has posting now only,
    unless thin_windows asks for its windows anyway (a buy for later, which exists only for its window and is
    then excluded "Forecast too uncertain").

    Args:
        f: The item.
        now_ms: Now.
        s: Settings.
        thin_windows: Keep the windows of a thin item.

    Returns:
        The options that have hours left to sell in.
    """
    out = []
    if f.post_price is not None:
        hours = tuple(sellprob.posting_now_window(now_ms, s.sell_window_hours))
        out.append(PostOption(now_ms, hours, record_price(f, f.post_price, s), None, _cap(f, hours, now_ms, s),
                           fees.shortest_duration_covering(s.sell_window_hours)))
    if f.level is None or (thin(f) and not thin_windows):
        return out
    for start, end, best, weeks in occurrences(f.windows, now_ms, s.horizon_days):
        post = max(best - s.post_lead_hours * HOUR_MS, now_ms)
        hours = tuple(sellprob.planned_window(post, end, now_ms))    # never empty: occurrences haven't ended
        price = record_price(f, slot_price(f.level, f.how[slot_of(best // HOUR_MS)], f.trend7, (best - now_ms) / DAY_MS,
                                           s.max_trend_total, f.p10, f.med7, f.p90, f.closes_30d), s)
        covering = -(-(best + HOUR_MS - post) // HOUR_MS)      # the listing stays up through the best hour
        out.append(PostOption(post, hours, price, (start, end, best), _cap(f, hours, now_ms, s),
                           fees.shortest_duration_covering(covering), weeks))
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
        P' in [0, 1]: the simulation (when sales are unknown, the default chance for the post's position,
        sellprob.default_chance, P25) blended with your own record when there is one (SPEC §3.3.3, O1, O2).
    """
    if f.sold_per_day is None:
        p = sellprob.default_chance(price, sellprob.cheapest_other(f.book, f.cur), s.default_sell_prob,
                                    s.default_sell_prob_cheapest)
        return _with_record(f, p, s)
    competitors = None if f.book is None else [(p, float(stack * count), band) for p, stack, band, count in f.book]
    ahead = sellprob.ahead_of(competitors, price, f.cur, f.cur_qty)
    if own_ahead:
        ahead = ahead + (sellprob.Competitor(price, own_ahead, 0),)
    u = s.default_undercut_share if f.undercut_share is None else f.undercut_share
    queue = sellprob.QueueInputs(f.sold_per_day, f.listed_per_day or 0.0, u,
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


def record_price(f: SellFacts, price: int, s: PlanSettings) -> int:
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


def required_uplift(weeks: int, s: PlanSettings) -> float:
    """How much more than posting now a window must pay, by how trusted its pattern is (SPEC §3.4C.1, R26).

    Args:
        weeks: The window's weeks of data.
        s: Settings (window_margin, pattern_full_weeks).

    Returns:
        window_margin × (1 − min(1, weeks ÷ pattern_full_weeks)), a fraction: 0.125 at 1 week, 0 from 6 weeks.
    """
    return s.window_margin * (1 - min(1.0, weeks / s.pattern_full_weeks))


def choose_option(opts: Sequence[PostOption], scored: Sequence[tuple[int, float, float]],
                  s: PlanSettings, value: OptionValue | None = None) -> tuple[int, float, float] | None:
    """The best option with room, where a window must beat posting now by its required uplift (SPEC §3.4C.1, R26).

    A window is eligible only when E_window ≥ E_now + required_uplift(weeks) × |E_now|, which is
    E_now × (1 + uplift) for the usual E_now ≥ 0; E_now is posting now's expected value for the same units.
    Without a posting-now option with room, windows compete among themselves. The trust gate always reads the
    raw expected values; `value` only changes which eligible option wins (the portfolio's timing premium,
    SPEC §3.4D.2: trust and time both apply, PF20).

    Args:
        opts: The post options.
        scored: (option index, expected value, P) of each option with room, in option order.
        s: Settings.
        value: (option index, expected value) -> what to maximize; None maximizes the expected value.

    Returns:
        The eligible option with the highest value (ties: the earlier option); None when none has room.
    """
    now = next((e for i, e, _ in scored if opts[i].window is None), None)
    best, best_value = None, 0.0
    for i, e, p in scored:
        if now is not None and opts[i].window is not None and e < now + required_uplift(opts[i].weeks, s) * abs(now):
            continue
        v = e if value is None else value(i, e)
        if best is None or v > best_value:
            best, best_value = (i, e, p), v
    return best


def _best_post(f: SellFacts, opts: list[PostOption], used: list[float], qty: int,
               s: PlanSettings, value: OptionValue | None = None) -> tuple[int, float, float] | None:
    """The best option for one more stack, within its window's cap (choose_option, R26).

    Args:
        f: The item.
        opts: The post options.
        used: Units already planned per option.
        qty: The stack.
        s: Settings.
        value: choose_option's value; None for the expected value.

    Returns:
        (option index, expected value, P); None when no option has room.
    """
    scored = []
    for i, o in enumerate(opts):
        if used[i] + qty > o.cap:
            continue
        p = post_chance(f, o.price, qty, o.hours, used[i], s)
        deposit = fees.deposit_copper(f.vendor_sell, qty, o.duration_h)
        scored.append((i, sellprob.expected_post(p, qty, o.price, max(alternative(f)[1], 0.0), deposit), p))
    return choose_option(opts, scored, s, value)


def window_id(opt: PostOption) -> int | None:
    """The key an option's units are counted under in the capacity ledger.

    Args:
        opt: A post option.

    Returns:
        Its window's start ms; None for posting now.
    """
    return opt.window[0] if opt.window else None


@dataclass(frozen=True)
class StackChoice:
    """Where one stack of an item you hold goes (SPEC §3.4C.1 allocation): a post option, or no post."""

    char_id: str
    qty: int
    option: int | None             # index into post_options; None when no post pays (hold, disenchant or vendor)
    expected: float                # the post's expected value, float copper; 0 without a post
    p: float                       # the post's chance to sell; 0 without a post
    alt_kind: str = ""             # without a post: "hold", "de" or "vendor"
    alt_value: float = 0.0         # without a post: what the stack fetches that way, float copper (0 for a hold)


def stack_choices(lots: Sequence[Lot], f: SellFacts, opts: Sequence[PostOption], s: PlanSettings,
                  ledger: dict[tuple[int, int | None], float] | None = None,
                  value: OptionValue | None = None) -> Iterator[StackChoice]:
    """The sell plan's per-stack loop for one item (SPEC §3.4C.1, R6, R7, R14): one choice per reference stack.

    Lots are taken in character order and split into reference stacks. Each stack goes to the post option
    with the best value that still has room in its window, when that pays more per unit than disenchanting or
    vendoring. Otherwise it is a hold while the AH still pays more than either (it waits for later weeks),
    else a disenchant or vendor sale. The portfolio's Sell stock candidates are these stacks (SPEC §3.4D.3).

    Args:
        lots: Your lots of this item.
        f: The item.
        opts: post_options of the item.
        s: Settings.
        ledger: (item key, window id) -> units already planned there by anyone (SPEC §3.4 market
            capacity, PL4); updated with this item's posts. None for a private one.
        value: choose_option's value (the timing premium); None picks by expected value.

    Yields:
        StackChoice per stack, in character order.
    """
    ledger = {} if ledger is None else ledger
    used = [ledger.get((f.key, window_id(o)), 0.0) for o in opts]
    alt_value = alternative(f)[1]
    # SPEC §3.3.3 quantity: a losing streak halves the stacks posted per step (O3, O4).
    stacks = sum(-(-lot.qty // f.ref_stack) for lot in lots)
    postable = stacks_to_post(stacks, f.record.streak) if f.record else stacks
    for lot in sorted(lots, key=lambda lot: lot.char_id):
        left = lot.qty
        while left > 0:
            qty = min(f.ref_stack, left)
            best = _best_post(f, list(opts), used, qty, s, value) if postable > 0 else None
            if best is not None and best[1] / qty > alt_value:
                postable -= 1
                used[best[0]] += qty
                yield StackChoice(lot.char_id, qty, best[0], best[1], best[2])
            else:
                yield stack_fallback(f, lot.char_id, qty)
            left -= qty
    for i, o in enumerate(opts):
        ledger[(f.key, window_id(o))] = used[i]


def stack_fallback(f: SellFacts, char_id: str, qty: int) -> StackChoice:
    """Units no post takes: a hold while the AH still pays more than disenchanting or vendoring (SPEC §3.4C.1).

    Args:
        f: The item.
        char_id: Who holds them.
        qty: Units.

    Returns:
        A StackChoice without a post: "hold" (worth 0 this week), else "de" or "vendor" at their value.
    """
    alt_kind, alt_value = alternative(f)
    held = f.post_price is not None and f.post_price * (1 - fees.AH_CUT) > alt_value
    kind = "hold" if held or alt_kind == "hold" else alt_kind
    return StackChoice(char_id, qty, None, 0.0, 0.0, kind, 0.0 if kind == "hold" else alt_value * qty)


def merge_stacks(choices: Sequence[StackChoice], f: SellFacts, opts: Sequence[PostOption],
                 now_ms: int) -> list[PlanAction]:
    """Stacks as actions: one per (kind, character, option) (SPEC §3.4C.1).

    Args:
        choices: Stack choices of one item.
        f: The item.
        opts: Its post options.
        now_ms: Now.

    Returns:
        Actions in time order (ties: character, kind).
    """
    merged: dict[tuple, list] = {}            # (kind, char, option) -> [qty, expected, expected sold units]
    for c in choices:
        kind, i = ("post", c.option) if c.option is not None else (c.alt_kind, -1)
        slot = merged.setdefault((kind, c.char_id, i), [0, 0.0, 0.0])
        slot[0] += c.qty
        slot[1] += c.expected if c.option is not None else c.alt_value
        slot[2] += c.p * c.qty
    return sorted((_action(kind, char, opts[i] if i >= 0 else None, totals, f, now_ms)
                   for (kind, char, i), totals in merged.items()),
                  key=lambda a: (a.at_ms, a.char_id, a.kind))


def plan_item(lots: Sequence[Lot], f: SellFacts, now_ms: int, s: PlanSettings,
              ledger: dict[tuple[int, int | None], float] | None = None) -> list[PlanAction]:
    """The sell plan for one item across your characters (SPEC §3.4C.1, R6, R7, R14).

    Args:
        lots: Your lots of this item.
        f: The item.
        now_ms: Now.
        s: Settings.
        ledger: (item key, window id) -> units already planned there by anyone (SPEC §3.4 market
            capacity, PL4); updated with this item's posts. None for a private one.

    Returns:
        Actions, one per (kind, character, option), in time order (ties: character, kind): stack_choices merged.
    """
    opts = post_options(f, now_ms, s)
    return merge_stacks(list(stack_choices(lots, f, opts, s, ledger)), f, opts, now_ms)


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
