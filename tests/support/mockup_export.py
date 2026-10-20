"""The mockups' portfolio world as the addon files export it (SPEC §3.4D, §3.5; EX17, EX18).

Monday 10:38, Amaya 21 g and Brugan 10 g 40 s in hand, 4 g 18 s committed in six positions, risk tolerance 30%:
the 16 candidates of the dashboard mockup (12 funded, 4 excluded), the copper chain's recipes, and the positions
of the Portfolio page's section A. Shared by the Python export tests and tools/gen_lua_fixtures.py, so the Lua
decoders read exactly what AHSync writes.
"""
from __future__ import annotations

from ahsync.engine import selection as sel
from ahsync.engine.allocation import PortfolioPlan, PositionRow, position_rows
from ahsync.engine.graph import Graph, build, graph_recipe
from ahsync.engine.plan import PlanAction
from ahsync.engine.portfolio import (Candidate, CharacterGold, OpenPosition, PortfolioSettings, Requirements,
                                     accounting, export_subset)
from ahsync.engine.selection import Eligibility, Pick
from ahsync.engine.timing import adjusted_profit, immediate, wait_hundredths
from ahsync.export.market_lua import market_lua
from ahsync.export.plan_lua import PortfolioFields, plan_lua
from ahsync.export.portfolio_lua import candidate_text, portfolio_text, position_text
from ahsync.intel.contract import MarketIntelligence
from ahsync.recommend.portfolio import snapshot_data

M = "forever.pvp.horde.us"
AMAYA, BRUGAN = "Amaya-Grommash", "Brugan-Grommash"
HOUR, DAY = 3_600_000, 86_400_000
MON = 1_728_259_200_000             # Mon 2024-10-07 00:00 UTC
NOW = MON + 10 * HOUR + 38 * 60_000
TONIGHT = MON + 18 * HOUR
PS = PortfolioSettings()
ORE, BAR, ROD, RUNED, ESSENCE, DUST = 2770, 2840, 6217, 6218, 10938, 10940
BRACERS = 6187
COPPER_CHAIN = (graph_recipe(2657, BAR, 1, 1, [(ORE, 1)]), graph_recipe(7818, ROD, 1, 1, [(BAR, 1)]),
                graph_recipe(7421, RUNED, 1, 1, [(ROD, 1), (DUST, 1), (ESSENCE, 1)]))


def at(day: int, hour: int) -> int:
    """A sale time in the mockup's week.

    Args:
        day: Days after Monday.
        hour: Hour of that day, UTC.

    Returns:
        UTC ms.
    """
    return MON + day * DAY + hour * HOUR


# name: (strategy, key, character, gold needed, expected net, sale ms, static reason or None, inputs, needs)
_ROWS = (
    ("Copper Rod", "sell_stock", ROD, BRUGAN, 100, 6306, TONIGHT, None, ((ROD, 5),), None),
    ("Silver Rod", "craft", 6338, BRUGAN, 3000, 4000, TONIGHT, None, ((2842, 1), (1206, 2)), ("Blacksmithing", 100)),
    ("Scouting Gloves of the Monkey", "flip", 7005, AMAYA, 800, 625, TONIGHT, None, (), None),
    ("Embossed Leather Gloves", "craft", 4239, AMAYA, 500, 380, TONIGHT, None, ((2318, 3), (2320, 2)),
     ("Leatherworking", 55)),
    ("Linen Cloth", "flip", 2589, AMAYA, 760, 203, TONIGHT, None, (), None),
    ("Lesser Mana Potion", "craft", 3385, BRUGAN, 4500, 80, TONIGHT, None, ((785, 1), (3820, 1), (3371, 1)),
     ("Alchemy", 120)),
    ("Grunt's Bracers of Defense", "disenchant", BRACERS, AMAYA, 1500, 600, TONIGHT,
     sel.static_reason(Eligibility("Enchanting", 150, 142, (AMAYA, 142)), 7), ((BRACERS, 1),), ("Enchanting", 150)),
    ("Elixir of Fortitude", "buy_later", 3825, AMAYA, 12000, 900, TONIGHT,
     sel.static_reason(Eligibility(sold_per_day=3.0, planned_units=40), 7), (), None),
    ("Mithril Spurs", "flip", 7969, BRUGAN, 30000, 7342, at(5, 18), None, (), None),
    ("Thick Leather", "buy_later", 4304, AMAYA, 16080, 5500, at(3, 19), None, (), None),
    ("Transmute: Iron to Gold", "craft", 3577, BRUGAN, 4200, 1855, at(3, 19), None, ((3575, 1),), ("Alchemy", 225)),
    ("Runed Copper Rod", "multistage", RUNED, AMAYA, 3700, 1630, at(6, 18), None, ((ROD, 1), (DUST, 1), (ESSENCE, 1)),
     ("Enchanting", 1)),
    ("Silk Cloth", "buy_later", 4306, AMAYA, 2800, 730, at(5, 19), None, (), None),
    ("Bolt of Silk Cloth", "craft", 4305, AMAYA, 560, 194, at(6, 19), None, ((4306, 4),), ("Tailoring", 125)),
    ("Heavy Grinding Stone", "craft", 3486, BRUGAN, 0, 134, at(5, 19), None, ((2838, 3),), ("Blacksmithing", 125)),
    ("Greater Magic Essence", "buy_later", 11082, AMAYA, 3600, 1200, at(6, 19),
     sel.static_reason(Eligibility(untrusted_weeks=1), 7), (), None),
)
# A craft's reagent sources in its route (the multistage chain crafts its rod; the grinding stone uses found stone).
_ROUTES = {"Silver Rod": "1206a+2842v", "Embossed Leather Gloves": "2318o+2320v", "Lesser Mana Potion": "785a+3371v+3820a",
           "Transmute: Iron to Gold": "3575o", "Runed Copper Rod": "6217c+10938a+10940a", "Bolt of Silk Cloth": "4306o",
           "Heavy Grinding Stone": "2838o"}


def candidates() -> list[Candidate]:
    """The mockup's 16 candidates, one batch each.

    Returns:
        Candidates in the mockup's row order.
    """
    out = []
    for name, strategy, key, char, gold, net, sale, _reason, inputs, needs in _ROWS:
        buys = gold if strategy in ("flip", "buy_later", "disenchant") else 0
        route = f"{_ROUTES.get(name, '-')}/{'now' if buys else '-'}/{'now' if sale == TONIGHT else f'h{sale // HOUR}'}"
        out.append(Candidate(strategy, key, char, route, 1, 5 if strategy == "sell_stock" else 1, tuple(sorted(inputs)),
                             gold_buys=buys, gold_mats=gold - buys, cost_basis=5000 if name == "Copper Rod" else 0,
                             expected_net=float(net), chance=0.9, exec_qty=1, conf=80,
                             needs=Requirements(*needs) if needs else Requirements(), sale_ms=sale,
                             window_end_ms=None if sale == TONIGHT else sale + 3 * HOUR))
    return out


def picks(cands: list[Candidate]) -> list[Pick]:
    """The candidates as selection sees them, with the mockup's static reasons.

    Args:
        cands: From candidates().

    Returns:
        One Pick each.
    """
    out = []
    for c, row in zip(cands, _ROWS):
        lst = "today" if immediate(wait_hundredths(NOW, c.sale_ms), PS.immediate_hours) else "week"
        out.append(Pick(c.cand_id, c.cand_id, 1, c.char_id, lst, c.gold_needed,
                        adjusted_profit(c.expected_net, NOW, c.sale_ms, PS.premium_base, PS.premium_per_day,
                                        PS.immediate_hours), c.expected_net, row[0], row[7]))
    return out


def chars() -> list[CharacterGold]:
    """Amaya's 21 g and Brugan's 10 g 40 s.

    Returns:
        The characters' gold.
    """
    return [CharacterGold(AMAYA, 210_000), CharacterGold(BRUGAN, 104_000)]


# The positions of section A: (character, key, units, listed, units bought, copper paid).
_POSITIONS = ((BRUGAN, 7920, 1, True, 1, 30_000), (BRUGAN, BAR, 6, False, 6, 3000), (BRUGAN, 2457, 2, False, 2, 1400),
              (BRUGAN, 2772, 10, False, 10, 400), (AMAYA, 4338, 10, False, 10, 3000), (AMAYA, 2319, 20, False, 20, 4000))


def positions() -> list[PositionRow]:
    """The six positions (4 g 18 s committed) with the plan's actions for them.

    Returns:
        allocation.position_rows of the mockup's stock: the Mithril Coif listed, the rest held; posts for the Copper
        Bar (Sat 19:00 peak), the Elixir of Agility (now), the Mageweave Cloth (Tue 20:00) and the coif's relist.
    """
    held = [(c, k, n) for c, k, n, listed, _b, _p in _POSITIONS if not listed]
    listed = [(c, k, n) for c, k, n, on_ah, _b, _p in _POSITIONS if on_ah]
    cost = {k: (b, p) for _c, k, _n, _l, b, p in _POSITIONS}

    def post(char: str, key: int, qty: int, net: float, peak: int | None) -> PlanAction:
        """A sell-plan post."""
        return PlanAction("post", char, key, qty, 100, NOW if peak is None else peak - 8 * HOUR, peak, peak, peak, 0.9,
                          net, net)
    actions = [post(BRUGAN, BAR, 6, 3157.0, at(5, 19)), post(BRUGAN, 2457, 2, 800.0, None),
               post(AMAYA, 4338, 10, 600.0, at(8, 20)), post(BRUGAN, 7920, 1, 4500.0, None)]
    return position_rows(held, listed, cost, actions)


def selection() -> tuple[list[Candidate], sel.Selection, dict[str, float]]:
    """Select the mockup's pool within its budget (PF6).

    Returns:
        (the candidates, the validated selection, candidate id -> adjusted profit).
    """
    cands = candidates()
    ps = picks(cands)
    acct = accounting(chars(), [OpenPosition("p", BRUGAN, 41_800)], PS.risk_tolerance)
    budget = sel.Budget(acct.capacity_left, acct.spendable)
    result = sel.select(ps, (), budget)
    sel.validate(ps, (), budget, result)
    return cands, result, {p.cand_id: p.adjusted for p in ps}


def k_strings(limit: int = 150) -> list[str]:
    """The market file's K for the mockup world (EX17).

    Args:
        limit: candidate_export.

    Returns:
        The exported candidates' strings, funded first in funding order, then the alternatives by adjusted profit.
    """
    cands, result, adjusted = selection()
    status = dict(result.reasons)
    return [candidate_text(c, status.get(c.cand_id, "")) for c in export_subset(cands, result.funded, adjusted, limit)]


def graph() -> Graph:
    """The mockup world's graph: the copper chain, the bracers one could disenchant.

    Returns:
        The Graph.
    """
    return build(COPPER_CHAIN, [BRACERS])


def market_file(limit: int = 150) -> str:
    """MarketIntelligence.lua of the mockup world: no items, its K and G.

    Args:
        limit: candidate_export.

    Returns:
        The file's text.
    """
    intel = MarketIntelligence(M, NOW, NOW - HOUR, 1, {})
    return market_lua([intel], {}, {AMAYA: M, BRUGAN: M}, NOW, candidates={M: k_strings(limit)}, graphs={M: graph()})


def portfolio_fields() -> PortfolioFields:
    """PortfolioPlan.lua's portfolio part for the mockup world (EX18).

    Returns:
        The snapshot at 30%, both characters' gold (last seen at 10:00), their reservations and positions.
    """
    cands, result, _ = selection()
    acct = accounting(chars(), [OpenPosition(p.position_id, p.char_id, p.cost_left) for p in positions()],
                      PS.risk_tolerance)
    snap = snapshot_data(PortfolioPlan([], (), (), result, acct), PS, positions(), NOW)
    out = PortfolioFields({M: portfolio_text(snap)}, {c.char_id: f"{c.gold},{MON + 10 * HOUR}" for c in chars()},
                          dict(snap["reserved"]))
    for row in snap["positions"]:
        out.positions.setdefault(row[1], []).append(position_text(row))
    return out


def plan_file() -> str:
    """PortfolioPlan.lua of the mockup world: no actions, its portfolio part.

    Returns:
        The file's text.
    """
    return plan_lua([], [], {AMAYA: NOW, BRUGAN: NOW}, NOW, portfolio=portfolio_fields())
