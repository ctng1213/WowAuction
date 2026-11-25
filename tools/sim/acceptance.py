"""SM21, the price acceptance against the truth (SPEC §8.10; `docs/plans/sold-price.md` §1d item 30(e), SP2d).

Until SP2d the sell chance read the same demand at every price, so a post at twice what an item sells for read the
chance of one at the level once nothing was ahead of it (the phantom flips of the 2026-11-18 review). P28 multiplies
each hour's demand by `a(p)`: 1 at or under the hour's level, falling with the sold curve above it (the continuous
form the user chose on 2026-11-22: the share of the curve's units at or above the price ÷ the share at or above the
level). SM21 measures it instead of arguing it: a poster lists one lot of every item every POST_EVERY_HOURS at each
of STEPS × the item's worth, into the live market, and the row sets the engine's chance for that post, read from
the newest document on the book at the posting time, against the share of those posts that sold. Two passes a
spread: on the live book, and with nothing ahead, where the poster first buys every other auction at or under its
price (a flip's relist; those buys enter the history as sales, which lowers the curve a little). Three readings of
the chance are reported for every post: today's (no acceptance), P28's (built), and the spec's `÷ 0.5` form (the
one SPEC §3.3.2 first wrote; reported beside, never built), plus today's and P28's computed with nothing ahead
whatever the book. The posts at or under the hour's level over their whole window read `a = 1`, so the gap there
is the queue model's alone: the under-read item 30(e) found (`S` counts the sales that happened, not the buyers a
cheaper post would find), reported, not fixed (the user decides a rule, if any, after the numbers).

The worlds are SP2s's shapes (`price_rules`): gear with a wide range of asks and 15% cancels, busy enough for a
sold curve (a curve needs 20 weighted clean units at a 1-day half-life, about 10 clean sales a day that the hourly
snapshots saw; at 20 listings and 15 buyers the clean units fell short, so the row runs 40 listings and 30 buyers a
day, where every item has one and the level sits under the planted worth, reported); the listing-limited gear (16
listings and 50 buyers a day), whose cheap listings sell within minutes, before any hourly snapshot sees them, so
the engine reads a tenth of its sales, no item has a curve and the level is the ask level (item 36(b): the row
prints the engine's sold/day against the truth, and the under-level read with the true unseen sales added to `S`,
capped at `S`, the candidate rule, from an untraded run of the same world); and the commodity. Steady, so the worth
is planted and known. The buyers' spread of willingness to pay is assumed, not measured (BUYER_SPREAD 0.25; 0.15
beside it), and the result is reported under both. Measured only: no pass band.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ahsync.engine import profile, sellprob
from ahsync.engine.capacity import slot_of
from ahsync.engine.sellprob import MarketSettings, QueueInputs, Supply
from ahsync.intel.contract import ItemIntel, MarketIntelligence, price_acceptance

from sim.inference import START_MS, Measure, commodity, gear, sold_rates
from sim.market import DAY_MS, HOUR_MS, Market, World
from sim.price_rules import BUYER_SPREAD, CANCEL_SHARE, CLOTH_WORTH, GEAR_WORTH, PRICE_MIX
from sim.runner import simulate
from sim.trader import client_band

DAYS, WARM_DAYS = 16, 8                          # the world's length; the first post
POST_EVERY_HOURS, POST_HOURS = 8, 8              # the poster's cadence and its auctions' duration (the posting-now window)
STEPS = (0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 2.0)   # a post's price ÷ the item's worth (0.7 and 0.8: under the level
                                                 # of the busy gear, whose floor sits about 0.77× the worth)
SPREADS = (BUYER_SPREAD, 0.15)                   # the buyers' spread: assumed, so both are run
REPLICATES = 16                                  # items per shape: each wake posts every step on two of them
SHAPES = {"gear": (40, 30), "gear, listing-limited": (16, 50), "commodity": (60, 120)}
# (listings a day, buyers a day): gear busy enough for a curve (about 20 clean sales a day seen by the snapshots; see the
# docstring); the listing-limited gear, whose cheap listings sell before a snapshot sees them (item 36(b): no curve, the
# ask level, and the under-read of item 30(e)); and SM20's commodity.
FORMS = ("today", "P28", "spec")                 # the chance's readings: no acceptance, built, the spec's ÷ 0.5
PASSES = {"live book": False, "nothing ahead": True}   # a pass's name -> whether the poster clears the book first
FLIPPER = "flipper"                              # who buys out the book in the nothing-ahead pass


@dataclass(frozen=True)
class Post:
    """One lot the poster listed, the chances it read for it, and what became of it."""

    key: int
    shape: str
    step: float                       # price ÷ the worth
    price: int                        # unit price, copper
    units: int
    sold: int                         # units bought before it expired
    chances: dict[str, float]         # FORMS, and "today, nothing ahead" and "P28, nothing ahead"
    ahead: float                      # units at or below the price when it was posted
    under: bool = False               # priced at or under the hour's level over its whole window (a = 1 by price)
    capped: bool = False              # above the level of an item without a curve: a price the plan never posts (item 41)


def acceptance_world(seed: int, replicates: int = REPLICATES, spread: float = BUYER_SPREAD
                     ) -> tuple[World, dict[int, tuple[str, int]]]:
    """SM21's market: `replicates` steady gear items and commodities with a planted worth.

    Args:
        seed: The world's seed.
        replicates: Items per shape.
        spread: The buyers' spread.

    Returns:
        (the World, item key -> (its shape, its index)).
    """
    items, keys = [], {}
    key = 2000
    for shape, (listings, buyers) in SHAPES.items():
        for n in range(replicates):
            key += 1
            keys[key] = (shape, n)
            if shape.startswith("gear"):
                items.append(gear(key, f"{shape} {n}", GEAR_WORTH, buyers, listings, price_mix=PRICE_MIX,
                                  cancel_share=CANCEL_SHARE, buyer_level=1.0, buyer_spread=spread))
            else:
                items.append(commodity(key, f"{shape} {n}", CLOTH_WORTH, buyers, listings, buyer_spread=spread))
    return World(seed, START_MS, DAYS, tuple(items)), keys


def live_book(market: Market, key: int, now_ms: int) -> list[tuple[int, int, int, int]]:
    """The others' auctions of an item as a document's book rows, cheapest first.

    Args:
        market: The market, run up to now_ms.
        key: Item key.
        now_ms: Now, UTC ms (the time-left bands).

    Returns:
        (price, units, band, 1) per auction.
    """
    return sorted((a.price, a.units, client_band(a.expires_ms - now_ms), 1)
                  for a in market.by_key[key].values() if a.seller != "us")


def spec_acceptance(price: int, level: int | None, curve: Sequence[int] | None, how: float) -> float:
    """The spec's first form of `a(p)` (SPEC §3.3.2 as merged on 2026-11-21): the share at or above p ÷ 0.5.

    Reported beside P28's continuous form, never built: where the level sits above the curve's 50th, this form
    jumps just above the level (the user, 2026-11-22).

    Args:
        price: The post's unit price, copper.
        level: The price level, copper; None without one.
        curve: The sold curve, copper; None without one.
        how: The hour's price ratio.

    Returns:
        a in [0, 1].
    """
    if level is None or curve is None:
        return 1.0
    hour_level = math.floor(level * how + 0.5)
    if price <= hour_level:
        return 1.0
    scaled = [math.floor(c * how + 0.5) for c in curve]
    return min(1.0, sellprob.share_at_or_above(price, scaled) / 0.5)


def queue_chance(item: ItemIntel, book: Sequence[tuple[int, int, int, int]] | None, price: int, units: int,
                 now_ms: int, settings: MarketSettings, factor: Callable[[int, float], float]) -> float:
    """The posting-now simulation with an hourly acceptance of one's own: `posting_chance` with `factor` in place
    of P28's `acceptance` (the test checks the two agree when `factor` is `sellprob.acceptance`).

    Args:
        item: The item's intelligence (the rates, the sales pattern, u(p), the level, curve and pattern).
        book: The others' auctions, (price, units, band, count); None for nothing ahead.
        price: The post's unit price, copper.
        units: Units posted.
        now_ms: Now, UTC ms.
        settings: The document's settings.
        factor: (price, the hour's `how`) -> the demand's multiplier, given the item's level and curve.

    Returns:
        P in [0, 1].
    """
    acc = price_acceptance(item)
    competitors = None if book is None else [(p, float(stack * count), band) for p, stack, band, count in book]
    ahead = sellprob.ahead_of(competitors, price, None, None)
    hours = sellprob.posting_now_window(now_ms, settings.sell_window_hours)
    sales_how = profile.unpack(item.sales_how, item.sales_weeks).values if item.sales_how else sellprob.FLAT_HOW
    how = acc.how or sellprob.FLAT_HOW
    demand = tuple(d * factor(price, how[slot_of(h)]) for d, h in zip(sellprob.demand_for(hours, sales_how), hours))
    u = settings.default_undercut_share if item.undercut_share is None else item.undercut_share
    return sellprob.simulate(QueueInputs(item.sold_per_day, item.listed_per_day or 0.0, u, demand, units, ahead,
                                         sellprob.demand_spread(acc)))


def under_level(item: ItemIntel, price: int, now_ms: int, settings: MarketSettings) -> bool:
    """Whether a post sits at or under the hour's level in every hour of its window (the acceptance reads 1).

    An item without a curve reads `a = 1` at every price, but a post above its level is not "under": the plan never
    posts one there (its high bound is the level, SPEC §3.3), so it is counted apart (plan §1d item 41).

    Args:
        item: The item's intelligence.
        price: The post's unit price, copper.
        now_ms: Now, UTC ms.
        settings: The document's settings (the window).

    Returns:
        True at or under the level over the window, and for an item without a level.
    """
    acc = price_acceptance(item)
    if acc.level is None:
        return True
    how = acc.how or sellprob.FLAT_HOW
    return all(price <= math.floor(acc.level * how[slot_of(h)] + 0.5)
               for h in sellprob.posting_now_window(now_ms, settings.sell_window_hours))


def chances(item: ItemIntel, book: list[tuple[int, int, int, int]], price: int, units: int, now_ms: int,
            settings: MarketSettings, unseen: float = 0.0) -> dict[str, float]:
    """Every reading of one post's chance (FORMS on the book; today's and P28's with nothing ahead; P28 with the
    unseen sales added to the demand, item 36(b)'s candidate rule). "today" is the engine without the acceptance
    (no level), so it keeps the demand spread an item with a curve reads (P31) and differs from P28 only by `a`.

    Args:
        item: The item's intelligence.
        book: The others' auctions now.
        price: The post's unit price, copper.
        units: Units posted.
        now_ms: Now, UTC ms.
        settings: The document's settings.
        unseen: The item's sales a day the snapshots never saw (the truth's, since the engine estimates none for
            gear, BZ45); 0 for none.

    Returns:
        Reading -> P.
    """
    acc = price_acceptance(item)
    sales_how = profile.unpack(item.sales_how, item.sales_weeks).values if item.sales_how else None
    out = {}
    for name, rows in (("", book), (", nothing ahead", None)):
        cur = min(p for p, _, _, _ in rows) if rows else None
        supply = Supply(item.sold_per_day, item.listed_per_day, tuple(rows) if rows is not None else None, cur,
                        sum(u for p, u, _, _ in rows if p == cur) if rows else None)
        for form, use in (("today", sellprob.Acceptance(None, acc.curve, acc.how, acc.commodity)), ("P28", acc)):   # P31's spread in both
            out[form + name] = sellprob.posting_chance(supply, price, units, now_ms, settings, sales_how,
                                                       item.undercut_share, use)[0]
    out["spec"] = queue_chance(item, book, price, units, now_ms, settings,
                               lambda p, how: spec_acceptance(p, acc.level, acc.curve, how))
    if unseen:                                     # item 36(b)'s candidate: S' = S + min(S, the unseen sales a day)
        more = Supply(item.sold_per_day + min(item.sold_per_day, unseen), item.listed_per_day, tuple(book),
                      min(p for p, _, _, _ in book) if book else None,
                      sum(u for p, u, _, _ in book if p == min(p for p, _, _, _ in book)) if book else None)
        out["P28, with unseen"] = sellprob.posting_chance(more, price, units, now_ms, settings, sales_how,
                                                          item.undercut_share, acc)[0]
    else:
        out["P28, with unseen"] = out["P28"]
    return out


class Acceptor:
    """The paper trader (`runner.Agent`): one lot of every item every POST_EVERY_HOURS, at a step × its worth."""

    def __init__(self, keys: dict[int, tuple[str, int]], clear_ahead: bool = False,
                 unseen: dict[int, float] | None = None) -> None:
        """A poster that starts after the warm-up.

        Args:
            keys: Item key -> (shape, index), from acceptance_world.
            clear_ahead: Buy every other auction at or under the post's price before posting (the nothing-ahead pass).
            unseen: Item key -> its true unseen sales a day (from a first run of the same world), for the "P28, with
                unseen" reading; None for none.
        """
        self.keys = keys
        self.clear_ahead = clear_ahead
        self.unseen = unseen or {}
        self.wake_ms: int = START_MS + WARM_DAYS * DAY_MS + 10 * HOUR_MS + 40 * 60_000   # after the daily run
        self.wakes = 0
        self.posted: list[tuple[int, Post]] = []                   # (auction id, the post with sold 0)

    def next_wake(self) -> int | None:
        """When it next posts.

        Returns:
            UTC ms; None once an auction could no longer end inside the world.
        """
        return self.wake_ms if self.wake_ms < START_MS + (DAYS - 1) * DAY_MS else None

    def act(self, market: Market, documents: list[MarketIntelligence], now_ms: int) -> None:
        """Post one lot of every item the newest document has sales for, each at the step its turn gives.

        Args:
            market: The market, run up to now_ms.
            documents: The documents published so far.
            now_ms: The wake's time, UTC ms.
        """
        self.wake_ms += POST_EVERY_HOURS * HOUR_MS
        self.wakes += 1
        if not documents:
            return
        doc = documents[-1]
        settings = MarketSettings.from_dict(doc.settings)
        for key, (shape, n) in sorted(self.keys.items()):
            item = doc.items.get(key)
            if item is None or item.sold_per_day is None:
                continue
            step = STEPS[(n + self.wakes) % len(STEPS)]
            price = max(1, math.floor(market.items[key].fair_price * step + 0.5))
            units = 5 if market.items[key].commodity else 1
            if self.clear_ahead:
                for a in list(market.by_key[key].values()):
                    if a.seller != "us" and a.price <= price:
                        market.take(a.id, a.units, FLIPPER)
            book = live_book(market, key, now_ms)
            read = chances(item, book, price, units, now_ms, settings, self.unseen.get(key, 0.0))
            auction = market.post(key, units, price, POST_HOURS, "us")
            ahead = float(sum(u for p, u, _, _ in book if p <= price))
            under = under_level(item, price, now_ms, settings)
            capped = not under and price_acceptance(item).curve is None
            self.posted.append((auction.id, Post(key, shape, step, price, units, 0, read, ahead, under, capped)))


def trade(seed: int, folder: Path, replicates: int = REPLICATES, spread: float = BUYER_SPREAD,
          clear_ahead: bool = False) -> tuple[list[Post], MarketIntelligence, dict[str, tuple[float, float]]]:
    """One pass's run: the world traded by the poster, through the unchanged cloud job.

    Args:
        seed: The world's seed.
        folder: An empty folder for the run.
        replicates: Items per shape.
        spread: The buyers' spread.
        clear_ahead: The nothing-ahead pass (PASSES).

    Returns:
        (every post with the units it sold, the last daily document, per shape the mean sold/day the engine reads
        against the truth's: a sale nobody's snapshot saw is not in the engine's).
    """
    world, keys = acceptance_world(seed, replicates, spread)
    quiet = simulate(world, folder / "quiet", agent=None)           # the same world untraded: each item's unseen sales
    unseen = {key: max(0.0, truth - engine) for key in keys for engine, truth in [sold_rates(quiet, key)]}
    poster = Acceptor(keys, clear_ahead, unseen)
    run = simulate(world, folder, agent=poster)
    rates: dict[str, tuple[float, float]] = {}
    for shape in SHAPES:
        pairs = [sold_rates(run, key) for key, (s, _) in sorted(keys.items()) if s == shape]
        rates[shape] = (sum(e for e, _ in pairs) / len(pairs), sum(t for _, t in pairs) / len(pairs))
    bought: dict[int, int] = {}
    for event in run.truth.events:
        if event.kind == "buy":
            bought[event.auction] = bought.get(event.auction, 0) + event.units
    posts = [Post(p.key, p.shape, p.step, p.price, p.units, min(p.units, bought.get(auction, 0)), p.chances, p.ahead,
                  p.under, p.capped) for auction, p in poster.posted]
    return posts, run.documents[-1], rates


def share_sold(posts: Sequence[Post]) -> float | None:
    """The share of the posts' units that sold.

    Args:
        posts: The posts.

    Returns:
        Units sold ÷ units posted; None without posts.
    """
    units = sum(p.units for p in posts)
    return sum(p.sold for p in posts) / units if units else None


def mean_chance(posts: Sequence[Post], form: str) -> float | None:
    """The mean of one reading of the chance over the posts.

    Args:
        posts: The posts.
        form: A key of Post.chances.

    Returns:
        The mean; None without posts.
    """
    return sum(p.chances[form] for p in posts) / len(posts) if posts else None


def curve_share(doc: MarketIntelligence, keys: Sequence[int]) -> float | None:
    """The share of some items that the document gives a sold curve.

    Args:
        doc: The document.
        keys: Item keys.

    Returns:
        The share; None without keys.
    """
    return sum(1 for k in keys if k in doc.items and doc.items[k].sold_curve is not None) / len(keys) if keys else None


def sm21(folder: Path, seed: int = 1, replicates: int = REPLICATES, spreads: Sequence[float] = SPREADS
         ) -> list[Measure]:
    """SM21: P28's chance against the true share sold, per shape and price step, under each buyers' spread.

    Args:
        folder: An empty folder for the runs (one sub-folder a spread).
        seed: The world's seed.
        replicates: Items per shape.
        spreads: The buyers' spreads to run.

    Returns:
        Per spread, from the live-book pass: the share of each shape's items with a sold curve (the acceptance needs
        one), the sold/day the engine reads against the truth's (what the snapshots did not see is not in it) and
        the level against the planted worth; then per pass (PASSES) and shape: per step P28's mean chance against
        the share sold, with today's and the spec's readings and the two computed with nothing ahead in the
        measure's words, and the read on the posts at or under the hour's level over their whole window (a = 1 by
        price), the queue model's alone. All reported.
    """
    out = []
    for spread in spreads:
        for name, clear_ahead in PASSES.items():
            posts, doc, rates = trade(seed, folder / f"spread-{spread:.2f}-{name.replace(' ', '-')}", replicates,
                                      spread, clear_ahead)
            for shape in SHAPES:
                if not clear_ahead:
                    out += world_measures(posts, doc, rates, shape, spread)
                out += pass_measures(posts, name, shape, spread)
    return out


def world_measures(posts: Sequence[Post], doc: MarketIntelligence, rates: dict[str, tuple[float, float]], shape: str,
                   spread: float) -> list[Measure]:
    """One shape's world measures: the curve share, sold/day engine against truth, the level against the worth.

    Args:
        posts: The pass's posts.
        doc: The last daily document.
        rates: From trade.
        shape: A SHAPES name.
        spread: The buyers' spread.

    Returns:
        Three reported measures.
    """
    keys = sorted({p.key for p in posts if p.shape == shape})
    levels = [doc.items[k].level for k in keys if doc.items[k].level is not None]
    worth = GEAR_WORTH if shape.startswith("gear") else CLOTH_WORTH
    return [Measure("SM21", f"world: items with a sold curve, {shape}, spread {spread:.2f}", curve_share(doc, keys), 1.0,
                    "reported"),
            Measure("SM21", f"world: sold/day the engine reads against the truth, {shape}, spread {spread:.2f}",
                    *rates[shape], "reported"),
            Measure("SM21", f"world: the level ÷ the planted worth, {shape}, spread {spread:.2f} (the steps are × the "
                    f"worth; a = 1 at or under the level)", sum(levels) / len(levels) / worth if levels else None, 1.0,
                    "reported")]


def pass_measures(posts: Sequence[Post], name: str, shape: str, spread: float) -> list[Measure]:
    """One pass's measures for a shape: per step the chance against the share sold, then the under-level read.

    Args:
        posts: The pass's posts.
        name: The pass's name (PASSES).
        shape: A SHAPES name.
        spread: The buyers' spread.

    Returns:
        len(STEPS) + 1 reported measures.
    """
    out = []
    for step in STEPS:
        at = [p for p in posts if p.shape == shape and p.step == step]
        words = "; ".join(f"{form} {mean_chance(at, form):.2f}" for form in
                          ("today", "spec", "today, nothing ahead", "P28, nothing ahead")) if at else "no posts"
        clear = sum(1 for p in at if p.ahead == 0)
        capped = sum(1 for p in at if p.capped)
        out.append(Measure("SM21", f"P28's chance against the share sold, {name}: {shape}, {step:.2f}× the worth, "
                           f"spread {spread:.2f} ({len(at)} posts, {clear} with nothing ahead, {capped} above the level "
                           f"without a curve, where the plan never posts; {words})",
                           mean_chance(at, "P28"), share_sold(at), "reported"))
    under = [p for p in posts if p.shape == shape and p.under]
    steps = sorted({p.step for p in under})
    more = mean_chance(under, "P28, with unseen")
    out.append(Measure("SM21", f"the queue model's read at or under the hour's level over the window (a = 1 by price), "
                       f"{name}: {shape}, spread {spread:.2f} ({len(under)} posts, at "
                       f"{', '.join(f'{s:.2f}×' for s in steps) or 'no step'}; with the true unseen sales added to S, "
                       f"capped at S: {more:.2f})" if under else
                       f"the queue model's read at or under the hour's level over the window (a = 1 by price), {name}: "
                       f"{shape}, spread {spread:.2f} (no posts)",
                       mean_chance(under, "P28"), share_sold(under), "reported"))
    return out


ACCEPTANCE_ROWS = {"SM21": sm21}          # tools/sim_report merges them with its ROWS
