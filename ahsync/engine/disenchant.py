"""Disenchant values and targets (SPEC §3.4A).

Ported to addon/AHProfit/Engine/Disenchant.lua (`outcomes_for`, `expected_value`,
`can_disenchant`); keep them identical. `targets` (ranking the AH) is Python only.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

Outcome = tuple[int, float, int, int]      # (material key, chance, qty_min, qty_max)


@dataclass(frozen=True)
class DisenchantTarget:
    """One AH item worth buying to disenchant."""

    key: int
    buy_copper: int           # its buyout
    ev: float                 # expected value of the materials, float copper
    profit: float             # ev − buy
    skill_required: int       # Enchanting skill needed


def outcomes_for(table: Sequence[tuple], quality: int, ilvl: int, kind: str) -> tuple[int, tuple[Outcome, ...]] | None:
    """The disenchant row for an item (SPEC §4.5).

    Args:
        table: DE_TABLE rows (quality, kind, ilvl_min, ilvl_max, skill, outcomes).
        quality: Item quality (2 uncommon, 3 rare, ...).
        ilvl: Item level.
        kind: "armor" or "weapon".

    Returns:
        (Enchanting skill needed, outcomes), or None when the item can't be disenchanted.
    """
    for row_quality, row_kind, lo, hi, skill, outcomes in table:
        if row_quality == quality and row_kind == kind and lo <= ilvl <= hi:
            return skill, outcomes
    return None


def expected_value(outcomes: Sequence[Outcome], sell_values: Sequence[float]) -> float:
    """EV = Σ P(mat) × E[qty] × sellValue(mat) (SPEC §3.4A).

    Args:
        outcomes: The item's materials (key, chance, qty_min, qty_max).
        sell_values: Sell value per unit of each material, in the same order, float copper
            (E_post per unit, so mats that don't sell are worth less; SPEC §3.4).

    Returns:
        Expected value of one disenchant, float copper.
    """
    total = 0.0
    for (_, chance, qty_min, qty_max), value in zip(outcomes, sell_values):
        total += chance * ((qty_min + qty_max) / 2) * value
    return total


def can_disenchant(skill_required: int, best_skill: int | None) -> bool:
    """Whether one of your characters can disenchant it (SPEC §3.4A).

    Args:
        skill_required: Enchanting skill the item needs.
        best_skill: Your highest Enchanting skill, or None when nobody has Enchanting.

    Returns:
        True when best_skill ≥ skill_required.
    """
    return best_skill is not None and best_skill >= skill_required


def targets(listings: Sequence[tuple[int, int, int, int, str]], table: Sequence[tuple],
            sell_value: dict[int, float]) -> list[DisenchantTarget]:
    """AH items worth evaluating to buy and disenchant, whoever can disenchant them (SPEC §3.4A, D71).

    Research is never gated by profession: every disenchantable listing is a target. Only funding needs
    a character with Enchanting at the item's skill (`can_disenchant`, §3.4D.4, PF9).

    Args:
        listings: (key, buyout copper, quality, ilvl, kind) for items on the AH.
        table: DE_TABLE.
        sell_value: Material key -> sell value per unit, float copper; a material
            without one counts as 0.

    Returns:
        One target per disenchantable listing, profit highest first (ties: lower buyout, then key).
    """
    found = []
    for key, buy, quality, ilvl, kind in listings:
        row = outcomes_for(table, quality, ilvl, kind)
        if row is None:
            continue
        ev = expected_value(row[1], [sell_value.get(mat, 0.0) for mat, _, _, _ in row[1]])
        found.append(DisenchantTarget(key, buy, ev, ev - buy, row[0]))
    found.sort(key=lambda t: (-t.profit, t.buy_copper, t.key))
    return found
