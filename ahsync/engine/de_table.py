"""Disenchant table: quality × item level band × armor/weapon → materials (SPEC §4.5, §3.4A).

UNVERIFIED for Forever (SPEC §6 item 3): seeded from Classic Era values. The addon's
Data/Disenchant.lua is generated from this table by tools/gen_disenchant_data.py, so
the two never drift. Item keys are AHF keys (base item ids; no materials have suffixes).
"""
from __future__ import annotations

# Material item ids.
STRANGE_DUST, SOUL_DUST, VISION_DUST, DREAM_DUST, ILLUSION_DUST = 10940, 11083, 11137, 11176, 16204
LESSER_MAGIC, GREATER_MAGIC = 10938, 10939
LESSER_ASTRAL, GREATER_ASTRAL = 10998, 11082
LESSER_MYSTIC, GREATER_MYSTIC = 11134, 11135
LESSER_NETHER, GREATER_NETHER = 11174, 11175
LESSER_ETERNAL, GREATER_ETERNAL = 16202, 16203
SMALL_GLIMMERING, LARGE_GLIMMERING = 10978, 11084
SMALL_GLOWING, LARGE_GLOWING = 11138, 11139
SMALL_RADIANT, LARGE_RADIANT = 11177, 11178
SMALL_BRILLIANT, LARGE_BRILLIANT = 14343, 14344

UNCOMMON, RARE = 2, 3
ARMOR, WEAPON = "armor", "weapon"

# (ilvl_min, ilvl_max, enchanting skill, dust (id, min, max), essence (id, min, max), shard id)
_UNCOMMON_BANDS = (
    (5, 15, 1, (STRANGE_DUST, 1, 2), (LESSER_MAGIC, 1, 2), None),
    (16, 20, 1, (STRANGE_DUST, 2, 3), (GREATER_MAGIC, 1, 2), SMALL_GLIMMERING),
    (21, 25, 25, (STRANGE_DUST, 4, 6), (LESSER_ASTRAL, 1, 2), SMALL_GLIMMERING),
    (26, 30, 50, (SOUL_DUST, 1, 2), (GREATER_ASTRAL, 1, 2), LARGE_GLIMMERING),
    (31, 35, 75, (SOUL_DUST, 2, 5), (LESSER_MYSTIC, 1, 2), SMALL_GLOWING),
    (36, 40, 100, (VISION_DUST, 1, 2), (GREATER_MYSTIC, 1, 2), LARGE_GLOWING),
    (41, 45, 125, (VISION_DUST, 2, 5), (LESSER_NETHER, 1, 2), SMALL_RADIANT),
    (46, 50, 150, (DREAM_DUST, 1, 2), (GREATER_NETHER, 1, 2), LARGE_RADIANT),
    (51, 55, 175, (DREAM_DUST, 2, 5), (LESSER_ETERNAL, 1, 2), SMALL_BRILLIANT),
    (56, 60, 200, (ILLUSION_DUST, 1, 2), (GREATER_ETERNAL, 1, 2), LARGE_BRILLIANT),
    (61, 65, 225, (ILLUSION_DUST, 2, 5), (GREATER_ETERNAL, 2, 3), LARGE_BRILLIANT),
)
_RARE_SHARDS = ((1, 25, 1, SMALL_GLIMMERING), (26, 30, 50, LARGE_GLIMMERING), (31, 35, 75, SMALL_GLOWING),
                (36, 40, 100, LARGE_GLOWING), (41, 45, 125, SMALL_RADIANT), (46, 50, 150, LARGE_RADIANT),
                (51, 55, 175, SMALL_BRILLIANT), (56, 65, 225, LARGE_BRILLIANT))


def _uncommon_rows() -> list[tuple]:
    """Uncommon armor and weapon rows: armor is mostly dust, weapons mostly essence.

    Returns:
        DE_TABLE rows for quality 2.
    """
    rows = []
    for lo, hi, skill, dust, essence, shard in _UNCOMMON_BANDS:
        shard_chance = 0.05 if shard else 0.0
        main, other = 0.75 if shard else 0.80, 0.20
        for kind, first, second in ((ARMOR, dust, essence), (WEAPON, essence, dust)):
            outcomes = [(first[0], main, first[1], first[2]), (second[0], other, second[1], second[2])]
            if shard:
                outcomes.append((shard, shard_chance, 1, 1))
            rows.append((UNCOMMON, kind, lo, hi, skill, tuple(outcomes)))
    return rows


def _rare_rows() -> list[tuple]:
    """Rare (blue) rows: almost always a shard, for armor and weapons alike.

    Returns:
        DE_TABLE rows for quality 3.
    """
    return [(RARE, kind, lo, hi, skill, ((shard, 1.0, 1, 1),))
            for lo, hi, skill, shard in _RARE_SHARDS for kind in (ARMOR, WEAPON)]


# (quality, kind, ilvl_min, ilvl_max, enchanting skill, ((material key, chance, qty_min, qty_max), ...))
DE_TABLE: tuple[tuple, ...] = tuple(_uncommon_rows() + _rare_rows())
