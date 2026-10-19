"""What you actually paid for items, account-wide (SPEC §3.2 cost_basis, T9-T13).

Purchases, crafts and disenchants are folded in time order: a purchase adds its units at its price;
a craft takes its mats out at their average cost (units beyond what was bought cost 0) and adds the
product at what those mats cost (T10); a disenchant takes one item out the same way and adds its
results at that cost, split by their value (units × price, T20), or by units when a result has no price (T18). The result is then reconciled with what you hold now
across every character (bags, bank, mail and your auctions): units that left take their average
cost with them, and units held beyond what was bought or crafted, found or held before tracking,
count at 0 (T9). An item never bought or crafted gets no basis: it shows "before tracking" (T12).
Mailing between your characters changes nothing (T13). Pure.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Bought:
    """A confirmed purchase, on the AH or from a vendor."""

    event_id: str
    key: int
    qty: int
    price: int                     # each, copper
    at_ms: int


@dataclass(frozen=True)
class Made:
    """A craft: the units it made and the mats it used."""

    event_id: str
    product: int
    units: int
    reagents: tuple[tuple[int, int], ...]   # (key, units used)
    at_ms: int


@dataclass(frozen=True)
class Disenchanted:
    """A disenchant: the item it took and what it gave."""

    event_id: str
    key: int
    results: tuple[tuple[int, int], ...]    # (key, units)
    at_ms: int


@dataclass(frozen=True)
class Basis:
    """An item's cost basis."""

    qty: int                       # units held, bought or crafted and untracked together
    total_cost: float              # copper
    untracked: int                 # units held beyond what was bought or crafted, at cost 0

    @property
    def paid_each(self) -> float:
        """Average cost per unit.

        Returns:
            total_cost ÷ qty, float copper (0 for no units).
        """
        return self.total_cost / self.qty if self.qty else 0.0


def _take(lots: dict[int, list[float]], used: tuple[tuple[int, int], ...]) -> float:
    """Take units out of the lots at their average cost.

    Args:
        lots: Item key -> [tracked units, their total cost]; changed in place.
        used: (key, units) taken.

    Returns:
        What the taken units cost, copper; units beyond what was tracked cost 0.
    """
    cost = 0.0
    for key, units in used:
        lot = lots.setdefault(key, [0.0, 0.0])
        take = min(units, lot[0])
        each = lot[1] / lot[0] if lot[0] else 0.0
        cost += each * take
        lot[0], lot[1] = lot[0] - take, lot[1] - each * take
    return cost


def _add(lots: dict[int, list[float]], key: int, units: float, cost: float) -> None:
    """Add units at a total cost.

    Args:
        lots: Item key -> [tracked units, their total cost]; changed in place.
        key: Item key.
        units: Units added.
        cost: Their total cost, copper.
    """
    lot = lots.setdefault(key, [0.0, 0.0])
    lot[0], lot[1] = lot[0] + units, lot[1] + cost


def _shares(results: tuple[tuple[int, int], ...], prices: Mapping[int, int]) -> list[tuple[int, int, float]]:
    """How a disenchant's cost splits over its results (T18, T20).

    Args:
        results: (key, units).
        prices: Item key -> a unit's market price, copper.

    Returns:
        (key, units, share of the cost) per result: by value when every result has a price above 0, else by units.
    """
    priced = all(prices.get(key, 0) > 0 for key, _ in results)
    weights = [n * (prices[key] if priced else 1) for key, n in results]
    total = sum(weights)
    return [(key, n, w / total) for (key, n), w in zip(results, weights)]


def _fold(bought: Sequence[Bought], made: Sequence[Made],
          disenchanted: Sequence[Disenchanted] = (), prices: Mapping[int, int] | None = None) -> dict[int, list[float]]:
    """Purchases, crafts and disenchants in time order (ties: event id).

    Args:
        bought: Purchases.
        made: Crafts.
        disenchanted: Disenchants.
        prices: Item key -> a unit's market price, copper, for splitting a disenchant's cost (T20).

    Returns:
        Item key -> [tracked units, their total cost].
    """
    lots: dict[int, list[float]] = {}
    steps = sorted([(s.at_ms, s.event_id, s) for s in (*bought, *made, *disenchanted)], key=lambda s: (s[0], s[1]))
    for _, _, step in steps:
        if isinstance(step, Bought):
            _add(lots, step.key, step.qty, step.qty * step.price)
        elif isinstance(step, Made):
            _add(lots, step.product, step.units, _take(lots, step.reagents))
        else:
            cost = _take(lots, ((step.key, 1),))
            for key, n, share in _shares(step.results, prices or {}):
                _add(lots, key, n, cost * share)
    return lots


def cost_basis(bought: Sequence[Bought], made: Sequence[Made], held: Mapping[int, int],
               disenchanted: Sequence[Disenchanted] = (), prices: Mapping[int, int] | None = None) -> dict[int, Basis]:
    """Each item's cost basis, reconciled with what you hold now (module docstring).

    Args:
        bought: Confirmed purchases.
        made: Crafts.
        held: Item key -> units you hold now, every character and place.
        disenchanted: Disenchants.
        prices: Item key -> a unit's market price, copper, for splitting a disenchant's cost (T20); None splits by units.

    Returns:
        Item key -> Basis, for items bought or crafted that you still hold; by key.
    """
    out = {}
    for key, (tracked, total) in sorted(_fold(bought, made, disenchanted, prices).items()):
        units = held.get(key, 0)
        if units <= 0 or tracked <= 0:
            continue
        if units < tracked:                        # units left at their average cost
            total, tracked = total * units / tracked, units
        out[key] = Basis(units, total, int(units - tracked))
    return out
