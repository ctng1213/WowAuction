"""The Cooldowns scenarios' world (SPEC §3.4F, docs/SCENARIOS.md "Cooldowns"): six characters, the shipped catalog."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine import cooldowns
from ahsync.engine.cooldowns import Holder
from ahsync.engine.cooldowns_view import PriceFact, stage
from ahsync.recommend.cooldowns_page import CATALOG_PATH, read_catalog

NOW = 1_760_000_000_000
HOUR, DAY = 3_600_000, 86_400_000
STONE = 9149                                   # Philosopher's Stone

NAMES = {12360: "Arcanite Bar", 12359: "Thorium Bar", 12363: "Arcane Crystal", 14342: "Mooncloth", 14256: "Felcloth",
         15409: "Refined Deeprock Salt", 8150: "Deeprock Salt", 7080: "Essence of Water", 7076: "Essence of Earth",
         12808: "Essence of Undeath", 7082: "Essence of Air", 6037: "Truesilver Bar", 3860: "Mithril Bar",
         3577: "Gold Bar", 3575: "Iron Bar", 7077: "Heart of Fire", 7068: "Elemental Fire", STONE: "Philosopher's Stone",
         15846: "Salt Shaker", 7078: "Essence of Fire", 12803: "Living Essence", 17202: "Snowball",
         159: "Refreshing Spring Water", 17716: "SnowMaster 9000"}
PRICES = {12360: 450000, 12359: 12000, 12363: 280000, 14342: 220000, 14256: 35000, 15409: 45000, 8150: 1500,
          7080: 30000, 7076: 24000, 12808: 10000, 7082: 28000, 6037: 21000, 3860: 6000, 3577: 13000, 3575: 4000,
          7077: 15000}
SOLD = {12360: 6, 14342: 3, 15409: 4, 7080: 9, 7082: 5, 6037: 14, 3577: 11, 7068: 0, 12808: 2}


def catalog() -> list[cooldowns.CooldownRecipe]:
    """The shipped catalog, validated."""
    rows, _, _ = read_catalog(CATALOG_PATH)
    return cooldowns.catalog(rows)[0]


def prices(overrides: dict | None = None) -> dict[int, PriceFact]:
    """Every catalog item's PriceFact: the world's prices seen now, Elemental Fire's 9 days ago."""
    facts = {key: PriceFact(price, NOW, SOLD.get(key)) for key, price in PRICES.items()}
    facts[7068] = PriceFact(12000, NOW - 9 * DAY, 0)
    return {**facts, **(overrides or {})}


def holder(char_id, skills, known=(), bags=None, bank=None, ready=None) -> Holder:
    """A character: its name is the part before the realm."""
    return Holder(char_id, char_id.split("-")[0], skills, frozenset(known), bags or {}, bank or {}, ready or {}, NOW)


AMAYA = holder("Amaya-Grommash", {"Alchemy": 280, "Herbalism": 290}, (17187, 17563, 17562, 11480, 11479),
               bags={12359: 1, 12363: 1, STONE: 1}, bank={3860: 1}, ready={17187: (NOW - HOUR, NOW - 49 * HOUR)})
TESSA = holder("Tessa-Grommash", {"Alchemy": 300}, (17187, 17561, 17563, 25146), bags={STONE: 1, 7077: 1},
               ready={17187: (NOW + 26 * HOUR, NOW - 22 * HOUR), 25146: (NOW - HOUR, NOW - 2 * HOUR)})
SORVIN = holder("Sorvin-Grommash", {"Alchemy": 230}, (11479, 11480), bags={STONE: 1, 3575: 1},
                ready={11479: (NOW - HOUR, NOW - 25 * HOUR)})
BRUGAN = holder("Brugan-Grommash", {"Leatherworking": 262, "Skinning": 260}, bags={15846: 1, 8150: 1},
                ready={19566: (NOW + 28 * HOUR, NOW - 44 * HOUR)})
MIRA = holder("Mira-Grommash", {"Tailoring": 260, "Leatherworking": 255}, (18560,), bags={14256: 2},
              ready={18560: (NOW + 51 * HOUR, NOW - 45 * HOUR)})
KELDOR = holder("Keldor-Grommash", {"Tailoring": 245})
EVERYONE = [AMAYA, TESSA, SORVIN, BRUGAN, MIRA, KELDOR]


def base(holders=None, price_facts=None, recipes=None) -> dict:
    """The staged payload of the world (or a variant)."""
    return stage(recipes or catalog(), 1, EVERYONE if holders is None else holders,
                 prices() if price_facts is None else price_facts, NAMES, NOW)


def row(payload: dict, spell: int) -> dict:
    """One row of a staged or rendered payload, by spell."""
    return next(r for r in payload["rows"] if r["spell"] == spell)


def without(name: str, holders=None) -> list[Holder]:
    """The world's characters but one."""
    return [h for h in (holders or EVERYONE) if h.name != name]


def with_(h: Holder, **changes) -> Holder:
    """A character with some fields changed."""
    return replace(h, **changes)
