"""The Characters page: one card per character, with its gold (the default budget) and professions (SPEC §3.5, D49).

Pure: every text and every bar width is made here.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ahsync.engine.views import age_text, money, round_half_up


@dataclass(frozen=True)
class CharacterSheet:
    """What the addon captured about one character."""

    char_id: str                                   # "Name-Realm"
    char_class: str | None                         # "MAGE"
    level: int | None
    faction: str | None
    market: str | None                             # "forever.pvp.horde.us"
    gold: int | None                               # copper
    budget: int | None                             # config [budgets] override, copper; None for none
    recipes: int                                   # known recipes
    updated_ms: int | None
    professions: tuple[tuple[str, int | None, int | None], ...] = ()   # (name, skill, max), by name


def _card(c: CharacterSheet, now_ms: int) -> dict:
    """One character's card.

    Args:
        c: The character.
        now_ms: Now, for "last seen".

    Returns:
        {"title", "subtitle", "lines": [[label, value]], "professions": [{"name", "skill", "pct"}]}.
    """
    name, _, realm = c.char_id.partition("-")
    ruleset = c.market.split(".")[1] if c.market and c.market.count(".") >= 3 else None
    where = " · ".join(p for p in (c.faction, f"{realm} ({ruleset})" if ruleset else realm) if p)
    sub = " · ".join(p for p in ((c.char_class or "").title(), str(c.level) if c.level else "") if p)
    profs = [{"name": p, "skill": f"{s if s is not None else '—'} / {m if m is not None else '—'}",
              "pct": round_half_up(100 * min(s, m) / m) if s is not None and m else 0} for p, s, m in c.professions]
    budget = ["Budget", money(c.budget) + " (set in config.toml)"] if c.budget is not None else \
        ["Gold (budget)", money(c.gold)]
    return {"title": name, "subtitle": sub, "lines": [
        ["Realm", where or "—"], ["Market", c.market or "none · realm not in config.toml"], budget,
        ["Known recipes", str(c.recipes)],
        ["Last seen", "—" if c.updated_ms is None else f"{age_text(now_ms - c.updated_ms)} ago"]], "professions": profs}


def characters_view(sheets: Sequence[CharacterSheet], now_ms: int) -> dict:
    """The Characters page (SPEC §3.5, D49).

    Args:
        sheets: Your characters.
        now_ms: Now.

    Returns:
        {"cards": [...] by character id, "empty": text when there are none, else ""}.
    """
    cards = [_card(c, now_ms) for c in sorted(sheets, key=lambda c: c.char_id)]
    return {"cards": cards, "empty": "" if cards else "No characters yet: log in with AHProfit enabled, then /reload."}
