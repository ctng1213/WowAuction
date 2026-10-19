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
