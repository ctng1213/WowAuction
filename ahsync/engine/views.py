"""View models: every number a tab or the tooltip shows, made and formatted here (SPEC §8.5).

The dashboard (`web/`) and the addon frames only display these values. Money is
rounded half up, floor(x + 0.5), only here, at display time (SPEC §3.3).
The addon's twin is UI/ViewModel.lua (M5).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ahsync.engine.confidence import level

HIGH_RISK_PCT = 25.0        # config high_risk_pct (SPEC §3.4)
VOLUME_SOURCE_LABEL = {"ahf": "AHF", "books": "est.", "mail": "est.", "qty_drop": "est."}


def round_half_up(x: float) -> int:
    """Round half up, as every display does (SPEC §3.3).

    Args:
        x: A float.

    Returns:
        floor(x + 0.5).
    """
    return math.floor(x + 0.5)


def money(copper: float | None) -> str:
    """Format copper as WoW money, e.g. "1g 18s 10c"; zero parts are left out.

    Args:
        copper: Amount in copper (float allowed; rounded half up), or None.

    Returns:
        The text; "0c" for zero; "—" for None; a leading "−" for negative amounts.
    """
    if copper is None:
        return "—"
    amount = round_half_up(copper)
    sign, amount = ("−", -amount) if amount < 0 else ("", amount)
    gold, silver, cop = amount // 10000, amount // 100 % 100, amount % 100
    parts = [f"{gold}g"] * bool(gold) + [f"{silver}s"] * bool(silver) + [f"{cop}c"] * bool(cop)
    return sign + (" ".join(parts) or "0c")


def signed_money(copper: float) -> str:
    """Money with a sign for expected net, e.g. "+73s 42c" or "−20c".

    Args:
        copper: Amount in copper.

    Returns:
        "+" before positive amounts; money() otherwise.
    """
    text = money(copper)
    return "+" + text if round_half_up(copper) > 0 else text


@dataclass(frozen=True)
class ChanceVolume:
    """The standard pair shown wherever something gets sold (SPEC §3.5, §4.3)."""

    chance: str            # "92%"; "—" when nothing is sold
    confidence: int | None # exact confidence %, shown on hover
    dot: str | None        # "high" / "medium" / "low": the dot's colour
    volume: str            # "48/day AHF · 93 on AH", "31/day est." or "—"


def chance_volume(p: float | None, confidence_percent: int | None, sold_per_day: float | None,
                  volume_source: str | None, units_on_ah: int | None) -> ChanceVolume:
    """Build the Chance · Volume pair in one place (SPEC §3.5).

    Args:
        p: P' for the row's own window, or None for rows with nothing to sell.
        confidence_percent: Confidence 0-100.
        sold_per_day: Units sold per day, or None when unknown.
        volume_source: The main volume source ("ahf", "books", ...), or None.
        units_on_ah: Units listed now, or None.

    Returns:
        The pair; "—" in both cells when p is None.
    """
    if p is None:
        return ChanceVolume("—", None, None, "—")
    volume = "—"
    if sold_per_day is not None:
        volume = f"{round_half_up(sold_per_day)}/day"
        if volume_source:
            volume += " " + VOLUME_SOURCE_LABEL.get(volume_source, "est.")
        if units_on_ah is not None:
            volume += f" · {units_on_ah} on AH"
    dot = level(confidence_percent) if confidence_percent is not None else None
    return ChanceVolume(f"{round_half_up(p * 100)}%", confidence_percent, dot, volume)


def risk(spend_copper: float, total_gold_copper: int, high_risk_pct: float = HIGH_RISK_PCT) -> tuple[str, bool]:
    """Risk % = gold the action spends ÷ all your gold × 100, one decimal (SPEC §3.4).

    Args:
        spend_copper: Gold at stake: a buy's total, a craft's mat gold, a post's deposit; 0 for a vendor sale.
        total_gold_copper: Sum of all your characters' gold.
        high_risk_pct: Above this the risk shows amber.

    Returns:
        ("10.0%", amber?); ("—", False) when you have no gold recorded.
    """
    if total_gold_copper <= 0:
        return "—", False
    tenths = round_half_up(spend_copper / total_gold_copper * 1000)
    return f"{tenths // 10}.{tenths % 10}%", tenths / 10 > high_risk_pct


@dataclass(frozen=True)
class CraftMoney:
    """A craft row's money cells (SPEC §3.4 money on every action)."""

    each: str          # mat gold per craft; "—" when you own every mat
    total: str         # mat gold for all crafts
    note: str          # e.g. "uses 6 owned Copper Bar"; "" when nothing owned is used


def craft_money(crafts: int, mat_gold: float, owned_used: dict[int, float], names: dict[int, str]) -> CraftMoney:
    """Each / total / note for a craft row (SPEC §3.4, D6-D8).

    Args:
        crafts: Number of crafts.
        mat_gold: Copper to buy the missing mats for all crafts.
        owned_used: Item key -> owned units the crafts use.
        names: Item key -> display name.

    Returns:
        The cells; each = total ÷ crafts, shown "—" when no gold is needed.
    """
    note = ", ".join(f"{round_half_up(units)} owned {names.get(key, str(key))}"
                     for key, units in sorted(owned_used.items()))
    note = f"uses {note}" if note else ""
    if mat_gold <= 0 or crafts <= 0:
        return CraftMoney("—", "—", note)
    return CraftMoney(money(mat_gold / crafts), money(mat_gold), note)


def delta_percent(value: float | None, current: float | None) -> int | None:
    """Tooltip delta against the current AH price (SPEC §4.3): (value − cur) ÷ cur × 100, half up.

    Args:
        value: The line's value in copper.
        current: The current AH price in copper.

    Returns:
        The whole percent; None when either is missing.
    """
    if value is None or not current:
        return None
    return round_half_up((value - current) / current * 100)


def _delta_text(value: float | None, current: float | None) -> tuple[str, str | None]:
    """A value with its delta, e.g. ("11s", "+38%"), and the delta's colour.

    Args:
        value: Copper, or None.
        current: Current price, or None.

    Returns:
        (text, "green" / "red" / None).
    """
    delta = delta_percent(value, current)
    if value is None:
        return "—", None
    if delta is None:
        return money(value), None
    sign = "+" if delta > 0 else ("−" if delta < 0 else "")
    return f"{money(value)} ({sign}{abs(delta)}%)", ("green" if delta > 0 else "red" if delta < 0 else None)


@dataclass(frozen=True)
class TooltipLine:
    """One label/value line of the tooltip (SPEC §4.3)."""

    label: str
    value: str
    colour: str | None = None     # colour of the delta, if any


def tooltip(cur: int | None, cur_age_text: str, med30: int | None, de_value: float | None,
            can_disenchant: bool, best_window: str | None, best_price: float | None,
            chance: float | None) -> list[TooltipLine]:
    """The tooltip block, one fact per line, prices per unit (SPEC §4.3, TT1-TT5).

    Args:
        cur: Current lowest buyout in copper, or None.
        cur_age_text: How long ago it was seen, e.g. "2h ago".
        med30: 30-day median in copper, or None.
        de_value: Disenchant value per item, float copper, or None.
        can_disenchant: Whether one of your characters can disenchant it.
        best_window: Label of the top selling window, or None.
        best_price: Expected price in that window, float copper, or None.
        chance: P' for posting one reference stack now, or None.

    Returns:
        The lines in spec order; the Disenchant line is left out without an enchanter.
    """
    lines = [TooltipLine("Current AH price", f"{money(cur)} ({cur_age_text})" if cur is not None else "—")]
    hist, colour = _delta_text(med30, cur)
    lines.append(TooltipLine("Historical price", hist.replace(" (", " (30d, ", 1) if med30 and cur else hist, colour))
    if can_disenchant:
        lines.append(TooltipLine("Disenchant value", *_delta_text(de_value, cur)))
    lines.append(TooltipLine("Best time to sell", best_window or "—"))
    lines.append(TooltipLine("Price at best time", *_delta_text(best_price, cur)))
    lines.append(TooltipLine("Chance to sell", "—" if chance is None else f"{round_half_up(chance * 100)}%"))
    return lines
