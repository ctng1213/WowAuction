"""Untracked changes: what your holdings did that no recorded event explains (SPEC §2.2.1, §5.2, FM21, D51).

Between two bag snapshots of a character, each item's change in bags, bank and mail together, less what the
events in between explain (vendor buys, crafts, disenchants), is untracked: looted, traded, used, sold to a
vendor or destroyed. Items moved by the auction house or by mail in that time are left out, because their
places are captured at different moments and would show changes that aren't real. Pure.
"""
from __future__ import annotations

from collections.abc import Mapping, Set


def unexplained(old: Mapping[int, int], new: Mapping[int, int], explained: Mapping[int, float],
                skip: Set[int]) -> dict[int, int]:
    """The change of each item that no event explains (D51).

    Args:
        old: Item key -> units held (bags, bank and mail) at the earlier bag snapshot.
        new: Item key -> units held at the later one.
        explained: Item key -> units the events in between added (negative: removed); crafts may give fractions.
        skip: Items moved by the auction house or by mail in between.

    Returns:
        Item key -> untracked units (positive: gained, negative: lost), non-zero ones only, by key; a fraction
        rounds half away from zero.
    """
    out = {}
    for key in sorted(set(old) | set(new) | set(explained)):
        if key in skip:
            continue
        rest = new.get(key, 0) - old.get(key, 0) - explained.get(key, 0.0)
        units = int(rest + 0.5) if rest >= 0 else -int(-rest + 0.5)
        if units:
            out[key] = units
    return out
