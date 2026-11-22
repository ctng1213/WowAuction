"""The pattern world: a market that moves with the clock (SPEC §8.10; `docs/plans/sold-price.md` §1d items 22, 23, 27).

The 2026-11-21 review measured, on 15 days of raw: sales follow the clock (the busiest evening hour sells about
1.9× the quietest morning one; evening against morning as blocks about 1.6×), the typical commodity's price does not
move by hour, about 13% of commodities sell about 4% dearer in the afternoon than at night, busy commodities fell a
median 17% a week, and one item's crash had been read as "Wednesdays are cheap" by the price pattern. This world
plants each of those with a known worth, so SP2a (SM23) can set the rebuilt pattern and the pattern-neutral level
against it:

- every item's buyers arrive by the measured rhythm (`buyer_rhythm`);
- `premium` items' fair price carries the afternoon premium (`afternoon_premium`), their buyers coming as often;
- `slide` items lose 2.5% a day from the first day;
- `early crash` items fall 90% over 3 days in the second week (a market as young as the real one was), `late crash`
  items in the fifth (a market with a pattern of its own by then);
- `steady` items do nothing: the control.

An item's **worth** is its fair price with its shifts and without its hour of the week: what a pattern-neutral
level should read. Measured and assumed (the `market-sim` agent's rule 2): the rhythm's two anchors, the premium's
size and share, the slide and the crash are the review's; the rhythm between its anchors, the premium's cosine
shape, and that sellers and buyers share the premium (the real asks do not show it) are assumptions.
"""
from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass

from ahsync.engine.capacity import slot_of
from ahsync.engine.volume import IdBook
from sim.inference import START_MS, Measure, commodity
from sim.market import DAY_MS, HOUR_MS, Item, World
from sim.runner import market_books
from sim.truth import Truth

UTC_OFFSET_HOURS = -5                    # the review took local time as UTC−5 (US Eastern, the realm's)
# Share of a day's sold units by local hour, 00:00 first (plan §1d item 22(a)): 5.0–5.6% an hour from 18:00 to 03:00
# and 2.8–3.0% from 06:00 to 11:00 are measured; the hours between are interpolated. Normalised where it is used.
SOLD_BY_LOCAL_HOUR = (5.4, 5.2, 5.0, 4.3, 3.6, 3.1, 2.8, 2.8, 2.9, 3.0, 3.0, 3.3,
                      3.6, 3.9, 4.2, 4.5, 4.8, 5.0, 5.2, 5.4, 5.6, 5.6, 5.5, 5.4)
PREMIUM = 0.045                          # afternoon against night in the fair price: sold prices then show the
                                         # measured 4% (item 25(b)), since old listings sell first
PREMIUM_ANCHOR = 0.9                     # premium items' sellers price off at most this × the fair price, so their
                                         # posts follow the clock (at inference.ANCHOR's 1.1 a tenth of it shows)
PREMIUM_PEAK_LOCAL_HOUR = 16             # dearest 15:00–17:00, cheapest 01:00–04:00 (item 22(c))
AFTERNOON, NIGHT = range(15, 18), range(1, 5)        # local hours the premium is measured over
EVENING, MORNING = (18, 19, 20, 21, 22, 23, 0, 1, 2), range(6, 11)   # the rhythm's measured blocks, local hours
SLIDE_PER_DAY = 0.025                    # items sliding 2.5% a day (item 25(d): busy commodities fell 17% a week)
CRASH_TO, CRASH_DAYS = 0.1, 3            # an item that falls 90% over 3 days
EARLY_CRASH_DAY, LATE_CRASH_DAY = 9, 26  # the day each crash starts
DAYS = 35                                # 5 weeks: a slot of the pattern stands on its own from 3
WORTH = 200                              # copper, every item's fair price at the start
BUYS_PER_DAY, LISTINGS_PER_DAY = 150, 60
# (kind, items): 2 of 16 carry the premium, about the measured 13%.
KINDS = (("steady", 8), ("premium", 2), ("slide", 2), ("early crash", 2), ("late crash", 2))
FIRST_KEY = 3000


@dataclass(frozen=True)
class Planted:
    """What one item of the pattern world is, and its worth through time."""

    kind: str                                          # a KINDS name
    worth: int                                         # copper at the start
    shifts: tuple[tuple[int, float, float], ...]       # the item's shifts (`market.Item.shifts`)

    def worth_at(self, at_ms: int) -> float:
        """The item's worth at a time: its fair price with its shifts, without its hour of the week.

        Args:
            at_ms: UTC ms.

        Returns:
            Copper, float.
        """
        return self.worth * factor_at(self.shifts, at_ms)


def local_hour(at_ms: int) -> int:
    """The local hour of the day of a time.

    Args:
        at_ms: UTC ms.

    Returns:
        0–23, UTC_OFFSET_HOURS from UTC.
    """
    return (at_ms // HOUR_MS + UTC_OFFSET_HOURS) % 24


def by_slot(by_local_hour: Sequence[float]) -> tuple[float, ...]:
    """A day's 24 local-hour values laid over the week's 168 UTC slots, every weekday alike.

    Args:
        by_local_hour: 24 values, local 00:00 first.

    Returns:
        168 values, Monday 00:00 UTC first (`capacity.slot_of`).
    """
    return tuple(by_local_hour[(slot % 24 + UTC_OFFSET_HOURS) % 24] for slot in range(168))


def buyer_rhythm() -> tuple[float, ...]:
    """Buyers' arrivals by hour of the week (`Item.weekly`), from the measured share of a day's sales by hour.

    Returns:
        168 multipliers with a mean of 1: SOLD_BY_LOCAL_HOUR ÷ its mean, in UTC slots.
    """
    mean = sum(SOLD_BY_LOCAL_HOUR) / 24
    return by_slot([share / mean for share in SOLD_BY_LOCAL_HOUR])


def afternoon_premium(premium: float = PREMIUM) -> tuple[float, ...]:
    """A fair price that is dearest in the afternoon and cheapest in the small hours (`Item.hourly`).

    Args:
        premium: The dearest hour ÷ the cheapest, less 1 (0.04 = 4%).

    Returns:
        168 multipliers with a mean of 1: 1 + premium ÷ 2 × cos of the hours from PREMIUM_PEAK_LOCAL_HOUR.
    """
    return by_slot([1 + premium / 2 * math.cos(2 * math.pi * (hour - PREMIUM_PEAK_LOCAL_HOUR) / 24)
                    for hour in range(24)])


def slide(from_ms: int, to_ms: int, per_day: float, start: float = 1.0) -> tuple[tuple[int, float, float], ...]:
    """A fair price losing a share a day, as hourly steps (`Item.shifts`); buyers come as often.

    Args:
        from_ms: The first step's time, UTC ms.
        to_ms: No step at or after this time.
        per_day: The share lost a day (0.025 = 2.5%); negative for a rise.
        start: The factor before the first step.

    Returns:
        (time, price factor, 1.0) per hour: start × (1 − per_day) ^ (days since from_ms, the hour's end).
    """
    hours = range(1, (to_ms - from_ms) // HOUR_MS + 1)
    return tuple((from_ms + (h - 1) * HOUR_MS, start * (1 - per_day) ** (h / 24), 1.0) for h in hours)


def crash(at_ms: int, to: float = CRASH_TO, days: int = CRASH_DAYS) -> tuple[tuple[int, float, float], ...]:
    """A fair price falling to a share of itself over some days, as hourly steps, and staying there.

    Args:
        at_ms: When the fall starts, UTC ms.
        to: The factor it ends at (0.1 = a 90% fall).
        days: How long the fall takes.

    Returns:
        (time, price factor, 1.0) per hour of the fall, the same share lost each hour; the last is `to`.
    """
    hours = days * 24
    return tuple((at_ms + (h - 1) * HOUR_MS, to ** (h / hours), 1.0) for h in range(1, hours + 1))


def factor_at(shifts: Sequence[tuple[int, float, float]], at_ms: int) -> float:
    """The price factor of an item's shifts at a time, as the market applies them.

    Args:
        shifts: `Item.shifts`, in ascending time order.
        at_ms: UTC ms.

    Returns:
        The factor of the last shift at or before at_ms; 1.0 before the first.
    """
    last = bisect.bisect_right([start for start, _, _ in shifts], at_ms) - 1
    return 1.0 if last < 0 else shifts[last][1]


def kind_shifts(kind: str, days: int = DAYS) -> tuple[tuple[int, float, float], ...]:
    """The shifts a kind of item has.

    Args:
        kind: A KINDS name.
        days: The world's length.

    Returns:
        `Item.shifts`: a slide over the whole world, a crash from its day, or () for a steady or premium item.
    """
    if kind == "slide":
        return slide(START_MS, START_MS + days * DAY_MS, SLIDE_PER_DAY)
    if kind in ("early crash", "late crash"):
        return crash(START_MS + (EARLY_CRASH_DAY if kind == "early crash" else LATE_CRASH_DAY) * DAY_MS)
    return ()


def pattern_world(seed: int, days: int = DAYS, kinds: Sequence[tuple[str, int]] = KINDS
                  ) -> tuple[World, dict[int, Planted]]:
    """The pattern world: real-shaped commodities whose buyers keep the measured hours, each kind planted.

    Args:
        seed: The world's seed.
        days: Its length (5 weeks; a test's shorter).
        kinds: (kind, how many items of it).

    Returns:
        (the World, item key -> what it is).
    """
    items: list[Item] = []
    planted: dict[int, Planted] = {}
    rhythm, premium = buyer_rhythm(), afternoon_premium()
    key = FIRST_KEY
    for kind, count in kinds:
        for n in range(count):
            key += 1
            planted[key] = Planted(kind, WORTH, kind_shifts(kind, days))
            items.append(commodity(key, f"{kind} {n}", WORTH, BUYS_PER_DAY, LISTINGS_PER_DAY, weekly=rhythm,
                                   shifts=planted[key].shifts,
                                   **(dict(hourly=premium, anchor=PREMIUM_ANCHOR) if kind == "premium" else {})))
    return World(seed, START_MS, days, tuple(items)), planted


def sold_by_local_hour(truth: Truth, keys: Sequence[int]) -> list[float]:
    """The share of the units bought in each local hour of the day.

    Args:
        truth: The truth log.
        keys: The items counted.

    Returns:
        24 shares summing to 1, local 00:00 first; zeros without a buy.
    """
    units = [0] * 24
    chosen = set(keys)
    for e in truth.events:
        if e.kind == "buy" and e.key in chosen:
            units[local_hour(e.at_ms)] += e.units
    total = sum(units)
    return [u / total if total else 0.0 for u in units]


def sold_ratio(truth: Truth, planted: dict[int, Planted], keys: Sequence[int], dear: Sequence[int],
               cheap: Sequence[int]) -> float | None:
    """What items sold for in some local hours against others, each sale ÷ the worth then.

    Args:
        truth: The truth log.
        planted: The world's items.
        keys: The items counted.
        dear: Local hours of the numerator.
        cheap: Local hours of the denominator.

    Returns:
        The units-weighted mean of price ÷ worth in the dear hours ÷ the same in the cheap hours; None without a
        buy in either.
    """
    sums = {True: [0.0, 0], False: [0.0, 0]}
    chosen = set(keys)
    for e in truth.events:
        if e.kind != "buy" or e.key not in chosen:
            continue
        hour = local_hour(e.at_ms)
        if hour in dear or hour in cheap:
            row = sums[hour in dear]
            row[0] += e.units * e.price / planted[e.key].worth_at(e.at_ms)
            row[1] += e.units
    if not sums[True][1] or not sums[False][1]:
        return None
    return (sums[True][0] / sums[True][1]) / (sums[False][0] / sums[False][1])


def ask_ratio(books: Sequence[IdBook], planted: dict[int, Planted], keys: Sequence[int], dear: Sequence[int],
              cheap: Sequence[int]) -> float | None:
    """The cheapest ask in some local hours against others, each snapshot's ÷ the worth then.

    Args:
        books: The snapshots.
        planted: The world's items.
        keys: The items counted.
        dear: Local hours of the numerator.
        cheap: Local hours of the denominator.

    Returns:
        The mean of cheapest ask ÷ worth over the dear hours' snapshots ÷ the same over the cheap hours'; None
        without a snapshot listing an item in either.
    """
    sums = {True: [0.0, 0], False: [0.0, 0]}
    chosen = set(keys)
    for book in books:
        hour = local_hour(book.at_ms)
        if hour not in dear and hour not in cheap:
            continue
        lowest: dict[int, int] = {}
        for a in book.auctions:
            if a.key in chosen and a.price < lowest.get(a.key, a.price + 1):
                lowest[a.key] = a.price
        row = sums[hour in dear]
        for key, price in sorted(lowest.items()):
            row[0] += price / planted[key].worth_at(book.at_ms)
            row[1] += 1
    if not sums[True][1] or not sums[False][1]:
        return None
    return (sums[True][0] / sums[True][1]) / (sums[False][0] / sums[False][1])


def calibration(folder: object, seed: int = 1, days: int = DAYS, kinds: Sequence[tuple[str, int]] = KINDS
                ) -> list[Measure]:
    """The pattern world against the real market's measurements (the `market-sim` agent's rule 5), market alone.

    Args:
        folder: Unused (the market runs without the cloud job); kept for the report's row signature.
        seed: The world's seed.
        days: The world's length.
        kinds: (kind, how many items of it).

    Returns:
        Reported, the simulated number against the real one: the busiest hour's sales ÷ the quietest's (1.9); an
        evening hour's ÷ a morning hour's (1.6); a steady item's afternoon sold price ÷ its night one (1.00); a
        premium item's (1.04) and its cheapest ask's (1.00: the real asks do not show the premium).
    """
    world, planted = pattern_world(seed, days, kinds)
    truth, books = market_books(world)
    every = sorted(planted)
    steady = [key for key in every if planted[key].kind == "steady"]
    premium = [key for key in every if planted[key].kind == "premium"]
    shares = sold_by_local_hour(truth, every)
    evening = sum(shares[h] for h in EVENING) / len(EVENING)
    morning = sum(shares[h] for h in MORNING) / len(MORNING)
    return [
        Measure("SM23", "world: the busiest hour's sales ÷ the quietest's (real 1.9)",
                max(shares) / min(shares) if min(shares) else None, 1.9, "reported"),
        Measure("SM23", "world: an evening hour's sales ÷ a morning hour's (real 1.6)",
                evening / morning if morning else None, 1.6, "reported"),
        Measure("SM23", "world: steady items' sold price, afternoon ÷ night (real 1.00)",
                sold_ratio(truth, planted, steady, AFTERNOON, NIGHT), 1.0, "reported"),
        Measure("SM23", "world: premium items' sold price, afternoon ÷ night (real 1.04)",
                sold_ratio(truth, planted, premium, AFTERNOON, NIGHT), 1.04, "reported"),
        Measure("SM23", "world: premium items' cheapest ask, afternoon ÷ night (real 1.00)",
                ask_ratio(books, planted, premium, AFTERNOON, NIGHT), 1.0, "reported"),
    ]
