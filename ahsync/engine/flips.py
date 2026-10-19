"""The weekly plan, part 2: buy to flip (SPEC §3.4C.2).

A flip buys auctions now, cheapest first, and posts them now, just under the listings
left after the buy, or for an upcoming selling window, whichever pays more (R24). It is
planned only when its expected profit, after fees and the chance to sell, is at least
min_flip_margin × its cost, and flips are taken best expected
profit × confidence first. Every flip stays within:
- its character's budget (SPEC §3.4C.4, R9);
- max_risk_pct of all your gold, so a buy may be smaller than a stack (§3.4 Risk, R19);
- the selling windows' capacity, shared with your own stock and every other character
  through one ledger (§3.4 market capacity, R16, PL4).
Pure: data in, numbers out.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from ahsync.engine import fees, sellprob
from ahsync.engine.plan import PlanAction, PlanSettings, PostOption, SellFacts, alternative, post_chance, \
    post_options, record_price, slot_price, window_id
from ahsync.engine.prices import post_price


@dataclass(frozen=True)
class FlipSettings:
    """The config values flips need."""

    min_flip_margin: float = 0.20      # config min_flip_margin: expected profit ÷ cost
    max_risk_pct: float = 25.0         # config max_risk_pct: the most one buy may spend, % of all your gold


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


def relisted(f_left: SellFacts, opts: list[PostOption], s: PlanSettings) -> list[PostOption]:
    """The options with posting now re-priced for the book left after the buy (SPEC §3.4C.2, R24).

    Args:
        f_left: The item, with the bought auctions out of its book.
        opts: post_options of the item (posting now included).
        s: Settings.

    Returns:
        The same options in order; posting now is post_price(the cheapest listing left, med7, p90) after your
        record, and has no room (cap 0) when nothing is left to price it by.
    """
    price = post_price(sellprob.cheapest_other(f_left.book, f_left.cur), f_left.med7, f_left.p90)
    out = []
    for o in opts:
        if o.window is None:
            o = replace(o, cap=0) if price is None else replace(o, price=record_price(f_left, price, s))
        out.append(o)
    return out


def _best_window(f: SellFacts, opts: list[PostOption], used: list[float], stage: _Stage, qty: int,
                 s: PlanSettings) -> tuple[int, float, float] | None:
    """The best way to sell one more auction's units, posting now or for a window, within capacity.

    Args:
        f: The item, with the bought auctions out of its book.
        opts: The options, posting now re-priced for that book (relisted).
        used: Units already planned per option by anyone (the ledger).
        stage: The flip so far.
        qty: Units of the auction.
        s: Settings.

    Returns:
        (option index, expected value, P), highest value first (ties: the earlier option); None when no option
        has room.
    """
    best = None
    for i, o in enumerate(opts):
        mine = sum(slot[0] for (j, _), slot in stage.posts.items() if j == i)
        if used[i] + mine + qty > o.cap:
            continue
        p = post_chance(f, o.price, qty, o.hours, used[i] + mine, s)
        deposit = fees.deposit_copper(f.vendor_sell, qty, o.duration_h)
        e = sellprob.expected_post(p, qty, o.price, alternative(f)[1], deposit)
        if best is None or e > best[1]:
            best = (i, e, p)
    return best


def build_flip(f: SellFacts, now_ms: int, s: PlanSettings, fs: FlipSettings, ledger: dict, spend_limit: int) -> _Stage | None:
    """The flip worth making for one item: auctions bought cheapest first while each still pays.

    An auction is added while it fits the spend limit and an option with room (posting now, under what is left
    after the buy, or a selling window), and while its own expected profit is at least min_flip_margin × its
    cost; prices only rise along the book, so the first one that fails ends the flip.

    Args:
        f: The item.
        now_ms: Now.
        s: Plan settings.
        fs: Flip settings.
        ledger: (item key, window id) -> units already planned; read only.
        spend_limit: The most this flip may spend: the character's budget and the risk limit, copper.

    Returns:
        The flip; None when not even one auction is worth it.
    """
    opts = post_options(f, now_ms, s)
    used = [ledger.get((f.key, window_id(o)), 0.0) for o in opts]
    stage = _Stage(0, 0.0, 0, 0, {})
    for n, (price, stack, _) in enumerate(auctions(f.book, f.cur, f.cur_qty)):
        cost = price * stack
        if stage.cost + cost > spend_limit:
            break
        left = _remaining_book(f, n + 1)
        here = relisted(left, opts, s)
        best = _best_window(left, here, used, stage, stack, s)
        if best is None or not pays(best[1], cost, fs.min_flip_margin):
            break
        i, e, p = best
        slot = stage.posts.setdefault((i, here[i].price), [0, 0.0, 0.0, 0])
        slot[0], slot[1], slot[2], slot[3] = slot[0] + stack, slot[1] + e, slot[2] + p * stack, slot[3] + cost
        stage = replace(stage, cost=stage.cost + cost, expected=stage.expected + e, units=stage.units + stack,
                        top_price=price)
    return stage if stage.units else None


def _flip_actions(f: SellFacts, stage: _Stage, char_id: str, now_ms: int, s: PlanSettings) -> list[PlanAction]:
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

    Returns:
        [buy, posts by time (ties: price)].
    """
    chain = f"flip:{f.key}"
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


def flip_plan(facts: dict[int, SellFacts], budgets: dict[str, int], total_gold: int, now_ms: int, s: PlanSettings,
              fs: FlipSettings, ledger: dict, exclude: set[int] = frozenset()) -> list[PlanAction]:
    """Every flip worth making, best expected profit × confidence first (SPEC §3.4C.2).

    Each item gets at most one flip, bought by the character with the most budget left
    (ties: character id), so two characters never fill the same demand (R16). The ledger
    is updated with the flips' posts.

    Args:
        facts: Item key -> what the plan knows.
        budgets: Character id -> copper it may spend; changed in place as flips spend.
        total_gold: All your characters' gold, copper (for the risk limit).
        now_ms: Now.
        s: Plan settings.
        fs: Flip settings.
        ledger: The shared capacity ledger (plan.plan_item).
        exclude: Items never to buy (rules no_buy and ignore, not-selling flags).

    Returns:
        Buys and posts, in the order the flips were taken.
    """
    risk_limit = int(total_gold * fs.max_risk_pct / 100)
    ranked = []
    for key in sorted(facts):
        if key in exclude or not budgets:
            continue
        stage = build_flip(facts[key], now_ms, s, fs, ledger, min(risk_limit, max(budgets.values())))
        if stage is not None:
            ranked.append((-(stage.expected - stage.cost) * facts[key].conf / 100, key))
    actions = []
    for _, key in sorted(ranked):
        char_id = min(budgets, key=lambda c: (-budgets[c], c))
        stage = build_flip(facts[key], now_ms, s, fs, ledger, min(risk_limit, budgets[char_id]))
        if stage is None:
            continue
        budgets[char_id] -= stage.cost
        opts = post_options(facts[key], now_ms, s)
        for (i, _), (units, *_rest) in sorted(stage.posts.items()):
            ledger[(key, window_id(opts[i]))] = ledger.get((key, window_id(opts[i])), 0.0) + units
        actions.extend(_flip_actions(facts[key], stage, char_id, now_ms, s))
    return actions


def flip_offer(level: int, how_best: float, trend7: float, days_ahead: float, p: float, fallback: float, deposit: int,
               ref: int) -> tuple[float, int]:
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

    Returns:
        (expected value per unit after fees, float copper; the window's unit price, copper).
    """
    price = slot_price(level, how_best, trend7, days_ahead)
    return sellprob.expected_post(p, ref, price, fallback, deposit) / ref, price


def flip_now_offer(p: float, units: int, cheapest: int, relist: int, fallback: float, deposit: int,
                   min_flip_margin: float) -> float | None:
    """A flip seen in a live book: buy the cheapest level, relist now under the next one (SPEC §4.0, FN1).

    The addon's Prices.FlipNow is its twin; both use post_price for relist.

    Args:
        p: Chance the units sell posted now at relist with nothing ahead (the curve at 0 ahead, or
            default_chance for the cheapest position when volume is unknown).
        units: Units bought: the cheapest level's, at most one reference stack.
        cheapest: The cheapest level's unit price, copper.
        relist: The relist unit price, post_price(second level, med7, p90), copper.
        fallback: Value per unsold unit, float copper.
        deposit: Deposit for the posting-now window, copper.
        min_flip_margin: Config min_flip_margin.

    Returns:
        E_post(p, units, relist, fallback, deposit), float copper, when its profit over the cost cheapest × units
        is at least the margin (pays); None otherwise.
    """
    expected = sellprob.expected_post(p, units, relist, fallback, deposit)
    return expected if pays(expected, cheapest * units, min_flip_margin) else None
