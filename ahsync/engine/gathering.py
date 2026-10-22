"""Gathering: the farmable catalog, eligibility, the rate priority and gold per hour (SPEC §3.4E).

Pure: the catalog's rows come in already read (recommend/gathering_page.py reads the data file),
rates come in already computed (observed rates arrive with M17's sessions), prices come from the
caller. Money is integer copper except the expected values (net per unit, net per hour), which are
float copper rounded only for display (SPEC §3.3).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ahsync.engine.fees import deposit_copper, sale_net
from ahsync.engine.sellprob import default_chance

CATEGORIES = ("Fishing", "Herbalism", "Mining", "Skinning", "Monster")    # display and filter order
# Source type -> the professions that may gather it (SPEC §3.4E.1); a monster source has none.
SOURCE_PROFESSIONS = {"node": ("Mining", "Herbalism"), "skinning": ("Skinning",), "fishing": ("Fishing",),
                      "monster": ()}
GATHERING_PROFESSIONS = ("Fishing", "Herbalism", "Mining", "Skinning")
MIN_SKILL, MAX_SKILL = 1, 300
DEPOSIT_STACK = 20                 # the deposit per unit is the 24 h deposit of one 20-unit post ÷ 20 (§3.4E.6)
DEPOSIT_HOURS = 24
SESSION_CONFIRM_UNITS = 20         # units that confirm a farming session (§3.4E.4; config session_confirm_units, M17)


@dataclass(frozen=True)
class Source:
    """One way to gather an item (SPEC §3.4E.1)."""

    type: str                      # "node" | "skinning" | "fishing" | "monster"
    profession: str | None         # None for monster loot
    skill: int | None              # required skill 1-300; None for monster loot


@dataclass(frozen=True)
class CatalogItem:
    """One farmable material of the catalog (SPEC §3.4E.1)."""

    key: int
    name: str
    categories: tuple[str, ...]    # in CATEGORIES order
    sources: tuple[Source, ...]
    url: str | None = None


@dataclass(frozen=True)
class Observed:
    """A character's observed rate for one material, with what it rests on (SPEC §3.4E.5; computed from M17)."""

    items_per_hour: int
    units: int
    minutes: int
    sessions: int


@dataclass(frozen=True)
class Rate:
    """The rate a row shows: whose it is, how many items per hour, and of what kind (SPEC §3.4E.5)."""

    char_id: str                   # whose rate it is (the source character for a fallback)
    items_per_hour: int
    kind: str                      # "manual" | "observed"
    observed: Observed | None = None   # the observed rate behind it, also under a manual override
    fallback: bool = False         # another character's rate, shown for display only


def _source(raw: Any) -> tuple[Source | None, str]:
    """One catalog source, validated.

    Args:
        raw: The source as read from the file: {type, profession, skill}.

    Returns:
        (the Source, "") or (None, why it is invalid).
    """
    if not isinstance(raw, dict) or raw.get("type") not in SOURCE_PROFESSIONS:
        return None, f"unknown source type {raw.get('type') if isinstance(raw, dict) else raw!r}"
    kind, profession, skill = raw["type"], raw.get("profession"), raw.get("skill")
    if kind == "monster":
        return Source("monster", None, None), ""
    if profession not in SOURCE_PROFESSIONS[kind]:
        return None, f"unknown profession {profession!r}" if profession not in GATHERING_PROFESSIONS \
            else f"profession {profession!r} can't gather a {kind} source"
    if not isinstance(skill, int) or isinstance(skill, bool) or not MIN_SKILL <= skill <= MAX_SKILL:
        return None, f"skill {skill} outside {MIN_SKILL}–{MAX_SKILL}"
    return Source(kind, profession, skill), ""


def category_of(source: Source) -> str:
    """The category a source backs (SPEC §3.4E.1).

    Args:
        source: A catalog source.

    Returns:
        "Monster" for monster loot, else its profession.
    """
    return "Monster" if source.type == "monster" else str(source.profession)


def _item(raw: Any) -> tuple[CatalogItem | None, str]:
    """One catalog row, validated (SPEC §3.4E.1, GA2).

    Args:
        raw: The row as read from the file.

    Returns:
        (the CatalogItem, "") or (None, why it is skipped).
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("key"), int) or not isinstance(raw.get("name"), str):
        return None, "no key or name"
    sources = []
    for entry in raw.get("sources") or ():
        source, why = _source(entry)
        if source is None:
            return None, why
        sources.append(source)
    if not sources:
        return None, "no source"
    categories = raw.get("categories") or []
    unknown = [c for c in categories if c not in CATEGORIES]
    if unknown:
        return None, f"unknown category {unknown[0]!r}"
    backed = {category_of(s) for s in sources}
    unbacked = [c for c in categories if c not in backed]
    if unbacked or not categories:
        return None, f"category {unbacked[0]!r} has no source" if unbacked else "no category"
    url = raw.get("url") if isinstance(raw.get("url"), str) else None
    return CatalogItem(raw["key"], raw["name"], tuple(c for c in CATEGORIES if c in categories), tuple(sources),
                       url), ""


def catalog(rows: Sequence[Any]) -> tuple[list[CatalogItem], list[str]]:
    """Validate the catalog's rows (SPEC §3.4E.1, GA2): a bad row is skipped, the others are kept.

    Args:
        rows: The data file's "items" list, as read.

    Returns:
        (the valid items by key, one "Name: why" per skipped row in file order). A second row with a key
        already seen is skipped as a duplicate.
    """
    kept: dict[int, CatalogItem] = {}
    skipped = []
    for raw in rows:
        item, why = _item(raw)
        if item is not None and item.key in kept:
            item, why = None, f"duplicate key {item.key}"
        if item is None:
            name = raw.get("name") if isinstance(raw, dict) and isinstance(raw.get("name"), str) else "?"
            skipped.append(f"{name}: {why}")
            continue
        kept[item.key] = item
    return [kept[k] for k in sorted(kept)], skipped


def skipped_text(skipped: Sequence[str]) -> str:
    """The Status line for skipped catalog rows (SPEC §3.4E.1, GA2).

    Args:
        skipped: From catalog(): "Name: why" per skipped row.

    Returns:
        E.g. "gathering catalog: 1 row skipped (Mithril Ore: unknown profession 'Minning')"; "" when none.
    """
    if not skipped:
        return ""
    return f"gathering catalog: {len(skipped)} row{'' if len(skipped) == 1 else 's'} skipped ({'; '.join(skipped)})"


def eligible(item: CatalogItem, skills: Mapping[str, int]) -> bool:
    """Whether a character can gather an item (SPEC §3.4E.2, GA3, GA4).

    Args:
        item: The catalog item.
        skills: The character's profession -> current skill.

    Returns:
        True for monster loot (always), or when the character knows one of its source professions at or above
        that source's required skill. Gear bonuses are ignored (V1).
    """
    return any(s.type == "monster" or skills.get(str(s.profession), 0) >= int(s.skill or 0) for s in item.sources)


def skill_source(item: CatalogItem) -> Source | None:
    """The gathering source the Skill column shows: the lowest required skill (SPEC §3.4E.2).

    Args:
        item: The catalog item.

    Returns:
        The non-monster source with the lowest skill (ties: the first listed); None for a monster-only item.
    """
    gathered = [s for s in item.sources if s.type != "monster"]
    return min(gathered, key=lambda s: int(s.skill or 0)) if gathered else None


def own_rate(char_id: str, key: int, manual: Mapping[tuple[str, int], int],
             observed: Mapping[tuple[str, int], Observed]) -> Rate | None:
    """A character's own rate for a material: the manual override, else her observed rate (SPEC §3.4E.5).

    Args:
        char_id: The character.
        key: The item key.
        manual: (character, key) -> manual items per hour.
        observed: (character, key) -> observed rate.

    Returns:
        The Rate, or None when she has neither.
    """
    seen = observed.get((char_id, key))
    if (char_id, key) in manual:
        return Rate(char_id, manual[(char_id, key)], "manual", seen)
    return Rate(char_id, seen.items_per_hour, "observed", seen) if seen is not None else None


def best_rate(key: int, chars: Sequence[str], manual: Mapping[tuple[str, int], int],
              observed: Mapping[tuple[str, int], Observed]) -> Rate | None:
    """The highest own rate among some characters (SPEC §3.4E.5: All characters, and the fallback pool).

    Args:
        key: The item key.
        chars: The characters to pick from (those eligible now).
        manual: (character, key) -> manual items per hour.
        observed: (character, key) -> observed rate.

    Returns:
        The highest Rate, ties by character id; None when none of them has one.
    """
    rates = [r for c in sorted(chars) if (r := own_rate(c, key, manual, observed)) is not None]
    return max(rates, key=lambda r: r.items_per_hour, default=None)


def effective_rate(char_id: str, key: int, manual: Mapping[tuple[str, int], int],
                   observed: Mapping[tuple[str, int], Observed], eligible_chars: Sequence[str]) -> Rate | None:
    """The rate a character's row shows (SPEC §3.4E.5, GA5): manual → own observed → fallback → none.

    Args:
        char_id: The character whose view it is.
        key: The item key.
        manual: (character, key) -> manual items per hour.
        observed: (character, key) -> observed rate.
        eligible_chars: The characters eligible for the item now.

    Returns:
        Her own Rate (even when she is no longer eligible: her history); else the best own rate among the other
        eligible characters, marked fallback (display only); else None.
    """
    own = own_rate(char_id, key, manual, observed)
    if own is not None:
        return own
    other = best_rate(key, [c for c in eligible_chars if c != char_id], manual, observed)
    return Rate(other.char_id, other.items_per_hour, other.kind, other.observed, True) if other else None


def deposit_per_unit(vendor_sell: int | None) -> float:
    """The deposit per unit: the 24 h deposit of one 20-unit post ÷ 20 (SPEC §3.4E.6, §3.4 Fees).

    Args:
        vendor_sell: The vendor sell price per unit, copper; None when unknown.

    Returns:
        Float copper (15% of the vendor price for 24 h, exact for 20 units); 0.0 when the vendor price is unknown.
    """
    if not vendor_sell:
        return 0.0
    return deposit_copper(vendor_sell, DEPOSIT_STACK, DEPOSIT_HOURS) / DEPOSIT_STACK


def chance_to_sell(market_chance: float | None, sold_per_day: float | None, price: int | None,
                   cur_others: int | None, default_sell_prob: float = 0.25,
                   default_sell_prob_cheapest: float = 0.5) -> tuple[float, bool]:
    """The chance a farmed unit posted at the AH price sells (SPEC §3.4E.6, §3.3.2 PD1).

    Args:
        market_chance: The item's posting-now chance (the Market activity Chance), or None.
        sold_per_day: Estimated sales per day (`S`), or None when unknown.
        price: The post price (the current AH price), copper.
        cur_others: The cheapest competing price, copper; None when nobody else lists it.
        default_sell_prob: Config default_sell_prob.
        default_sell_prob_cheapest: Config default_sell_prob_cheapest.

    Returns:
        (P in [0, 1], True when it is §3.3.2's default because `S` is unknown or no chance is known).
    """
    if sold_per_day is None or market_chance is None:
        return default_chance(price, cur_others, default_sell_prob, default_sell_prob_cheapest), True
    return market_chance, False


def net_per_unit(price: int, chance: float, vendor_sell: int | None) -> float:
    """Net per unit = post price − 5% AH cut − (1 − chance) × deposit (SPEC §3.4E.6, GA6).

    Args:
        price: The post price (the current AH price), copper.
        chance: The chance it sells, 0-1.
        vendor_sell: The vendor sell price, copper, for the deposit; None when unknown.

    Returns:
        Float copper (an expected value).
    """
    return sale_net(price, 1) - (1 - chance) * deposit_per_unit(vendor_sell)


def net_per_hour(items_per_hour: int, net_unit: float) -> float:
    """Net gold per hour = whole items per hour × net per unit (SPEC §3.4E.6, GA20).

    Args:
        items_per_hour: The effective rate, whole items per hour.
        net_unit: Net per unit, float copper.

    Returns:
        Float copper per hour; rounded half up only for display.
    """
    return items_per_hour * net_unit


def gross_per_hour(items_per_hour: int, price: int) -> int:
    """Gross gold per hour = items per hour × the current AH unit price (SPEC §3.4E.6).

    Args:
        items_per_hour: The effective rate.
        price: The current AH unit price, copper.

    Returns:
        Copper per hour (integer).
    """
    return items_per_hour * price
