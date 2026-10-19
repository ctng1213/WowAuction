"""Tests for ahsync/engine/item_key.py: item keys from an item id and its bonus IDs (SRC5)."""
from __future__ import annotations

from ahsync.engine.item_key import item_key

SUFFIXES = {12742: 14322}


def test_SRC5_a_bonus_list_keys_an_item_as_ahf_and_the_addon_do():
    assert item_key(2840, [], SUFFIXES) == 2840
    assert item_key(15210, [1, 12742], SUFFIXES) == 15210 + 2_147_483_648 * 28644      # v = 2 × 14322
    assert item_key(15210, [99], SUFFIXES) == -15210                                    # unknown suffix: never valued
