"""Tests for ahsync/engine/planned_crafts.py: the plan's craft chains on the Craft pages (D56)."""
from __future__ import annotations

from ahsync.engine.plan import PlanAction
from ahsync.engine.planned_crafts import chain_lines, craft_chains

SAT10, SAT18, SUN23 = 1760176800000, 1760205600000, 1760313360000   # Sat 11 Oct 2025 10:00 and 18:00, Sun 23:56 UTC
COPPER_ORE, TIN_ORE, COPPER_BAR, TIN_BAR, BRONZE = 2770, 2771, 2840, 3576, 2841
NAMES = {COPPER_ORE: "Copper Ore", TIN_ORE: "Tin Ore", COPPER_BAR: "Copper Bar", TIN_BAR: "Tin Bar", BRONZE: "Bronze Bar"}


def act(kind: str, key: int, qty: int, chain: str, at_ms: int = SAT10, price: int | None = None, net: float = 0.0,
        peak_ms: int | None = None, source: str | None = None) -> PlanAction:
    """A plan action for these tests.

    Args:
        kind: Action kind.
        key: Item key.
        qty: Units.
        chain: Its chain.
        at_ms: When, UTC ms.
        price: Each, copper.
        net: Expected net, copper.
        peak_ms: Posts: the window's best hour.
        source: Buys: "vendor" or "ah".

    Returns:
        A PlanAction for Amaya.
    """
    return PlanAction(kind, "Amaya-G", key, qty, price, at_ms, None, None, peak_ms, None, 0.0, net, chain=chain,
                      source=source)


BRONZE_CHAIN = [act("buy", COPPER_ORE, 22, "craft:2659", price=231, source="ah"),
                act("buy", TIN_ORE, 22, "craft:2659", price=275, source="ah"),
                act("craft", COPPER_BAR, 22, "craft:2659"), act("craft", TIN_BAR, 22, "craft:2659"),
                act("craft", BRONZE, 44, "craft:2659", at_ms=SAT10 + 60_000, net=15000 - 5082 - 6050),
                act("post", BRONZE, 44, "craft:2659", at_ms=SUN23, price=400, net=15000, peak_ms=SAT18 + 3_600_000)]
TIN_CHAIN = [act("buy", TIN_ORE, 40, "craft:3304", price=279, source="ah"),
             act("craft", TIN_BAR, 40, "craft:3304", net=14000 - 11160),
             act("post", TIN_BAR, 40, "craft:3304", at_ms=SAT10 + 3_600_000, price=400, net=14000)]
FLIP = [act("buy", 2589, 20, "flip:2589", price=40, net=-800), act("post", 2589, 20, "flip:2589", price=55, net=1010)]


def test_D56_the_plans_craft_chains_are_one_row_each_for_their_final_product_best_net_first():
    bronze, tin = craft_chains(TIN_CHAIN + FLIP + BRONZE_CHAIN)
    assert (bronze.final.key, bronze.net, bronze.mat_gold) == (BRONZE, 15000 - 5082 - 6050, 22 * 231 + 22 * 275)
    assert (tin.final.key, tin.net, tin.mat_gold) == (TIN_BAR, 14000 - 11160, 40 * 279)   # 2840 < 3868: second
    assert chain_lines(bronze, NAMES, 0) == (
        "buy 22 Copper Ore on the AH · 50s 82c", "buy 22 Tin Ore on the AH · 60s 50c",
        "craft 22 Copper Bar first", "craft 22 Tin Bar first", "post Sun 23:56, for the 19:00 peak")
    assert chain_lines(tin, NAMES, 0)[-1] == "post Sat 11:00"


def test_D56_a_chain_sold_another_way_says_so():
    vendor = [act("buy", 4470, 7, "craft:6413", price=38, source="vendor"), act("craft", 4471, 7, "craft:6413"),
              act("vendor", 4471, 7, "craft:6413", price=25, net=10)]
    held = [act("craft", 4471, 1, "craft:1"), act("hold", 4471, 1, "craft:1")]
    de = [act("craft", 4471, 1, "craft:2"), act("de", 4471, 1, "craft:2")]
    unsold = [act("craft", 4471, 1, "craft:3")]
    lines = [chain_lines(c, {4470: "Simple Wood"}, 0) for c in craft_chains(vendor + held + de + unsold)]
    assert lines == [("buy 7 Simple Wood from a vendor · 2s 66c", "sell it to a merchant at 25c each"),
                     ("hold for a later week",), ("disenchant it",), ("post it at the AH",)]


def test_D59_a_chains_profit_is_counted_once_on_the_craft_page():
    rod = [act("buy", 2840, 2, "craft:2663", price=500, source="ah"),
           act("craft", 6217, 1, "craft:2663", net=971),                         # carries the chain's profit
           act("post", 6217, 1, "craft:2663", price=4099, net=971)]              # its own net, for later views
    [chain] = craft_chains(rod)
    assert chain.net == 971                                                      # +9s 71c, as on Today; never 19s 42c
    no_step = [act("craft", 6217, 1, "craft:1"), act("post", 6217, 1, "craft:1", price=4099, net=971)]
    assert craft_chains(no_step)[0].net == 971                                   # no profit step: the post's own net
