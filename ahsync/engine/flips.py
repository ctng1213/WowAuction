"""The weekly plan, part 2: buy to flip (SPEC §3.4C.2).

A flip buys auctions now, cheapest first, and posts them now, just under the listings
left after the buy, or for an upcoming selling window, whichever pays more (R24). Each
auction is added only while its expected profit, after fees and the chance to sell, is at
least min_flip_margin × its cost. `flip_steps` yields the auctions one at a time: they are
the portfolio's Flip batches (SPEC §3.4D.3), and the optimizer decides how many are funded
within your gold and the exposure budget (§3.4D.4, R9, R19). Pure: data in, numbers out.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace

from ahsync.engine import fees, sellprob
from ahsync.engine.plan import OptionValue, PlanAction, PlanSettings, PostOption, SellFacts, alternative, \
    choose_option, post_chance, post_options, record_price, slot_price, window_id
from ahsync.engine.prices import post_price, relist_price


@dataclass(frozen=True)
class FlipSettings:
    """The config values flips need."""

    min_flip_margin: float = 0.20      # config min_flip_margin: expected profit ÷ cost


@dataclass(frozen=True)
class _Stage:
    """A flip being built: the auctions bought so far and where each goes."""

    cost: int                          # copper spent
    expected: float                    # expected value of the posts, float copper
    units: int
    top_price: int                     # the highest unit price paid ("buy at or below")
    posts: dict[tuple[int, int], list]  # (option index, unit price) -> [units, expected, expected units sold, cost]


def pays(expected: float, cost: int, min_flip_margin: float) -> bool:
    """Whether a flip earns enough: expected profit ≥ min_flip_margin × cost (SPEC §3.4C.2, R8, R13).

    Args:
        expected: Expected value of the posts (P-weighted, after fees and deposits), float copper.
        cost: What the buy costs, copper.
        min_flip_margin: Config min_flip_margin.

    Returns:
        True when expected − cost ≥ margin × cost.
    """
    return expected - cost >= min_flip_margin * cost


def auctions(book: Sequence[tuple[int, int, int, int]] | None, cur: int | None, cur_qty: int | None) -> list[tuple[int, int, int]]:
    """Other sellers' auctions you could buy, cheapest first.

    Args:
        book: (price, stack, band, count) rows, or None without a book.
        cur: Lowest buyout, used without a book.
        cur_qty: Units at it, used without a book (as one auction).

    Returns:
        (unit price, stack, band) per auction, by price then stack (ties: band).
    """
    if book is None:
        return [(cur, cur_qty, 0)] if cur is not None and cur_qty else []
    found = [(price, stack, band) for price, stack, band, count in book for _ in range(count)]
    return sorted(found)


def _remaining_book(f: SellFacts, bought: int) -> SellFacts:
    """The item with the auctions you bought taken out of the competition.

    Args:
        f: The item.
        bought: How many of the cheapest auctions you bought.

    Returns:
        A copy whose book (or cur and cur_qty) no longer has them.
    """
    if f.book is None:
        return replace(f, cur=None, cur_qty=None) if bought else f
    left: dict[tuple[int, int, int], int] = {}
    for auction in auctions(f.book, None, None)[bought:]:
        left[auction] = left.get(auction, 0) + 1
    return replace(f, book=tuple((p, s, b, n) for (p, s, b), n in sorted(left.items())))


def relisted(f_left: SellFacts, opts: list[PostOption], s: PlanSettings, grounded: bool = True) -> list[PostOption]:
    """The options with posting now re-priced for the book left after the buy (SPEC §3.4C.2, R24, R28).

    Args:
        f_left: The item, with the bought auctions out of its book.
        opts: post_options of the item (posting now included).
        s: Settings.
        grounded: True prices posting now by relist_price (no room on an empty book with thin data, R28); False
            by post_price, to value the flip the grounding rule turns down (its excluded row).

    Returns:
        The same options in order; posting now is relist_price(the cheapest listing left, med7, p90, closes) after
        your record, and has no room (cap 0) when nothing grounds a price.
    """
    price_of = relist_price if grounded else post_price
    price = price_of(sellprob.cheapest_other(f_left.book, f_left.cur), f_left.med7, f_left.p90, f_left.closes_30d)
    out = []
    for o in opts:
        if o.window is None:
            o = replace(o, cap=0) if price is None else replace(o, price=record_price(f_left, price, s))
        out.append(o)
    return out


def _best_window(f: SellFacts, opts: list[PostOption], used: list[float], stage: _Stage, qty: int,
                 s: PlanSettings, value: OptionValue | None = None) -> tuple[int, float, float] | None:
    """The best way to sell one more auction's units, posting now or for a window, within capacity.

    Args:
        f: The item, with the bought auctions out of its book.
        opts: The options, posting now re-priced for that book (relisted).
        used: Units already planned per option by anyone (the ledger).
        stage: The flip so far.
        qty: Units of the auction.
        s: Settings.
        value: choose_option's value (the timing premium); None picks by expected value.

    Returns:
        (option index, expected value, P) as plan.choose_option picks it: a window must beat relisting now by
        its required uplift (R26); None when no option has room.
    """
    scored = []
    for i, o in enumerate(opts):
        mine = sum(slot[0] for (j, _), slot in stage.posts.items() if j == i)
        if used[i] + mine + qty > o.cap:
            continue
        p = post_chance(f, o.price, qty, o.hours, used[i] + mine, s)
        deposit = fees.deposit_copper(f.vendor_sell, qty, o.duration_h)
        scored.append((i, sellprob.expected_post(p, qty, o.price, alternative(f)[1], deposit), p))
    return choose_option(opts, scored, s, value)


@dataclass(frozen=True)
class FlipStep:
    """One auction a flip buys, and where its units are posted (a Flip batch, SPEC §3.4D.3)."""

    price: int                         # unit price paid, copper
    stack: int                         # units
    option: int                        # index into post_options of the item
    post_price: int                    # the unit price it is posted at (posting now: relisted under what is left)
    expected: float                    # the post's expected value, float copper
    p: float                           # its chance to sell


def flip_steps(f: SellFacts, now_ms: int, s: PlanSettings, fs: FlipSettings, ledger: dict,
               value: OptionValue | None = None, cost_value: bool = False, grounded: bool = True) -> Iterator[FlipStep]:
    """The auctions a flip buys, cheapest first, while each still pays (SPEC §3.4C.2, R24, R27).

    An auction is added while it fits an option with room (posting now, under what is left after the buy, or a
    selling window), while that option is worth more per unit than disenchanting or vendoring the units (else
    it is a disenchant buy, not a flip, R27), and while its own expected profit is at least min_flip_margin ×
    its cost; prices only rise along the book, so the first one that fails ends the flip.

    Args:
        f: The item.
        now_ms: Now.
        s: Plan settings.
        fs: Flip settings.
        ledger: (item key, window id) -> units already planned; read only.
        value: choose_option's value for an option given its expected value (the timing premium); None picks by
            expected value.
        cost_value: When True, value is given the auction's profit (expected − cost) instead of its expected value.
        grounded: False relists on an empty book at the ungrounded post price (relisted), to value an excluded flip.

    Yields:
        FlipStep per auction bought, in buying order.
    """
    opts = post_options(f, now_ms, s)
    used = [ledger.get((f.key, window_id(o)), 0.0) for o in opts]
    stage = _Stage(0, 0.0, 0, 0, {})
    for n, (price, stack, _) in enumerate(auctions(f.book, f.cur, f.cur_qty)):
        cost = price * stack
        left = _remaining_book(f, n + 1)
        here = relisted(left, opts, s, grounded)
        best_unit = max([o.price * (1 - fees.AH_CUT) for i, o in enumerate(here) if used[i] + stack <= o.cap],
                        default=None)
        if best_unit is None or max(best_unit, alternative(f)[1]) * stack < (1 + fs.min_flip_margin) * cost:
            return                                  # can't pay even if every unit sells: no simulation needed
        scored_value = value
        if value is not None and cost_value:
            scored_value = lambda i, e, cost=cost: value(i, e - cost)        # noqa: E731  the auction's profit
        best = _best_window(left, here, used, stage, stack, s, scored_value)
        if best is None or best[1] / stack <= alternative(f)[1] or not pays(best[1], cost, fs.min_flip_margin):
            return
        i, e, p = best
        slot = stage.posts.setdefault((i, here[i].price), [0, 0.0, 0.0, 0])
        slot[0], slot[1], slot[2], slot[3] = slot[0] + stack, slot[1] + e, slot[2] + p * stack, slot[3] + cost
        stage = replace(stage, cost=stage.cost + cost, expected=stage.expected + e, units=stage.units + stack,
                        top_price=price)
        yield FlipStep(price, stack, i, here[i].price, e, p)


def build_flip(f: SellFacts, now_ms: int, s: PlanSettings, fs: FlipSettings, ledger: dict, spend_limit: int | None = None,
               max_auctions: int | None = None, value: OptionValue | None = None,
               cost_value: bool = False) -> _Stage | None:
    """The flip worth making for one item: flip_steps' auctions, within a spend limit and a count.

    Args:
        f: The item.
        now_ms: Now.
        s: Plan settings.
        fs: Flip settings.
        ledger: (item key, window id) -> units already planned; read only.
        spend_limit: The most this flip may spend, copper; None for no limit (the portfolio funds batches).
        max_auctions: The most auctions to buy (the funded batches); None for all that pay.
        value: flip_steps' value.
        cost_value: flip_steps' cost_value.

    Returns:
        The flip; None when not even one auction is worth it.
    """
    stage = _Stage(0, 0.0, 0, 0, {})
    for n, step in enumerate(flip_steps(f, now_ms, s, fs, ledger, value, cost_value)):
        cost = step.price * step.stack
        if (spend_limit is not None and stage.cost + cost > spend_limit) or (max_auctions is not None and n >= max_auctions):
            break
        slot = stage.posts.setdefault((step.option, step.post_price), [0, 0.0, 0.0, 0])
        slot[0], slot[1], slot[2], slot[3] = slot[0] + step.stack, slot[1] + step.expected, \
            slot[2] + step.p * step.stack, slot[3] + cost
        stage = replace(stage, cost=stage.cost + cost, expected=stage.expected + step.expected,
                        units=stage.units + step.stack, top_price=step.price)
    return stage if stage.units else None


def flip_actions(f: SellFacts, stage: _Stage, char_id: str, now_ms: int, s: PlanSettings,
                 chain: str | None = None) -> list[PlanAction]:
    """A flip as a buy now and its follow-up posts, linked by one chain id.

    The buy carries the chain's expected profit; each post carries its own net against
    what its units cost, which views use once the buy has left the view (SPEC §3.4 expected net).
    A post for now has no window: it is due now, for the posting-now window's duration (R24).

    Args:
        f: The item.
        stage: The flip.
        char_id: Who buys and posts.
        now_ms: Now.
        s: Settings.
        chain: The chain id; "flip:<key>" by default.

    Returns:
        [buy, posts by time (ties: price)].
    """
    chain = chain or f"flip:{f.key}"
    opts = post_options(f, now_ms, s)
    buy = PlanAction("buy", char_id, f.key, stage.units, stage.top_price, now_ms, None, None, None, None,
                     stage.expected, stage.expected - stage.cost, chain=chain, source="ah")
    posts = []
    for (i, price), (units, expected, sold, cost) in sorted(stage.posts.items()):
        o = opts[i]
        start, end, best = o.window if o.window else (None, None, None)
        posts.append(PlanAction("post", char_id, f.key, units, price, o.at_ms, start, end, best, sold / units,
                                expected, expected - cost, o.duration_h, chain))
    return [buy] + sorted(posts, key=lambda a: (a.at_ms, a.price))


def flip_offer(level: int, how_best: float, trend7: float, days_ahead: float, p: float, fallback: float, deposit: int,
               ref: int, max_trend_total: float, p10: int | None = None, med7: int | None = None,
               p90: int | None = None, closes: int | None = None) -> tuple[float, int]:
    """What one reference stack bought now could fetch at the item's best window, market-wide (SPEC §4.3, EX11).

    Args:
        level: Price level (med7, else med30), copper.
        how_best: The best window's best-hour price ratio.
        trend7: Trend, % per day (clamped by slot_price).
        days_ahead: Days until that hour.
        p: Chance a reference stack sells (the posting-now chance).
        fallback: Value per unsold unit, float copper.
        deposit: Deposit for the stack, copper.
        ref: Reference stack, units.
        max_trend_total: Config max_trend_total (slot_price's total trend clamp, TT7).
        p10: 30-day 10th percentile, copper (slot_price's clamp, TT22); None for none.
        med7: 7-day median, copper.
        p90: 30-day 90th percentile, copper.
        closes: Hourly closes in 30 days (TT23); None when unknown.

    Returns:
        (expected value per unit after fees, float copper; the window's unit price, copper).
    """
    price = slot_price(level, how_best, trend7, days_ahead, max_trend_total, p10, med7, p90, closes)
    return sellprob.expected_post(p, ref, price, fallback, deposit) / ref, price


def flip_now_offer(p: float, units: int, cheapest: int, relist: int, fallback: float, deposit: int,
                   min_flip_margin: float) -> float | None:
    """A flip seen in a live book: buy the cheapest level, relist now under the next one (SPEC §4.0, FN1).

    The addon's Prices.FlipNow is its twin; both use relist_price for relist (R28).

    Args:
        p: Chance the units sell posted now at relist with nothing ahead (the curve at 0 ahead, or
            default_chance for the cheapest position when volume is unknown).
        units: Units bought: the cheapest level's, at most one reference stack.
        cheapest: The cheapest level's unit price, copper.
        relist: The relist unit price, relist_price(second level, med7, p90, closes), copper.
        fallback: Value per unsold unit, float copper.
        deposit: Deposit for the posting-now window, copper.
        min_flip_margin: Config min_flip_margin.

    Returns:
        E_post(p, units, relist, fallback, deposit), float copper, when its profit over the cost cheapest × units
        is at least the margin (pays); None otherwise.
    """
    expected = sellprob.expected_post(p, units, relist, fallback, deposit)
    return expected if pays(expected, cheapest * units, min_flip_margin) else None
