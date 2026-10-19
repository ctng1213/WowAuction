"""AHSync's item key from an item id and its bonus IDs (SPEC §2.1, §2.2.1, §6 item 11).

The key is AHF's: `item id + 2^31 × v`, where v = 2 × the random suffix id that one of the item's bonus IDs maps
to (AHF's bonusLists table, generated into ahsync/data/bonus_suffixes.json). A provider that gives an item id
plus bonus lists (a Blizzard snapshot) keys its auctions this way, the same rule as the addon's
Adapter.ItemKey. Pure.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

KEY_SUFFIX_FACTOR = 2_147_483_648      # 2^31


def item_key(item_id: int, bonus_ids: Sequence[int], suffixes: Mapping[int, int]) -> int:
    """The item key of one item (SRC5).

    Args:
        item_id: The item's id.
        bonus_ids: Its bonus IDs, in the provider's order.
        suffixes: Bonus ID -> random suffix id.

    Returns:
        item id when it has no bonus IDs; item + 2^31 × 2 × suffix id for the first bonus ID that is a known
        suffix; −item id when it has bonus IDs but none is a known suffix (stored, never valued as its base item).
    """
    for bonus in bonus_ids:
        suffix = suffixes.get(bonus)
        if suffix is not None:
            return item_id + KEY_SUFFIX_FACTOR * 2 * suffix
    return -item_id if bonus_ids else item_id


def suffix_id(key: int) -> int | None:
    """The random suffix id a positive item key encodes (SPEC §2.1: v = 2 × suffix id).

    Args:
        key: An AHF item key.

    Returns:
        v ÷ 2 for a suffixed key; None for a plain item, a negative (unknown-suffix) key or an odd v.
    """
    v = key // KEY_SUFFIX_FACTOR if key > 0 else 0
    return v // 2 if v and v % 2 == 0 else None


def variant_name(key: int, base_name: str, suffix_names: Mapping[int, str]) -> str:
    """The display name of an item key from its base item's name (SPEC §2.2.1, D60).

    AHF's `variants` list misses some suffixed keys, so a variant is named from its base item plus AHF's suffix
    name table (true for all 523 variants AHF does list: "Grunt's Bracers" + "of Defense").

    Args:
        key: An AHF item key, or −item id for an item whose suffix is unknown.
        base_name: The base item's name.
        suffix_names: Suffix id -> name ("of Defense").

    Returns:
        The base name for a plain key; "<base> <suffix name>" for a suffixed key, "<base> (variant)" when its
        suffix has no known name; "<base> (unknown suffix)" for a negative key.
    """
    if key < 0:
        return f"{base_name} (unknown suffix)"
    if key < KEY_SUFFIX_FACTOR:
        return base_name
    suffix_of_key = suffix_id(key)
    suffix = suffix_names.get(suffix_of_key) if suffix_of_key is not None else None
    return f"{base_name} {suffix}" if suffix else f"{base_name} (variant)"
