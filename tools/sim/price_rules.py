"""SM20, the price-level rules paper-traded (SPEC §8.10; `docs/plans/sold-price.md` §1d, the 2026-11-21 review).

The review found today's post-price ceiling, max(med7, p90_30) of asks, at a median 2.7× what sold on the real
market. SM20 measures what each candidate level earns instead of arguing it: a poster lists one lot of every item
every 12 hours at `min(the cheapest other auction − 1 c, the rule's level)` and the row reports what a post earned
against the item's worth. The rules:

- `today`: the ask ceiling (SPEC §3.4, `prices.post_price`).
- `ask blend`: the 7, 14 and 30-day ask medians weighted 4:2:1.
- `sold 7d`: the published `sold_med7` at 5 or more sold units in 7 days, else today's.
- `sold half-life`: the sold median over 30 days with a unit's weight halving every 2 days, at 5 or more sold
  units in those days, else the ask blend. The rule the user chose for SP2 (2026-11-21).

The worlds are shaped to the real summary of 2026-11-21 (gear at 5–50 sales a week: med7 1.22–1.30× the sold
median, p90_30 2.6–3.0×): sellers who price over a wide range, a high ask that sits unsold, 15% cancels, and a
commodity under lone dumps (SM18's). Each item comes steady, with its worth doubling on day 11, and with it falling
40% then. Measured only: the level is built in SP2, and the engine's constants are real data's (plan §2).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ahsync.engine import prices
from ahsync.engine.lifetime_prices import weighted_median
from ahsync.intel.contract import ItemIntel, MarketIntelligence

from sim.inference import GOLD, START_MS, Measure, commodity, gear
from sim.market import DAY_MS, HOUR_MS, Market, Planted, World
from sim.runner import simulate

DAYS, WARM_DAYS, CHANGE_DAY = 16, 8, 11          # the world's length; the first post; the day the worth changes
POST_EVERY_HOURS, POST_HOURS = 12, 8             # the poster's cadence and its auctions' duration (SPEC §3.3.2's window)
GEAR_WORTH, CLOTH_WORTH = 2 * GOLD, 100          # copper
# Gear shapes: (listings a day, buyers a day, the high ask × the worth, the hours a day it is up), tuned on the
# market alone to the real ratios above; the names say about how many units sell a week.
SHAPES = {"gear 2 a week": (0.6, 4, 1.6, 8), "gear 6 a week": (1.7, 10, 3.0, 8),
          "gear 14 a week": (3.6, 20, 3.0, 12), "gear 30 a week": (8.5, 30, 3.0, 24)}
CLOTH = "commodity"
# A seller's price ÷ the cheapest other auction's: a third at it, a quarter under it, the rest up to 1.6× above.
PRICE_MIX = ((0.35, 1.0, 1.0), (0.25, 0.7, 1.0), (0.20, 1.0, 1.4), (0.20, 1.4, 1.6))
REGIMES = {"steady": 1.0, "rise": 2.0, "drop": 0.6}          # the worth's factor from CHANGE_DAY on
BUYER_SPREAD = 0.25                              # assumed, not measured: sd of log(a buyer's maximum ÷ the worth)
CANCEL_SHARE = 0.15
DUMP = (5, 0.6, 3, 2)                            # the commodity's lone dump: units, × the worth, every h, up h
THRESHOLD_UNITS = 5.0                            # sold units a sold level needs (the review's proposal)
HALF_LIFE_DAYS = 2.0                             # the chosen weighting
LEVEL_DAYS = 30                                  # the days of sold prices a half-life level reads
ASK_WEIGHTS = (4, 2, 1)                          # the ask blend's weights on med7, med14 and med30
RULES = ("today", "ask blend", "sold 7d", "sold half-life")
SEGMENTS = ("steady", "after the rise", "after the drop")
GROUPS = ("gear 2 a week", "gear at 5+ a week", CLOTH)


@dataclass(frozen=True)
class Kind:
    """What one simulated item is: its shape, its regime and its worth before the change."""

    shape: str                        # a SHAPES name, or CLOTH
    regime: str                       # a REGIMES name
    worth: int                        # copper, before CHANGE_DAY

    def worth_at(self, at_ms: int) -> float:
        """The item's worth at a time.

        Args:
            at_ms: UTC ms.

        Returns:
            Copper: the worth, times the regime's factor from CHANGE_DAY on.
        """
        return self.worth * (REGIMES[self.regime] if at_ms >= START_MS + CHANGE_DAY * DAY_MS else 1.0)


@dataclass(frozen=True)
class Post:
    """One lot the poster listed, and what became of it."""

    key: int
    at_ms: int
    price: int                        # unit price, copper
    units: int
    sold: int                         # units bought before it expired


def _high_asks(key: int, kind: Kind, multiple: float, hours: int) -> list[Planted]:
    """A gear item's high ask, posted daily at a multiple of the worth then.

    Args:
        key: Item key.
        kind: The item.
        multiple: The ask ÷ the worth.
        hours: Hours a day it is up: 8, 12 (an 8 h auction and two 2 h ones) or 24.

    Returns:
        The planted auctions over the world's days.
    """
    out = []
    for day in range(DAYS):
        at = START_MS + (1 + 24 * day) * HOUR_MS
        price = int(kind.worth_at(at) * multiple)
        out.append(Planted(at, key, 1, price, 24 if hours == 24 else 8))
        if hours == 12:
            out += [Planted(at + 8 * HOUR_MS, key, 1, price, 2), Planted(at + 10 * HOUR_MS, key, 1, price, 2)]
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
    for regime, factor in REGIMES.items():
        shifts = () if factor == 1.0 else ((START_MS + CHANGE_DAY * DAY_MS, factor, 1.0),)
        for shape, (listings, buyers, multiple, hours) in SHAPES.items():
            for n in range(replicates):
                key += 1
                kinds[key] = Kind(shape, regime, GEAR_WORTH)
                items.append(gear(key, f"{shape}, {regime} {n}", GEAR_WORTH, buyers, listings, price_mix=PRICE_MIX,
                                  cancel_share=CANCEL_SHARE, buyer_level=1.0, buyer_spread=spread, shifts=shifts))
                planted += _high_asks(key, kinds[key], multiple, hours)
        for n in range(replicates):
            key += 1
            kinds[key] = Kind(CLOTH, regime, CLOTH_WORTH)
            items.append(commodity(key, f"{CLOTH}, {regime} {n}", CLOTH_WORTH, 120, 60, buyer_spread=spread,
                                   shifts=shifts))
            units, share, every, hours = DUMP
            for hour in range(0, DAYS * 24, every):
                at = START_MS + hour * HOUR_MS + 50 * 60_000
                planted.append(Planted(at, key, units, int(kinds[key].worth_at(at) * share), hours))
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


def rule_price(rule: str, item: ItemIntel, cheapest: int | None, sold: Sequence[tuple[int, float, int]]) -> int | None:
    """The post price a rule gives: the undercut, held under the rule's level.

    Args:
        rule: One of RULES.
        item: The item's published intelligence.
        cheapest: The cheapest other auction's unit price now, copper, or None.
        sold: The item's `sold_rows`.

    Returns:
        Copper; None when the rule knows no price.

    Raises:
        KeyError: An unknown rule.
    """
    if rule not in RULES:
        raise KeyError(rule)
    if rule == "today" or (rule == "sold 7d" and (item.sold_med7 is None or item.sold_units7 < THRESHOLD_UNITS)):
        return prices.post_price(cheapest, item.med7, item.p90_30, item.closes_30d)
    level = item.sold_med7 if rule == "sold 7d" else None
    if rule == "sold half-life":
        level, units = sold_level(sold)
        level = level if units >= THRESHOLD_UNITS else None
    if level is None:                 # the ask blend, and the half-life's fallback
        return prices.post_price(cheapest, ask_blend(item), None, item.closes_30d)
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
        self.posted: list[tuple[int, int, int, int, int]] = []     # (auction id, key, at ms, price, units)
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
            price = rule_price(self.rule, item, min(others) if others else None, self._sold.get(key, ()))
            if price is not None and price >= 1:
                units = 5 if market.items[key].commodity else 1
                auction = market.post(key, units, price, POST_HOURS, "us")
                self.posted.append((auction.id, key, now_ms, price, units))


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
    return [Post(key, at, price, units, bought.get(auction, 0)) for auction, key, at, price, units in poster.posted], kinds


def segment_of(kind: Kind, at_ms: int) -> str | None:
    """Which SEGMENTS column a post counts in.

    Args:
        kind: The post's item.
        at_ms: When it was posted, UTC ms.

    Returns:
        "steady" for a steady item and for any item before the change, else the regime's column.
    """
    if kind.regime == "steady" or at_ms < START_MS + CHANGE_DAY * DAY_MS:
        return "steady"
    return "after the rise" if kind.regime == "rise" else "after the drop"


def group_of(kind: Kind) -> str:
    """Which GROUPS row an item counts in.

    Args:
        kind: The item.

    Returns:
        The thin gear shape, the other gear shapes together, or the commodity.
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


def sm20(folder: Path, seed: int = 1, replicates: int = 4, rules: Sequence[str] = RULES) -> list[Measure]:
    """SM20: each rule's earnings per post against the worth, by group and segment (reported).

    Args:
        folder: An empty folder for the runs (one sub-folder a rule).
        seed: The world's seed.
        replicates: Items per shape and regime.
        rules: The rules to run, each one of RULES.

    Returns:
        Per rule, group and segment: earned per unit posted ÷ the worth, against 1.0 (a post sold at the worth),
        with the share sold in the measure's words.
    """
    out = []
    for rule in rules:
        posts, kinds = trade(rule, seed, folder / rule.replace(" ", "-"), replicates)
        for segment in SEGMENTS:
            for group in GROUPS:
                made, share = earned(posts, kinds, group, segment)
                sold = "no posts" if share is None else f"{share:.0%} sold"
                out.append(Measure("SM20", f"{rule}: earned per post ÷ the worth, {group}, {segment} ({sold})",
                                   made, 1.0, "reported"))
    return out


PRICE_RULE_ROWS = {"SM20": sm20}          # tools/sim_report merges them with its ROWS
