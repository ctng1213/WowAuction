"""Scoring a paper-trading run: the ledger every event goes through, and the benchmarks (SPEC §8.10; plan §1.6).

The ledger keeps our characters' value in buckets at cost: cash, bags, listed auctions, deposits held, gold waiting
in the mailbox and items waiting in the mailbox. Every event moves value between the buckets it names, and after
each one the buckets add up to the start plus the realized gains, less the AH cuts and the deposits lost (SU12,
plan §1.34). Costs move proportionally and exactly: a part of a lot carries `cost × units ÷ held` rounded down,
and the last unit carries what is left, so integer copper never leaks.

The benchmarks score the same world without the plan: doing nothing (zero), a naive trader (buys the cheapest
listing when reposting it at the document's med7 would pay, reposts there), and an oracle that knows the future
(an upper bound, see `oracle_profit`).
"""
from __future__ import annotations

import heapq
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ahsync.engine.fees import AH_CUT
from sim.truth import Event

BUCKETS = ("cash", "bags", "listed", "deposits", "mail_gold", "mail_items")


@dataclass
class Lot:
    """Units of one item in one place, with their total cost in copper."""

    units: int = 0
    cost: int = 0

    def take(self, units: int) -> int:
        """Take units out at their share of the cost.

        Args:
            units: 1 to self.units.

        Returns:
            The cost taken, copper: all of it with the last unit.

        Raises:
            ValueError: More units than the lot holds.
        """
        if not 0 < units <= self.units:
            raise ValueError(f"cannot take {units} of {self.units} units")
        cost = self.cost if units == self.units else self.cost * units // self.units
        self.units -= units
        self.cost -= cost
        return cost


@dataclass
class Ledger:
    """Our characters' value by bucket, and the running terms of the conservation identity (SU12)."""

    start: int                                            # copper the characters started with
    cash: dict[str, int] = field(default_factory=dict)    # char -> copper
    bags: dict[tuple[str, int], Lot] = field(default_factory=dict)       # (char, key) -> lot
    listed: dict[int, Lot] = field(default_factory=dict)                 # auction id -> lot
    listed_key: dict[int, int] = field(default_factory=dict)            # auction id -> item key
    deposits: dict[int, int] = field(default_factory=dict)              # auction id -> deposit held, copper
    mail_gold: dict[str, int] = field(default_factory=dict)             # char -> copper waiting
    mail_items: dict[tuple[str, int], Lot] = field(default_factory=dict)  # (char, key) -> lot waiting
    gains: int = 0                                        # realized: proceeds − cost of what was sold
    cuts: int = 0                                         # AH cuts paid
    deposits_lost: int = 0                                # deposits of auctions that expired or were cancelled
    events: int = 0                                       # events applied

    def totals(self) -> dict[str, int]:
        """Each bucket's value, copper.

        Returns:
            Bucket -> copper, over all characters.
        """
        return {"cash": sum(self.cash.values()), "bags": sum(l.cost for l in self.bags.values()),
                "listed": sum(l.cost for l in self.listed.values()), "deposits": sum(self.deposits.values()),
                "mail_gold": sum(self.mail_gold.values()), "mail_items": sum(l.cost for l in self.mail_items.values())}

    def balanced(self) -> bool:
        """The SU12 identity: the buckets add up to start + gains − cuts − deposits lost.

        Returns:
            True when they do, to the copper.
        """
        return sum(self.totals().values()) == self.start + self.gains - self.cuts - self.deposits_lost

    def fund(self, char: str, copper: int) -> None:
        """A character's starting gold (counts in `start`).

        Args:
            char: Character id.
            copper: Gold, copper.
        """
        self.cash[char] = self.cash.get(char, 0) + copper
        self.start += copper

    def buy(self, char: str, key: int, units: int, cost: int) -> None:
        """Units bought from the AH or a vendor: cash → bags.

        Args:
            char: Who buys.
            key: Item key.
            units: Units bought.
            cost: Copper paid in all.
        """
        self.cash[char] -= cost
        lot = self.bags.setdefault((char, key), Lot())
        lot.units, lot.cost = lot.units + units, lot.cost + cost
        self.events += 1

    def craft(self, char: str, reagents: tuple[tuple[int, int], ...], product: int, units: int) -> None:
        """A craft: its reagents leave the bags and their cost goes with the units it made (bags → bags).

        Args:
            char: Who crafts.
            reagents: (key, units) used.
            product: The item made.
            units: Units made.

        Raises:
            ValueError: A reagent short in the bags.
        """
        cost = sum(self.bags[(char, key)].take(n) for key, n in reagents)
        lot = self.bags.setdefault((char, product), Lot())
        lot.units, lot.cost = lot.units + units, lot.cost + cost
        self.events += 1

    def post(self, char: str, key: int, units: int, auction: int, deposit: int) -> None:
        """An auction posted: bags → listed, and the deposit cash → deposits held.

        Args:
            char: Who posts.
            key: Item key.
            units: Units in the auction.
            auction: Its id.
            deposit: Copper.
        """
        self.listed[auction] = Lot(units, self.bags[(char, key)].take(units))
        self.listed_key[auction] = key
        self.cash[char] -= deposit
        self.deposits[auction] = deposit
        self.events += 1

    def sale(self, char: str, auction: int, units: int, unit_price: int) -> int:
        """Units of our auction bought: listed → mailbox gold, with their share of the deposit returned.

        Args:
            char: The auction's seller.
            auction: Its id.
            units: Units bought.
            unit_price: Copper a unit.

        Returns:
            The mail's gold: proceeds − the AH cut + the deposit share, copper.
        """
        lot = self.listed[auction]
        left_before = lot.units
        cost = lot.take(units)
        deposit = self.deposits[auction] if units == left_before else self.deposits[auction] * units // left_before
        self.deposits[auction] -= deposit
        gross = unit_price * units
        cut = int(gross * AH_CUT)                     # UNVERIFIED: the game's rounding of the 5% cut
        self.gains += gross - cost
        self.cuts += cut
        money = gross - cut + deposit
        self.mail_gold[char] = self.mail_gold.get(char, 0) + money
        self._close(auction)
        self.events += 1
        return money

    def returned(self, char: str, key: int, auction: int) -> None:
        """An auction expired or was cancelled: listed → mailbox items; its deposit is lost (plan §1.16).

        Args:
            char: The auction's seller.
            key: Item key.
            auction: Its id.
        """
        lot = self.listed[auction]
        units, cost = lot.units, lot.take(lot.units)
        waiting = self.mail_items.setdefault((char, key), Lot())
        waiting.units, waiting.cost = waiting.units + units, waiting.cost + cost
        self.deposits_lost += self.deposits.pop(auction)
        self.listed.pop(auction)
        self.listed_key.pop(auction)
        self.events += 1

    def collect(self, char: str, copper: int, items: dict[int, int]) -> None:
        """A character takes mail that has arrived: mailbox gold → cash, mailbox items → bags.

        Args:
            char: Who collects.
            copper: Gold taken, copper; at most what waits.
            items: Item key -> units taken; at most what waits.
        """
        self.mail_gold[char] = self.mail_gold.get(char, 0) - copper      # 0 before any sale
        self.cash[char] = self.cash.get(char, 0) + copper
        for key, units in sorted(items.items()):
            cost = self.mail_items[(char, key)].take(units)
            bag = self.bags.setdefault((char, key), Lot())
            bag.units, bag.cost = bag.units + units, bag.cost + cost
        self.mail_items = {k: v for k, v in self.mail_items.items() if v.units}
        self.events += 1

    def vendor_sell(self, char: str, key: int, units: int, unit_price: int) -> None:
        """Units sold to a vendor: bags → cash, the difference to cost realized.

        Args:
            char: Who sells.
            key: Item key.
            units: Units sold.
            unit_price: The vendor's price a unit, copper.
        """
        cost = self.bags[(char, key)].take(units)
        self.cash[char] += unit_price * units
        self.gains += unit_price * units - cost
        self.events += 1

    def mail(self, char: str, to_char: str, key: int, units: int) -> None:
        """Units mailed to another character: bags → their mailbox items (postage not modelled).

        Args:
            char: The sender.
            to_char: The recipient.
            key: Item key.
            units: Units sent.
        """
        cost = self.bags[(char, key)].take(units)
        waiting = self.mail_items.setdefault((to_char, key), Lot())
        waiting.units, waiting.cost = waiting.units + units, waiting.cost + cost
        self.events += 1

    def _close(self, auction: int) -> None:
        """Forget an auction with no units left.

        Args:
            auction: Its id.
        """
        if not self.listed[auction].units:
            del self.listed[auction], self.deposits[auction], self.listed_key[auction]


def value_at(ledger: Ledger, price_of: dict[int, int]) -> int:
    """What the characters hold, with unsold stock marked at a price instead of its cost (for stranded stock).

    Args:
        ledger: The ledger.
        price_of: Item key -> copper a unit to mark it at; a key without one is marked at 0.

    Returns:
        Cash + mailbox gold + deposits held + every held, listed or mailed unit at its mark, copper.
    """
    held = [(key, lot.units) for (_, key), lot in list(ledger.bags.items()) + list(ledger.mail_items.items())]
    held += [(ledger.listed_key[auction], lot.units) for auction, lot in ledger.listed.items()]
    t = ledger.totals()
    return t["cash"] + t["mail_gold"] + t["deposits"] + sum(units * price_of.get(key, 0) for key, units in held)


DAY_MS = 86_400_000
GOLD = 10_000                                            # copper
ORACLE_HOLD_MS = DAY_MS                                  # the oracle resells within a day of the listing it bought


@dataclass(frozen=True)
class Score:
    """One benchmark's result over a run (plan §1.6)."""

    name: str
    made: int                     # realized copper: proceeds − cost − AH cuts − deposits lost
    gold_days: float              # capital tied up (stock at cost, deposits held) × days, in gold-days
    drawdown: int                 # the largest fall of the realized result from an earlier peak, copper
    stranded_units: int           # units still held, listed or in the mail at the end
    stranded_cost: int            # their cost, copper
    oracle_share: float | None    # made ÷ the oracle's profit; None without an oracle profit


def score(name: str, ledger: Ledger, samples: Sequence[tuple[int, int, int]], oracle: int | None) -> Score:
    """Score a run from its ledger and its samples.

    Args:
        name: The benchmark's name.
        ledger: Its ledger at the end.
        samples: (at ms, copper tied up, realized copper), in time order.
        oracle: The oracle's profit, copper; None or 0 leaves the share out.

    Returns:
        The Score.
    """
    made = ledger.gains - ledger.cuts - ledger.deposits_lost
    gold_days = sum(a[1] * (b[0] - a[0]) for a, b in zip(samples, samples[1:])) / DAY_MS / GOLD
    peak = drawdown = 0
    for _, _, realized in samples:
        peak = max(peak, realized)
        drawdown = max(drawdown, peak - realized)
    lots = list(ledger.bags.values()) + list(ledger.listed.values()) + list(ledger.mail_items.values())
    return Score(name, made, gold_days, drawdown, sum(l.units for l in lots), sum(l.cost for l in lots),
                 made / oracle if oracle else None)


def oracle_profit(events: Iterable[Event], stock: dict[int, int] | None = None,
                  ours: frozenset[str] = frozenset()) -> int:
    """The oracle's profit (plan §1.35): a trader that knows the future, as an optimistic yardstick.

    It starts with the characters' stock (found, at cost 0, as the trader's ledger holds it) and goes through every
    sale to others in time order, reselling into it the cheapest units of the same item listed in the day before
    while the sale's price after the cut beats theirs, then its stock, which never ages out; each unit is used
    once. It ignores capital, deposits and its own effect on buyers, so it
    bounds what trading could have made rather than proving a maximum.

    Args:
        events: The truth log's events, in time order.
        stock: Item key -> units held at the start; None for none.
        ours: Seller and buyer names to leave out (our characters' own trades).

    Returns:
        Copper.
    """
    asks: dict[int, list[list[int]]] = {}                 # key -> heap of [price, posted ms, units left]
    held = dict(stock or {})
    profit = 0
    for e in events:
        if e.actor in ours:
            continue
        if e.kind == "post":
            heapq.heappush(asks.setdefault(e.key, []), [e.price, e.at_ms, e.units])
        elif e.kind == "buy":
            left, made = _resell(asks.get(e.key, []), e)
            from_stock = min(left, held.get(e.key, 0))
            if from_stock:
                held[e.key] -= from_stock
                made += int(from_stock * e.price * (1 - AH_CUT))
            profit += made
    return profit


def _resell(heap: list[list[int]], sale: Event) -> tuple[int, int]:
    """Fill one sale from the cheapest units listed in the day before it, while that pays.

    Args:
        heap: The item's asks so far, [price, posted ms, units left], a min-heap by price (changed in place; asks
            over a day old are dropped, since every later sale is later still).
        sale: A buy by someone else.

    Returns:
        (the sale's units left unfilled, the profit made on it in copper).
    """
    units, profit, kept = sale.units, 0, []
    net = sale.price * (1 - AH_CUT)
    while heap and units:
        ask = heap[0]
        if ask[0] >= net:
            break
        heapq.heappop(heap)
        if ask[1] <= sale.at_ms - ORACLE_HOLD_MS:
            continue                                       # listed over a day before: too old for this and later sales
        take = min(units, ask[2])
        profit += int(take * (net - ask[0]))
        units, ask[2] = units - take, ask[2] - take
        if ask[2]:
            kept.append(ask)
    for ask in kept:
        heapq.heappush(heap, ask)
    return units, profit

