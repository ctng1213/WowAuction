"""Cooldowns: the curated catalog of item-making profession cooldowns (SPEC §3.4F).

Pure: the catalog's rows come in already read (recommend/cooldowns_page.py reads the data file
`ahsync/data/cooldown_catalog.json`; tools/gen_cooldown_data.py writes the addon's twin from the same
rows). A price never makes an item a cooldown item: only the catalog does (§3.4F.1). Built CD1: the
catalog's validation and its Status text. Built CD3: a character's standing for a row (§3.4F.2, with
groups resolved to the newest observation of any member), the money (§3.4F.4) and Can Make (§3.4F.5).
Standings carry absolute ready times, so the clock is applied only where a view is made (cooldowns_view).
Money is integer copper except Net / use and Per week, which are float copper (SPEC §3.3).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ahsync.engine.fees import sale_net

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


DAY_S = 86_400
MINUTE_MS, HOUR_MS, DAY_MS = 60_000, 3_600_000, 86_400_000


@dataclass(frozen=True)
class Holder:
    """One character as the cooldowns see it: what the addon stored, never recalculated (SPEC §3.4F.2, D1)."""

    char_id: str
    name: str
    skills: Mapping[str, int]                    # profession -> current skill
    known: frozenset[int]                        # spell ids of the recipes it knows
    bags: Mapping[int, int]                      # item key -> units in its bags (bound too)
    bank: Mapping[int, int]                      # item key -> units in its bank, as of its last visit
    ready: Mapping[int, tuple[int, int]]         # spell -> (ready at, observed at), UTC ms
    updated_ms: int | None = None                # when its data was captured


@dataclass(frozen=True)
class Standing:
    """A character's state for one row, without the clock (SPEC §3.4F.2, §3.4F.5)."""

    char_id: str
    name: str
    skill: int                                   # its skill in the row's profession
    state: str                                   # "below" | "not_learned" | "needs_item" | "not_read" | "timed"
    ready_at_ms: int | None = None               # for "timed": when the row (or its group) is ready
    bank: bool = False                           # a needed mat or the tool is only in its bank
    missing: str = ""                            # the first mat it lacks, e.g. "1 Essence of Water"


def group_members(recipe: CooldownRecipe, recipes: Sequence[CooldownRecipe]) -> list[int]:
    """The spells that share a row's cooldown, itself included (SPEC §3.4F.2, D10).

    Args:
        recipe: A catalog row.
        recipes: The whole catalog.

    Returns:
        Spell ids, ascending; just its own when it has no group.
    """
    if recipe.group is None:
        return [recipe.spell]
    return sorted(r.spell for r in recipes if r.group == recipe.group)


def ready_at(members: Sequence[int], holder: Holder) -> int | None:
    """When a character can use a row again: the newest observation of any member (SPEC §3.4F.2, §3.4F.3, CD12).

    Args:
        members: From group_members.
        holder: The character.

    Returns:
        UTC ms (in the past = ready), or None when nothing is stored (not read).
    """
    seen = [holder.ready[s] for s in members if s in holder.ready]
    return max(seen, key=lambda r: (r[1], r[0]))[0] if seen else None


def _mats_place(recipe: CooldownRecipe, holder: Holder, names: Mapping[int, str]) -> tuple[str, str]:
    """Where a character holds a row's mats and tool (SPEC §3.4F.5).

    Args:
        recipe: A catalog row.
        holder: The character.
        names: Item key -> name, for the missing mat.

    Returns:
        ("bags", "") when its bags hold everything, ("bank", "") when the bank is needed too,
        or ("missing", "1 Essence of Water") for the first mat (by key) it lacks.
    """
    place = "bags"
    for key, count in recipe.reagents:
        bags, bank = holder.bags.get(key, 0), holder.bank.get(key, 0)
        if bags >= count:
            continue
        if bags + bank >= count:
            place = "bank"
            continue
        return "missing", f"{count - bags - bank} {names.get(key, 'item ' + str(key))}"
    if recipe.tool is not None and not holder.bags.get(recipe.tool):
        place = "bank"                           # standing() has already checked it is held somewhere
    return place, ""


def standing(recipe: CooldownRecipe, members: Sequence[int], holder: Holder,
             names: Mapping[int, str]) -> Standing | None:
    """A character's standing for a row: the first state of SPEC §3.4F.2 that applies.

    Args:
        recipe: A catalog row.
        members: From group_members.
        holder: The character.
        names: Item key -> name.

    Returns:
        The Standing, or None when the character lacks the profession (it is not in the row's Who).
    """
    skill = holder.skills.get(recipe.profession)
    if skill is None:
        return None
    base = (holder.char_id, holder.name, skill)
    if skill < recipe.skill:
        return Standing(*base, "below")
    if not recipe.tool_use and recipe.spell not in holder.known:
        return Standing(*base, "not_learned")
    if recipe.tool is not None and not (holder.bags.get(recipe.tool) or holder.bank.get(recipe.tool)):
        return Standing(*base, "needs_item")
    when = ready_at(members, holder)
    if when is None:
        return Standing(*base, "not_read")
    place, missing = _mats_place(recipe, holder, names)
    return Standing(*base, "timed", when, place == "bank", missing)


def mats_copper(recipe: CooldownRecipe, prices: Mapping[int, int | None]) -> int | None:
    """The mats of one use at their AH price (SPEC §3.4F.4).

    Args:
        recipe: A catalog row.
        prices: Item key -> its price (`cur`), copper; None or absent = no price.

    Returns:
        The sum of count × price, copper; None when a mat has no price.
    """
    total = 0
    for key, count in recipe.reagents:
        price = prices.get(key)
        if price is None:
            return None
        total += count * price
    return total


def net_per_use(recipe: CooldownRecipe, price: int | None, mats: int | None) -> float | None:
    """What one use earns if the product sells (SPEC §3.4F.4, CD4, CD5): the deposit is refunded on a sale.

    Args:
        recipe: A catalog row.
        price: The product's price, copper; None = no price.
        mats: From mats_copper.

    Returns:
        sale_net(price, average yield) − mats, float copper; None without a price.
    """
    if price is None or mats is None:
        return None
    return sale_net(price, (recipe.yield_min + recipe.yield_max) / 2) - mats


def per_day(recipe: CooldownRecipe, net: float | None) -> float | None:
    """Profit / day: what one character earns a day using the row every time it is ready (SPEC §3.4F.4, D4, D13).

    Args:
        recipe: A catalog row.
        net: From net_per_use.

    Returns:
        net ÷ the cooldown in days, float copper; None without a net, or for a cooldown under 1 day,
        which doesn't limit the output.
    """
    if net is None or recipe.cooldown_s < DAY_S:
        return None
    return net * DAY_S / recipe.cooldown_s


def duration_text(ms: int) -> str:
    """A time span as the tab shows it (SPEC §3.4F.8): "2 d", "1 d 2 h", "6 h 10 m", "20 h", "10 m".

    Args:
        ms: The span, ms (whole minutes are kept, the rest dropped).

    Returns:
        The text; at least "1 m".
    """
    minutes = max(ms // MINUTE_MS, 1)
    days, hours, mins = minutes // 1440, minutes // 60 % 24, minutes % 60
    if days:
        return f"{days} d" + (f" {hours} h" if hours else "")
    if hours:
        return f"{hours} h" + (f" {mins} m" if mins else "")
    return f"{mins} m"


def can_make(recipe: CooldownRecipe, st: Standing, now_ms: int, names: Mapping[int, str]) -> tuple[str, str]:
    """Whether a character can use a row right now (SPEC §3.4F.5, D8, D11, CD13).

    Args:
        recipe: The row.
        st: The character's standing for it.
        now_ms: Now, UTC ms.
        names: Item key -> name, for the tool.

    Returns:
        ("yes" | "bank" | "no", the first reason for a no: "needs skill 300", "not learned", "needs Salt Shaker",
        "cooldown not read", "on cooldown 1 d 2 h", "missing 1 Essence of Water"; "" otherwise).
    """
    if st.state == "below":
        return "no", f"needs skill {recipe.skill}"
    if st.state == "not_learned":
        return "no", "not learned"
    if st.state == "needs_item":
        return "no", f"needs {names.get(recipe.tool or 0, 'its tool')}"
    if st.state == "not_read" or st.ready_at_ms is None:
        return "no", "cooldown not read"
    if st.ready_at_ms > now_ms:
        return "no", f"on cooldown {duration_text(st.ready_at_ms - now_ms)}"
    if st.missing:
        return "no", f"missing {st.missing}"
    return ("bank" if st.bank else "yes"), ""


def catalog_keys(recipes: Sequence[CooldownRecipe]) -> set[int]:
    """Every item the catalog names: products, mats and tools (the export's shortlist, SPEC §3.5, EX24).

    Args:
        recipes: The catalog.

    Returns:
        Item keys.
    """
    keys: set[int] = set()
    for r in recipes:
        keys |= {r.product} | {k for k, _ in r.reagents} | ({r.tool} if r.tool is not None else set())
    return keys
