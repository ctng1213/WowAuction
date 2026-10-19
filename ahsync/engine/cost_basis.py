"""What you actually paid for items, account-wide (SPEC §3.2 cost_basis, T9-T13).

Purchases and crafts are folded in time order: a purchase adds its units at its price; a craft
takes its mats out at their average cost (units beyond what was bought cost 0) and adds the
product at what those mats cost (T10). The result is then reconciled with what you hold now
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


def _fold(bought: Sequence[Bought], made: Sequence[Made]) -> dict[int, list[float]]:
    """Purchases and crafts in time order (ties: event id).

    Args:
        bought: Purchases.
        made: Crafts.

    Returns:
        Item key -> [tracked units, their total cost].
    """
    lots: dict[int, list[float]] = {}
    steps = sorted([(b.at_ms, b.event_id, b) for b in bought] + [(m.at_ms, m.event_id, m) for m in made],
                   key=lambda s: (s[0], s[1]))
    for _, _, step in steps:
        if isinstance(step, Bought):
            lot = lots.setdefault(step.key, [0.0, 0.0])
            lot[0], lot[1] = lot[0] + step.qty, lot[1] + step.qty * step.price
            continue
        cost = 0.0
        for key, used in step.reagents:
            lot = lots.setdefault(key, [0.0, 0.0])
            take = min(used, lot[0])
            each = lot[1] / lot[0] if lot[0] else 0.0
            cost += each * take                    # mats beyond what was tracked cost 0
            lot[0], lot[1] = lot[0] - take, lot[1] - each * take
        lot = lots.setdefault(step.product, [0.0, 0.0])
        lot[0], lot[1] = lot[0] + step.units, lot[1] + cost
    return lots


def cost_basis(bought: Sequence[Bought], made: Sequence[Made], held: Mapping[int, int]) -> dict[int, Basis]:
    """Each item's cost basis, reconciled with what you hold now (module docstring).

    Args:
        bought: Confirmed purchases.
        made: Crafts.
        held: Item key -> units you hold now, every character and place.

    Returns:
        Item key -> Basis, for items bought or crafted that you still hold; by key.
    """
    out = {}
    for key, (tracked, total) in sorted(_fold(bought, made).items()):
        units = held.get(key, 0)
        if units <= 0 or tracked <= 0:
            continue
        if units < tracked:                        # units left at their average cost
            total, tracked = total * units / tracked, units
        out[key] = Basis(units, total, int(units - tracked))
    return out
