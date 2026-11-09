"""The decision report's craft rows (SPEC §8.10, plan §1.12): steady craft deals and their decoys (SD10, SD11).

Our character knows Tailoring recipes; the plan's crafts are carried out (`trader.Trader.craft`, MS4b-2), so a craft
row scores what the plan funded and what its crafted units sold for. Each world is built by hand from the market
model's real-shaped commodities and gear (`inference.commodity`, `inference.gear`).
"""
from __future__ import annotations

from pathlib import Path

from sim.decision import GOLD, Line, our_buys, plan_run
from sim.inference import START_MS, commodity, gear
from sim.market import HOUR_MS, Planted, World
from sim.trader import OUR_PREFIX, CharacterSpec, Login, Recipe
from sim.truth import Truth

SILK, MAGE = 4306, 4338
BOLT, VEST = 4305, 10002                         # SD10's deals: a commodity craft and a crafted gear item
DECOYS = {20001: "a margin without sales", 20002: "sales without a margin", 20003: "a margin from one outlier ask",
          20004: "a stale mat price"}
STALE_MAT = 20010                                # 20004's reagent: listed cheap once on day 0, never again
FIRST_DAYS = 2                                   # plan §1.12: the deal is funded within 2 days of data
MARGIN_POINTS = 5                                # plan §1.12: realized margin within 5 points of the planned one
RECIPES = (Recipe(3839, "Tailoring", BOLT, ((SILK, 4),)), Recipe(12048, "Tailoring", VEST, ((MAGE, 6),)),
           Recipe(20101, "Tailoring", 20001, ((SILK, 2),)), Recipe(20102, "Tailoring", 20002, ((SILK, 4),)),
           Recipe(20103, "Tailoring", 20003, ((SILK, 2),)), Recipe(20104, "Tailoring", 20004, ((STALE_MAT, 2),)))
CRAFTER = (CharacterSpec("Amaya", 100 * GOLD, recipes=RECIPES),)


def deal_world(seed: int, days: int) -> World:
    """SD10/SD11's market: Silk and Mageweave as mats; a bolt of 4 Silk selling about 150 a day at about 25% over its
    mats, a vest of 6 Mageweave selling about 10 a day at about 30%; and SD11's four decoys beside them.

    Args:
        seed: The world's seed.
        days: Its length.

    Returns:
        The World.
    """
    items = (commodity(SILK, "Silk Cloth", 150, 200, 120, cancel_share=0.06, max_units=8),
             commodity(MAGE, "Mageweave Cloth", 400, 60, 40, cancel_share=0.06, max_units=6),
             commodity(BOLT, "Bolt of Silk Cloth", 800, 120, 50, cancel_share=0.06, max_units=4),
             gear(VEST, "Black Mageweave Vest", 3500, 30, 12),
             # the decoys vendor for 1 c: a vendor price above their mats would make crafting them a real deal
             commodity(20001, "Silk Banner", 2000, 0.0, 1, max_units=1, vendor_sell=1),     # asks 2000 c, no buyer
             commodity(20002, "Silk Rug", 500, 120, 60, max_units=4, vendor_sell=1),        # sells, under its 4 Silk
             commodity(20003, "Silk Pennant", 300, 30, 20, max_units=2, vendor_sell=1),     # one outlier ask at 3,000 c
             commodity(20004, "Spider Silk Sash", 1500, 30, 15, max_units=2, vendor_sell=1),
             commodity(STALE_MAT, "Ironweb Thread", 200, 0.0, 0.0))
    planted = (Planted(START_MS + HOUR_MS, 20003, 1, 3000, 24),
               Planted(START_MS + 2 * HOUR_MS, STALE_MAT, 4, 20, 2))
    return World(seed, START_MS, days, items, planted=planted)


def first_funded(logins: list[Login], key: int) -> int | None:
    """When the plan first funded a craft of an item.

    Args:
        logins: The session's logins.
        key: The product.

    Returns:
        The login's time, ms; None when it never did.
    """
    return next((l.at_ms for l in logins if any(a["kind"] == "craft" and a["key"] == key for a in l.actions)), None)


def planned_margin(login: Login, recipe: Recipe) -> float | None:
    """The margin a craft was planned at: its product's first planned post price after the cut, over its reagents
    at the cheapest price the plan saw.

    Args:
        login: The login that funded it.
        recipe: Its recipe.

    Returns:
        (post price × 0.95 − reagents' cost) ÷ reagents' cost; None without a planned post or a reagent's price.
    """
    post = next((a["price"] for a in login.actions if a["kind"] == "post" and a["key"] == recipe.product and a["price"]),
                None)
    if post is None or any(key not in login.prices for key, _ in recipe.reagents):
        return None
    cost = sum(login.prices[key] * n for key, n in recipe.reagents)
    return (post * 0.95 - cost) / cost


def realized_margin(truth: Truth, recipe: Recipe) -> float | None:
    """The margin our crafted units sold at: their mean sale after the cut over their reagents' mean cost.

    Args:
        truth: The run's truth log.
        recipe: The recipe.

    Returns:
        (mean sale price × 0.95 − mean reagents' cost) ÷ mean reagents' cost; None before a sale or a reagent buy.
    """
    seller = {e.auction: e.actor for e in truth.events if e.key == recipe.product and e.kind == "post"}
    sold = [e for e in truth.events if e.key == recipe.product and e.kind == "buy"
            and seller.get(e.auction, "").startswith(OUR_PREFIX)]
    units = sum(e.units for e in sold)
    cost = 0.0
    for key, n in recipe.reagents:
        bought = our_buys(truth, key)
        if not bought:
            return None
        cost += n * sum(e.units * e.price for e in bought) / sum(e.units for e in bought)
    if not units:
        return None
    return (sum(e.units * e.price for e in sold) / units * 0.95 - cost) / cost


def sd10(folder: Path, seed: int = 1, days: int = 5) -> list[Line]:
    """SD10: a commodity craft and a crafted gear item, each a steady deal; the plan should fund both within 2 days
    and sell what it makes near the margin it planned.

    Args:
        folder: An empty folder.
        seed: The world's seed.
        days: The world's length.

    Returns:
        Per deal: the hours from the first planning login to its first funded craft (≤ 48), and its realized margin
        against the planned one (within 5 points; "not exercised" before a sale).
    """
    plan = plan_run(deal_world(seed, days), folder, CRAFTER)
    planned_logins = plan.session.logins
    start = planned_logins[0].at_ms if planned_logins else None
    lines = []
    for recipe in RECIPES[:2]:
        name = plan.items[recipe.product].name
        at = first_funded(planned_logins, recipe.product)
        hours = None if at is None or start is None else (at - start) / HOUR_MS
        lines.append(Line("SD10", f"{name}: hours from the first plan to its first funded craft",
                          "never" if hours is None else f"{hours:.0f}", f"≤ {FIRST_DAYS * 24}",
                          hours is not None and hours <= FIRST_DAYS * 24))
        funded = next((l for l in planned_logins if l.at_ms == at), None)
        planned = planned_margin(funded, recipe) if funded else None
        realized = realized_margin(plan.truth, recipe)
        if planned is None or realized is None:
            lines.append(Line("SD10", f"{name}: margin realized; planned", "no sale or no planned price",
                              f"within {MARGIN_POINTS} points", None))
        else:
            lines.append(Line("SD10", f"{name}: margin realized; planned", f"{realized:.0%}; {planned:.0%}",
                              f"within {MARGIN_POINTS} points", abs(realized - planned) * 100 <= MARGIN_POINTS))
    return lines


def sd11(folder: Path, seed: int = 1, days: int = 5) -> list[Line]:
    """SD11: the decoys beside SD10's deals (a margin without sales, sales without a margin, a margin from one outlier
    ask, a stale mat price); none should ever be funded.

    Args:
        folder: An empty folder.
        seed: The world's seed.
        days: The world's length.

    Returns:
        Per decoy, the logins that funded a craft of it (0 is met), and the false positives in all.
    """
    plan = plan_run(deal_world(seed, days), folder, CRAFTER)
    lines, false = [], 0
    for key, what in sorted(DECOYS.items()):
        funded = sum(1 for l in plan.session.logins if any(a["kind"] == "craft" and a["key"] == key for a in l.actions))
        false += funded > 0
        lines.append(Line("SD11", f"{plan.items[key].name} ({what}): logins funding a craft of it", str(funded), "0",
                          funded == 0))
    lines.append(Line("SD11", "false positives (decoys funded at least once)", f"{false} of {len(DECOYS)}", "0",
                      false == 0))
    return lines


CRAFT_ROWS = {"SD10": sd10, "SD11": sd11}        # the report's craft rows (sim_report merges them with SD_ROWS)
