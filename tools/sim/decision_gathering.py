"""The decision report's gathering row (SPEC §8.10, plan §1.12): a monster drop worth farming (SD9).

Our character farms monster drops every afternoon and evening (`trader.Farm`, MS4b-3); the addon's gather events
reach AHSync, which builds the Gathering page from the farming sessions they make (§3.4E). The row reads that page
at each login and what became of the found stock.
"""
from __future__ import annotations

import json
from pathlib import Path

from sim.decision import GOLD, Line, plan_run
from sim.inference import START_MS, commodity
from sim.market import HOUR_MS, World
from sim.trader import OUR_PREFIX, CharacterSpec, Farm
from sim.truth import Truth

CATALOG = Path(__file__).resolve().parents[2] / "ahsync" / "data" / "gathering_catalog.json"
DEAL = 7068                                     # Elemental Fire: SD9's monster drop, about 100+ sold a day
DEAL_FAIR, DEAL_RATE = 900, 12                  # copper; units looted an hour
FILLER_RATES = (60, 40, 30, 25, 20, 15, 10)     # units an hour of the other drops our character farms
MARKET_ITEMS = 50                               # catalog monster drops on the market (plan §1.12: "about 50 items")
TOP = 5                                         # plan §1.12: in Gathering's top 5 by gold an hour
FIRST_DAYS = 2                                  # plan §1.12: within 2 days of data
PACE = 0.75                                     # the found stock sells: at least this share of what was looted


def monster_drops() -> list[tuple[int, str]]:
    """The catalog's monster-only drops, by key.

    Returns:
        (key, name) of each catalog item whose only category is Monster.
    """
    rows = json.loads(CATALOG.read_text(encoding="utf-8"))
    rows = rows["items"] if isinstance(rows, dict) else rows
    return sorted((r["key"], r["name"]) for r in rows if r["categories"] == ["Monster"])


def drop_world(seed: int, days: int) -> tuple[World, Farm]:
    """SD9's market: MARKET_ITEMS of the catalog's monster drops, Elemental Fire busy and dear among them, and the
    farm our character runs twice a day (Elemental Fire and seven others).

    Args:
        seed: The world's seed.
        days: Its length.

    Returns:
        (the World, the farm).
    """
    drops = [d for d in monster_drops() if d[0] != DEAL][:MARKET_ITEMS - 1]
    items = [commodity(DEAL, "Elemental Fire", DEAL_FAIR, 120, 50, cancel_share=0.06, max_units=3)]
    items += [commodity(key, name, 40 + key * 37 % 900, 6, 6, max_units=5) for key, name in drops]
    loot = ((DEAL, DEAL_RATE),) + tuple((key, rate) for (key, _), rate in zip(drops, FILLER_RATES))
    return World(seed, START_MS, days, tuple(items)), Farm(14, 40, loot)


def our_sales(truth: Truth, key: int) -> int:
    """Units of ours that others bought.

    Args:
        truth: The truth log.
        key: Item key.

    Returns:
        Units.
    """
    seller = {e.auction: e.actor for e in truth.events if e.key == key and e.kind == "post"}
    return sum(e.units for e in truth.events if e.key == key and e.kind == "buy"
               and seller.get(e.auction, "").startswith(OUR_PREFIX))


def sd9(folder: Path, seed: int = 1, days: int = 5) -> list[Line]:
    """SD9: a monster drop selling 100+ a day among about 50 items: the Gathering page should rank it in its top 5
    within 2 days, and the stock farmed should sell.

    Args:
        folder: An empty folder.
        seed: The world's seed.
        days: The world's length.

    Returns:
        The hours from the first login to the deal in Gathering's top 5 by net gold an hour (≤ 48), its rank then,
        and the share of its farmed units sold by the end, the last day's farming left out (≥ 75%).
    """
    world, farm = drop_world(seed, days)
    farmer = (CharacterSpec("Amaya", 10 * GOLD, farms=(farm, Farm(20, 40, farm.loot))),)
    plan = plan_run(world, folder, farmer)
    logins = plan.session.logins
    top = next((l for l in logins if DEAL in l.gathering[:TOP]), None)
    hours = None if top is None or not logins else (top.at_ms - logins[0].at_ms) / HOUR_MS
    rank = None if top is None else top.gathering.index(DEAL) + 1
    looted = 2 * DEAL_RATE * farm.minutes // 60 * (days - 1)              # the farms before the last day
    sold = our_sales(plan.truth, DEAL)
    return [Line("SD9", f"hours from the first login to Elemental Fire in Gathering's top {TOP}; its rank then",
                 "never" if hours is None else f"{hours:.0f}; {rank}", f"≤ {FIRST_DAYS * 24}",
                 hours is not None and hours <= FIRST_DAYS * 24),
            Line("SD9", "Elemental Fire farmed before the last day; sold by the end", f"{looted}; {sold}",
                 f"sold ≥ {PACE:.0%}", sold >= PACE * looted)]


GATHERING_ROWS = {"SD9": sd9}                   # the report's gathering row (sim_report merges it with SD_ROWS)
