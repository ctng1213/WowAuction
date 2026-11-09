"""The simulated market: sellers and buyers moving one auction house through time (SPEC §8.10).

Event-driven and seeded: each item's sellers and buyers arrive as Poisson processes (buyers shaped by the hour of
the week when an item has a weekly profile), every auction's expiry and any cancel are queued when it is posted,
and events run in time order with a sequence number breaking ties, so the same world always gives the same
history. Every event is written to the truth log.

Buyers have a maximum price drawn around the item's fair price. A commodity buyer takes units from the cheapest
auctions first, as Forever's commodity purchases do (measured, SPEC §2.4), except a small share that skips the
cheapest (the measured 9 of 4,583); a gear buyer picks one of the three cheapest auctions it can afford, whole.
Sellers post a stack at a price relative to the cheapest other auction, held inside a band around the fair price:
uniform over `list_range`, or, for a market fitted from the collected raw (`fit.py`), drawn from a mix of
positions (some exactly at the cheapest price, some just above, a few under it), with stack sizes drawn likewise.
"""
from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass, field

from ahsync.engine.capacity import slot_of
from sim.truth import Event, Truth

MINUTE_MS, HOUR_MS, DAY_MS = 60_000, 3_600_000, 86_400_000
BOT_MINUTE = 7                       # bot n acts at n × this many minutes past each hour
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
    buyer_level: float = 1.0          # a buyer's median maximum price ÷ the fair price
    list_range: tuple[float, float] = (0.97, 1.15)   # a post's price ÷ the cheapest other auction's
    durations: tuple[tuple[int, float], ...] = ((2, 0.1), (8, 0.3), (24, 0.6))   # (hours, weight)
    cancel_share: float = 0.0         # share of posts their seller cancels, at a uniform time in their life
    miss_cheapest: float = 0.002      # share of commodity buyers who take from the second-cheapest auction first
    max_units: int = 0                # a commodity buyer wants 1..max_units units; 0 = 1..stack
    # A post's price ÷ the cheapest other auction's, as (weight, low, high) parts: a part with low == high posts at
    # exactly that ratio, another uniformly in [low, high). () = uniform over list_range (MS1's hand-set sellers).
    price_mix: tuple[tuple[float, float, float], ...] = ()
    stacks: tuple[tuple[int, float], ...] = ()       # (units, weight) a commodity seller posts; () = always `stack`
    # Sellers price off min(the cheapest other auction, anchor × the fair price): above it they price off their own
    # sense of the item's worth, so the floor mean-reverts as real floors do (fitted markets). 0 = no anchor (MS1).
    anchor: float = 0.0
    relist_share: float = 0.0         # share of cancels whose seller posts the same units again at once (a relist)
    bots: int = 0                     # undercut bots: each hour each cancels its auction and posts 1 c under the rest


@dataclass(frozen=True)
class Planted:
    """One auction a scenario posts at a set time, outside the sellers' model (SM10's 15 g listing)."""

    at_ms: int                        # when it is posted, UTC ms
    key: int                          # item key; must be one of the world's items
    units: int
    price: int                        # unit price, copper
    hours: int                        # duration: 2, 8 or 24
    cancel_at_ms: int | None = None   # when its seller cancels it, or None


@dataclass(frozen=True)
class World:
    """A scenario: the items, how long it runs and its seed."""

    seed: int
    start_ms: int                     # UTC ms; the first events come after it
    days: int
    items: tuple[Item, ...]
    market: str = "forever.normal.horde.us"
    planted: tuple[Planted, ...] = ()


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
    kind: str = field(compare=False)    # "seller", "buyer", "bot", "plant", "expire" or "cancel"
    ref: int = field(compare=False)     # item key for arrivals, bot number for a bot, index into World.planted for
                                        # a plant, auction id for expire and cancel


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
        self._bots: list[list] = []                 # bot number -> [item key, name, its live auction id or None]
        for key in sorted(self.items):
            self._schedule_arrival(self.items[key], "seller", world.start_ms)
            self._schedule_arrival(self.items[key], "buyer", world.start_ms)
            for _ in range(self.items[key].bots):
                self._bots.append([key, self._actor("bot"), None])
                self._push(world.start_ms + BOT_MINUTE * MINUTE_MS * len(self._bots), "bot", len(self._bots) - 1)
        for number, planted in enumerate(world.planted):
            self._push(planted.at_ms, "plant", number)

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
            elif event.kind == "bot":
                self._undercut(event.ref)
                self._push(event.at_ms + HOUR_MS, "bot", event.ref)
            elif event.kind == "plant":
                planted = self.world.planted[event.ref]
                self.post(planted.key, planted.units, planted.price, planted.hours, f"p{event.ref}",
                          planted.cancel_at_ms)
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
        price = max(1, int(min(max(self._price(item, self._reference(item)), item.fair_price * FAIR_BAND[0]),
                               item.fair_price * FAIR_BAND[1])))
        hours = self.rng.choices([h for h, _ in item.durations], [w for _, w in item.durations])[0]
        cancels = self.rng.random() < item.cancel_share
        cancel_at = self.now_ms + int(self.rng.uniform(0, hours * HOUR_MS)) if cancels else None
        units = 1 if not item.commodity else (
            self.rng.choices([u for u, _ in item.stacks], [w for _, w in item.stacks])[0] if item.stacks else item.stack)
        self.post(item.key, units, price, hours, self._actor("s"), cancel_at)

    def _reference(self, item: Item) -> int:
        """The price a seller prices off: the cheapest other auction, or the fair price on an empty book, held
        under the item's anchor.

        Args:
            item: The item.

        Returns:
            Copper.
        """
        live = self.by_key[item.key]
        floor = min(a.price for a in live.values()) if live else item.fair_price
        return min(floor, max(1, int(item.fair_price * item.anchor))) if item.anchor > 0 else floor

    def _price(self, item: Item, floor: int) -> float:
        """A seller's price before the fair-price band, from the cheapest other auction's.

        Args:
            item: The item.
            floor: The cheapest other auction's unit price, copper (the fair price on an empty book).

        Returns:
            Copper: floor × a ratio uniform over list_range; with a price_mix, the ratio drawn from it and rounded
            away from the floor to whole copper (a post above it is at least 1 c above, one under it at least 1 c
            under), as a seller types a price.
        """
        if not item.price_mix:
            return floor * self.rng.uniform(*item.list_range)
        _, low, high = self.rng.choices(item.price_mix, [w for w, _, _ in item.price_mix])[0]
        ratio = low if low == high else self.rng.uniform(low, high)
        if ratio > 1:
            return max(floor + 1, math.ceil(floor * ratio))
        return floor if ratio == 1 else min(floor - 1, math.floor(floor * ratio))

    def _buy_as_buyer(self, item: Item) -> None:
        """One buyer of the model arrives: buys what it can afford, cheapest first for a commodity.

        Args:
            item: The item.
        """
        most = item.fair_price * item.buyer_level * math.exp(self.rng.gauss(0.0, item.buyer_spread))
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

    def _end(self, auction_id: int, kind: str, may_relist: bool = True) -> None:
        """An auction expires or is cancelled, if it is still up; a cancelling seller may relist (relist_share).

        Args:
            auction_id: The auction.
            kind: "expire" or "cancel".
            may_relist: False for a bot, which reposts on its own schedule.
        """
        for key, live in self.by_key.items():
            auction = live.pop(auction_id, None)
            if auction is not None:
                actor = auction.seller if kind == "cancel" else ""
                self.truth.add(Event(self.now_ms, kind, auction_id, key, auction.units, auction.price, 0, actor))
                item = self.items[key]
                if kind == "cancel" and may_relist and item.relist_share > 0 and self.rng.random() < item.relist_share:
                    self._relist(item, auction)
                return

    def _relist(self, item: Item, ended: Live) -> None:
        """A cancelling seller posts the same units again at once, priced as a new post.

        Args:
            item: The item.
            ended: The auction just cancelled.
        """
        price = max(1, int(min(max(self._price(item, self._reference(item)), item.fair_price * FAIR_BAND[0]),
                               item.fair_price * FAIR_BAND[1])))
        hours = self.rng.choices([h for h, _ in item.durations], [w for _, w in item.durations])[0]
        self.post(item.key, ended.units, price, hours, ended.seller)

    def _undercut(self, number: int) -> None:
        """An undercut bot's hour: it cancels its auction and posts a stack 1 c under the cheapest other one.

        Args:
            number: The bot.
        """
        key, name, auction_id = self._bots[number]
        item = self.items[key]
        if auction_id is not None and auction_id in self.by_key[key]:
            self._end(auction_id, "cancel", may_relist=False)
        live = self.by_key[key]
        floor = min(a.price for a in live.values()) if live else item.fair_price
        price = max(1, floor - 1, int(item.fair_price * FAIR_BAND[0]))
        self._bots[number][2] = self.post(key, item.stack if item.commodity else 1, price, 8, name).id

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
