"""The dashboard mockup's Gathering world (docs/mockups/dashboard.html, `// ---------- gathering`; SPEC §3.4E).

Monday 10:38. Brugan has Mining 68 and Skinning 40, Amaya Herbalism 45 and Fishing 20. AH prices come from
an AHF scan 26 h old (Sun 08:38). The mockup's 16-item catalog (its own small catalog: Rough Stone and the
grey drops are not in it, unlike the shipped data/gathering_catalog.json), each item's price, quantity,
30-day median, chance (None = no sales data) and vendor price. The rates come from the addon's gather
events (EVENTS), grouped by engine.sessions into the mockup's 7 confirmed sessions (220 min) and Brugan's open
candidate (Mon 10:21–10:30): Brugan's Copper Ore 38/hr (70 units over 3 sessions, 110 min), Tin Ore 4/hr,
Light Leather 21/hr; Amaya's Peacebloom 26/hr, Silverleaf 19/hr, Earthroot 10/hr observed with a manual 12,
Linen Cloth 14/hr. Two short candidates are dropped: Brugan's Sun 15:14 (2 Rough Stone, the GA15 event that
ends his 14:02–14:59 session by inactivity) and Amaya's Mon 09:05 (1 Small Egg, the next login that makes
her Sun 20:40–21:08 session "ended at logout").

The price history (M18) is the mockup's too: each item's seasonality strength and top selling window
(Copper Ore's three windows and its hourly profile `how`, gaCopperHow: Sat 17:00–21:00 at 1.12, Sun
19:00–22:00 at 1.09, Wed 20:00–22:00 at 1.08, strength 61; Peacebloom 22, so "no timing edge"; Silver Ore
insufficient evidence). med30 rests on 30 hourly closes.
"""
from __future__ import annotations

import math
from dataclasses import replace

from ahsync.engine.gathering import catalog
from ahsync.engine.gathering_view import GatheringWorld, PriceFacts
from ahsync.engine.patterns import Window
from ahsync.engine.profile import Profile
from ahsync.engine.sessions import GatherEvent, sessions

HOUR, MINUTE = 3_600_000, 60_000
MON = 1_728_259_200_000             # Mon 2024-10-07 00:00 UTC (offset 0: local = UTC)
NOW = MON + 10 * HOUR + 38 * MINUTE
AHF_SEEN = NOW - 26 * HOUR          # Sun 08:38
AMAYA, BRUGAN = "Amaya-Grommash", "Brugan-Grommash"
COPPER, TIN, SILVER, PEACEBLOOM, SILVERLEAF, EARTHROOT = 2770, 2771, 2775, 2447, 765, 2449
MAGEROYAL, BRIARTHORN, LIGHT_LEATHER, MEDIUM_LEATHER, LINEN, WOOL = 785, 2450, 2318, 2319, 2589, 2592
SMALL_EGG, SMALLFISH, MUD_SNAPPER, LIGHT_FEATHER = 6889, 6291, 6289, 17056
ROUGH_STONE, THORIUM = 2835, 10620
RUINED_SCRAPS, LIGHT_HIDE, BROKEN_FANG, CHIPPED_CLAW = 2934, 783, 7073, 7074   # looted, never in the catalog
SAT, SUN = MON - 2 * 24 * HOUR, MON - 24 * HOUR
GAP_MS = 15 * MINUTE
SKILLS = {AMAYA: {"Herbalism": 45, "Fishing": 20, "Tailoring": 160, "Enchanting": 142},
          BRUGAN: {"Mining": 68, "Skinning": 40, "Blacksmithing": 112, "Alchemy": 225}}

# key, name, categories, gathering source (profession, skill) or None, price, qty, med30, chance %, vendor
_ITEMS = (
    (COPPER, "Copper Ore", ["Mining"], ("Mining", 1), 220, 64, 200, 85, 5),
    (TIN, "Tin Ore", ["Mining"], ("Mining", 65), 275, 22, 300, None, 25),
    (SILVER, "Silver Ore", ["Mining"], ("Mining", 75), 900, 3, 1000, 60, 75),
    (PEACEBLOOM, "Peacebloom", ["Herbalism"], ("Herbalism", 1), 18, 140, 20, 90, 5),
    (SILVERLEAF, "Silverleaf", ["Herbalism"], ("Herbalism", 1), 24, 180, 20, 90, 5),
    (EARTHROOT, "Earthroot", ["Herbalism"], ("Herbalism", 15), 60, 75, 50, 85, 5),
    (MAGEROYAL, "Mageroyal", ["Herbalism"], ("Herbalism", 50), 240, 30, 225, 75, 5),
    (BRIARTHORN, "Briarthorn", ["Herbalism"], ("Herbalism", 70), 300, 18, 280, 70, 15),
    (LIGHT_LEATHER, "Light Leather", ["Skinning", "Monster"], ("Skinning", 1), 95, 85, 100, 88, 15),
    (MEDIUM_LEATHER, "Medium Leather", ["Skinning"], ("Skinning", 100), 200, 40, 190, 80, 25),
    (LINEN, "Linen Cloth", ["Monster"], None, 38, 93, 49, 92, 13),
    (WOOL, "Wool Cloth", ["Monster"], None, 30, 140, 32, 81, 33),
    (SMALL_EGG, "Small Egg", ["Monster"], None, 12, 50, 10, 70, 4),
    (SMALLFISH, "Raw Brilliant Smallfish", ["Fishing"], ("Fishing", 1), 8, 60, 8, 65, 1),
    (MUD_SNAPPER, "Raw Longjaw Mud Snapper", ["Fishing"], ("Fishing", 1), None, 0, 15, 50, 2),
    (LIGHT_FEATHER, "Light Feather", ["Monster"], None, 9, 200, 12, 60, 4),
)
TYPES = {"Mining": "node", "Herbalism": "node", "Skinning": "skinning", "Fishing": "fishing"}

MANUAL = {(AMAYA, EARTHROOT): 12}

# Seasonality strength (None = insufficient evidence), from the mockup's GA_ITEMS.
STRENGTH = {COPPER: 61, TIN: 48, SILVER: None, PEACEBLOOM: 22, SILVERLEAF: 35, EARTHROOT: 44, MAGEROYAL: 57,
            BRIARTHORN: 31, LIGHT_LEATHER: 52, MEDIUM_LEATHER: 46, LINEN: 78, WOOL: 55, SMALL_EGG: 12, SMALLFISH: None,
            MUD_SNAPPER: None, LIGHT_FEATHER: 25}
SAT_SLOT, SUN_SLOT, WED_SLOT, THU_SLOT = 5 * 24, 6 * 24, 2 * 24, 3 * 24


def window(day_slot: int, start: int, end: int, best: int, how_avg: float, weeks: int = 6) -> Window:
    """A selling window of the mockup's week (UTC slots; offset 0).

    Args:
        day_slot: The weekday's first slot (SAT_SLOT, ...).
        start: First hour.
        end: End hour (exclusive).
        best: Best hour.
        how_avg: Its average price ratio.
        weeks: Weeks of data behind its thinnest slot.

    Returns:
        The Window (no score, no forecast: Gathering reads neither).
    """
    return Window(day_slot + start, end - start, 0.0, how_avg, weeks, day_slot + best)


# Each item's selling windows, best first (the mockup's top window; Copper Ore's three, GA_COPPER_WINDOWS).
WINDOWS = {COPPER: (window(SAT_SLOT, 17, 21, 19, 1.12, 7), window(SUN_SLOT, 19, 22, 20, 1.09, 7),
                    window(WED_SLOT, 20, 22, 20, 1.08, 4)),
           TIN: (window(SAT_SLOT, 18, 21, 19, 1.05),), EARTHROOT: (window(SUN_SLOT, 19, 22, 20, 1.06),),
           MAGEROYAL: (window(WED_SLOT, 19, 22, 20, 1.07),), LIGHT_LEATHER: (window(SUN_SLOT, 18, 21, 19, 1.06),),
           MEDIUM_LEATHER: (window(THU_SLOT, 19, 22, 19, 1.05),), LINEN: (window(SAT_SLOT, 17, 21, 19, 1.12),),
           WOOL: (window(SUN_SLOT, 19, 22, 19, 1.06),)}


def copper_how(slot: int) -> float:
    """Copper Ore's price ratio in one slot: the mockup's gaCopperHow, in hundredths, half up.

    Args:
        slot: UTC slot (offset 0: local), 0 = Mon 00:00.

    Returns:
        A midday low, an evening rise, weekends a little higher and three bumps (its windows): Mon 10:00 0.97,
        Sat 18:00 1.12.
    """
    day, hour = divmod(slot, 24)
    bump = (0.04 if day == 5 and 17 <= hour <= 20 else 0.01 if day == 6 and 19 <= hour <= 21
            else 0.02 if day == 2 and 20 <= hour <= 21 else 0)
    ratio = (1 + 0.03 * math.sin((hour - 14) / 3.8) + (0.03 if 17 <= hour <= 21 else 0) + (0.02 if day >= 5 else 0)
             + bump)
    return math.floor(ratio * 100 + 0.5) / 100


# 7 weeks of data in every slot but Wed 20:00-21:00 (4 weeks, the third window's), as the mockup's heatmap titles.
COPPER_HOW = Profile(tuple(copper_how(s) for s in range(168)),
                     tuple(4 if WED_SLOT + 20 <= s <= WED_SLOT + 21 else 7 for s in range(168)),
                     tuple(True for _ in range(168)))


def at(day: int, hh_mm: str) -> int:
    """A time of the mockup's week.

    Args:
        day: SAT, SUN or MON (UTC ms of midnight).
        hh_mm: "21:10".

    Returns:
        UTC ms (offset 0: local = UTC).
    """
    hours, minutes = hh_mm.split(":")
    return day + int(hours) * HOUR + int(minutes) * MINUTE


# Each loot run: (character, login, first loot, last loot, {source: [(key, units), ...]}). A run's loot is spread
# over events every 10 min or less from its first to its last loot, one event list per source.
RUNS = (
    (BRUGAN, at(SAT, "18:50"), at(SAT, "19:00"), at(SAT, "19:32"),
     {"skinning": [(LIGHT_LEATHER, 12), (LIGHT_HIDE, 2)], "monster": [(RUINED_SCRAPS, 8)]}),
    (BRUGAN, at(SAT, "18:50"), at(SAT, "21:10"), at(SAT, "21:33"),
     {"mining": [(COPPER, 8)], "skinning": [(LIGHT_LEATHER, 7)], "monster": [(RUINED_SCRAPS, 5)]}),
    (BRUGAN, at(SUN, "13:55"), at(SUN, "14:02"), at(SUN, "14:59"), {"mining": [(COPPER, 17), (ROUGH_STONE, 11)]}),
    (BRUGAN, at(SUN, "13:55"), at(SUN, "15:14"), at(SUN, "15:14"), {"mining": [(ROUGH_STONE, 2)]}),
    (BRUGAN, at(MON, "08:00"), at(MON, "08:05"), at(MON, "08:35"),
     {"mining": [(COPPER, 45), (TIN, 2), (ROUGH_STONE, 8)]}),
    (BRUGAN, at(MON, "10:15"), at(MON, "10:21"), at(MON, "10:30"), {"mining": [(COPPER, 9), (ROUGH_STONE, 3)]}),
    (AMAYA, at(SAT, "16:55"), at(SAT, "17:00"), at(SAT, "17:30"),
     {"monster": [(LINEN, 7), (BROKEN_FANG, 7), (CHIPPED_CLAW, 6)]}),
    (AMAYA, at(SUN, "19:55"), at(SUN, "20:00"), at(SUN, "20:20"),
     {"herbalism": [(PEACEBLOOM, 10), (SILVERLEAF, 6), (EARTHROOT, 4)]}),
    (AMAYA, at(SUN, "19:55"), at(SUN, "20:40"), at(SUN, "21:08"),
     {"herbalism": [(PEACEBLOOM, 11), (SILVERLEAF, 9), (EARTHROOT, 4)]}),
    (AMAYA, at(MON, "09:00"), at(MON, "09:05"), at(MON, "09:05"), {"monster": [(SMALL_EGG, 1)]}),
)


def spread(char_id: str, login: int, first: int, last: int, loot: dict[str, list[tuple[int, int]]]) -> list[tuple]:
    """One loot run as (char, time, login, source, loot) rows: per source, events every 10 min or less.

    Args:
        char_id: The character.
        login: Her initial login, ms.
        first: The run's first loot, ms.
        last: Its last loot, ms.
        loot: Source -> [(key, units)].

    Returns:
        Rows (char, at ms, login ms, source, ((key, units), ...)); each source's units split as evenly as possible
        over its events, the remainder on the first ones; every source loots at the first and the last time.
    """
    rows = []
    for source, items in loot.items():
        n = max(2, -(-(last - first) // (10 * MINUTE)) + 1) if last > first else 1
        n = min(n, max(units for _, units in items))        # every event loots at least one unit
        for i in range(n):
            t = first + ((last - first) * i // (n - 1)) // 1000 * 1000 if n > 1 else first
            share = tuple((k, u // n + (1 if i < u % n else 0)) for k, u in items)
            rows.append((char_id, t, login, source, tuple((k, q) for k, q in share if q)))
    return rows


def gather_events(runs=RUNS) -> list[GatherEvent]:
    """The addon's gather events for some loot runs, with ids as the addon writes them (charId:timeMs:seq).

    Args:
        runs: Loot runs (RUNS by default).

    Returns:
        The events, by character, time and seq.
    """
    rows = sorted(row for run in runs for row in spread(*run))
    seq: dict[tuple[str, int], int] = {}
    out = []
    for char_id, t, login, source, loot in rows:
        n = seq.get((char_id, t), 0)
        seq[(char_id, t)] = n + 1
        out.append(GatherEvent(f"{char_id}:{t}:{n}", char_id, t, login, loot, source))
    return out


EVENTS = gather_events()


def catalog_rows() -> list[dict]:
    """The mockup's catalog as data-file rows.

    Returns:
        16 rows {key, name, categories, sources}; Light Leather has a Skinning 1 and a monster source.
    """
    rows = []
    for key, name, cats, src, *_ in _ITEMS:
        sources = [{"type": TYPES[src[0]], "profession": src[0], "skill": src[1]}] if src else []
        if "Monster" in cats:
            sources.append({"type": "monster", "profession": None, "skill": None})
        rows.append({"key": key, "name": name, "categories": cats, "sources": sources})
    return rows


def price_facts(day_one: bool = False) -> dict[int, PriceFacts]:
    """Each catalog item's market facts, plus Thorium Ore and Rough Stone (priced, never in the catalog).

    Args:
        day_one: True for the day-one state: your own scan 12 min old, no sales data, no price history.

    Returns:
        Item key -> PriceFacts. Copper Ore sells 30 a day (AHF) with confidence 80; an item with a chance sells
        5 a day; med30 rests on 30 closes; strength, windows and Copper Ore's profile as STRENGTH, WINDOWS and
        COPPER_HOW (none of them on day one).
    """
    out = {}
    for key, _, _, _, price, qty, med, chance, vendor in _ITEMS:
        known = chance is not None and not day_one
        out[key] = PriceFacts(price, NOW - 12 * MINUTE if day_one else AHF_SEEN, "local" if day_one else "ahf",
                              qty, vendor, 1, chance / 100 if known else None,
                              (30.0 if key == COPPER else 5.0) if known else None, "ahf" if known else None,
                              "ids" if known else None, 80 if known else None, price,
                              None if day_one else med, None if day_one else 30,
                              None if day_one else STRENGTH[key], COPPER_HOW if key == COPPER and not day_one else None,
                              () if day_one else WINDOWS.get(key, ()))

    out[THORIUM] = PriceFacts(2500, AHF_SEEN, "ahf", 10, 100)
    out[ROUGH_STONE] = PriceFacts(15, AHF_SEEN, "ahf", 200, 2)
    return out


def view_rows(payload: dict, char: str = "") -> list[dict]:
    """One view's rows as the dashboard draws them: the shared rows in the view's key order, its cells overlaid.

    Args:
        payload: A gathering payload.
        char: The character id, "" for All characters.

    Returns:
        The rows.
    """
    shared = {r["key"]: r for r in payload["rows"]}
    view = payload["by_char"][char]
    return [{**shared[k], **view["cells"].get(str(k), {})} for k in view["keys"]]


def view_card(payload: dict, char: str, key: int | str) -> dict:
    """One view's rate card as the dashboard draws it: the item's blocks (only the picked character's), its lines.

    Args:
        payload: A gathering payload.
        char: The character id, "" for All characters.
        key: The item key.

    Returns:
        {"blocks", "lines"}.
    """
    row = next(r for r in view_rows(payload, char) if str(r["key"]) == str(key))
    blocks = payload["rate_cards"][str(key)]["blocks"]
    return {"blocks": [b for b in blocks if not char or b["char"] == char], "lines": row["card_lines"]}


def looted_sources(events: list[GatherEvent]) -> dict[int, tuple[str, ...]]:
    """What state.gathering.looted_sources reads: the sources each item was looted from.

    Args:
        events: Gather events.

    Returns:
        Item key -> distinct sources, sorted.
    """
    found: dict[int, set[str]] = {}
    for e in events:
        for key, _ in e.loot:
            found.setdefault(key, set()).add(e.source)
    return {key: tuple(sorted(s)) for key, s in sorted(found.items())}


def world(day_one: bool = False, rates: bool = True, now_ms: int = NOW, extra=(), **changes) -> GatheringWorld:
    """The mockup's Gathering world, its sessions grouped from the gather events by engine.sessions.

    Args:
        day_one: The day-one state (price_facts).
        rates: False for the no-rates state (no event, no manual rate).
        now_ms: When the page is built (the open candidate ends 15 min after its last loot, GA24).
        extra: More loot runs, as in RUNS, added to the mockup's.
        changes: GatheringWorld field overrides.

    Returns:
        The world at Mon 10:38: 7 sessions over 220 min and Brugan's open candidate, professions synced Mon 10:38,
        the events' sources for the observed categories (none in the mockup's catalog: every source is listed).
    """
    items, skipped = catalog(catalog_rows())
    assert not skipped
    events = gather_events((*RUNS, *extra)) if rates else []
    found, open_ = sessions(events, now_ms, GAP_MS, 20)
    base = GatheringWorld(items, price_facts(day_one), SKILLS, [AMAYA, BRUGAN], now_ms, 0,
                          manual=MANUAL if rates else {}, sessions=found, open=open_, professions_ms=NOW,
                          sources=looted_sources(events))
    return replace(base, **changes)

