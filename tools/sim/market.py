"""The simulated market: sellers and buyers moving one auction house through time (SPEC §8.10).

Event-driven and seeded: each item's sellers and buyers arrive as Poisson processes (buyers shaped by the hour of
the week when an item has a weekly profile), every auction's expiry and any cancel are queued when it is posted,
and events run in time order with a sequence number breaking ties, so the same world always gives the same
history. Every event is written to the truth log.

Buyers have a maximum price drawn around the item's fair price. A commodity buyer takes units from the cheapest
auctions first, as Forever's commodity purchases do (measured, SPEC §2.4), except a small share that skips the
cheapest (the measured 9 of 4,583); a gear buyer picks one of the three cheapest auctions it can afford, whole.
Sellers post a stack at a price relative to the cheapest other auction, held inside a band around the fair price.
"""
from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass, field

from ahsync.engine.capacity import slot_of
from sim.truth import Event, Truth

MINUTE_MS, HOUR_MS, DAY_MS = 60_000, 3_600_000, 86_400_000
GEAR_CHOICE = 3                      # a gear buyer picks among this many of the cheapest affordable auctions
FAIR_BAND = (0.7, 1.6)               # a post's price stays within this × the fair price
FIRST_AUCTION_ID = 1_000


@dataclass(frozen=True)
class Item:
    """One item's market: what it is, who buys it and who sells it."""

    key: int                          # item key (the item id for an item without a random suffix)
    name: str
    commodity: bool                   # listed in Blizzard's commodities list: bought cheapest first
    fair_price: int                   # copper: the price a typical buyer will pay
    buys_per_day: float               # buyer arrivals a day (each may buy several units of a commodity)
    listings_per_day: float           # seller posts a day
    stack: int = 20                   # units a seller posts at once; gear posts 1
    vendor_sell: int = 0              # copper, for the catalog
    item_class: int = 7               # WoW item class (7 trade goods, 2 weapon, 4 armor)
    quality: int = 1                  # 0 poor .. 4 epic
    weekly: tuple[float, ...] = ()    # 168 buyer-arrival multipliers by hour of week (UTC, Mon 00:00 first); () = flat
    buyer_spread: float = 0.15        # standard deviation of log(a buyer's maximum price ÷ the fair price)
    list_range: tuple[float, float] = (0.97, 1.15)   # a post's price ÷ the cheapest other auction's
    durations: tuple[tuple[int, float], ...] = ((2, 0.1), (8, 0.3), (24, 0.6))   # (hours, weight)
    cancel_share: float = 0.0         # share of posts their seller cancels, at a uniform time in their life
    miss_cheapest: float = 0.002      # share of commodity buyers who take from the second-cheapest auction first
    max_units: int = 0                # a commodity buyer wants 1..max_units units; 0 = 1..stack


@dataclass(frozen=True)
class World:
    """A scenario: the items, how long it runs and its seed."""

    seed: int
    start_ms: int                     # UTC ms; the first events come after it
    days: int
    items: tuple[Item, ...]
    market: str = "forever.normal.horde.us"


@dataclass
class Live:
    """An auction on the AH right now."""

    id: int
    key: int
    units: int                        # units left
    price: int                        # unit price, copper
    posted_ms: int
    expires_ms: int
    commodity: bool
    seller: str


@dataclass(order=True)
class _Queued:
    """A scheduled event; ordered by time, then by the order it was scheduled in."""

    at_ms: int
    seq: int
    kind: str = field(compare=False)    # "seller", "buyer", "expire" or "cancel"
    ref: int = field(compare=False)     # item key for arrivals, auction id for expire and cancel


class Market:
    """The auction house's state and its event queue."""

    def __init__(self, world: World, truth: Truth | None = None) -> None:
        """A market at the world's start, with every item's first arrivals queued.

        Args:
            world: The scenario.
            truth: The log to write to; a new one when None.
        """
        self.world, self.truth = world, truth if truth is not None else Truth()
        self.rng = random.Random(world.seed)
        self.items = {item.key: item for item in world.items}
        self.by_key: dict[int, dict[int, Live]] = {key: {} for key in self.items}
        self.now_ms = world.start_ms
        self._queue: list[_Queued] = []
        self._seq = 0
        self._next_id = FIRST_AUCTION_ID
        self._actors = 0
        for key in sorted(self.items):
            self._schedule_arrival(self.items[key], "seller", world.start_ms)
            self._schedule_arrival(self.items[key], "buyer", world.start_ms)

    def book(self) -> list[Live]:
        """Every live auction, by id.

        Returns:
            The auctions (live objects: read, don't change).
        """
        return sorted((a for live in self.by_key.values() for a in live.values()), key=lambda a: a.id)

    def run_until(self, at_ms: int) -> None:
        """Run every event at or before a time.

        Args:
            at_ms: UTC ms; the market's clock is set to it afterwards.
        """
        while self._queue and self._queue[0].at_ms <= at_ms:
            event = heapq.heappop(self._queue)
            self.now_ms = event.at_ms
            if event.kind in ("seller", "buyer"):
                item = self.items[event.ref]
                if event.kind == "seller":
                    self._post_as_seller(item)
                else:
                    self._buy_as_buyer(item)
                self._schedule_arrival(item, event.kind, event.at_ms)
            else:
                self._end(event.ref, event.kind)
        self.now_ms = max(self.now_ms, at_ms)

    def post(self, key: int, units: int, price: int, hours: int, seller: str, cancel_at_ms: int | None = None) -> Live:
        """Post an auction now (a seller of the model, or our agent).

        Args:
            key: Item key; must be one of the world's items.
            units: Units in the auction, at least 1.
            price: Unit price, copper, at least 1.
            hours: Duration, 2, 8 or 24.
            seller: Who posts it.
            cancel_at_ms: When its seller cancels it, or None.

        Returns:
            The new auction.
        """
        item = self.items[key]
        auction = Live(self._new_id(), key, units, price, self.now_ms, self.now_ms + hours * HOUR_MS,
                       item.commodity, seller)
        self.by_key[key][auction.id] = auction
        self.truth.add(Event(self.now_ms, "post", auction.id, key, units, price, units, seller))
        self._push(auction.expires_ms, "expire", auction.id)
        if cancel_at_ms is not None and cancel_at_ms < auction.expires_ms:
            self._push(cancel_at_ms, "cancel", auction.id)
        return auction

    def take(self, auction_id: int, units: int, buyer: str) -> None:
        """Buy units of one auction now.

        Args:
            auction_id: A live auction's id.
            units: Units to buy, 1 to the auction's units.
            buyer: Who buys.
        """
        auction = next(live[auction_id] for live in self.by_key.values() if auction_id in live)
        auction.units -= units
        self.truth.add(Event(self.now_ms, "buy", auction.id, auction.key, units, auction.price, auction.units, buyer))
        if auction.units == 0:
            del self.by_key[auction.key][auction.id]

    def _post_as_seller(self, item: Item) -> None:
        """One seller of the model posts a stack: priced off the cheapest other auction, held near the fair price.

        Args:
            item: The item.
        """
        live = self.by_key[item.key]
        floor = min(a.price for a in live.values()) if live else item.fair_price
        low, high = item.list_range
        price = floor * self.rng.uniform(low, high)
        price = max(1, int(min(max(price, item.fair_price * FAIR_BAND[0]), item.fair_price * FAIR_BAND[1])))
        hours = self.rng.choices([h for h, _ in item.durations], [w for _, w in item.durations])[0]
        cancels = self.rng.random() < item.cancel_share
        cancel_at = self.now_ms + int(self.rng.uniform(0, hours * HOUR_MS)) if cancels else None
        self.post(item.key, item.stack if item.commodity else 1, price, hours, self._actor("s"), cancel_at)

    def _buy_as_buyer(self, item: Item) -> None:
        """One buyer of the model arrives: buys what it can afford, cheapest first for a commodity.

        Args:
            item: The item.
        """
        most = item.fair_price * math.exp(self.rng.gauss(0.0, item.buyer_spread))
        affordable = sorted((a for a in self.by_key[item.key].values() if a.price <= most), key=lambda a: (a.price, a.id))
        if not item.commodity:
            if affordable:
                pick = affordable[self.rng.randrange(min(GEAR_CHOICE, len(affordable)))]
                self.take(pick.id, pick.units, self._actor("b"))
            return
        want = self.rng.randint(1, item.max_units or item.stack)
        if len(affordable) > 1 and self.rng.random() < item.miss_cheapest:
            affordable = affordable[1:] + affordable[:1]
        buyer = self._actor("b") if affordable else ""
        for auction in affordable:
            units = min(want, auction.units)
            self.take(auction.id, units, buyer)
            want -= units
            if want == 0:
                break

    def _end(self, auction_id: int, kind: str) -> None:
        """An auction expires or is cancelled, if it is still up.

        Args:
            auction_id: The auction.
            kind: "expire" or "cancel".
        """
        for key, live in self.by_key.items():
            auction = live.pop(auction_id, None)
            if auction is not None:
                actor = auction.seller if kind == "cancel" else ""
                self.truth.add(Event(self.now_ms, kind, auction_id, key, auction.units, auction.price, 0, actor))
                return

    def _schedule_arrival(self, item: Item, who: str, after_ms: int) -> None:
        """Queue an item's next seller or buyer: a Poisson process, thinned by the weekly profile for buyers.

        Args:
            item: The item.
            who: "seller" or "buyer".
            after_ms: The time to start from, UTC ms.
        """
        rate = item.listings_per_day if who == "seller" else item.buys_per_day
        if rate <= 0:
            return
        shaped = who == "buyer" and bool(item.weekly)
        peak = max(item.weekly) if shaped else 1.0
        at = float(after_ms)
        while True:
            at += self.rng.expovariate(rate * peak / DAY_MS)
            if not shaped or self.rng.random() * peak <= item.weekly[slot_of(int(at) // HOUR_MS)]:
                break
        self._push(int(at), who, item.key)

    def _push(self, at_ms: int, kind: str, ref: int) -> None:
        """Queue an event.

        Args:
            at_ms: When, UTC ms.
            kind: The event kind.
            ref: Its item key or auction id.
        """
        self._seq += 1
        heapq.heappush(self._queue, _Queued(at_ms, self._seq, kind, ref))

    def _new_id(self) -> int:
        """The next auction id.

        Returns:
            An id never used before in this market.
        """
        self._next_id += 1
        return self._next_id

    def _actor(self, prefix: str) -> str:
        """A new seller or buyer name.

        Args:
            prefix: "s" or "b".

        Returns:
            A name unique in this market.
        """
        self._actors += 1
        return f"{prefix}{self._actors}"
