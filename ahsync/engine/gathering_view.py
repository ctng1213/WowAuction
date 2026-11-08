"""The Gathering page: every cell, note, footer, filter field and sort key (SPEC §3.4E, §3.5 Gathering, §8.5).

Built M16 (the day-one page): the catalog's rows ranked by AH price, eligibility per character, the
rate priority, net gold per hour with the default chance, the filters' data, one complete view per
character (`by_char`), the freshness line, the rate cards and the compact detail. Built M17: the
observed rates come from the confirmed sessions (engine.sessions, engine.gathering.observed_rates),
and the rate card lists the open candidate, the last sessions with how each ended, and the manual
override's controls. Built M18: the historical columns (Median 30d, Deviation, Sell window, Sell window
price), the full detail's heatmap, reading line, windows and seasonality (engine.gathering_history, from the
intelligence only) and the observed categories. Pure: data in, texts out.
"""
from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property

from ahsync.engine import gathering_history as gh
from ahsync.engine.fees import AH_CUT, DEPOSIT_BP, MIN_DEPOSIT_COPPER
from ahsync.engine.gathering import (CATEGORIES, GATHERING_PROFESSIONS, SESSION_CONFIRM_UNITS, SESSION_GAP_MINUTES,
                                     CatalogItem, Observed, Rate, best_rate, chance_to_sell, deposit_per_unit,
                                     effective_rate, eligible, gross_per_hour, minutes_of, net_per_hour, net_per_unit,
                                     observed_categories, observed_rate, observed_rates, own_rate, skill_source,
                                     why_not_eligible)
from ahsync.engine.patterns import Window
from ahsync.engine.profile import Profile
from ahsync.engine.sessions import LOGOUT_ENDED, Candidate, Session
from ahsync.engine.views import SOURCE_URLS, market_activity, money, round_half_up, source_label

MINUTE_MS, HOUR_MS = 60_000, 3_600_000
STALE_MS = 24 * HOUR_MS            # prices over 24 h are stale: amber, used as they are (§3.4E.6)
DASH = "—"
CLICK_HINT = " · click the row for the sessions behind this rate and to override it"
HISTORY_NOTE = ' · needs price history: "—" until weeks of data arrive'
HISTORY_HOVER = 'Needs price history: "—" until weeks of data arrive'

HISTORY_COLUMNS = ("Median 30d", "Deviation", "Sell window", "Sell window price")
NO_HISTORY_LINE = ("no price history yet: the heatmap, the top selling windows and seasonality strength appear as "
                   "weeks of data arrive")
NO_HISTORY_FRESH = "no price history yet: medians, deviations and sell windows appear as weeks of data arrive"
CATALOG_NOTE = ("Items the catalog doesn't know are never shown as farmable, even if you looted them; AHSync lists what "
                "you gathered that it couldn't place under Status.")
FORMULA_NOTE = ("Net gold/hr = items/hr × net per unit, where net per unit = post price − 5% AH cut − (1 − chance to "
                "sell) × deposit (24 h, refunded on a sale). Posts are priced at the current AH price. Gross gold/hr "
                "(items/hr × price) is on the detail below.")
NO_RATES = ("Farm for {n}+ units and the addon records your rate. Every row keeps its price and sell window "
            "meanwhile.")
CARD_SESSIONS = 3                  # the rate card lists the last 3 confirmed sessions with the material (§3.4E.7)
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
    "Rank": "Top selling windows, best first",
    "Window": "Day and hours of the week, local time",
    "Median price": "30-day median × the window's average price ratio",
    "Weeks of data": "Weeks behind the window's thinnest hour",
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
    med30: int | None = None           # price history (M18): copper
    closes_30d: int | None = None      # hourly closes in 30 days; None = unknown
    seasonality: int | None = None     # 0-100; None = insufficient evidence
    how: Profile | None = None         # the 168-slot price profile; None = none published
    windows: tuple[Window, ...] = ()   # selling windows, best first


@dataclass(frozen=True)
class GatheringWorld:
    """Everything the Gathering page needs (SPEC §3.4E)."""

    items: Sequence[CatalogItem]
    facts: Mapping[int, PriceFacts]
    skills: Mapping[str, Mapping[str, int]]            # character id -> profession -> current skill
    characters: Sequence[str]                          # the market's character ids
    now_ms: int
    offset_ms: int = 0
    manual: Mapping[tuple[str, int], int] = field(default_factory=dict)   # (character, key) -> manual items/hr
    sessions: Sequence[Session] = ()                   # the characters' confirmed sessions in the 90 days kept
    open: Mapping[str, Candidate] = field(default_factory=dict)          # character id -> her open candidate
    professions_ms: int | None = None                  # when the professions were last captured
    default_sell_prob: float = 0.25
    default_sell_prob_cheapest: float = 0.5
    confirm_units: int = SESSION_CONFIRM_UNITS         # config session_confirm_units (the texts quote it)
    gap_minutes: int = SESSION_GAP_MINUTES             # config session_gap_minutes
    sources: Mapping[int, Sequence[str]] = field(default_factory=dict)  # item key -> its gather events' sources
    min_strength: int = gh.TIMING_EDGE_MIN_STRENGTH    # config timing_edge_min_strength

    @cached_property
    def observed(self) -> dict[tuple[str, int], Observed]:
        """Every character's observed rate per material, computed once from the sessions (SPEC §3.4E.5).

        Returns:
            (character id, item key) -> Observed.
        """
        return observed_rates(self.sessions)

    @cached_property
    def card_sessions(self) -> dict[tuple[str, int], list[Session]]:
        """The sessions a rate card lists: per character and material, those with a duration, newest first.

        Returns:
            (character id, item key) -> sessions (start time descending, then id), zero-duration ones left out (GA17).
        """
        out: dict[tuple[str, int], list[Session]] = {}
        for s in sorted(self.sessions, key=lambda s: (-s.start_ms, s.session_id)):
            if s.duration_ms > 0:
                for key, _ in s.items:
                    out.setdefault((s.char_id, key), []).append(s)
        return out


@dataclass(frozen=True)
class _Base:
    """One catalog item's character-independent cells and numbers."""

    item: CatalogItem
    facts: PriceFacts
    eligible_chars: tuple[str, ...]
    chance: float
    default: bool
    net_unit: float | None
    history: gh.History
    observed: tuple[str, ...] = ()     # categories its gather events taught it (GA33)


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


def hhmm(ms: int, offset_ms: int) -> str:
    """A local time of day, e.g. "14:59".

    Args:
        ms: UTC ms.
        offset_ms: Local time minus UTC, ms.

    Returns:
        The text.
    """
    return time.strftime("%H:%M", time.gmtime((ms + offset_ms) // 1000))


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
    """An item's character-independent numbers: who can gather it, its chance, its net per unit and its history.

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
    history = gh.History(f.price, f.med30, f.closes_30d, f.seasonality, f.how, f.windows, w.min_strength)
    return _Base(item, f, chars, p, default, net_unit, history,
                 observed_categories(item, w.sources.get(item.key, ())))


def _price_title(f: PriceFacts, w: GatheringWorld) -> str:
    """The AH Price cell's hover: its age and source (D87).

    Args:
        f: The item's facts.
        w: The world.

    Returns:
        E.g. "seen 26 h ago (AHF scan Sun 08:38) · stale: over 24 h", "seen 12 min ago (your scan)",
        "seen 26 h ago (AHledger)" or "No listing in the newest scan".
    """
    if f.price is None or f.seen_ms is None:
        return "No listing in the newest scan"
    old = w.now_ms - f.seen_ms
    src = str(f.source)
    if f.source == "local":
        where = "your scan"
    elif src in SOURCE_URLS:                                     # AHledger's price table is not a scan (S19)
        where = source_label(src)
    else:
        where = f"{source_label(src)} scan {clock(f.seen_ms, w.offset_ms)}"
    return f"seen {age(old)} ago ({where})" + (" · stale: over 24 h" if old > STALE_MS else "")


def _price_credit(f: PriceFacts) -> dict:
    """The AH Price cell's credit for a source that must be linked (S19).

    Args:
        f: The item's facts.

    Returns:
        {"price_source": label, "price_url": URL} for an AHledger price; both None for any other source or no price.
    """
    url = SOURCE_URLS.get(str(f.source)) if f.price is not None else None
    return {"price_source": None if url is None else source_label(str(f.source)), "price_url": url}


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
    assert o is not None and o.duration_ms > 0         # observed rates come from sessions with a duration (GA17)
    ms = o.duration_ms
    tenths = (o.units * 20 * HOUR_MS + ms) // (2 * ms)                    # items per hour in tenths, half up
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


def _row(b: _Base, r: Rate | None, view_char: str, w: GatheringWorld, hist: dict) -> dict:
    """One table row for one view (SPEC §3.5 Gathering): formatted cells, note, filter fields and sort keys.

    Args:
        b: The item's base.
        r: The row's rate for this view, or None.
        view_char: The view's character id ("" for All characters).
        w: The world.
        hist: The item's historical cells (gathering_history.columns, or blank_columns on day one).

    Returns:
        The row; the Category cell adds the observed categories with "(observed)", and so does its filter field
        `cats` (GA33).
    """
    f, qty = b.facts, _qty(b.facts)
    net = _net_cells(b, r, w)
    skill = _skill_cells(b)
    category = " · ".join([*b.item.categories, *(f"{c} (observed)" for c in b.observed)])
    return {
        "key": b.item.key, "item": b.item.name, "quality": f.quality, "url": b.item.url or "",
        "note": _note(b, r, view_char), "category": category,
        **{k: v for k, v in skill.items() if k != "skill"},
        "ah_price": money(f.price), "price_title": _price_title(f, w),
        **_price_credit(f), "qty": DASH if qty is None else str(qty),
        **{k: v for k, v in hist.items() if k != "sort"},
        **_rate_cells(r, view_char), **{k: v for k, v in net.items() if k != "net_copper"},
        "eligible": view_char == "" or view_char in b.eligible_chars,
        "cats": [*b.item.categories, *b.observed], "price": f.price, "name": b.item.name, "skill": skill["skill"],
        "sort": {"price": f.price, "net": net["net_copper"], "rate": r.items_per_hour if r else None,
                 "qty": qty, "skill": skill["skill"], **hist["sort"]},
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


def _ended_text(s: Session, w: GatheringWorld) -> str:
    """A session line's hover: how the session ended (SPEC §3.4E.7, GA25).

    Args:
        s: The session.
        w: The world (gap and local offset).

    Returns:
        "ended at logout", or "ended by inactivity: no loot 14:59–15:14; those 15 min are not counted".
    """
    if s.ended == LOGOUT_ENDED:
        return "ended at logout"
    quiet = s.end_ms + w.gap_minutes * MINUTE_MS
    return (f"ended by inactivity: no loot {hhmm(s.end_ms, w.offset_ms)}–{hhmm(quiet, w.offset_ms)}; those "
            f"{w.gap_minutes} min are not counted")


def _session_lines(char_id: str, item: CatalogItem, w: GatheringWorld) -> list[dict]:
    """The rate card's lines for one character: her open candidate, then her last sessions with the material.

    Args:
        char_id: The character.
        item: The material.
        w: The world.

    Returns:
        [{"text", "title", "open"}]: "open: Mon 10:21– · 9 Copper Ore so far · not yet confirmed (12 of the 20 units
        needed)" when her open candidate contains it (never a rate, GA24); then the last 3 confirmed sessions with a
        duration that contained it, newest first, "Mon 08:05–08:35 · 30 min · 45 Copper Ore · 90/hr" (the material
        named on the first only), each with how it ended as its hover (GA25).
    """
    lines = []
    cand = w.open.get(char_id)
    qty = dict(cand.items).get(item.key) if cand else None
    if cand is not None and qty:
        state = (f"not yet confirmed ({cand.units} of the {w.confirm_units} units needed)"
                 if cand.units < w.confirm_units else f"{cand.units} units: a session once it ends")
        lines.append({"text": f"open: {clock(cand.start_ms, w.offset_ms)}– · {qty} {item.name} so far · {state}",
                      "title": f"still collecting: it ends {w.gap_minutes} min after the last loot "
                               f"({hhmm(cand.last_ms, w.offset_ms)}) or at logout; no rate uses it", "open": True})
    for i, s in enumerate(w.card_sessions.get((char_id, item.key), [])[:CARD_SESSIONS]):
        units = dict(s.items)[item.key]
        what = f"{units} {item.name}" if i == 0 else str(units)
        lines.append({"text": f"{clock(s.start_ms, w.offset_ms)}–{hhmm(s.end_ms, w.offset_ms)} · "
                              f"{duration(minutes_of(s.duration_ms))} · {what} · "
                              f"{observed_rate(units, s.duration_ms)}/hr",
                      "title": _ended_text(s, w), "open": False})
    return lines


def _card_block(b: _Base, char_id: str, w: GatheringWorld) -> dict:
    """One character's block of the rate card (SPEC §3.4E.7 item 2; GA25, GA26, D85).

    Args:
        b: The item's base.
        char_id: The character.
        w: The world.

    Returns:
        {"char", "kind": "manual" | "observed" | "none" | "ineligible", "head", "sub", "foot", "lines" (the open
        candidate and the last sessions, _session_lines), "clear" (a Clear button by the head: an active override),
        "override" (the Manual override input: {"placeholder", "note", "clear"}, or None where she can't set one:
        an active override, which only clears, or a character not eligible now)}.
    """
    who, item = name_of(char_id), b.item.name
    r = own_rate(char_id, b.item.key, w.manual, w.observed)
    can = char_id in b.eligible_chars
    note = f"an override replaces the observed rate in the table for {who} until cleared"
    block = {"char": char_id, "kind": "", "head": "", "sub": "", "foot": "",
             "lines": _session_lines(char_id, b.item, w), "clear": False, "override": None}
    if r is not None and r.kind == "manual":
        o = r.observed
        sub = f"(observed {o.items_per_hour}/hr from {o.units} {item} over {_sessions_text(o)})" if o else "(no sessions)"
        return {**block, "kind": "manual", "head": f"{who} · {r.items_per_hour}/hr manual", "sub": sub,
                "foot": f"the override replaces the observed rate in the table for {who} until cleared", "clear": True}
    if r is not None and r.observed is not None:
        return {**block, "kind": "observed", "head": f"{who} · {r.items_per_hour}/hr observed",
                "sub": f"· {r.observed.units} {item} over {_sessions_text(r.observed)}",
                "override": {"placeholder": str(r.items_per_hour), "note": note, "clear": True} if can else None}
    if can:
        return {**block, "kind": "none", "head": f"{who} — no sessions with {item} yet",
                "override": {"placeholder": "", "note": "", "clear": False}}
    why = why_not_eligible(b.item, w.skills.get(char_id, {}))
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


def _view(bases: Sequence[_Base], view_char: str, w: GatheringWorld, hist: Mapping[int, dict]) -> dict:
    """One complete view: its rows, both footers, the toggle, the alert and the rate cards (SPEC §3.5 by_char).

    Args:
        bases: The items, in the default order.
        view_char: The character id, or "" for All characters.
        w: The world.
        hist: Item key -> its historical cells (the same in every view).

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
    return {"rows": [{**_row(b, r, view_char, w, hist[b.item.key]), "card_lines": c["lines"]}
                     for b, r, c in zip(bases, rates, cards)],

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


def _price_lines(b: _Base, w: GatheringWorld, day_one: bool) -> list[list]:
    """The detail's price card (SPEC §3.4E.7 item 1, §3.4E.8; D83, D84).

    Args:
        b: The item's base.
        w: The world.
        day_one: True for the compact form.

    Returns:
        [label, value, note, note class, dot] lines. Full: current AH price with its age, the 30-day median, the
        deviation, listed quantity. Day one: current AH price, listed quantity, the 30-day median and deviation
        ("—": needs price history), the chance to sell.
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
    listed = ["Listed quantity", DASH if qty is None else f"{qty} units", "", "", None]
    if not day_one:
        return [price, *gh.median_lines(b.history), listed]
    dot = "low" if b.default else market_activity(b.chance, f.confidence, None, None, None).dot
    return [price, listed,
            ["30-day median · deviation", DASH, "needs price history", "", None],
            ["Chance to sell", f"{round_half_up(b.chance * 100)}%", "default: no sales data yet" if b.default else "",
             "", dot]]


def _detail(b: _Base, w: GatheringWorld, day_one: bool) -> dict:
    """An item's character-independent detail, in E.7's order (SPEC §3.4E.7, §3.4E.8; GA31, GA32, D83, D84).

    Args:
        b: The item's base.
        w: The world.
        day_one: True when no catalog item has price history yet: the compact form.

    Returns:
        {"title", "subtitle", "price", "market" (the market activity cells), "net_unit" (the formula, or None
        without a price), "deposit" (its legend), "history" (the line in place of the history on day one, else
        ""), and gathering_history.detail's "heat", "reading", "legend", "windows", "windows_note", "timing",
        "timing_note" (None, [], {}, [], "", [], "" on day one)}.
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
    history = ({"heat": None, "reading": [], "legend": {}, "windows": [], "windows_note": "", "timing": [],
                "timing_note": ""}

               if day_one else gh.detail(b.history, w.now_ms, w.offset_ms))
    return {"title": f"{b.item.name}: day one" if day_one else f"{b.item.name}: price, timing and your rate",
            "subtitle": "selected row · compact" if day_one else f"selected row · {where}",
            "price": _price_lines(b, w, day_one),
            "market": {"chance": m.chance, "confidence": m.confidence, "dot": "low" if b.default else m.dot,
                       "sales": m.sales, "supply": m.supply, "days": m.days, "chance_note": m.chance_note},
            "net_unit": net_unit,
            "deposit": (f"Deposit: {money(deposit_per_unit(f.vendor_sell))} a unit for 24 h ({DEPOSIT_BP[24] // 100}% of "
                        f"the {money(f.vendor_sell or 0)} vendor price, at least {money(MIN_DEPOSIT_COPPER)} a 20-unit "
                        f"auction), refunded on a sale, so only the {100 - percent}% chance it expires costs it. "
                        f"Estimates: a listed price is not a sale."),
            "history": NO_HISTORY_LINE if day_one else "", **history}


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
        [text, class] segments: the price age (amber over 24 h, "(your scan)" for your own), then, for a source
        that must be credited with a link (AHledger), a segment "prices from AHledger" carrying the URL as its
        third element (S19), then the professions' sync time and the sessions; on day one the no-history
        sentence in their place.
    """
    newest = _newest(bases)
    credit: list[list[str]] = []
    if newest is None or newest.seen_ms is None:
        first = ["no AH prices yet", "amber"]
    else:
        old = w.now_ms - newest.seen_ms
        mine = " (your scan)" if newest.source == "local" else ""
        first = [f"AH prices {age(old)} old{mine}", "amber" if old > STALE_MS else "pos" if mine else ""]
        url = SOURCE_URLS.get(str(newest.source))
        credit = [[f"prices from {source_label(str(newest.source))}", "", url]] if url else []
    if day_one:
        return [first, *credit, [NO_HISTORY_FRESH, ""]]
    synced = (f"professions synced {clock(w.professions_ms, w.offset_ms)}" if w.professions_ms is not None
              else "professions not synced yet")
    count, observed_ms = len(w.sessions), sum(s.duration_ms for s in w.sessions)
    sessions = (f"{count} farming session{'' if count == 1 else 's'}, {duration(minutes_of(observed_ms))} observed"
                if count else "0 farming sessions")
    return [first, *credit, [synced, ""], [sessions, ""]]


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
        while no character has any rate, else None), "no_match", "no_match_filters", "heat_hours" (the heatmap
        hovers' hour labels, gathering_history.heat), "help"}.

    """
    bases = _order([_base(item, w) for item in w.items])
    day_one = not any(gh.has_median(b.history) for b in bases)       # E.8: decided per page build
    hist = {b.item.key: gh.blank_columns(HISTORY_HOVER) if day_one else gh.columns(b.history, w.now_ms, w.offset_ms)
            for b in bases}
    rows, cards, by_char = _shared({c: _view(bases, c, w, hist) for c in ["", *sorted(w.characters)]})
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
        "no_rates": None if any(_any_rate(b, w) for b in bases) else NO_RATES.format(n=w.confirm_units),
        "no_match": NO_MATCH, "no_match_filters": NO_MATCH_FILTERS, "heat_hours": gh.HEAT_HOURS,
        "help": {k: v + (HISTORY_NOTE if day_one and k in HISTORY_COLUMNS else "") for k, v in GATHERING_HELP.items()},
    }
