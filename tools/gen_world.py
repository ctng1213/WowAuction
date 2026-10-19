"""Generate the whole-plan snapshot's fixture world, tests/fixtures/worlds/w1.json (SPEC §8.6, WP1).

One market (forever.pvp.horde.us) and two characters, written as the rows AHSync's database
would hold after catch-up, ingest and a SavedVariables read:
- 12 items; 5 with 6 weeks of AHF-style hourly closes and a clear weekly pattern (Linen, Wool,
  Copper Bar, Copper Rod, Strange Dust), Silk with 10 days (one Saturday: an early pattern), the rest
  with 4 days; Small Venom Sac has no volume at all (volume unknown);
- daily volume per item with its market-wide cover, as the AHF ingest writes it;
- three complete local scans with order books (Wool has one dumped stack, a flip-now; the
  Embossed Leather Gloves are a disenchant target), and `current` rows from both sources;
- Brugan (Mining, Blacksmithing: Copper Ore -> Copper Bar -> Copper Rod) and Amaya (Enchanting,
  owns Linen, Silk, Light Leather and a disenchantable green), their bags and bank, and one own auction.

Deterministic: a fixed seed and a fixed now_ms; tables, rows and keys are written in sorted
order. Run: python tools/gen_world.py (rewrites the JSON; review the diff, then
python tools/snapshot.py --update).
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "worlds" / "w1.json"
SEED = 8601
M = "forever.pvp.horde.us"
HOUR, DAY = 3_600_000, 86_400_000
NOW = 20_005 * DAY + 12 * HOUR + 30 * 60_000        # a Wednesday, 12:30 UTC
NOW_HOUR = NOW // HOUR * HOUR
BRUGAN, AMAYA = "Brugan-Grommash", "Amaya-Grommash"
ORE, BAR, ROD, LINEN, WOOL, SILK = 2770, 2840, 6217, 2589, 2592, 4306
DUST, ESSENCE, GLOVES, LEATHER, STONE, VENOM = 10940, 10938, 4239, 2318, 2835, 1475

# key -> (name, quality, ilvl, class, vendor_sell, stack size)
ITEMS = {
    ORE: ("Copper Ore", 1, 10, 7, 5, 20), BAR: ("Copper Bar", 1, 10, 7, 10, 20),
    ROD: ("Copper Rod", 1, 15, 7, 50, 20), LINEN: ("Linen Cloth", 1, 5, 7, 13, 20),
    WOOL: ("Wool Cloth", 1, 15, 7, 33, 20), SILK: ("Silk Cloth", 1, 25, 7, 150, 20),
    DUST: ("Strange Dust", 1, 20, 7, 20, 20), ESSENCE: ("Lesser Magic Essence", 2, 20, 7, 30, 10),
    GLOVES: ("Embossed Leather Gloves", 2, 25, 4, 100, 1), LEATHER: ("Light Leather", 1, 10, 7, 15, 20),
    STONE: ("Rough Stone", 1, 5, 7, 2, 20), VENOM: ("Small Venom Sac", 1, 15, 7, 25, 20),
}

# key -> (price level copper, days of hourly history, listed units, sold per day or None, pattern name)
MARKET = {
    LINEN: (50, 42, 300, 1200, "saturday"), WOOL: (1000, 42, 200, 150, "wool"),
    BAR: (800, 42, 120, 200, "nights"), ROD: (3000, 42, 12, 20, "rod"),
    DUST: (600, 42, 400, 500, "sunday"), SILK: (2000, 10, 60, 300, "early_saturday"),
    ORE: (250, 4, 300, 300, "flat"), ESSENCE: (900, 4, 50, 80, "flat"), GLOVES: (1500, 4, 3, 2, "flat"),
    LEATHER: (120, 4, 200, 100, "flat"), STONE: (30, 4, 400, 150, "flat"), VENOM: (110, 4, 40, None, "flat"),
}


def slot_of(t_ms: int) -> tuple[int, int]:
    """Weekday (Mon 0 … Sun 6) and hour of a UTC time.

    Args:
        t_ms: UTC ms.

    Returns:
        (weekday, hour).
    """
    return (t_ms // DAY + 3) % 7, t_ms // HOUR % 24


def pattern(name: str, t_ms: int) -> float:
    """The weekly price shape of one item: a ratio of its level at one hour.

    Args:
        name: A pattern name from MARKET.
        t_ms: The hour, UTC ms.

    Returns:
        The ratio; nights (02:00-07:59) dip to 0.94 except on the flat pattern. A peak must stand more than
        1 ÷ window_threshold (1.11×) above the rest for a selling window to exist (SPEC §3.3.4).
    """
    day, hour = slot_of(t_ms)
    if name == "flat":
        return 1.0
    peaks = {"saturday": {(5, h): 1.3 for h in range(17, 21)} | {(6, h): 1.15 for h in range(19, 22)},
             "wool": {(5, h): 1.25 for h in range(18, 21)} | {(2, h): 1.12 for h in range(19, 21)},
             "rod": {(5, h): 1.2 for h in range(17, 21)},
             "sunday": {(6, h): 1.18 for h in range(18, 22)},
             "nights": {},
             "early_saturday": {(5, h): 1.12 for h in range(24)}}[name]
    if (day, hour) in peaks:
        return peaks[(day, hour)]
    if name == "early_saturday":
        return 1.0            # an early pattern is a weekday's median: keep the other days flat
    return 0.94 if 2 <= hour < 8 else 1.0


def candles(rng: random.Random) -> list[list]:
    """AHF hourly closes and listed units, oldest first per item.

    Args:
        rng: The seeded generator (±2% noise on prices, ±10% on units).

    Returns:
        candles_h1 rows [market, key, t, open, high, low, close, qty, src].
    """
    rows = []
    for key in sorted(MARKET):
        level, days, listed, _, shape = MARKET[key]
        for h in range(days * 24, 0, -1):
            t = NOW_HOUR - h * HOUR
            close = math.floor(level * pattern(shape, t) * rng.uniform(0.98, 1.02) + 0.5)
            qty = max(1, math.floor(listed * rng.uniform(0.9, 1.1) + 0.5))
            rows.append([M, key, t, close, close, close, close, qty, "ahf"])
    return rows


def volume(rng: random.Random) -> tuple[list[list], list[list]]:
    """Daily volume for the last 14 days per item with known sales, and each day's market-wide cover.

    Args:
        rng: The seeded generator (±10% noise).

    Returns:
        (volume_d1 rows [market, key, day, listed, sold, expired, src, conf, method],
         volume_cover rows [market, day, src, kind, covered]).
    """
    today = NOW // DAY
    rows = []
    for key in sorted(MARKET):
        sold = MARKET[key][3]
        if sold is None:
            continue
        for day in range(today - 13, today + 1):
            units = float(math.floor(sold * rng.uniform(0.9, 1.1) + 0.5))
            rows.append([M, key, day, float(math.floor(units / 2)), units, float(math.floor(units * 0.2)), "ahf",
                         "medium", None])
    covers = [[M, day, "ahf", "groups", None] for day in range(today - 13, today + 1)]
    return rows, covers


def book(key: int, rng: random.Random) -> list[tuple[int, int, int, int]]:
    """One item's order book in a scan: a few price levels around its level.

    Args:
        key: The item.
        rng: The seeded generator.

    Returns:
        (price, stack, band, count) rows; Wool has one dumped stack at 30% of its level; Silk's cheapest
        listing is 3% above its level (posting now is close to its early Saturday window).
    """
    level, _, listed, _, _ = MARKET[key]
    stack = 1 if key == GLOVES else min(20, ITEMS[key][5])
    rows = []
    low = 1.03 if key == SILK else 0.99
    for step, share in ((low, 0.2), (low + 0.03, 0.3), (low + 0.07, 0.3), (low + 0.13, 0.2)):
        count = max(1, math.floor(listed * share / stack + 0.5))
        rows.append((math.floor(level * step + 0.5), stack, rng.choice([2, 3, 4]), count))
    if key == WOOL:
        rows.append((300, 20, 4, 1))          # a dumped stack: buy it and relist (flip-now)
    if key == GLOVES:
        rows = [(800, 1, 3, 1), (1600, 1, 4, 1)]     # cheap enough to disenchant (2.5 Strange Dust ≈ 1425c)
    return sorted(rows)


def scans(rng: random.Random) -> tuple[list[list], list[list], list[list]]:
    """Three complete local scans, 49, 25 and 1 hour ago, and the `current` rows they and AHF give.

    Args:
        rng: The seeded generator.

    Returns:
        (scans rows, book_entries rows, current rows).
    """
    scan_rows, entries, current = [], [], []
    for sid, hours_ago in ((1, 49), (2, 25), (3, 1)):
        at = NOW - hours_ago * HOUR
        scan_rows.append([sid, f"{AMAYA}:{at}:1", M, "local", AMAYA, at, "full", 1.0])
        for key in sorted(MARKET):
            rows = book(key, rng)
            entries.extend([sid, key, price, stack, band, count] for price, stack, band, count in rows)
            if sid == 3:
                cheapest = rows[0]
                units = sum(s * c for p, s, _, c in rows if p == cheapest[0])
                current.append([M, key, cheapest[0], units, at, "local"])
                current.append([M, key, cheapest[0], units, NOW - 2 * HOUR, "ahf"])
    return scan_rows, entries, current


def characters() -> dict[str, list[list]]:
    """The two characters: sheets, professions, recipes, holdings, one own auction.

    Returns:
        Table name -> rows.
    """
    seen = NOW - 2 * HOUR
    return {
        "characters": [[AMAYA, "Amaya", "Grommash", "Horde", M, "Mage", 28, 250_000, seen],
                       [BRUGAN, "Brugan", "Grommash", "Horde", M, "Warrior", 30, 400_000, seen]],
        "professions": [[AMAYA, "Enchanting", 142, 150], [AMAYA, "Tailoring", 95, 150],
                        [BRUGAN, "Blacksmithing", 112, 150], [BRUGAN, "Mining", 120, 150]],
        "recipes": [["Blacksmithing", 7818, ROD, 1, 1, 1, None], ["Enchanting", 7418, None, 1, 1, 1, None],
                    ["Mining", 2657, BAR, 1, 1, 1, None]],
        "reagents": [["Blacksmithing", 7818, BAR, 1], ["Enchanting", 7418, DUST, 1], ["Mining", 2657, ORE, 1]],
        "known_recipes": [[AMAYA, 7418], [BRUGAN, 2657], [BRUGAN, 7818]],
        "inventory": [[AMAYA, "bags", GLOVES, 1, 0], [AMAYA, "bags", LEATHER, 30, 0], [AMAYA, "bags", LINEN, 120, 0],
                      [AMAYA, "bags", SILK, 40, 0], [AMAYA, "bank", LINEN, 80, 0], [BRUGAN, "bags", BAR, 4, 0],
                      [BRUGAN, "bags", ORE, 20, 0], [BRUGAN, "bags", STONE, 40, 0]],
        "snapshots": [[c, place, seen] for c in (AMAYA, BRUGAN) for place in ("auctions", "bags", "bank", "mail")],
        "own_auctions": [[BRUGAN, STONE, 30, 20, 3, 1, seen, NOW + 10 * HOUR]],
    }


COLUMNS = {
    "items": ["key", "item_id", "name", "quality", "ilvl", "class", "vendor_buy", "vendor_sell", "stack"],
    "candles_h1": ["market", "key", "t", "open", "high", "low", "close", "qty", "src"],
    "volume_d1": ["market", "key", "day", "listed", "sold", "expired", "src", "conf", "method"],
    "volume_cover": ["market", "day", "src", "kind", "covered"],
    "scans": ["sid", "scan_id", "market", "src", "char_id", "at", "kind", "coverage"],
    "book_entries": ["sid", "key", "price", "stack", "band", "count"],
    "current": ["market", "key", "price", "qty", "at", "src"],
    "characters": ["id", "name", "realm", "faction", "market", "class", "level", "gold", "updated"],
    "professions": ["char_id", "profession", "skill", "max_skill"],
    "recipes": ["profession", "spell_id", "product_key", "yield_min", "yield_max", "skill_req", "cooldown_s"],
    "reagents": ["profession", "spell_id", "key", "count"],
    "known_recipes": ["char_id", "spell_id"],
    "inventory": ["char_id", "location", "key", "qty", "bound"],
    "snapshots": ["char_id", "location", "at"],
    "own_auctions": ["char_id", "key", "price", "stack", "band", "count", "seen", "end_ms"],
}


def world() -> dict:
    """The whole world document.

    Returns:
        {"about", "now_ms", "config", "tables": {name: {"columns", "rows"}}}, deterministic.
    """
    rng = random.Random(SEED)
    tables = {"items": [[k, k, n, q, il, c, None, vs, st] for k, (n, q, il, c, vs, st) in sorted(ITEMS.items())],
              "candles_h1": candles(rng)}
    tables["volume_d1"], tables["volume_cover"] = volume(rng)
    tables["scans"], tables["book_entries"], tables["current"] = scans(rng)
    tables.update(characters())
    return {
        "about": "Whole-plan snapshot world (SPEC §8.6, WP1); generated by tools/gen_world.py, seed "
                 f"{SEED}. Do not edit by hand.",
        "now_ms": NOW,
        "config": {"markets": [M]},
        "tables": {name: {"columns": COLUMNS[name], "rows": sorted(rows, key=json.dumps)}
                   for name, rows in sorted(tables.items())},
    }


def main() -> int:
    """Write w1.json.

    Returns:
        0.
    """
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(world(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    OUT.write_text(text.replace('],[', '],\n[') + "\n", encoding="utf-8", newline="\n")
    print(f"gen_world: wrote {OUT.relative_to(ROOT)} ({len(text.encode('utf-8')) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
