"""SM20, the price-level rules paper-traded (SPEC §8.10; `docs/plans/sold-price.md` §1d, the 2026-11-21 review).

The review found today's post-price ceiling, max(med7, p90_30) of asks, at a median 2.7× what sold on the real
market. SM20 measures what each candidate level earns instead of arguing it: a poster lists one lot of every item
every 12 hours at `min(the cheapest other auction − 1 c, the rule's level)` and the row reports what a post earned
against the item's worth. The rules:

- `today`: the built rule (SPEC §3.4, `prices.post_price`: the document's level with this hour's pattern, within
  its bounds; until SP2c the ask ceiling, max(med7, p90_30)).
- `ask blend`: the 7, 14 and 30-day ask medians weighted 4:2:1.
- `sold 7d`: the published `sold_med7` at 5 or more sold units in 7 days, else today's.
- `sold half-life`: the sold median over 30 days with a unit's weight halving every day, at 3 or more sold units
  in those days, else the ask blend. SP2's level as the user settled it (2026-11-21, plan §1d item 26), without
  its pattern; a sale's age is counted in days here, the stored sold prices being daily until SP2b.

The worlds are shaped to the real summary of 2026-11-21 (gear at 5–50 sales a week: med7 1.22–1.30× the sold
median, p90_30 2.6–3.0×): sellers who price over a wide range, a high ask that sits unsold, 15% cancels, a
commodity under lone dumps (SM18's), and a commodity shaped like the real ones (plan §1d item 27), which opened the
season at about twice what they sold for a fortnight later (measured on the 354 snapshots of 2026-11-06..21: over
757 commodities the 90th percentile of the hourly closes is a median 1.93× the last week's median close, and four
fifths of those dear hours are in the first five days). Each item comes steady, with its worth doubling on day 11,
with it falling 40% then, and sliding 2.5% a day from the first day (the real fortnight: busy commodities fell a
median 17% a week). `calibration` sets each shape's ratios beside the real ones and reports the share of the
counted sold units that nobody bought (expiries and cancels read as sales; about 10% on the real market, item
25(h)). Measured only: the level is built in SP2, and the engine's constants are real data's (plan §2).
"""
from __future__ import annotations

import sqlite3
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ahsync.engine import prices
from ahsync.engine.lifetime_prices import weighted_median
from ahsync.intel.contract import ItemIntel, MarketIntelligence, pattern_at, published_level

from sim.inference import GOLD, START_MS, Measure, commodity, gear
from sim.market import DAY_MS, HOUR_MS, Market, Planted, World
from sim.pattern_world import factor_at, slide
from sim.runner import Run, simulate

DAYS, WARM_DAYS, CHANGE_DAY = 16, 8, 11          # the world's length; the first post; the day the worth changes
POST_EVERY_HOURS, POST_HOURS = 12, 8             # the poster's cadence and its auctions' duration (SPEC §3.3.2's window)
GEAR_WORTH, CLOTH_WORTH = 2 * GOLD, 100          # copper
# Gear shapes: (listings a day, buyers a day, the high ask × the worth, the durations in hours it is posted for, back
# to back, each day), tuned through the cloud job to REAL_RATIOS below; the names say about how many units sell a week.
# Retuned at SP2s (plan §1d item 27): the first shapes' gear med7 ran 1.33–1.56× the sold median against the real
# 1.01–1.30 (item 25(i)), so the cheap listings now sell sooner after the high ask's few hours.
SHAPES = {"gear 2 a week": (0.6, 4, 1.6, (2, 2)), "gear 6 a week": (1.7, 10, 3.0, (2, 2)),
          "gear 14 a week": (3.6, 20, 3.0, (2, 2)), "gear 30 a week": (8.5, 30, 3.3, (8, 8))}
CLOTH, REAL = "commodity", "commodity, real-shaped"
# A seller's price ÷ the cheapest other auction's: a third at it, a quarter under it, the rest up to 1.6× above.
PRICE_MIX = ((0.35, 1.0, 1.0), (0.25, 0.7, 1.0), (0.20, 1.0, 1.4), (0.20, 1.4, 1.6))
REGIMES = ("steady", "rise", "drop", "fall")
STEP = {"rise": 2.0, "drop": 0.6}                # the worth's factor from CHANGE_DAY on
FALL_PER_DAY = 0.025                             # the fall regime: the worth's loss a day, from the first day
LAUNCH = (2.4, 6)                                # the real-shaped commodity opens at × its worth, down to it in days
REAL_BUYS, REAL_LISTINGS = 120, 60               # the real-shaped commodity's buyers and posts a day
BUYER_SPREAD = 0.25                              # assumed, not measured: sd of log(a buyer's maximum ÷ the worth)
CANCEL_SHARE = 0.15
DUMP = (5, 0.6, 3, 2)                            # the commodity's lone dump: units, × the worth, every h, up h
WEEK_UNITS = 5.0                                 # sold units in 7 days the `sold 7d` rule needs
LEVEL_UNITS = 3.0                                # sold units in 30 days SP2's level needs (plan §1d item 26(c))
HALF_LIFE_DAYS = 1.0                             # SP2's weighting (item 26(b))
LEVEL_DAYS = 30                                  # the days of sold prices a half-life level reads
ASK_WEIGHTS = (4, 2, 1)                          # the ask blend's weights on med7, med14 and med30
RULES = ("today", "ask blend", "sold 7d", "sold half-life")
SAME_BAND, SAME_SHARE = 0.02, 0.85               # SP2c: the built level within ±2% of the half-life rule's on 85%+ of the same steady
                                                 # posts (seeds 1-3 at 4 replicates: busy gear 0.89-0.94, hours against days on the rest)
SEGMENTS = ("steady", "after the rise", "after the drop", "falling")
GROUPS = ("gear 2 a week", "gear at 5+ a week", CLOTH, REAL)
# The real summary's ratios each shape is set beside (plan §1d items 1, 25(e) and 25(i)): (med7 ÷ the sold median,
# p90_30 ÷ the sold median), by sold units in 7 days for gear.
REAL_RATIOS = {"gear 2 a week": (1.01, 1.6), "gear 6 a week": (1.22, 2.6), "gear 14 a week": (1.30, 2.8),
               "gear 30 a week": (1.27, 3.0), CLOTH: (1.02, 1.89), REAL: (1.02, 1.89)}
REAL_AMBIGUOUS = 0.10                            # the real share of sold units that may be expiries (item 25(h))


@dataclass(frozen=True)
class Kind:
    """What one simulated item is: its shape, its regime and its worth before the change."""

    shape: str                        # a SHAPES name, CLOTH or REAL
    regime: str                       # a REGIMES name
    worth: int                        # copper: the worth its shifts multiply
    shifts: tuple[tuple[int, float, float], ...] = ()   # how the worth moves (`regime_shifts`)

    def worth_at(self, at_ms: int) -> float:
        """The item's worth at a time.

        Args:
            at_ms: UTC ms.

        Returns:
            Copper: the worth, times its shifts' factor then.
        """
        return self.worth * factor_at(self.shifts, at_ms)


def regime_shifts(regime: str, launch: bool = False) -> tuple[tuple[int, float, float], ...]:
    """How an item's worth moves over the world (`market.Item.shifts`).

    Args:
        regime: A REGIMES name: steady; a step on CHANGE_DAY (rise, drop); or a slide from the first day (fall).
        launch: The item opens at LAUNCH's multiple of its worth and falls to it over LAUNCH's days (the
            real-shaped commodity), with the regime on top.

    Returns:
        (time, price factor, 1.0) in time order: one step for a rise or a drop alone, a step an hour with a slide
        or a launch, () for a steady item without a launch.

    Raises:
        KeyError: An unknown regime.
    """
    if regime not in REGIMES:
        raise KeyError(regime)
    change = START_MS + CHANGE_DAY * DAY_MS
    if not launch and regime != "fall":
        return ((change, STEP[regime], 1.0),) if regime in STEP else ()
    multiple, days = LAUNCH
    opening = slide(START_MS, START_MS + days * DAY_MS, 1 - (1 / multiple) ** (1 / days), multiple) if launch else ()
    falling = slide(START_MS, START_MS + DAYS * DAY_MS, FALL_PER_DAY) if regime == "fall" else ()
    hours = (START_MS + hour * HOUR_MS for hour in range(DAYS * 24))
    return tuple((at, factor_at(opening, at) * factor_at(falling, at) * (STEP.get(regime, 1.0) if at >= change else 1.0),
                  1.0) for at in hours)


@dataclass(frozen=True)
class Post:
    """One lot the poster listed, and what became of it."""

    key: int
    at_ms: int
    price: int                        # unit price, copper
    units: int
    sold: int                         # units bought before it expired
    # A `today` run only, when the document and the prototype both read a sold level (SP2c's "same prices" check;
    # None otherwise): the document's level, the sold half-life prototype's level for the same post, and the price
    # without the hour's pattern (min(cheapest − 1, level)).
    level: int | None = None
    twin: int | None = None
    plain: int | None = None


def _high_asks(key: int, kind: Kind, multiple: float, durations: Sequence[int]) -> list[Planted]:
    """A gear item's high ask, posted daily at a multiple of the worth then.

    Args:
        key: Item key.
        kind: The item.
        multiple: The ask ÷ the worth.
        durations: The auctions it is posted as each day, back to back from 01:00 UTC: hours, each 2, 8 or 24.

    Returns:
        The planted auctions over the world's days.
    """
    out = []
    for day in range(DAYS):
        at = START_MS + (1 + 24 * day) * HOUR_MS
        price = int(kind.worth_at(at) * multiple)
        for hours in durations:
            out.append(Planted(at, key, 1, price, hours))
            at += hours * HOUR_MS
    return out


def rules_world(seed: int, replicates: int = 4, spread: float = BUYER_SPREAD) -> tuple[World, dict[int, Kind]]:
    """SM20's market: every gear shape and the commodity, in each regime, `replicates` items of each.

    Args:
        seed: The world's seed.
        replicates: Items per shape and regime (the report's 4; a test's 1).
        spread: The buyers' spread (BUYER_SPREAD; the review also ran 0.15).

    Returns:
        (the World, item key -> its Kind).
    """
    items, planted, kinds = [], [], {}
    key = 1000
    for regime in REGIMES:
        shifts = regime_shifts(regime)
        for shape, (listings, buyers, multiple, durations) in SHAPES.items():
            for n in range(replicates):
                key += 1
                kinds[key] = Kind(shape, regime, GEAR_WORTH, shifts)
                items.append(gear(key, f"{shape}, {regime} {n}", GEAR_WORTH, buyers, listings, price_mix=PRICE_MIX,
                                  cancel_share=CANCEL_SHARE, buyer_level=1.0, buyer_spread=spread, shifts=shifts))
                planted += _high_asks(key, kinds[key], multiple, durations)
        for n in range(replicates):
            key += 1
            kinds[key] = Kind(CLOTH, regime, CLOTH_WORTH, shifts)
            items.append(commodity(key, f"{CLOTH}, {regime} {n}", CLOTH_WORTH, 120, 60, buyer_spread=spread,
                                   shifts=shifts))
            units, share, every, hours = DUMP
            for hour in range(0, DAYS * 24, every):
                at = START_MS + hour * HOUR_MS + 50 * 60_000
                planted.append(Planted(at, key, units, int(kinds[key].worth_at(at) * share), hours))
        for n in range(replicates):
            key += 1
            kinds[key] = Kind(REAL, regime, CLOTH_WORTH, regime_shifts(regime, launch=True))
            items.append(commodity(key, f"{REAL}, {regime} {n}", CLOTH_WORTH, REAL_BUYS, REAL_LISTINGS,
                                   buyer_spread=spread, shifts=kinds[key].shifts))
    return World(seed, START_MS, DAYS, tuple(items), planted=tuple(planted)), kinds


def sold_rows(history: Path, today: int, days: int = LEVEL_DAYS) -> dict[int, list[tuple[int, float, int]]]:
    """The sold prices a run's history holds for the last days (`sold_prices_d1`, SPEC §2.4 Sold prices).

    Args:
        history: The run's `state/history.db`.
        today: The day number (UTC ms ÷ DAY_MS) of the document read with them.
        days: Days back from today, today included.

    Returns:
        Item key -> (unit price copper, units, age in days) per stored price; empty without the file.
    """
    out: dict[int, list[tuple[int, float, int]]] = {}
    if not history.exists():
        return out
    db = sqlite3.connect(f"file:{history.as_posix()}?mode=ro", uri=True)
    try:
        for key, day, text in db.execute("SELECT key, day, prices FROM sold_prices_d1 WHERE day > ? ORDER BY key, day",
                                         (today - days,)):
            for part in filter(None, text.split(",")):
                price, units = part.split(":")
                out.setdefault(key, []).append((int(price), float(units), today - day))
    finally:
        db.close()
    return out


def sold_level(rows: Sequence[tuple[int, float, int]], half_life_days: float = HALF_LIFE_DAYS
               ) -> tuple[int | None, float]:
    """The recency-weighted sold median: a unit's weight halves every half_life_days of age.

    Args:
        rows: (unit price copper, units, age in days) per sold price.
        half_life_days: Days for a unit's weight to halve.

    Returns:
        (the weighted median by SPEC §3.3's rule, copper, or None without units; the unweighted units behind it).
    """
    level, _ = weighted_median((price, units * 0.5 ** (age / half_life_days)) for price, units, age in rows)
    return level, sum(units for _, units, _ in rows)


def ask_blend(item: ItemIntel) -> int | None:
    """The 7, 14 and 30-day ask medians weighted 4:2:1, over the ones known.

    Args:
        item: The item's published intelligence.

    Returns:
        Copper, floored; None when none of the three is known.
    """
    parts = [(w, v) for w, v in zip(ASK_WEIGHTS, (item.med7, item.med14, item.med30)) if v is not None]
    return sum(w * v for w, v in parts) // sum(w for w, _ in parts) if parts else None


def rule_price(rule: str, item: ItemIntel, cheapest: int | None, sold: Sequence[tuple[int, float, int]],
               at_ms: int) -> int | None:
    """The post price a rule gives: the undercut, held under the rule's level.

    Args:
        rule: One of RULES.
        item: The item's published intelligence.
        cheapest: The cheapest other auction's unit price now, copper, or None.
        sold: The item's `sold_rows`.
        at_ms: The posting time, UTC ms (`today` reads the hour's pattern).

    Returns:
        Copper; None when the rule knows no price.

    Raises:
        KeyError: An unknown rule.
    """
    if rule not in RULES:
        raise KeyError(rule)
    if rule == "today" or (rule == "sold 7d" and (item.sold_med7 is None or item.sold_units7 < WEEK_UNITS)):
        priced = published_level(item)
        return prices.post_price(cheapest, priced.level, pattern_at(item, at_ms), priced.low, priced.high)
    level = item.sold_med7 if rule == "sold 7d" else None
    if rule == "sold half-life":
        level, units = sold_level(sold)
        level = level if units >= LEVEL_UNITS else None
    if level is None:                 # the ask blend, and the half-life's fallback
        level = ask_blend(item)
        return prices.post_price(cheapest, level, 1.0, level, level)
    return level if cheapest is None else min(cheapest - 1, level)


class Poster:
    """The paper trader (`runner.Agent`): one lot of every item every POST_EVERY_HOURS, priced by one rule."""

    def __init__(self, rule: str, kinds: dict[int, Kind], folder: Path) -> None:
        """A poster that starts after the warm-up.

        Args:
            rule: One of RULES.
            kinds: The world's items.
            folder: The run's folder (its history holds the sold prices).
        """
        self.rule, self.kinds, self.folder = rule, kinds, folder
        self.wake_ms: int = START_MS + WARM_DAYS * DAY_MS + 10 * HOUR_MS + 40 * 60_000   # after the daily run
        self.posted: list[tuple] = []            # (auction id, key, at ms, price, units, level, twin, plain)
        self._sold: dict[int, list[tuple[int, float, int]]] = {}
        self._sold_of = -1                                         # the document the sold rows were read for

    def next_wake(self) -> int | None:
        """When it next posts.

        Returns:
            UTC ms; None once an auction could no longer end inside the world.
        """
        return self.wake_ms if self.wake_ms < START_MS + (DAYS - 1) * DAY_MS else None

    def act(self, market: Market, documents: list[MarketIntelligence], now_ms: int) -> None:
        """Post one lot of every item the newest document prices (5 units of a commodity, 1 of gear).

        Args:
            market: The market, run up to now_ms.
            documents: The documents published so far.
            now_ms: The wake's time, UTC ms.
        """
        self.wake_ms += POST_EVERY_HOURS * HOUR_MS
        if not documents:
            return
        doc = documents[-1]
        if self._sold_of != doc.built_ms:
            self._sold_of = doc.built_ms
            self._sold = sold_rows(self.folder / "data" / "state" / "history.db", doc.built_ms // DAY_MS)
        for key in sorted(self.kinds):
            item = doc.items.get(key)
            if item is None:
                continue
            others = [a.price for a in market.by_key[key].values() if a.seller != "us"]
            cheapest, rows = min(others) if others else None, self._sold.get(key, ())
            price = rule_price(self.rule, item, cheapest, rows, now_ms)
            if price is not None and price >= 1:
                units = 5 if market.items[key].commodity else 1
                auction = market.post(key, units, price, POST_HOURS, "us")
                level = twin = plain = None
                if self.rule == "today" and item.level_source == "sold":
                    twin, twin_units = sold_level(rows)                          # the same post under both rules
                    if twin_units >= LEVEL_UNITS:
                        level = item.level
                        plain = level if cheapest is None else min(cheapest - 1, level)
                    else:
                        twin = None
                self.posted.append((auction.id, key, now_ms, price, units, level, twin, plain))


def trade(rule: str, seed: int, folder: Path, replicates: int = 4, spread: float = BUYER_SPREAD
          ) -> tuple[list[Post], dict[int, Kind]]:
    """One rule's run: the world traded by a poster, through the unchanged cloud job.

    Args:
        rule: One of RULES.
        seed: The world's seed.
        folder: An empty folder for the run.
        replicates: Items per shape and regime.
        spread: The buyers' spread.

    Returns:
        (every post with the units it sold, the world's items).
    """
    world, kinds = rules_world(seed, replicates, spread)
    poster = Poster(rule, kinds, folder)
    run = simulate(world, folder, agent=poster)
    bought: dict[int, int] = {}
    for event in run.truth.events:
        if event.kind == "buy":
            bought[event.auction] = bought.get(event.auction, 0) + event.units
    return [Post(key, at, price, units, bought.get(auction, 0), level, twin, plain)
            for auction, key, at, price, units, level, twin, plain in poster.posted], kinds


def segment_of(kind: Kind, at_ms: int) -> str | None:
    """Which SEGMENTS column a post counts in.

    Args:
        kind: The post's item.
        at_ms: When it was posted, UTC ms.

    Returns:
        "falling" for an item of the fall regime; "steady" for a steady item and for a rising or dropping one
        before the change, else the regime's column.
    """
    if kind.regime == "fall":
        return "falling"
    if kind.regime == "steady" or at_ms < START_MS + CHANGE_DAY * DAY_MS:
        return "steady"
    return "after the rise" if kind.regime == "rise" else "after the drop"


def group_of(kind: Kind) -> str:
    """Which GROUPS row an item counts in.

    Args:
        kind: The item.

    Returns:
        The thin gear shape, the other gear shapes together, or either commodity.
    """
    return kind.shape if kind.shape in GROUPS else "gear at 5+ a week"


def earned(posts: Sequence[Post], kinds: dict[int, Kind], group: str, segment: str) -> tuple[float | None, float | None]:
    """What a group's posts earned in a segment, against the worth.

    Args:
        posts: A run's posts.
        kinds: The world's items.
        group: One of GROUPS.
        segment: One of SEGMENTS.

    Returns:
        (sold units × their price ÷ the worth then, per unit posted; the share of posted units that sold); None
        and None without a post.
    """
    chosen = [p for p in posts if group_of(kinds[p.key]) == group and segment_of(kinds[p.key], p.at_ms) == segment]
    units = sum(p.units for p in chosen)
    if not units:
        return None, None
    return (sum(p.sold * p.price / kinds[p.key].worth_at(p.at_ms) for p in chosen) / units,
            sum(p.sold for p in chosen) / units)


def same_levels(posts: Sequence[Post], kinds: dict[int, Kind], group: str) -> tuple[float | None, int]:
    """How often the built level and the sold half-life prototype's agreed on the same post (SP2c's acceptance).

    Two runs drift apart (our own posts change later sales), so the rules are compared on the same posts: those of a
    `today` run where both read a sold level, in the steady segment (after a step in the worth the two age
    conventions, hours against days, disagree for a day by design). The levels are compared, not the prices: the
    built rule multiplies the hour's pattern in by design (`pattern_cut` reports that).

    Args:
        posts: A `today` run's posts.
        kinds: The world's items.
        group: One of GROUPS.

    Returns:
        (the share of the group's steady twinned posts whose document level is within ±SAME_BAND of the prototype's;
        their count); None and 0 without one.
    """
    pairs = [(p.level, p.twin) for p in posts if p.twin is not None and group_of(kinds[p.key]) == group
             and segment_of(kinds[p.key], p.at_ms) == "steady"]
    if not pairs:
        return None, 0
    return sum(abs(level - twin) <= SAME_BAND * twin for level, twin in pairs) / len(pairs), len(pairs)


def pattern_cut(posts: Sequence[Post], kinds: dict[int, Kind], group: str) -> float | None:
    """The share of today's twinned posts the hour's pattern priced under min(cheapest − 1, the level) (reported).

    Args:
        posts: A `today` run's posts.
        kinds: The world's items.
        group: One of GROUPS.

    Returns:
        The share; None without a twinned post.
    """
    chosen = [p for p in posts if p.plain is not None and group_of(kinds[p.key]) == group]
    return sum(p.price < p.plain for p in chosen) / len(chosen) if chosen else None


def shape_ratios(run: Run, kinds: dict[int, Kind], shape: str) -> tuple[float | None, float | None]:
    """A shape's asks against what sold, as the real summary's ratios are read (plan §1d item 1).

    Args:
        run: A finished run of the world, without a poster.
        kinds: The world's items.
        shape: A REAL_RATIOS name.

    Returns:
        (med7 ÷ sold_med7, p90_30 ÷ sold_med7), each the median over the shape's steady items that have both
        numbers in the last document; None without one.
    """
    doc = run.documents[-1]
    chosen = [doc.items[key] for key, kind in sorted(kinds.items())
              if kind.shape == shape and kind.regime == "steady" and key in doc.items]
    med7 = [i.med7 / i.sold_med7 for i in chosen if i.med7 and i.sold_med7]
    p90 = [i.p90_30 / i.sold_med7 for i in chosen if i.p90_30 and i.sold_med7]
    return (statistics.median(med7) if med7 else None, statistics.median(p90) if p90 else None)


def ambiguous_share(run: Run, keys: Sequence[int]) -> float | None:
    """The share of the units the history counts sold that no buyer bought (plan §1d item 25(h)).

    A listing that expires or is cancelled between two snapshots can read as a sale (SPEC §2.4). Per item and
    price, the units the history's sold prices hold beyond the units really bought at that price after a snapshot
    saw their auction are such endings.

    Args:
        run: A finished run.
        keys: The items counted.

    Returns:
        Those units ÷ every unit counted sold; None when none is.
    """
    last = run.snapshots[-1]
    stored = sold_rows(run.folder / "data" / "state" / "history.db", last // DAY_MS, days=last // DAY_MS)
    counted = unbought = 0.0
    for key in sorted(keys):
        bought = run.truth.seen_sold_prices(key, 0, last, run.snapshots)
        by_price: dict[int, float] = {}
        for price, units, _ in stored.get(key, ()):
            by_price[price] = by_price.get(price, 0.0) + units
        counted += sum(by_price.values())
        unbought += sum(max(0.0, units - bought.get(price, 0)) for price, units in by_price.items())
    return unbought / counted if counted else None


def calibration(folder: Path, seed: int = 1, replicates: int = 4) -> list[Measure]:
    """SM20's world beside the real market (the `market-sim` agent's rule 5): the cloud job alone, no poster.

    Args:
        folder: An empty folder for the run.
        seed: The world's seed.
        replicates: Items per shape and regime.

    Returns:
        Reported, the simulated number against the real one: per shape, med7 and p90_30 ÷ the sold median over
        its steady items; per group, the share of the counted sold units that nobody bought.
    """
    world, kinds = rules_world(seed, replicates)
    run = simulate(world, folder)
    out = []
    for shape, (real_med7, real_p90) in REAL_RATIOS.items():
        med7, p90 = shape_ratios(run, kinds, shape)
        out += [Measure("SM20", f"world: med7 ÷ the sold median, {shape}", med7, real_med7, "reported"),
                Measure("SM20", f"world: p90_30 ÷ the sold median, {shape}", p90, real_p90, "reported")]
    for group in GROUPS:
        keys = [key for key, kind in kinds.items() if group_of(kind) == group]
        out.append(Measure("SM20", f"world: counted sold units that nobody bought, {group}",
                           ambiguous_share(run, keys), REAL_AMBIGUOUS, "reported"))
    return out


def sm20(folder: Path, seed: int = 1, replicates: int = 4, rules: Sequence[str] = RULES) -> list[Measure]:
    """SM20: each rule's earnings per post against the worth, by group and segment (reported).

    Args:
        folder: An empty folder for the runs (one sub-folder a rule, and the calibration's).
        seed: The world's seed.
        replicates: Items per shape and regime.
        rules: The rules to run, each one of RULES.

    Returns:
        The world's `calibration`; then per rule, group and segment: earned per unit posted ÷ the worth, against
        1.0 (a post sold at the worth), with the share sold in the measure's words; for `today`, per group, the
        share of its steady posts whose built level is within ±SAME_BAND of the sold half-life prototype's for the
        same post (SP2c: the built level and the prototype give the same prices; band SAME_SHARE), and the share of
        them the hour's pattern priced under the level (reported).
    """
    out = calibration(folder / "world", seed, replicates)
    for rule in rules:
        posts, kinds = trade(rule, seed, folder / rule.replace(" ", "-"), replicates)
        for segment in SEGMENTS:
            for group in GROUPS:
                made, share = earned(posts, kinds, group, segment)
                sold = "no posts" if share is None else f"{share:.0%} sold"
                out.append(Measure("SM20", f"{rule}: earned per post ÷ the worth, {group}, {segment} ({sold})",
                                   made, 1.0, "reported"))
        if rule == "today":
            for group in GROUPS:
                share, count = same_levels(posts, kinds, group)
                out.append(Measure("SM20", f"the built level within ±{SAME_BAND:.0%} of sold half-life's on the same steady "
                                   f"posts, {group} ({count} posts)", share, 1.0, f"≥ {SAME_SHARE:.0%}",
                                   None if share is None else share >= SAME_SHARE))
                out.append(Measure("SM20", f"today's posts the hour's pattern priced under the level, {group}",
                                   pattern_cut(posts, kinds, group), 0.0, "reported"))
    return out


PRICE_RULE_ROWS = {"SM20": sm20}          # tools/sim_report merges them with its ROWS
