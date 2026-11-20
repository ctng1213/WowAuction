"""Cooldowns: the curated catalog of item-making profession cooldowns (SPEC §3.4F).

Pure: the catalog's rows come in already read (recommend/cooldowns_page.py reads the data file
`ahsync/data/cooldown_catalog.json`; tools/gen_cooldown_data.py writes the addon's twin from the same
rows). A price never makes an item a cooldown item: only the catalog does (§3.4F.1). Built CD1: the
catalog's validation and its Status text. The row states, ready times, money and Can Make come with
CD3.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# The professions a character can have (SPEC §2.2.1 Professions.NAMES), in name order: the Profession filter's list.
PROFESSIONS = ("Alchemy", "Blacksmithing", "Cooking", "Enchanting", "Engineering", "First Aid", "Fishing",
               "Herbalism", "Leatherworking", "Mining", "Skinning", "Tailoring")
MIN_SKILL, MAX_SKILL = 1, 300


@dataclass(frozen=True)
class CooldownRecipe:
    """One catalog row: a recipe or a tool use that makes an item and has a cooldown (SPEC §3.4F.1)."""

    spell: int                             # the recipe's spell id, or the tool's Use spell
    name: str                              # the spell's name, e.g. "Transmute: Arcanite"
    profession: str                        # one of PROFESSIONS
    skill: int                             # required skill, 1-300
    cooldown_s: int                        # the cooldown after each use, seconds (> 0)
    product: int                           # the item made (its AHF key)
    yield_min: int                         # units made per use, at least
    yield_max: int                         # units made per use, at most
    reagents: tuple[tuple[int, int], ...]  # (item key, count) per mat, by key
    group: str | None = None               # the shared cooldown's name; None = its own cooldown
    tool: int | None = None                # an item the character must hold (Philosopher's Stone, Salt Shaker)
    tool_use: bool = False                 # the row is the tool's Use spell: no recipe to learn (Salt Shaker)
    url: str | None = None                 # the source page
    note: str | None = None                # an uncertainty or a Forever caveat


def _positive_int(value: Any) -> bool:
    """Whether a value read from JSON is a whole number above zero.

    Args:
        value: The value as read.

    Returns:
        True for an int > 0 (bool excluded).
    """
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _reagents(raw: Any) -> tuple[tuple[int, int], ...] | None:
    """A row's mats, validated.

    Args:
        raw: The row's "reagents" as read: [[key, count], ...].

    Returns:
        (key, count) pairs sorted by key, or None when the list is missing, empty or holds a bad pair
        (a key or count that isn't a positive int, or a key listed twice).
    """
    if not isinstance(raw, list) or not raw:
        return None
    pairs = []
    for entry in raw:
        if not isinstance(entry, list) or len(entry) != 2 or not all(_positive_int(v) for v in entry):
            return None
        pairs.append((entry[0], entry[1]))
    keys = [k for k, _ in pairs]
    return tuple(sorted(pairs)) if len(set(keys)) == len(keys) else None


def _optional_text(raw: dict, field: str) -> str | None:
    """An optional text field.

    Args:
        raw: The row as read.
        field: The field's name.

    Returns:
        The text, or None when absent, empty or not a string.
    """
    value = raw.get(field)
    return value if isinstance(value, str) and value else None


def _why_invalid(raw: dict) -> str:
    """The first reason a row can't be used, in the order SPEC §3.4F.1 lists them.

    Args:
        raw: The row as read (a dict).

    Returns:
        Why it is skipped, or "" when every field is good.
    """
    skill, yield_min, yield_max = raw.get("skill"), raw.get("yield_min"), raw.get("yield_max")
    checks = (
        (not _positive_int(raw.get("spell")) or not isinstance(raw.get("name"), str), "no spell or name"),
        (raw.get("profession") not in PROFESSIONS, f"unknown profession {raw.get('profession')!r}"),
        (not _positive_int(skill) or not MIN_SKILL <= skill <= MAX_SKILL, f"skill {skill} outside {MIN_SKILL}–{MAX_SKILL}"),
        (not _positive_int(raw.get("cooldown_s")), f"cooldown {raw.get('cooldown_s')} not positive"),
        (not _positive_int(raw.get("product")), "no product"),
        (_reagents(raw.get("reagents")) is None, "no reagents list"),
        (not _positive_int(yield_min) or not _positive_int(yield_max) or yield_max < yield_min,
         f"yield {yield_min}–{yield_max} not valid"),
        ("tool" in raw and raw["tool"] is not None and not _positive_int(raw["tool"]), f"tool {raw.get('tool')!r} not an item"),
        (raw.get("tool_use") is True and not _positive_int(raw.get("tool")), "a tool use without a tool"),
    )
    return next((why for failed, why in checks if failed), "")


def _recipe(raw: Any) -> tuple[CooldownRecipe | None, str]:
    """One catalog row, validated (SPEC §3.4F.1, CD2).

    Args:
        raw: The row as read from the file.

    Returns:
        (the CooldownRecipe, "") or (None, why it is skipped).
    """
    if not isinstance(raw, dict):
        return None, "not a row"
    why = _why_invalid(raw)
    if why:
        return None, why
    reagents = _reagents(raw["reagents"])
    assert reagents is not None                      # _why_invalid checked it
    return CooldownRecipe(raw["spell"], raw["name"], raw["profession"], raw["skill"], raw["cooldown_s"],
                          raw["product"], raw["yield_min"], raw["yield_max"], reagents,
                          _optional_text(raw, "group"), raw.get("tool"), raw.get("tool_use") is True,
                          _optional_text(raw, "url"), _optional_text(raw, "note")), ""


def _row_name(raw: Any) -> str:
    """The name a skipped row is reported under.

    Args:
        raw: The row as read.

    Returns:
        Its name, or "?" when it has none.
    """
    return raw["name"] if isinstance(raw, dict) and isinstance(raw.get("name"), str) else "?"


def _mixed_groups(kept: dict[int, CooldownRecipe]) -> set[str]:
    """The groups whose members have different professions (SPEC §3.4F.1): a shared cooldown is one profession's.

    Args:
        kept: The valid rows so far, by spell.

    Returns:
        The names of the groups to reject.
    """
    seen: dict[str, set[str]] = {}
    for recipe in kept.values():
        if recipe.group is not None:
            seen.setdefault(recipe.group, set()).add(recipe.profession)
    return {group for group, professions in seen.items() if len(professions) > 1}


def catalog(rows: Sequence[Any]) -> tuple[list[CooldownRecipe], list[str]]:
    """Validate the catalog's rows (SPEC §3.4F.1, CD1, CD2): a bad row is skipped, the others are kept.

    Args:
        rows: The data file's "recipes" list, as read.

    Returns:
        (the valid rows by spell id, one "Name: why" per skipped row in file order). A second row with a
        spell already seen is skipped as a duplicate; every member of a group that mixes professions is
        skipped, after the rows that were already skipped.
    """
    kept: dict[int, CooldownRecipe] = {}
    skipped = []
    for raw in rows:
        recipe, why = _recipe(raw)
        if recipe is not None and recipe.spell in kept:
            recipe, why = None, f"duplicate spell {recipe.spell}"
        if recipe is None:
            skipped.append(f"{_row_name(raw)}: {why}")
            continue
        kept[recipe.spell] = recipe
    mixed = _mixed_groups(kept)
    for spell in sorted(kept):
        if kept[spell].group in mixed:
            skipped.append(f"{kept[spell].name}: group {kept[spell].group!r} mixes professions")
    return [kept[s] for s in sorted(kept) if kept[s].group not in mixed], skipped


def catalog_text(kept: Sequence[CooldownRecipe], skipped: Sequence[str]) -> str:
    """The catalog's Status line (SPEC §3.4F.1, §3.4F.7, CD1, CD2).

    Args:
        kept: From catalog(): the valid rows.
        skipped: From catalog(): "Name: why" per skipped row.

    Returns:
        "cooldown catalog: 14 recipes", or "cooldown catalog: 1 row skipped (Mooncloth: skill 350 outside 1–300)".
    """
    if skipped:
        return f"cooldown catalog: {len(skipped)} row{'' if len(skipped) == 1 else 's'} skipped ({'; '.join(skipped)})"
    return f"cooldown catalog: {len(kept)} recipe{'' if len(kept) == 1 else 's'}"
