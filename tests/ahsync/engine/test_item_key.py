"""Tests for ahsync/engine/item_key.py: item keys from an item id and its bonus IDs (SRC5)."""
from __future__ import annotations

from ahsync.engine.item_key import KEY_SUFFIX_FACTOR, item_key, suffix_id, variant_name

SUFFIXES = {12742: 14322}


def test_SRC5_a_bonus_list_keys_an_item_as_ahf_and_the_addon_do():
    assert item_key(2840, [], SUFFIXES) == 2840
    assert item_key(15210, [1, 12742], SUFFIXES) == 15210 + 2_147_483_648 * 28644      # v = 2 × 14322
    assert item_key(15210, [99], SUFFIXES) == -15210                                    # unknown suffix: never valued


GRUNTS_BRACERS = 15507
GRUNTS_OF_DEFENSE = 61486751825043          # 15507 + 2^31 × 28632: suffix 14316, "of Defense"


def test_D60_a_variant_is_named_from_its_base_item_and_the_suffix_table():
    names = {14316: "of Defense"}
    assert suffix_id(GRUNTS_OF_DEFENSE) == 14316 and suffix_id(GRUNTS_BRACERS) is None and suffix_id(-15507) is None
    assert variant_name(GRUNTS_BRACERS, "Grunt's Bracers", names) == "Grunt's Bracers"
    assert variant_name(GRUNTS_OF_DEFENSE, "Grunt's Bracers", names) == "Grunt's Bracers of Defense"
    assert variant_name(GRUNTS_OF_DEFENSE, "Grunt's Bracers", {}) == "Grunt's Bracers (variant)"
    assert variant_name(-GRUNTS_BRACERS, "Grunt's Bracers", names) == "Grunt's Bracers (unknown suffix)"
    assert suffix_id(GRUNTS_BRACERS + KEY_SUFFIX_FACTOR) is None                  # an odd v names no suffix
