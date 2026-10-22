"""The Gathering page: every cell, note, footer, filter field and sort key (SPEC §3.4E, §3.5 Gathering, §8.5).

Built M16 (the day-one page): the catalog's rows ranked by AH price, eligibility per character, the
rate priority over whatever rates exist (none before M17's sessions), net gold per hour with the
default chance, the filters' data, one complete view per character (`by_char`), the freshness line,
the rate cards and the compact detail. The historical columns (Median 30d, Deviation, Sell window,
Sell window price) are "—" until M18 fills the same row fields. Pure: data in, texts out.
"""
from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ahsync.engine.fees import AH_CUT, DEPOSIT_BP
from ahsync.engine.gathering import (CATEGORIES, GATHERING_PROFESSIONS, SESSION_CONFIRM_UNITS, CatalogItem,
                                     Observed, Rate, best_rate, chance_to_sell, deposit_per_unit, effective_rate,
                                     eligible, gross_per_hour, net_per_hour, net_per_unit, own_rate, skill_source)
from ahsync.engine.prices import MIN_HOURLY_CLOSES
from ahsync.engine.views import market_activity, money, round_half_up, source_label

MINUTE_MS, HOUR_MS = 60_000, 3_600_000
STALE_MS = 24 * HOUR_MS            # prices over 24 h are stale: amber, used as they are (§3.4E.6)
DASH = "—"
CLICK_HINT = " · click the row for the sessions behind this rate and to override it"
HISTORY_NOTE = ' · needs price history: "—" until weeks of data arrive'
HISTORY_COLUMNS = ("Median 30d", "Deviation", "Sell window", "Sell window price")
NO_HISTORY_LINE = ("no price history yet: the heatmap, the top selling windows and seasonality strength appear as "
                   "weeks of data arrive")
NO_HISTORY_FRESH = "no price history yet: medians, deviations and sell windows appear as weeks of data arrive"
CATALOG_NOTE = ("Items the catalog doesn't know are never shown as farmable, even if you looted them; AHSync lists what "
                "you gathered that it couldn't place under Status.")
FORMULA_NOTE = ("Net gold/hr = items/hr × net per unit, where net per unit = post price − 5% AH cut − (1 − chance to "
                "sell) × deposit (24 h, refunded on a sale). Posts are priced at the current AH price. Gross gold/hr "
                "(items/hr × price) is on the detail below.")
NO_RATES = (f"Farm for {SESSION_CONFIRM_UNITS}+ units and the addon records your rate. Every row keeps its price and "
            f"sell window meanwhile.")
NO_MATCH = ('No farmable material matches "{q}". The catalog covers what it knows how to gather; clear the search or '
            'the filters.')
NO_MATCH_FILTERS = ("No farmable material matches the filters. The catalog covers what it knows how to gather; clear "
                    "the search or the filters.")
MIN_PRICE_OPTIONS = [["", "Any"], ["100", "1s"], ["1000", "10s"], ["5000", "50s"], ["10000", "1g"],
                     ["custom", "Custom…"]]
SKILL_OPTIONS = [["", "Any", None, None], ["1-75", "1–75", 1, 75], ["75-150", "75–150", 75, 150],
                 ["150-225", "150–225", 150, 225], ["225-300", "225–300", 225, 300]]

# Header tooltips (the mockup's GA_HELP and Gathering's columns). Character and Sell window mean something else
# here than on the plan pages, so the page carries its own texts; views.COLUMN_HELP keeps the plan's.
GATHERING_HELP = {
    "Item": "The item, in its quality colour. Click to open it on auctionhouse.fyi",
    "Category": "Where it comes from: Fishing, Herbalism, Mining, Skinning or Monster (corpses). An item can be in two",
    "Skill": 'Skill the gathering source requires. "—" for monster drops. Amber: none of your characters has it',
    "AH Price": 'Cheapest buyout per unit in the newest scan; hover for its age. "—" = no listing',
    "AH Qty": "Units listed in the newest scan",
    "Median 30d": "Median unit price over the last 30 days",
    "Deviation": "AH price vs the 30-day median, whole percent",
    "Items/hr": "Your farming rate: manual override, else your own observed rate (units ÷ session time). Grey "
                "\"Fallback\" = another eligible character's rate, display only. \"—\" = no rate",
    "Character": "Whose observed or manual rate this is. Under All characters: the highest among the characters who "
                 "can gather it. Grey with \"Fallback\" = another character's rate, display only",
    "Net gold/hr": "Items/hr × net per unit; net per unit = price − 5% cut − (1 − chance) × deposit. An estimate: a "
                   "listed price is not a sale",
    "Sell window": "The top selling window's start (day and hour, local time); see Sell window price. \"no timing "
                   "edge\" = seasonality strength under 40; \"—\" = too little data",
    "Sell window price": "Forecast unit price in the top selling window: 30-day median × the window's average price "
                         "ratio. The line under it compares it with the AH price now. \"—\" = no window (no timing "
                         "edge or too little data)",
    "Chance": "Chance it sells in its planned window. The dot is the confidence in that chance: green ≥ 75%, amber "
              "≥ 45%, grey below. Hover a cell for the exact %",
    "Est. Sales/Day": "Units the market sells per day (last 7 days), with its source: AHF, or est. from order books in "
                      'your scans. "—" = no sales data',
    "Current Supply": "Units on the AH now, in the newest scan",
    "Supply/Volume": "Days of supply: current supply ÷ sales per day. Not a time-to-sell guarantee",
}


@dataclass(frozen=True)
class PriceFacts:
    """One catalog item's market facts: the newest AH price and the intelligence behind the chance."""

    price: int | None                  # newest AH unit price, copper; None = no listing
    seen_ms: int | None                # when that price (or no listing) was seen, UTC ms; None = never seen
    source: str | None                 # "local" (your scan), "ahf" or a provider's name
    supply: int | None                 # units listed now (current listed supply); None when unknown
    vendor_sell: int | None            # vendor sell price per unit, copper, for the deposit
    quality: int = 1
    market_chance: float | None = None # the posting-now chance (the Market activity Chance)
    sold_per_day: float | None = None  # `S`; None = no sales data
    volume_source: str | None = None
    volume_kind: str | None = None
    confidence: int | None = None
    cur_others: int | None = None      # the cheapest competing price, copper
    med30: int | None = None           # price history (M18 fills the columns from these)
    closes_30d: int | None = None


@dataclass(frozen=True)
class GatheringWorld:
    """Everything the Gathering page needs (SPEC §3.4E)."""

    items: Sequence[CatalogItem]
    facts: Mapping[int, PriceFacts]
    skills: Mapping[str, Mapping[str, int]]            # character id -> profession -> current skill
    characters: Sequence[str]                          # the market's character ids
    now_ms: int
    offset_ms: int = 0
    manual: Mapping[tuple[str, int], int] = field(default_factory=dict)
    observed: Mapping[tuple[str, int], Observed] = field(default_factory=dict)
    sessions: int = 0                                  # confirmed farming sessions in the 90 days kept (M17)
    session_minutes: int = 0
    professions_ms: int | None = None                  # when the professions were last captured
    default_sell_prob: float = 0.25
    default_sell_prob_cheapest: float = 0.5


@dataclass(frozen=True)
class _Base:
    """One catalog item's character-independent cells and numbers."""

    item: CatalogItem
    facts: PriceFacts
    eligible_chars: tuple[str, ...]
    chance: float
    default: bool
    net_unit: float | None


def name_of(char_id: str) -> str:
    """A character's display name.

    Args:
        char_id: "Name-Realm".

    Returns:
        "Name".
    """
    return char_id.split("-")[0]


def duration(minutes: int) -> str:
    """A duration for display, e.g. "48 min", "1 h 50 min", "3 h".

    Args:
        minutes: Whole minutes.

    Returns:
        The text.
    """
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h" + (f" {minutes % 60} min" if minutes % 60 else "")


def age(age_ms: int) -> str:
    """A price age for display, e.g. "12 min", "26 h", "4 d".

    Args:
        age_ms: Age in ms (≥ 0).

    Returns:
        Minutes under an hour, hours under 72 h, else days.
    """
    minutes = age_ms // MINUTE_MS
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h" if minutes < 72 * 60 else f"{minutes // (24 * 60)} d"


def clock(ms: int, offset_ms: int) -> str:
    """A local day and time, e.g. "Mon 10:38".

    Args:
        ms: UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        The text.
    """
    t = time.gmtime((ms + offset_ms) // 1000)
    return f"{time.strftime('%a', t)} {time.strftime('%H:%M', t)}"


def copper2(x: float) -> str:
    """Copper with two decimals, half up, e.g. "208.89c" (the detail's formulas).

    Args:
        x: Float copper (≥ 0).

    Returns:
        The text.
    """
    hundredths = round_half_up(x * 100)
    return f"{hundredths // 100}.{hundredths % 100:02d}c"


def _base(item: CatalogItem, w: GatheringWorld) -> _Base:
    """An item's character-independent numbers: who can gather it, its chance and its net per unit.

    Args:
        item: The catalog item.
        w: The world.

    Returns:
        The _Base.
    """
    f = w.facts.get(item.key) or PriceFacts(None, None, None, None, None)
    chars = tuple(c for c in sorted(w.characters) if eligible(item, w.skills.get(c, {})))
    p, default = chance_to_sell(f.market_chance, f.sold_per_day, f.price, f.cur_others, w.default_sell_prob,
                                w.default_sell_prob_cheapest)
    net_unit = net_per_unit(f.price, p, f.vendor_sell) if f.price is not None else None
    return _Base(item, f, chars, p, default, net_unit)


def _price_title(f: PriceFacts, w: GatheringWorld) -> str:
    """The AH Price cell's hover: its age and source (D87).

    Args:
        f: The item's facts.
        w: The world.

    Returns:
        E.g. "seen 26 h ago (AHF scan Sun 08:38) · stale: over 24 h", "seen 12 min ago (your scan)", or
        "No listing in the newest scan".
    """
    if f.price is None or f.seen_ms is None:
        return "No listing in the newest scan"
    old = w.now_ms - f.seen_ms
    where = "your scan" if f.source == "local" else f"{source_label(str(f.source))} scan {clock(f.seen_ms, w.offset_ms)}"
    return f"seen {age(old)} ago ({where})" + (" · stale: over 24 h" if old > STALE_MS else "")


def _skill_cells(b: _Base) -> dict:
    """The Skill column: the lowest gathering skill, amber when no character has it (SPEC §3.4E.2, GA1, GA3).

    Args:
        b: The item's base.

    Returns:
        {"skill_text", "skill_amber", "skill_sub", "skill_title", "skill"} (skill: the number, None for "—").
    """
    src = skill_source(b.item)
    if src is None:
        return {"skill_text": DASH, "skill_amber": False, "skill_sub": "", "skill_title": "", "skill": None}
    nobody = not b.eligible_chars
    return {"skill_text": str(src.skill), "skill_amber": nobody,
            "skill_sub": "or monster drop" if "Monster" in b.item.categories and not nobody else "",
            "skill_title": f"no character has {src.profession} {src.skill}" if nobody else f"{src.profession} {src.skill}",
            "skill": src.skill}


def _rate_title(r: Rate) -> str:
    """The Items/hr cell's hover for a character's own rate.

    Args:
        r: A manual or observed rate (not a fallback).

    Returns:
        E.g. "70 units ÷ 1 h 50 min over 3 sessions = 38.2/hr, rounded", or "Manual override 12/hr (observed …)".
    """
    o = r.observed
    if r.kind == "manual":
        seen = f" (observed {o.items_per_hour}/hr: {o.units} units ÷ {duration(o.minutes)})" if o else ""
        return f"Manual override {r.items_per_hour}/hr{seen}"
    assert o is not None
    tenths = round_half_up(o.units * 600 / o.minutes) if o.minutes else 0
    return (f"{o.units} units ÷ {duration(o.minutes)} over {o.sessions} session{'' if o.sessions == 1 else 's'} = "
            f"{tenths // 10}.{tenths % 10}/hr, rounded")


def _rate_cells(r: Rate | None, view_char: str) -> dict:
    """The Items/hr and Character cells (SPEC §3.4E.5).

    Args:
        r: The row's rate, or None.
        view_char: The view's character id ("" for All characters).

    Returns:
        {"rate", "rate_sub", "rate_title", "fallback", "character"}.
    """
    if r is None:
        return {"rate": DASH, "rate_sub": "", "rate_title": "No rate yet" + CLICK_HINT, "fallback": False,
                "character": DASH}
    if r.fallback:
        who = name_of(view_char)
        title = (f"{name_of(r.char_id)}'s {r.kind} rate; {who} has none yet. Replaced by {who}'s own rate once {who} "
                 f"has one, even if lower.")
        return {"rate": str(r.items_per_hour), "rate_sub": "", "rate_title": title + CLICK_HINT, "fallback": True,
                "character": name_of(r.char_id)}
    return {"rate": str(r.items_per_hour), "rate_sub": "manual" if r.kind == "manual" else "",
            "rate_title": _rate_title(r) + CLICK_HINT, "fallback": False, "character": name_of(r.char_id)}


def _note(b: _Base, r: Rate | None, view_char: str) -> str:
    """The Item cell's note line (SPEC §3.5 Gathering columns).

    Args:
        b: The item's base.
        r: The row's rate, or None.
        view_char: The view's character id ("" for All characters).

    Returns:
        The bits joined by " · ", e.g. "also drops from monsters · Brugan: 3 sessions, 1 h 50 min"; "" when none.
    """
    bits = []
    if "Monster" in b.item.categories and len(b.item.categories) > 1:
        bits.append("also drops from monsters")
    if b.facts.price is None:
        bits.append("no listing in the newest scan")
    if b.default and r is not None:
        bits.append("no sales data yet: default chance")
    if r is not None and r.fallback:
        bits.append(f"{name_of(view_char)} has no rate yet")
    elif r is not None and r.kind == "manual":
        bits.append(f"{name_of(r.char_id)}: manual rate")
    elif r is not None and r.observed is not None:
        o = r.observed
        bits.append(f"{name_of(r.char_id)}: {o.sessions} session{'' if o.sessions == 1 else 's'}, {duration(o.minutes)}")
    return " · ".join(bits)


def _net_cells(b: _Base, r: Rate | None, w: GatheringWorld) -> dict:
    """The Net gold/hr cell with its hover and the default-chance dot (SPEC §3.4E.6, GA6-GA8).

    Args:
        b: The item's base.
        r: The row's rate, or None.
        w: The world.

    Returns:
        {"net", "net_title", "net_dot", "net_copper"}: "—" without a rate or a price (net_copper None).
    """
    if r is None or b.net_unit is None or b.facts.price is None:
        return {"net": DASH, "net_title": "", "net_dot": False, "net_copper": None}
    net = net_per_hour(r.items_per_hour, b.net_unit)
    gross = gross_per_hour(r.items_per_hour, b.facts.price)
    percent = round_half_up(b.chance * 100)
    if b.default:
        cheapest = ", as you'd be the cheapest" if b.chance == w.default_sell_prob_cheapest else ""
        chance = f" · chance is the default: no sales data yet ({percent}%{cheapest})"
    else:
        chance = f" · chance {percent}%"
    title = (f"{r.items_per_hour}/hr × {copper2(b.net_unit)} net per unit (gross {r.items_per_hour} × "
             f"{b.facts.price}c = {money(gross)}){chance}")
    return {"net": money(net), "net_title": title, "net_dot": b.default, "net_copper": round_half_up(net)}


def _qty(f: PriceFacts) -> int | None:
    """AH Qty: units listed in the newest scan.

    Args:
        f: The item's facts.

    Returns:
        0 with no listing; the listed supply; None when listed but the supply is unknown.
    """
    return 0 if f.price is None else f.supply


def _row(b: _Base, r: Rate | None, view_char: str, w: GatheringWorld) -> dict:
    """One table row for one view (SPEC §3.5 Gathering): formatted cells, note, filter fields and sort keys.

    Args:
        b: The item's base.
        r: The row's rate for this view, or None.
        view_char: The view's character id ("" for All characters).
        w: The world.

    Returns:
        The row. The historical cells are "—" until M18 fills them (median, deviation, window, window_price).
    """
    f, qty = b.facts, _qty(b.facts)
    net = _net_cells(b, r, w)
    skill = _skill_cells(b)
    return {
        "key": b.item.key, "item": b.item.name, "quality": f.quality, "url": b.item.url or "",
        "note": _note(b, r, view_char), "category": " · ".join(b.item.categories),
        **{k: v for k, v in skill.items() if k != "skill"},
        "ah_price": money(f.price), "price_title": _price_title(f, w), "qty": DASH if qty is None else str(qty),
        "median": DASH, "deviation": DASH, "deviation_class": "", "window": DASH, "window_sub": "",
        "window_title": "", "window_muted": False, "window_price": DASH, "window_delta": "", "window_delta_class": "",
        **_rate_cells(r, view_char), **{k: v for k, v in net.items() if k != "net_copper"},
        "eligible": view_char == "" or view_char in b.eligible_chars,
        "cats": list(b.item.categories), "price": f.price, "name": b.item.name, "skill": skill["skill"],
        "sort": {"price": f.price, "net": net["net_copper"], "rate": r.items_per_hour if r else None,
                 "deviation": None, "qty": qty, "skill": skill["skill"], "window_price": None},
    }


def _order(bases: Sequence[_Base]) -> list[_Base]:
    """The default order: AH price, highest first; no listing last; ties by name, then key (GA1).

    Args:
        bases: The items.

    Returns:
        The items sorted.
    """
    return sorted(bases, key=lambda b: (b.facts.price is None, -(b.facts.price or 0), b.item.name, b.item.key))


def _view_rate(b: _Base, view_char: str, w: GatheringWorld) -> Rate | None:
    """The rate a row shows in one view (SPEC §3.4E.5).

    Args:
        b: The item's base.
        view_char: The view's character id ("" for All characters).
        w: The world.

    Returns:
        All characters: the highest own rate among the eligible characters; a character: her effective rate.
    """
    if view_char == "":
        return best_rate(b.item.key, b.eligible_chars, w.manual, w.observed)
    return effective_rate(view_char, b.item.key, w.manual, w.observed, b.eligible_chars)


def _foot_all(bases: Sequence[_Base], view_char: str) -> list[list[str]]:
    """The footer with every row shown: All characters, or a character with the toggle off (D81).

    Args:
        bases: The items, in the default order.
        view_char: The view's character id ("" for All characters).

    Returns:
        [text, class] segments, joined by " · " on the page.
    """
    nobody = sum(1 for b in bases if not b.eligible_chars)
    segments = [[f'{len(bases)} materials · sorted by AH Price, highest first; rows with "—" sort last', ""]]
    if nobody:
        segments.append([f"{nobody} no character can gather (amber Skill)", "amber"])
    whose = ("the highest among the characters who can gather it" if view_char == ""
             else f"{name_of(view_char)}'s own, else a Fallback from another character who can gather it")
    return segments + [[f"Character = whose rate: {whose}", "muted"]]


def _foot_toggle(bases: Sequence[_Base], view_char: str) -> list[list[str]]:
    """The footer with "Only what <character> can gather" on (SPEC §3.5 Gathering footers, GA11).

    Args:
        bases: The items, in the default order.
        view_char: The view's character id.

    Returns:
        [text, class] segments, e.g. ["7 of 16 shown", ""], ["9 hidden: 5 only Amaya can gather (…), 4 no character
        can (…)", "muted"]; the hidden rows grouped by who else can gather them (by names), nobody last.
    """
    hidden = [b for b in bases if view_char not in b.eligible_chars]
    groups: dict[tuple[str, ...], list[str]] = {}
    for b in hidden:
        groups.setdefault(tuple(name_of(c) for c in b.eligible_chars), []).append(b.item.name)
    parts = [f"{len(groups[names])} only {' and '.join(names)} can gather ({', '.join(groups[names])})"
             for names in sorted(k for k in groups if k)]
    if () in groups:
        parts.append(f"{len(groups[()])} no character can ({', '.join(groups[()])})")
    segments = [[f"{len(bases) - len(hidden)} of {len(bases)} shown", ""]]
    return segments + ([[f"{len(hidden)} hidden: {', '.join(parts)}", "muted"]] if hidden else [])


def _sessions_text(o: Observed) -> str:
    """How many sessions and how long, e.g. "3 sessions, 1 h 50 min".

    Args:
        o: An observed rate.

    Returns:
        The text.
    """
    return f"{o.sessions} session{'' if o.sessions == 1 else 's'}, {duration(o.minutes)}"


def _ineligible_why(item: CatalogItem, skills: Mapping[str, int]) -> str:
    """Why a character can't gather an item, for the rate card.

    Args:
        item: A catalog item with a gathering source (a monster drop is always eligible).
        skills: The character's profession -> skill.

    Returns:
        "no Herbalism", or "Mining 68 of 75" when she has the profession below the requirement.
    """
    src = skill_source(item)
    assert src is not None
    have = skills.get(str(src.profession))
    return f"no {src.profession}" if have is None else f"{src.profession} {have} of {src.skill}"


def _card_block(b: _Base, char_id: str, w: GatheringWorld) -> dict:
    """One character's block of the rate card (SPEC §3.4E.7 item 2).

    Args:
        b: The item's base.
        char_id: The character.
        w: The world.

    Returns:
        {"kind": "manual" | "observed" | "none" | "ineligible", "head", "sub", "foot", "lines"} (lines: the last
        sessions, filled from M17).
    """
    who, item = name_of(char_id), b.item.name
    r = own_rate(char_id, b.item.key, w.manual, w.observed)
    block = {"char": char_id, "kind": "", "head": "", "sub": "", "foot": "", "lines": []}
    if r is not None and r.kind == "manual":
        o = r.observed
        sub = f"(observed {o.items_per_hour}/hr from {o.units} {item} over {_sessions_text(o)})" if o else "(no sessions)"
        return {**block, "kind": "manual", "head": f"{who} · {r.items_per_hour}/hr manual", "sub": sub,
                "foot": f"the override replaces the observed rate in the table for {who} until cleared"}
    if r is not None and r.observed is not None:
        return {**block, "kind": "observed", "head": f"{who} · {r.items_per_hour}/hr observed",
                "sub": f"· {r.observed.units} {item} over {_sessions_text(r.observed)}"}
    if char_id in b.eligible_chars:
        return {**block, "kind": "none", "head": f"{who} — no sessions with {item} yet"}
    why = _ineligible_why(b.item, w.skills.get(char_id, {}))
    return {**block, "kind": "ineligible", "head": f"{who} — no sessions with {item} yet · not eligible ({why})"}


def _rate_card(b: _Base, view_char: str, r: Rate | None, w: GatheringWorld) -> dict:
    """An item's rate card for one view, with its gross and net lines (SPEC §3.4E.7 items 2 and 4).

    Args:
        b: The item's base.
        view_char: The view's character id ("" for All characters: every character's block).
        r: The row's rate in this view, or None.
        w: The world.

    Returns:
        {"blocks": characters with a rate first (highest first), then by name; "lines": [[label, text]] gross and
        net gold/hr, [] without a rate or a price}.
    """
    chars = [view_char] if view_char else list(w.characters)
    rated = {c: own_rate(c, b.item.key, w.manual, w.observed) for c in chars}
    order = sorted(chars, key=lambda c: (-(rated[c].items_per_hour if rated[c] else -1), name_of(c), c))
    lines = []
    if r is not None and b.net_unit is not None and b.facts.price is not None:
        rate, price = r.items_per_hour, b.facts.price
        lines = [["Gross gold/hr", f"{rate} × {money(price)} = {money(gross_per_hour(rate, price))}"],
                 ["Net gold/hr", f"{rate} × {copper2(b.net_unit)} = {money(net_per_hour(rate, b.net_unit))}"]]
    return {"blocks": [_card_block(b, c, w) for c in order], "lines": lines}


def _alert(bases: Sequence[_Base], view_char: str, w: GatheringWorld) -> dict | None:
    """The info alert for a character with no gathering profession (D88).

    Args:
        bases: The items, in the default order.
        view_char: The view's character id.
        w: The world.

    Returns:
        {"title", "text"} when she knows none of Fishing, Herbalism, Mining and Skinning; else None.
    """
    skills = w.skills.get(view_char, {})
    if any(p in skills for p in GATHERING_PROFESSIONS):
        return None
    who = name_of(view_char)
    monster = [b.item.name for b in bases if "Monster" in b.item.categories]
    return {"title": f"{who} can gather nothing yet: Monster drops only.",
            "text": f'Turn off "Only what {who} can gather" to see every material. {len(monster)} Monster rows '
                    f"remain: {', '.join(monster)}."}


def _view(bases: Sequence[_Base], view_char: str, w: GatheringWorld) -> dict:
    """One complete view: its rows, both footers, the toggle, the alert and the rate cards (SPEC §3.5 by_char).

    Args:
        bases: The items, in the default order.
        view_char: The character id, or "" for All characters.
        w: The world.

    Returns:
        {"rows", "foot", "foot_all", "toggle": {"label", "disabled", "title"}, "alert", "rate_cards"}.
    """
    rates = [_view_rate(b, view_char, w) for b in bases]
    if view_char:
        who = name_of(view_char)
        toggle = {"label": f"Only what {who} can gather", "disabled": False,
                  "title": f"Hides what {who} can't gather. Monster drops always stay"}
    else:
        toggle = {"label": "Only what a character can gather", "disabled": True, "title": "Pick a character first"}
    cards = [_rate_card(b, view_char, r, w) for b, r in zip(bases, rates)]
    return {"rows": [{**_row(b, r, view_char, w), "card_lines": c["lines"]} for b, r, c in zip(bases, rates, cards)],
            "foot": _foot_toggle(bases, view_char) if view_char else _foot_all(bases, ""),
            "foot_all": _foot_all(bases, view_char), "toggle": toggle,
            "alert": _alert(bases, view_char, w) if view_char else None,
            "rate_cards": {str(b.item.key): {"blocks": c["blocks"]} for b, c in zip(bases, cards)}}


def _shared(views: Mapping[str, dict]) -> tuple[list[dict], dict, dict]:
    """Each row once, with each character's view as the keys it shows and the cells that differ (SPEC §3.5).

    Args:
        views: Character id ("" for All characters) -> its complete view (_view).

    Returns:
        (the All-characters rows, in the default order; item key -> its rate card's blocks, every character's,
        each with its "char"; character id -> {"keys": the view's item keys in order, "cells": item key -> only
        the row fields that differ from the shared row, "foot", "foot_all", "toggle", "alert"}).
    """
    shared = views[""]["rows"]
    by_key = {r["key"]: r for r in shared}
    by_char = {}
    for char, view in views.items():
        cells = {}
        for r in view["rows"]:
            diff = {f: v for f, v in r.items() if by_key[r["key"]][f] != v}
            if diff:
                cells[str(r["key"])] = diff
        by_char[char] = {"keys": [r["key"] for r in view["rows"]], "cells": cells,
                         **{k: view[k] for k in ("foot", "foot_all", "toggle", "alert")}}
    return shared, views[""]["rate_cards"], by_char


def _price_lines(b: _Base, w: GatheringWorld) -> list[list]:
    """The detail's price card in its day-one form (SPEC §3.4E.7 item 1, §3.4E.8).

    Args:
        b: The item's base.
        w: The world.

    Returns:
        [label, value, note, note class, dot] lines: current AH price with its age, listed quantity, the 30-day
        median and deviation ("—": needs price history), the chance to sell.
    """
    f = b.facts
    if f.price is None or f.seen_ms is None:
        price = ["Current AH price", DASH, "no listing in the newest scan", "", None]
    else:
        old = w.now_ms - f.seen_ms
        cls = "amber" if old > STALE_MS else "pos" if f.source == "local" else ""
        mine = " (your scan)" if f.source == "local" else ""
        price = ["Current AH price", money(f.price), f"· {age(old)} old{mine}", cls, None]
    qty = _qty(f)
    dot = "low" if b.default else market_activity(b.chance, f.confidence, None, None, None).dot
    return [price, ["Listed quantity", DASH if qty is None else f"{qty} units", "", "", None],
            ["30-day median · deviation", DASH, "needs price history", "", None],
            ["Chance to sell", f"{round_half_up(b.chance * 100)}%", "default: no sales data yet" if b.default else "",
             "", dot]]


def _detail(b: _Base, w: GatheringWorld, day_one: bool) -> dict:
    """An item's character-independent detail, the compact form of M16 (SPEC §3.4E.7, §3.4E.8).

    Args:
        b: The item's base.
        w: The world.
        day_one: True when no catalog item has price history yet.

    Returns:
        {"title", "subtitle", "price", "market" (the market activity cells), "net_unit" (the formula, or None
        without a price), "deposit" (its legend), "history" (the line in place of the history)}.
    """
    f, src = b.facts, skill_source(b.item)
    where = (f"{src.profession} {src.skill}{' or monster drop' if 'Monster' in b.item.categories else ''}"
             if src else "monster drop")
    m = market_activity(b.chance, f.confidence, f.sold_per_day, f.volume_source, _qty(f), f.volume_kind)
    percent = round_half_up(b.chance * 100)
    net_unit = None
    if f.price is not None and b.net_unit is not None:
        net_unit = (f"{money(f.price)} − 5% cut {copper2(f.price * AH_CUT)} − (1 − {percent}%) × "
                    f"{copper2(deposit_per_unit(f.vendor_sell))} deposit = {copper2(b.net_unit)}")
    return {"title": f"{b.item.name}: day one" if day_one else f"{b.item.name}: price, timing and your rate",
            "subtitle": "selected row · compact" if day_one else f"selected row · {where}",
            "price": _price_lines(b, w),
            "market": {"chance": m.chance, "confidence": m.confidence, "dot": "low" if b.default else m.dot,
                       "sales": m.sales, "supply": m.supply, "days": m.days, "chance_note": m.chance_note},
            "net_unit": net_unit,
            "deposit": (f"Deposit: {DEPOSIT_BP[24] // 100}% of the {money(f.vendor_sell or 0)} vendor price per unit "
                        f"for 24 h, refunded on a sale, so only the {100 - percent}% chance it expires costs it. "
                        f"Estimates: a listed price is not a sale."),
            "history": NO_HISTORY_LINE}


def _newest(bases: Sequence[_Base]) -> PriceFacts | None:
    """The newest price seen among the catalog's items.

    Args:
        bases: The items.

    Returns:
        The facts with the latest seen_ms (ties: your scan first, then the lower key); None when none was seen.
    """
    seen = [b for b in bases if b.facts.seen_ms is not None]
    if not seen:
        return None
    return min(seen, key=lambda b: (-int(b.facts.seen_ms or 0), b.facts.source != "local", b.item.key)).facts


def _fresh(bases: Sequence[_Base], w: GatheringWorld, day_one: bool) -> list[list[str]]:
    """The freshness line (SPEC §3.5 Gathering, GA12, D84, D87).

    Args:
        bases: The items.
        w: The world.
        day_one: True when no catalog item has price history yet.

    Returns:
        [text, class] segments: the price age (amber over 24 h, "(your scan)" for your own), then the professions'
        sync time and the sessions; on day one the no-history sentence in their place.
    """
    newest = _newest(bases)
    if newest is None or newest.seen_ms is None:
        first = ["no AH prices yet", "amber"]
    else:
        old = w.now_ms - newest.seen_ms
        mine = " (your scan)" if newest.source == "local" else ""
        first = [f"AH prices {age(old)} old{mine}", "amber" if old > STALE_MS else "pos" if mine else ""]
    if day_one:
        return [first, [NO_HISTORY_FRESH, ""]]
    synced = (f"professions synced {clock(w.professions_ms, w.offset_ms)}" if w.professions_ms is not None
              else "professions not synced yet")
    sessions = (f"{w.sessions} farming session{'' if w.sessions == 1 else 's'}, {duration(w.session_minutes)} observed"
                if w.sessions else "0 farming sessions")
    return [first, [synced, ""], [sessions, ""]]


def _note_text(bases: Sequence[_Base], w: GatheringWorld) -> str:
    """The note under the table, always present (SPEC §3.5 Gathering footers, D87).

    Args:
        bases: The items.
        w: The world.

    Returns:
        The formula note, the stale-price sentence when the newest price is over 24 h old, and the catalog note.
    """
    newest = _newest(bases)
    stale = ""
    if newest is not None and newest.seen_ms is not None and w.now_ms - newest.seen_ms > STALE_MS:
        stale = (f" Prices are {age(w.now_ms - newest.seen_ms)} old (amber over 24 h): every gold/hr uses them as "
                 f"they are.")
    return f"{FORMULA_NOTE}{stale} {CATALOG_NOTE}"


def _any_rate(b: _Base, w: GatheringWorld) -> bool:
    """Whether any character has an own rate for an item, eligible or not.

    Args:
        b: The item's base.
        w: The world.

    Returns:
        True when some character has a manual or observed rate for it.
    """
    return any(own_rate(c, b.item.key, w.manual, w.observed) is not None for c in w.characters)


def gathering_view(w: GatheringWorld) -> dict:
    """The Gathering payload, without the gold pill (SPEC §3.5 `gathering`, §3.4E; GA1-GA12, D80-D88).

    Args:
        w: The catalog, prices, professions, rates and clock.

    Returns:
        {"fresh", "header", "characters", "filters", "rows" (each row once, as All characters shows it),
        "rate_cards" (item key -> {"blocks"}: every character's block), "by_char" (per character id plus "" for all
        characters: the keys it shows, the cells that differ from the shared row, its footers, toggle and alert;
        _shared), "details" (item key -> its detail), "selected" (the row selected by default: the highest net
        gold/hr under All characters, else the first row), "note", "default_note", "no_rates" (the last row's text
        while no character has any rate, else None), "no_match", "no_match_filters", "help"}.
    """
    bases = _order([_base(item, w) for item in w.items])
    day_one = not any(b.facts.med30 is not None and (b.facts.closes_30d or 0) >= MIN_HOURLY_CLOSES for b in bases)
    rows, cards, by_char = _shared({c: _view(bases, c, w) for c in ["", *sorted(w.characters)]})
    rated = [r for r in rows if r["sort"]["rate"] is not None]
    best = max((r for r in rows if r["sort"]["net"] is not None), key=lambda r: r["sort"]["net"], default=None)
    priced = [b for b in bases if b.facts.price is not None]
    default_note = ""
    if priced and all(b.default for b in priced):
        default_note = (f"Every Net gold/hr here uses the default chance "
                        f"({round_half_up(w.default_sell_prob_cheapest * 100)}%): no sales data yet.")
    return {
        "fresh": _fresh(bases, w, day_one),
        "header": f"{len(bases)} in the catalog · {len(rated)} with a rate · desktop only: the addon only records loot",
        "characters": [["", "All characters"], *[[c, name_of(c)] for c in sorted(w.characters)]],
        "filters": {"categories": list(CATEGORIES), "min_price": MIN_PRICE_OPTIONS, "skill": SKILL_OPTIONS,
                    "search": "Search materials…"},
        "rows": rows, "rate_cards": cards, "by_char": by_char,
        "details": {str(b.item.key): _detail(b, w, day_one) for b in bases},
        "selected": str(best["key"]) if best else (str(rows[0]["key"]) if rows else ""),
        "note": _note_text(bases, w), "default_note": default_note,
        "no_rates": None if any(_any_rate(b, w) for b in bases) else NO_RATES,
        "no_match": NO_MATCH, "no_match_filters": NO_MATCH_FILTERS,
        "help": {k: v + (HISTORY_NOTE if k in HISTORY_COLUMNS else "") for k, v in GATHERING_HELP.items()},
    }
