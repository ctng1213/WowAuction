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
    percent: int | None = None   # the chance as a whole percent, for sorting; None when nothing is sold


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
    percent = round_half_up(p * 100)
    return ChanceVolume(f"{percent}%", confidence_percent, dot, volume, percent)


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


def age_text(age_ms: int | None) -> str:
    """A data age for display, e.g. "20 min", "2 h", "6 d" (the largest whole unit).

    Args:
        age_ms: Age in ms, or None when unknown.

    Returns:
        The text; "—" when unknown; "now" under a minute.
    """
    if age_ms is None:
        return "—"
    minutes = age_ms // 60_000
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h" if minutes < 24 * 60 else f"{minutes // (24 * 60)} d"


def risk_tenths(spend_copper: float, total_gold_copper: int) -> int:
    """Risk in tenths of a percent, for sorting (the same rounding as `risk`).

    Args:
        spend_copper: Gold at stake.
        total_gold_copper: All your gold.

    Returns:
        round_half_up(spend ÷ total × 1000); -1 when you have no gold recorded.
    """
    return round_half_up(spend_copper / total_gold_copper * 1000) if total_gold_copper > 0 else -1


def _pair_cells(pair: ChanceVolume) -> dict:
    """The Chance · Volume pair as row cells.

    Args:
        pair: From chance_volume.

    Returns:
        {"chance", "confidence", "dot", "volume"}.
    """
    return {"chance": pair.chance, "confidence": pair.confidence, "dot": pair.dot, "volume": pair.volume}


@dataclass(frozen=True)
class DisenchantInput:
    """Engine values behind one Disenchant tab row."""

    key: int
    name: str
    quality: int
    ilvl: int
    buy: int                              # buyout, copper
    mats: tuple[tuple[str, float], ...]   # (material name, expected units per disenchant)
    ev: float                             # expected value of the mats, float copper
    pair: ChanceVolume                    # Chance and Volume of the mats
    price_age_ms: int | None


def disenchant_row(d: DisenchantInput, total_gold: int) -> dict:
    """One Disenchant tab row (SPEC §3.5, mockup columns), all cells formatted.

    Args:
        d: The engine values.
        total_gold: All your characters' gold, in copper, for risk.

    Returns:
        Row dict with display cells and numeric "sort" keys (the page sorts by these, never by text).
    """
    risk_text, amber = risk(d.buy, total_gold)
    profit = d.ev - d.buy
    return {
        "key": d.key, "item": d.name, "quality": d.quality, "ilvl": d.ilvl, "buy": money(d.buy),
        "mats": " · ".join(f"{units:.1f} {mat}" for mat, units in d.mats), "ev": money(d.ev),
        "profit": signed_money(profit), "profit_class": "pos" if profit > 0 else "neg",
        **_pair_cells(d.pair), "risk": risk_text, "risk_amber": amber, "price_age": age_text(d.price_age_ms),
        "sort": {"ilvl": d.ilvl, "buy": d.buy, "ev": round_half_up(d.ev), "profit": round_half_up(profit),
                 "price_age": d.price_age_ms if d.price_age_ms is not None else -1,
                 "chance": d.pair.percent if d.pair.percent is not None else -1,
                 "risk": risk_tenths(d.buy, total_gold)},
    }


@dataclass(frozen=True)
class CraftInput:
    """Engine values behind one Craft tab row."""

    spell: int
    product: str
    quality: int
    profession: str             # for the Profession filter
    crafter: str                # "Brugan (Blacksmithing 112)"
    source: str                 # "Owned" / "AH" / "Mixed" (+ " + crafted")
    crafts: int
    cells: CraftMoney           # mats each / total / owned note
    mat_gold: float             # copper to buy the mats for all crafts
    value_each: float | None    # expected value of one craft's product, float copper
    profit: float               # expected profit of all crafts, float copper
    pair: ChanceVolume          # Chance and Volume of the product
    cooldown: str               # "ready", a time left, or ""
    note: str                   # chain, bought mats, why crafts stop


def craft_row(c: CraftInput, total_gold: int) -> dict:
    """One Craft tab row (SPEC §3.5, mockup columns), all cells formatted.

    Args:
        c: The engine values.
        total_gold: All your characters' gold, in copper, for risk.

    Returns:
        Row dict with display cells and numeric "sort" keys.
    """
    risk_text, amber = risk(c.mat_gold, total_gold)
    return {
        "spell": c.spell, "product": c.product, "quality": c.quality, "profession": c.profession,
        "crafter": c.crafter, "source": c.source,
        "crafts": c.crafts, "mats_each": c.cells.each, "mats_total": c.cells.total,
        "value_each": money(c.value_each), "profit": signed_money(c.profit),
        "profit_class": "pos" if c.profit > 0 else "neg", **_pair_cells(c.pair),
        "risk": risk_text, "risk_amber": amber, "cooldown": c.cooldown,
        "note": "; ".join(part for part in (c.cells.note, c.note) if part),
        "sort": {"crafts": c.crafts, "mats_total": round_half_up(c.mat_gold), "profit": round_half_up(c.profit),
                 "value_each": round_half_up(c.value_each) if c.value_each is not None else -1,
                 "chance": c.pair.percent if c.pair.percent is not None else -1,
                 "risk": risk_tenths(c.mat_gold, total_gold)},
    }


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
