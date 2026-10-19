"""View models: every number a tab or the tooltip shows, made and formatted here (SPEC §8.5).

The dashboard (`web/`) and the addon frames only display these values. Money is
rounded half up, floor(x + 0.5), only here, at display time (SPEC §3.3).
The addon's twin is UI/ViewModel.lua (M5).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ahsync.engine.confidence import level
from ahsync.engine.volume import supply_to_volume

HIGH_RISK_PCT = 25.0        # config high_risk_pct (SPEC §3.4)
VOLUME_SOURCE_LABEL = {"ahf": "AHF", "books": "est.", "mail": "est."}


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


# The market activity columns, in their required order (SPEC §3.5): header -> row cell name.
MARKET_COLUMNS = (("Chance", "chance"), ("Est. Sales/Day", "sales"), ("Current Supply", "supply"),
                  ("Supply/Volume", "days"))

# Header tooltips (SPEC §3.5): one text per column name, so a column reads the same on every page.
COLUMN_HELP = {
    "Item": "The item, in its quality colour. Click to open it on auctionhouse.fyi",
    "Product": "What the recipe makes",
    "ilvl": "Item level: sets the disenchant results",
    "Buy": "Cheapest buyout on the AH now",
    "Expected mats": "Average disenchant results for this quality and item level",
    "EV": "Expected value of the mats: average results × their sell value",
    "Profit": "EV − buy price, after fees",
    "Price age": "How long ago the buy price was seen. Older prices are less likely to still be there",
    "Recipe known by": "The character who knows it, and their skill",
    "Source": "Where the mats come from: Owned, AH, Mixed, + crafted (another character makes a reagent first)",
    "Crafts": "How many to make: stops where one more would lose money",
    "Mats each": 'Gold for mats you must buy, per craft. "—" = you own them all',
    "Mats total": "Mats each × crafts",
    "Value each": "What one product is expected to sell for",
    "Expected profit": "Average profit after fees and the chance it sells; owned mats count at their hold value",
    "Cooldown": "When the recipe can be used again",
    "Chance": "Chance it sells in its planned window. The dot is the confidence in that chance: green ≥ 75%, "
              "amber ≥ 45%, grey below. Hover a cell for the exact %",
    "Est. Sales/Day": "Units the market sells per day (last 7 days), with its source: AHF, or est. from order books "
                      'in your scans. "—" = no sales data',
    "Current Supply": "Units on the AH now, in the newest scan",
    "Supply/Volume": "Days of supply: current supply ÷ sales per day. Not a time-to-sell guarantee",
    "Risk": "Gold this action spends ÷ all your gold. Amber above 25%",
    "Action": "What to do: BUY, POST, DE (disenchant), CRAFT, VENDOR, MAIL or HOLD",
    "Qty": "Units (or crafts) this row is about",
    "Each": "Per unit: the price you pay, or post at",
    "Total": "Each × quantity",
    "Expected net": "What this action adds on average, after fees, deposits and the chance it sells. A chain counts it "
                    "once, on its first action",
    "Character": "Which of your characters does it",
    "Buy each": "Price to pay now, per unit (buy at or below it)",
    "Buy total": "What the whole buy costs",
    "Sell each": "Expected sell price per unit at the sell slot",
    "Sell slot": "When to sell: the peak hour the flip is for",
    "Why": "How far the peak's price is above the 30-day median",
    "Seasonality Strength": '0–100: how predictable the weekly price pattern is, tested on a held-out week. Not how '
                            'profitable it is. "—" = too little evidence',
    "Expected ROI": "Expected profit ÷ buy cost. Flips need at least 20%",
    "Where": "Bags, bank or mail. Auctions you have up are on On the AH",
    "When": "When it happened, in your local time",
    "Event": "What happened: posted, sold, expired, paid out, bought, crafted, disenchanted, mailed. \"—\" is a plan action "
             "with no event",
    "Price": "Each, copper as the AH or vendor charged it; for Paid out, the gold taken from the mailbox",
    "Plan action": "The plan action this event completed: done, partial, or missed and skipped ones",
    "Detail": "The deposit, the post it resolved, what it cost or realized, or why the plan action was missed",
    "Listed each": "Your buyout per unit",
    "Listed total": "Listed each × quantity",
    "Cost each": "What it cost you per unit: what you paid, or the mats for a craft; 0 for items you found or held "
                 "before tracking started",
    "Cost total": "Cost each × quantity",
    "Deposit": 'Deposit you paid to post; back on a sale, lost on expiry. "—" = its post was not captured',
    "Profit if sold": "Listed total after the 5% cut, less the cost",
    "Time left": "The time left the AH shows for the auction",
    "Position": '"cheapest" at or below the cheapest listing now; else who is cheaper, and at what price',
    "Paid each": "What you paid per unit, averaged across all your characters; 0 for items you found or held "
                 "before tracking started",
    "Paid total": "Paid each × quantity",
    "Worth": "What it is expected to bring under its plan: planned sales after fees and the chance they sell, other "
             "units at their hold value",
    "Best selling windows": "The hours of the week it sells best (price × buyers), local time. \"early pattern\" = "
                            "under 3 weeks of data",
    "Plan": "What this week's plan does with it",
}


@dataclass(frozen=True)
class MarketActivity:
    """The market activity cells shown wherever something gets sold (SPEC §3.5, §3.3.6)."""

    chance: str              # "92%"; "—" when nothing is sold
    confidence: int | None   # exact confidence %, shown on hover
    dot: str | None          # "high" / "medium" / "low": the dot's colour
    sales: str               # "48 AHF", "31 est." or "—"
    supply: str              # "93" or "—"
    days: str                # "1.9 days" or "—"
    percent: int | None = None    # sort keys: the chance as a whole percent, None when nothing is sold;
    sales_tenths: int = -1        # sales per day in tenths; -1 when unavailable
    supply_units: int = -1        # units listed; -1 when never seen
    days_tenths: int = -1         # days of supply in tenths; -1 when unavailable
    chance_note: str = ""         # "volume unknown" when the chance is the default (SPEC §3.3.2)


def market_activity(p: float | None, confidence_percent: int | None, sold_per_day: float | None,
                    volume_source: str | None, units_on_ah: int | None) -> MarketActivity:
    """Build the market activity cells in one place (SPEC §3.5, D23).

    Args:
        p: P' for the row's own window, or None for rows with nothing to sell.
        confidence_percent: Confidence 0-100.
        sold_per_day: Estimated units sold per day, or None when unavailable.
        volume_source: The main volume source ("ahf", "books", "mail"), or None.
        units_on_ah: Units listed now, or None when never seen.

    Returns:
        The cells; "—" for anything the data can't support, never 0 (SPEC §3.3.6).
    """
    sales, sales_tenths = "—", -1
    if sold_per_day is not None:
        label = VOLUME_SOURCE_LABEL.get(volume_source, "est.") if volume_source else ""
        sales = f"{round_half_up(sold_per_day)} {label}".rstrip()
        sales_tenths = round_half_up(sold_per_day * 10)
    days, days_tenths = "—", -1
    ratio = supply_to_volume(units_on_ah, sold_per_day)
    if ratio is not None:
        days_tenths = round_half_up(ratio * 10)
        days = f"{days_tenths // 10}.{days_tenths % 10} days"
    supply = str(units_on_ah) if units_on_ah is not None else "—"
    supply_units = units_on_ah if units_on_ah is not None else -1
    if p is None:
        return MarketActivity("—", None, None, sales, supply, days, None, sales_tenths, supply_units, days_tenths)
    dot = level(confidence_percent) if confidence_percent is not None else None
    percent = round_half_up(p * 100)
    note = "volume unknown" if sold_per_day is None else ""
    return MarketActivity(f"{percent}%", confidence_percent, dot, sales, supply, days, percent, sales_tenths,
                          supply_units, days_tenths, note)


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


@dataclass(frozen=True)
class CraftMats:
    """Where one craft option's mats come from, for its lines (D44)."""

    owned: dict[int, float]                              # key -> owned units used
    from_vendor: dict[int, float]                        # key -> units bought from a vendor
    bought: dict[int, float]                             # key -> units bought (vendor and AH)
    spent: dict[int, float]                              # key -> copper spent
    crafted: dict[int, str]                              # key -> who crafts it first
    mail: dict[int, tuple[tuple[str, int], ...]]         # key -> (from character, units) to gather owned mats


def craft_lines(m: CraftMats, names: dict[int, str]) -> tuple[str, ...]:
    """One line per mat, by source then key, as in the addon (A34, D44).

    Args:
        m: The option's mats.
        names: Item key -> display name.

    Returns:
        "use 10 owned Linen Cloth · mail 6 from Cara", "buy 2 Coarse Thread from a vendor · 20c",
        "buy 1 Wool Cloth on the AH · 33c", "craft Bolt of Linen Cloth first (Brugan)".
    """
    def name(key: int) -> str:
        """The mat's name.

        Args:
            key: Item key.

        Returns:
            Its name, or its key.
        """
        return names.get(key, str(key))
    lines = []
    for key, units in sorted(m.owned.items()):
        sends = ", ".join(f"{n} from {c.split('-')[0]}" for c, n in m.mail.get(key, ()))
        lines.append(f"use {round_half_up(units)} owned {name(key)}" + (f" · mail {sends}" if sends else ""))
    for key, units in sorted(m.from_vendor.items()):
        lines.append(f"buy {round_half_up(units)} {name(key)} from a vendor · {money(m.spent.get(key, 0.0))}")
    for key, units in sorted(m.bought.items()):
        if key not in m.from_vendor:
            lines.append(f"buy {round_half_up(units)} {name(key)} on the AH · {money(m.spent.get(key, 0.0))}")
    for key, crafter in sorted(m.crafted.items()):
        lines.append(f"craft {name(key)} first ({crafter.split('-')[0]})")
    return tuple(lines)


CRAFT_SKIPS = (("pay", "doesn't pay now", "don't pay now"), ("mats", "lacks mats", "lack mats"),
               ("risk", "over your risk limit", "over your risk limit"),
               ("held", "already held or listed", "already held or listed"))


def craft_footer(shown: int, skipped: dict[str, int]) -> str:
    """The Craft page's footer: what is shown and why the other recipes are not (D46).

    Args:
        shown: Rows shown.
        skipped: Reason ("pay", "mats", "risk", "held") -> recipes not shown for it.

    Returns:
        E.g. "1 recipe worth crafting · 4 not shown: 1 doesn't pay now · 1 lacks mats · …".
    """
    text = f"{shown} recipe{'' if shown == 1 else 's'} worth crafting"
    hidden = sum(skipped.values())
    if hidden:
        parts = [f"{skipped[k]} {one if skipped[k] == 1 else many}" for k, one, many in CRAFT_SKIPS if skipped.get(k)]
        text += f" · {hidden} not shown: " + " · ".join(parts)
    return text


STALE_SNAPSHOT_MS = 86_400_000      # SPEC §3.4.1 stale state: older than 24 h


def stale_note(places: Sequence[tuple[str, str, int]]) -> str:
    """Which character snapshots a row is built on are old (SPEC §3.4.1 stale state, D24).

    Args:
        places: (character id, place, age ms) the row's owned units come from.

    Returns:
        "based on Amaya's bags from 3 d ago, Brugan's bank from 2 d ago" for those over 24 h old, by
        character then place; "" when none is.
    """
    old = sorted((c, place, age) for c, place, age in places if age > STALE_SNAPSHOT_MS)
    if not old:
        return ""
    return "based on " + ", ".join(f"{c.split('-')[0]}'s {place} from {age_text(age)} ago" for c, place, age in old)


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


def _market_cells(m: MarketActivity) -> dict:
    """The market activity columns as row cells (SPEC §3.5).

    Args:
        m: From market_activity.

    Returns:
        {"chance", "chance_note", "confidence", "dot", "sales", "supply", "days"}.
    """
    return {"chance": m.chance, "chance_note": m.chance_note, "confidence": m.confidence, "dot": m.dot,
            "sales": m.sales, "supply": m.supply, "days": m.days}


def _market_sort(m: MarketActivity) -> dict:
    """Sort keys of the market activity columns; -1 sorts an unavailable value last.

    Args:
        m: From market_activity.

    Returns:
        {"chance", "sales", "supply", "days"}.
    """
    return {"chance": m.percent if m.percent is not None else -1, "sales": m.sales_tenths,
            "supply": m.supply_units, "days": m.days_tenths}


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
    market: MarketActivity                # market activity of the mats (what you sell)
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
        **_market_cells(d.market), "risk": risk_text, "risk_amber": amber, "price_age": age_text(d.price_age_ms),
        "sort": {"ilvl": d.ilvl, "buy": d.buy, "ev": round_half_up(d.ev), "profit": round_half_up(profit),
                 "price_age": d.price_age_ms if d.price_age_ms is not None else -1,
                 **_market_sort(d.market), "risk": risk_tenths(d.buy, total_gold)},
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
    market: MarketActivity      # market activity of the product
    cooldown: str               # "ready", a time left, or ""
    note: str                   # chain, bought mats, why crafts stop
    lines: tuple[str, ...] = () # one per mat, by source (craft_lines, D44)


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
        "profit_class": "pos" if c.profit > 0 else "neg", **_market_cells(c.market),
        "risk": risk_text, "risk_amber": amber, "cooldown": c.cooldown,
        "note": "; ".join(part for part in ((c.cells.note,) if not c.lines else ()) + (c.note,) if part),
        "lines": list(c.lines),
        "sort": {"crafts": c.crafts, "mats_total": round_half_up(c.mat_gold), "profit": round_half_up(c.profit),
                 "value_each": round_half_up(c.value_each) if c.value_each is not None else -1,
                 **_market_sort(c.market), "risk": risk_tenths(c.mat_gold, total_gold)},
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
