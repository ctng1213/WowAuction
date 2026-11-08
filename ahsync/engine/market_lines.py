"""The item detail's market lines: the tooltip's lines as a table (SPEC §3.5 item detail, §4.3; D105).

The same numbers and words as the addon's tooltip (`UI/TooltipView.lua`, TT34-TT41): Market Price, Market Inventory,
Sell Chance Now, Best Buy and Best Sell Time, Disenchant Value and Vendor Price, each one row with its value now, its
7, 14 and 30-day values and its word. Shares round as the market file carries them (thousandths, then a whole percent
half up), so the dashboard and the tooltip never differ by a point. Pure: facts in, rows out.
"""
from __future__ import annotations

from dataclasses import dataclass

from ahsync.engine.patterns import WEEKDAYS
from ahsync.engine.views import delta_percent, money, round_half_up

HOUR_MS = 3_600_000
MINUTE_MS = 60_000
SLOTS = 168
COVERAGE_HOURS = 720           # the last 30 days (TT40)
HELP = {
    "Market Price": "The lowest listing now, against the medians of the hourly lowest listing over 7, 14 and 30 days.",
    "Market Inventory": "Units on the AH now, against the typical units listed over 7, 14 and 30 days: a stock, "
                        "never sales.",
    "Sell Chance Now": "Now: the chance a 24 h auction posted now at the market price sells before it expires. 7, 14 "
                       "and 30 days: the share of listings at the lowest price that sold.",
    "Best Buy Time": "The best buying window's expected price, its best hour, and how often it offered that price.",
    "Best Sell Time": "The best selling window's expected price, its best hour, and the chance a 24 h post then sells.",
    "Disenchant Value": "One disenchant's materials at their prices now, against the item's price now.",
    "Vendor Price": "What a merchant pays for one.",
}


@dataclass(frozen=True)
class BestWindow:
    """An item's best buying or selling window, as the line shows it (TT38)."""

    price: int | None             # its expected price, copper
    best_slot: int                # its best hour, UTC slot 0-167
    weeks: int                    # weeks of data behind it
    pct: float | None             # a selling window's 24 h chance or a buying window's found share, 0-1


@dataclass(frozen=True)
class Mat:
    """One possible disenchant material (TT39)."""

    chance: float                 # 0-1
    qty_min: int
    qty_max: int
    name: str
    price: int | None             # its price now, copper; None = no price


@dataclass(frozen=True)
class LineFacts:
    """What the market lines need about one item (from its intelligence, catalog and the market's coverage)."""

    cur: int | None               # lowest listing, copper
    cur_age_ms: int | None
    med7: int | None
    med14: int | None
    med30: int | None
    listed: int | None            # units on the AH now
    supply7: int | None
    supply14: int | None
    supply30: int | None
    sell_chance: float | None     # 0-1, a 24 h post at cur (TT37)
    commodity: bool | None
    sell_through: tuple[float | None, float | None, float | None] = (None, None, None)
    sell_through_how: str = "---"
    buy: BestWindow | None = None
    sell: BestWindow | None = None
    mats: tuple[Mat, ...] | None = None    # None when the item can't be disenchanted
    vendor: str = "unknown"                # item_detail.vendor_text's line
    coverage30: int | None = None          # hours the market was observed in the last 720


def qualifier(now: int | None, v7: int | None, v14: int | None, v30: int | None) -> tuple[str | None, str]:
    """The qualifier word (SPEC §4.3, TT34): the addon's `TooltipView.Qualifier`, the same integer rule.

    Args:
        now: The value now.
        v7: Its 7-day value.
        v14: Its 14-day value.
        v30: Its 30-day value.

    Returns:
        ("rising" | "falling" | "high" | "low" | "steady", its class "pos" | "neg" | "amber" | ""); (None, "") without
        now and the 7-day value.
    """
    if now is None or v7 is None:
        return None, ""
    if v14 is not None and v30 is not None:
        if now * 100 >= v7 * 105 and v7 * 100 >= v14 * 105 and v14 * 100 >= v30 * 105:
            return "rising", "pos"
        if now * 100 <= v7 * 95 and v7 * 100 <= v14 * 95 and v14 * 100 <= v30 * 95:
            return "falling", "neg"
    if now * 10 >= v7 * 11:
        return "high", "amber"
    if now * 10 <= v7 * 9:
        return "low", "amber"
    return "steady", ""


def percent(share: float | None) -> str:
    """A share as the tooltip shows it: thousandths half up (the market file), then a whole percent half up.

    Args:
        share: 0-1, or None.

    Returns:
        "74%"; "—" for None.
    """
    if share is None:
        return "—"
    return f"{(round_half_up(share * 1000) + 5) // 10}%"


def coverage_text(hours: int | None) -> str:
    """The market's coverage (TT40): observed hours of the last 720, half up to a whole percent.

    Args:
        hours: Observed hours, or None.

    Returns:
        "5% coverage"; "— coverage" without observed hours.
    """
    if not hours or hours <= 0:
        return "— coverage"
    return f"{(hours * 200 + 720) // 1440}% coverage"


def age_text(age_ms: int | None) -> str:
    """A price's age as the tooltip says it (`TooltipView.AgeText`).

    Args:
        age_ms: ms, or None.

    Returns:
        "now" under a minute, "25 min", "2 h" or "3 d".
    """
    minutes = max(0, age_ms or 0) // MINUTE_MS
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h" if minutes < 1440 else f"{minutes // 1440} d"


def strength(weeks: int) -> tuple[str, str]:
    """A window's strength word (TT28) and its class.

    Args:
        weeks: Weeks of data behind the window.

    Returns:
        ("early", "amber") under 3 weeks, ("pattern", "") under 6, else ("established", "").
    """
    if weeks < 3:
        return "early", "amber"
    return ("pattern" if weeks < 6 else "established"), ""


def _row(label: str, now: list[list[str]], values: tuple = ("", "", ""), word: str | None = None,
         word_class: str = "", indent: bool = False) -> dict:
    """One table row.

    Args:
        label: The line's name.
        now: Its value now as [text, class] parts.
        values: Its 7, 14 and 30-day texts.
        word: Its word, or None.
        word_class: The word's class.
        indent: A disenchant material under its title.

    Returns:
        {"label", "help", "now": [[text, class]], "d7", "d14", "d30", "word", "word_class", "indent"}.
    """
    return {"label": label, "help": HELP.get(label, ""), "now": now, "d7": values[0], "d14": values[1],
            "d30": values[2], "word": word or "", "word_class": word_class, "indent": indent}


def _thousands(units: int | None) -> str:
    """Units with thousands separators.

    Args:
        units: A count, or None.

    Returns:
        "14,859"; "—" for None.
    """
    return "—" if units is None else f"{units:,}"


def _best(label: str, w: BestWindow, est: bool, offset_ms: int) -> dict:
    """Best Buy / Best Sell Time (TT38): price, local best hour, percentage; the strength as the word.

    Args:
        label: "Best Buy Time" | "Best Sell Time".
        w: The window.
        est: Mark the percentage est. (a selling window of an item that is no commodity).
        offset_ms: Local time minus UTC, ms.

    Returns:
        The row.
    """
    local = (w.best_slot + offset_ms // HOUR_MS) % SLOTS
    when = f"{WEEKDAYS[local // 24]} {local % 24:02d}:00"
    text = f"{money(w.price)} · {when} · {percent(w.pct)}" + (" est." if est and w.pct is not None else "")
    word, cls = strength(w.weeks)
    return _row(label, [[text, ""]], word=word, word_class=cls)


def _disenchant(mats: tuple[Mat, ...], cur: int | None) -> list[dict]:
    """Disenchant Value (TT39): the comparison first, then the value; a row per material.

    Args:
        mats: The possible materials.
        cur: The item's price now.

    Returns:
        The title row, then one row per material.
    """
    total, rows = 0.0, []
    for m in mats:
        qty = (m.qty_min + m.qty_max) / 2
        label = f"{round_half_up(m.chance * 100)}% · {qty:g} {m.name}"
        if m.price is None:
            rows.append(_row(label, [["no price", "amber"]], indent=True))
            continue
        share = m.chance * qty * m.price
        total += share
        rows.append(_row(f"{label} @ {money(m.price)}", [[money(share), ""]], indent=True))
    delta = delta_percent(total, cur)
    now = [[money(total), ""]]
    if delta is not None:
        text = "same as AH" if delta == 0 else f"{abs(delta)}% {'above' if delta > 0 else 'below'} AH"
        now = [[text, "pos" if delta > 0 else "neg" if delta < 0 else ""], [f" · {money(total)}", ""]]
    return [_row("Disenchant Value", now)] + rows


def market_lines(f: LineFacts, offset_ms: int) -> dict:
    """The item detail's market lines (SPEC §3.5 item detail, D105; the tooltip's TT34-TT41 as one table).

    Args:
        f: The item's facts.
        offset_ms: Local time minus UTC, ms (the best hours are local).

    Returns:
        {"heading", "coverage", "rows": [row]} with rows Market Price, Market Inventory, Sell Chance Now, Best Buy
        Time and Best Sell Time (each when the item has the window), Disenchant Value and its materials (when it can
        be disenchanted) and Vendor Price.
    """
    word, cls = qualifier(f.cur, f.med7, f.med14, f.med30)
    price_now = "none listed" if f.cur is None else (
        f"{money(f.cur)} now" if age_text(f.cur_age_ms) == "now" else f"{money(f.cur)} ({age_text(f.cur_age_ms)})")
    rows = [_row("Market Price", [[price_now, ""]], (money(f.med7), money(f.med14), money(f.med30)), word, cls)]
    listed = f.listed or 0
    word, cls = qualifier(listed, f.supply7, f.supply14, f.supply30)
    rows.append(_row("Market Inventory", [[f"{listed:,} listed", ""]],
                     (_thousands(f.supply7), _thousands(f.supply14), _thousands(f.supply30)), word, cls))
    at = "" if f.cur is None else f" at {money(f.cur)}"
    est = "class est." if "c" in f.sell_through_how else ("est." if f.commodity is False else None)
    rows.append(_row("Sell Chance Now", [[f"{percent(f.sell_chance)}{at}", ""]],
                     tuple(percent(s) for s in f.sell_through), est, "amber" if est else ""))
    if f.buy is not None:
        rows.append(_best("Best Buy Time", f.buy, False, offset_ms))
    if f.sell is not None:
        rows.append(_best("Best Sell Time", f.sell, f.commodity is False, offset_ms))
    if f.mats is not None:
        rows += _disenchant(f.mats, f.cur)
    rows.append(_row("Vendor Price", [[f.vendor, ""]]))
    coverage = coverage_text(f.coverage30)
    covers = coverage.replace(" coverage", "")
    return {"heading": f"Market lines (the tooltip's; the market's data covers {covers} of the last 30 days)",
            "coverage": coverage, "rows": rows}
