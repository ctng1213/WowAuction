"""The mockups' portfolio world as the addon files export it (SPEC §3.4D, §3.5; EX17, EX18).

Monday 10:38, Amaya 21 g and Brugan 10 g 40 s in hand, 4 g 18 s committed in six positions, risk tolerance 30%:
the 16 candidates of the dashboard mockup (12 funded, 4 excluded), the copper chain's recipes, and the positions
of the Portfolio page's section A, replayed from their lots and events by engine.positions and evaluated by
engine.position_views (M14), so the committed capital comes from positions. Shared by the Python export tests and tools/gen_lua_fixtures.py, so the Lua
decoders read exactly what AHSync writes, and by the Portfolio page's tests (D72, D73, D77-D79: page_inputs), so the
page shows the mockup's numbers.
"""
from __future__ import annotations

from ahsync.engine import selection as sel
from ahsync.engine.allocation import Alternative, PortfolioPlan
from ahsync.engine.graph import Graph, build, graph_recipe
from ahsync.engine.plan import PlanAction
from ahsync.engine.position_views import (Clock, MarketNow, Outlook, PositionView, SellStep, evaluate,
                                          found_rows)
from ahsync.engine.positions import Lot, Move, Position, Seed, World, committed, track
from ahsync.engine.plan_views import ItemLook
from ahsync.engine.portfolio import (Candidate, CharacterGold, OpenPosition, PortfolioSettings, Requirements,
                                     accounting, export_subset)
from ahsync.engine.portfolio_view import PortfolioInputs
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


# name: (strategy, key, character, gold needed, expected net, sale ms, eligibility failing a static check or None,
#        inputs, needs)
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
     Eligibility("Enchanting", 150, 142, (AMAYA, 142)), ((BRACERS, 1),), ("Enchanting", 150)),
    ("Elixir of Fortitude", "buy_later", 3825, AMAYA, 12000, 900, TONIGHT,
     Eligibility(sold_per_day=3.0, planned_units=40), (), None),
    ("Mithril Spurs", "flip", 7969, BRUGAN, 30000, 7342, at(5, 18), None, (), None),
    ("Thick Leather", "buy_later", 4304, AMAYA, 16080, 5500, at(3, 19), None, (), None),
    ("Transmute: Iron to Gold", "craft", 3577, BRUGAN, 4200, 1855, at(3, 19), None, ((3575, 1),), ("Alchemy", 225)),
    ("Runed Copper Rod", "multistage", RUNED, AMAYA, 3700, 1630, at(6, 18), None, ((ROD, 1), (DUST, 1), (ESSENCE, 1)),
     ("Enchanting", 1)),
    ("Silk Cloth", "buy_later", 4306, AMAYA, 2800, 730, at(5, 19), None, (), None),
    ("Bolt of Silk Cloth", "craft", 4305, AMAYA, 560, 194, at(6, 19), None, ((4306, 4),), ("Tailoring", 125)),
    ("Heavy Grinding Stone", "craft", 3486, BRUGAN, 0, 134, at(5, 19), None, ((2838, 3),), ("Blacksmithing", 125)),
    ("Greater Magic Essence", "buy_later", 11082, AMAYA, 3600, 1200, at(6, 19),
     Eligibility(untrusted_weeks=1), (), None),
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
                                        PS.immediate_hours), c.expected_net, row[0],
                        sel.static_reason(row[7], PS.horizon_days) if row[7] else None))
    return out


def chars() -> list[CharacterGold]:
    """Amaya's 21 g and Brugan's 10 g 40 s.

    Returns:
        The characters' gold.
    """
    return [CharacterGold(AMAYA, 210_000), CharacterGold(BRUGAN, 104_000)]


# The positions of section A (SPEC §3.4D.9 table, D73): (key, character, strategy, product, units, unit cost, expected
# net at open, original window start or None, opened at). The engine replays their events (moves()) against the
# snapshots (world()) and evaluates them against the plan's actions and the market (outlooks()).
WOOL, MAGEWEAVE, COIF, ELIXIR, LEATHER = 2592, 4338, 7920, 2457, 2319
IRON_ORE, IRON_BAR = 2772, 3575
_SEEDS = (
    (COIF, BRUGAN, "flip", COIF, 1, 30_000, 4500.0, None, NOW - 30 * HOUR),
    (BAR, BRUGAN, "buy_later", BAR, 6, 500, 3157.0, at(5, 19), NOW - 29 * HOUR),
    (ELIXIR, BRUGAN, "buy_later", ELIXIR, 2, 700, 800.0, at(2, 20), NOW - 28 * HOUR),
    (MAGEWEAVE, AMAYA, "buy_later", MAGEWEAVE, 25, 120, 600.0, at(8, 20), NOW - 27 * HOUR),
    (IRON_ORE, BRUGAN, "craft", IRON_BAR, 10, 40, 150.0, at(3, 19), NOW - 26 * HOUR),
    (LEATHER, AMAYA, "buy_later", LEATHER, 20, 200, 0.0, None, NOW - 25 * HOUR),
)


def seeds() -> list[Seed]:
    """The six durable positions' opening facts: 4 g 18 s paid in all.

    Returns:
        Seeds, one lot each (its id is the position id: the opening purchase's event id).
    """
    out = []
    for key, char, strategy, product, units, cost, net, window, opened in _SEEDS:
        event = f"{char}:{opened}:1"
        out.append(Seed(event, char, M, f"{strategy}:{product}:{char}:-/now/now:1", key, opened,
                        (Lot(event, units, cost, opened),), net, window))
    return out


def moves() -> list[Move]:
    """The events after the purchases: the coif and the Mageweave posted, 15 Mageweave sold (not paid out yet).

    Returns:
        Moves.
    """
    coif, mw_sold, mw_left = f"{BRUGAN}:post:1", f"{AMAYA}:post:1", f"{AMAYA}:post:2"
    return [Move(NOW - 20 * HOUR, coif, "post", BRUGAN, COIF, 1, coif, deposit=300),
            Move(NOW - 20 * HOUR, mw_sold, "post", AMAYA, MAGEWEAVE, 15, mw_sold, deposit=30),
            Move(NOW - 20 * HOUR, mw_left, "post", AMAYA, MAGEWEAVE, 10, mw_left, deposit=20),
            Move(NOW - 2 * HOUR, f"{AMAYA}:mail:1", "sold", AMAYA, MAGEWEAVE, 15, mw_sold, f"{AMAYA}:mail:1", 180)]


def world() -> World:
    """The newest snapshots (10:00): Amaya's bags show 14 of her 20 Medium Leather and her 20 found Wool Cloth.

    Returns:
        The World.
    """
    holdings = {(BRUGAN, BAR): 6, (BRUGAN, ELIXIR): 2, (BRUGAN, IRON_ORE): 10, (AMAYA, LEATHER): 14, (AMAYA, WOOL): 20}
    snaps = {(c, place): MON + 10 * HOUR for c in (AMAYA, BRUGAN) for place in ("bags", "bank", "mail")}
    return World(holdings, snaps)


def mockup_positions() -> list[Position]:
    """The six positions as their events leave them (4 g 18 s committed).

    Returns:
        engine.positions.track's records, oldest first.
    """
    return track(seeds(), moves(), world(), NOW)


def _step(kind: str, char: str, key: int, qty: int, at_ms: int, window: int | None, expected: float,
          net: float) -> SellStep:
    """A plan action of the mockup's sell plan.

    Args:
        kind: post | hold | craft.
        char: Its character.
        key: Its item.
        qty: Units.
        at_ms: When to act.
        window: Its selling window's start (3 h long, its peak at the start), or None.
        expected: Expected value, copper.
        net: Expected net, copper.

    Returns:
        The SellStep.
    """
    return SellStep(kind, char, key, qty, at_ms, window, None if window is None else window + 3 * HOUR, window,
                    expected, net)


STEPS = (
    _step("post", BRUGAN, BAR, 6, at(5, 11), at(5, 19), 6157.0, 3157.0),
    _step("post", BRUGAN, ELIXIR, 2, at(2, 12), at(2, 20), 1000.0, 800.0),
    _step("hold", AMAYA, WOOL, 20, NOW, at(6, 19), 472.0, 472.0),
    _step("craft", BRUGAN, IRON_BAR, 10, NOW + HOUR, None, 0.0, 0.0),
    _step("post", BRUGAN, IRON_BAR, 10, at(3, 11), at(3, 19), 550.0, 150.0),
)
# The market now: the elixir's price spikes to 4 s 50 c over its 3 s 26 c median; another seller lists a coif at 4 g 50 s.
MARKETS = {BAR: MarketNow(600.0, 650, 600), ELIXIR: MarketNow(450.0, 450, 326),
           COIF: MarketNow(42_750.0, 45_000, 45_000, ((45_000, 2),))}
CLOCK = Clock(NOW)


def outlook(p: Position) -> Outlook:
    """What the plan and the market say about one mockup position.

    Args:
        p: The position.

    Returns:
        Its Outlook (the ore: smelt today, then the bars' Thursday post).
    """
    own = tuple(s for s in STEPS if (s.char_id, s.key) == (p.char_id, p.key))
    mine = ((45_000, 1),) if p.key == COIF else ()
    if p.key == IRON_ORE:
        return Outlook(own, 1.0, None, mine, "smelt", NOW + HOUR, STEPS[4])
    return Outlook(own, 1.0, MARKETS.get(p.key), mine)


def positions(found: bool = False) -> list[PositionView]:
    """The mockup's positions evaluated, as section A and PortfolioPlan.lua show them.

    Args:
        found: Add Amaya's found Wool Cloth, held for Sunday (section A shows it; the EX18 world leaves it out).

    Returns:
        The six positions oldest first (Mithril Coif, Copper Bar, Elixir of Agility, Mageweave Cloth, Iron Ore,
        Medium Leather), then the found row.
    """
    rows = [evaluate(p, outlook(p), CLOCK) for p in mockup_positions()]
    if found:
        covered = {(p.char_id, p.key) for p in mockup_positions()}
        steps = {(s.char_id, s.key): [s] for s in STEPS}
        rows += found_rows([(AMAYA, WOOL, 20)], {}, steps, covered, CLOCK)
    return rows


def mockup_committed() -> list[OpenPosition]:
    """The committed capital of the mockup world: the open positions' cost left (4 g 18 s).

    Returns:
        engine.positions.committed of mockup_positions().
    """
    return committed(mockup_positions())


def selection() -> tuple[list[Candidate], sel.Selection, dict[str, float]]:
    """Select the mockup's pool within its budget (PF6).

    Returns:
        (the candidates, the validated selection, candidate id -> adjusted profit).
    """
    cands = candidates()
    ps = picks(cands)
    acct = accounting(chars(), mockup_committed(), PS.risk_tolerance)
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
    acct = accounting(chars(), mockup_committed(), PS.risk_tolerance)
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


def page_plan(gold: list[CharacterGold] | None = None, committed: int | None = None, risk: float = 0.30,
              paying_only: bool = False) -> PortfolioPlan:
    """The mockup's plan run as the page reads it: picks, the funded rows, the alternatives with their facts.

    Args:
        gold: The characters' gold; chars() when None.
        committed: Committed capital, copper; the mockup positions' (4 g 18 s) when None.
        risk: Risk tolerance.
        paying_only: Leave out the rows that need no gold (PF14's world, where nothing is funded).

    Returns:
        The PortfolioPlan; its one action is Copper Rod's post at 41 s each (the row's Each).
    """
    cands = candidates()
    facts = {c.cand_id: row[7] for c, row in zip(cands, _ROWS)}
    ps = [p for p in picks(cands) if p.gold > 0 or not paying_only]
    held = mockup_committed() if committed is None else [OpenPosition("p", BRUGAN, committed)]
    acct = accounting(gold or chars(), held, risk)
    budget = sel.Budget(acct.capacity_left, acct.spendable)
    result = sel.select(ps, (), budget)
    sel.validate(ps, (), budget, result)
    by_id, pick_of = {c.cand_id: c for c in cands}, {p.cand_id: p for p in ps}
    alternatives = tuple(Alternative(by_id[i], pick_of[i].lst, reason, pick_of[i].adjusted, facts[i])
                         for i, reason in result.reasons)
    rod = cands[0].cand_id
    actions = [PlanAction("post", BRUGAN, ROD, 5, 4100, NOW, None, None, None, 0.58, 6306.0, 6306.0)]
    spans = ((pick_of[rod].opp, 0, 1),) if rod in result.funded else ()
    return PortfolioPlan(actions, tuple(by_id[i] for i in result.funded), alternatives, result, acct,
                         tuple((i, pick_of[i].adjusted) for i in result.funded), tuple(ps), spans)


def page_inputs(plan: PortfolioPlan | None = None, gold: list[CharacterGold] | None = None,
                risk: float = 0.30) -> PortfolioInputs:
    """The Portfolio page's inputs in the mockup world.

    Args:
        plan: The plan run; page_plan() when None.
        gold: The characters' gold; chars() when None.
        risk: Risk tolerance the plan ran with.

    Returns:
        PortfolioInputs at Mon 10:38 (UTC, offset 0) with section A's six positions and the found Wool Cloth.
    """
    names = {ROD: "Copper Rod", BAR: "Copper Bar", COIF: "Mithril Coif", ELIXIR: "Elixir of Agility",
             WOOL: "Wool Cloth", MAGEWEAVE: "Mageweave Cloth", IRON_ORE: "Iron Ore", LEATHER: "Medium Leather"}
    looks = {key: ItemLook(name, 1) for key, name in names.items()}
    return PortfolioInputs(plan if plan is not None else page_plan(), tuple(gold or chars()), tuple(positions(True)),
                           ("act:rod:1",), looks, PortfolioSettings(risk_tolerance=risk), NOW)
