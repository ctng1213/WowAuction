"""The dashboard mockup's Gathering world (docs/mockups/dashboard.html, `// ---------- gathering`; SPEC §3.4E).

Monday 10:38. Brugan has Mining 68 and Skinning 40, Amaya Herbalism 45 and Fishing 20. AH prices come from
an AHF scan 26 h old (Sun 08:38). The mockup's 16-item catalog (its own small catalog: Rough Stone and the
grey drops are not in it, unlike the shipped data/gathering_catalog.json), each item's price, quantity,
30-day median, chance (None = no sales data) and vendor price. The rates are the mockup's, injected as
M17 will compute them from sessions: Brugan's Copper Ore 38/hr (70 units over 3 sessions, 110 min), Tin Ore
4/hr, Light Leather 21/hr; Amaya's Peacebloom 26/hr, Silverleaf 19/hr, Earthroot 10/hr observed with a
manual 12, Linen Cloth 14/hr.
"""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine.gathering import Observed, catalog
from ahsync.engine.gathering_view import GatheringWorld, PriceFacts

HOUR, MINUTE = 3_600_000, 60_000
MON = 1_728_259_200_000             # Mon 2024-10-07 00:00 UTC (offset 0: local = UTC)
NOW = MON + 10 * HOUR + 38 * MINUTE
AHF_SEEN = NOW - 26 * HOUR          # Sun 08:38
AMAYA, BRUGAN = "Amaya-Grommash", "Brugan-Grommash"
COPPER, TIN, SILVER, PEACEBLOOM, SILVERLEAF, EARTHROOT = 2770, 2771, 2775, 2447, 765, 2449
MAGEROYAL, BRIARTHORN, LIGHT_LEATHER, MEDIUM_LEATHER, LINEN, WOOL = 785, 2450, 2318, 2319, 2589, 2592
SMALL_EGG, SMALLFISH, MUD_SNAPPER, LIGHT_FEATHER = 6889, 6291, 6289, 17056
ROUGH_STONE, THORIUM = 2835, 10620
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

OBSERVED = {
    (BRUGAN, COPPER): Observed(38, 70, 110, 3), (BRUGAN, TIN): Observed(4, 2, 30, 1),
    (BRUGAN, LIGHT_LEATHER): Observed(21, 19, 55, 2), (AMAYA, PEACEBLOOM): Observed(26, 21, 48, 2),
    (AMAYA, SILVERLEAF): Observed(19, 15, 48, 2), (AMAYA, EARTHROOT): Observed(10, 8, 48, 2),
    (AMAYA, LINEN): Observed(14, 7, 30, 1),
}
MANUAL = {(AMAYA, EARTHROOT): 12}


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
        5 a day; med30 rests on 30 closes.
    """
    out = {}
    for key, _, _, _, price, qty, med, chance, vendor in _ITEMS:
        known = chance is not None and not day_one
        out[key] = PriceFacts(price, NOW - 12 * MINUTE if day_one else AHF_SEEN, "local" if day_one else "ahf",
                              qty, vendor, 1, chance / 100 if known else None,
                              (30.0 if key == COPPER else 5.0) if known else None, "ahf" if known else None,
                              "ids" if known else None, 80 if known else None, price,
                              None if day_one else med, None if day_one else 30)
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


def world(day_one: bool = False, rates: bool = True, **changes) -> GatheringWorld:
    """The mockup's Gathering world.

    Args:
        day_one: The day-one state (price_facts).
        rates: False for the no-rates state (no session, no manual rate).
        changes: GatheringWorld field overrides.

    Returns:
        The world at Mon 10:38, 7 sessions over 220 min, professions synced Mon 10:38.
    """
    items, skipped = catalog(catalog_rows())
    assert not skipped
    base = GatheringWorld(items, price_facts(day_one), SKILLS, [AMAYA, BRUGAN], NOW, 0,
                          MANUAL if rates else {}, OBSERVED if rates else {}, 7 if rates else 0,
                          220 if rates else 0, NOW)
    return replace(base, **changes)
