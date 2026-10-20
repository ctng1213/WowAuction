"""The portfolio's strings in the addon files (SPEC §3.5, §3.4D.10, M11): candidates, the graph, the snapshot.

In MarketIntelligence.lua, per market (inside its checksum):

    K[n]    = "cid;strategy;charId;outKey;outQty;inputs;gold;cashBasis;net;chance1000;saleMs;windowEndMs;conf;needs;status"
    R[spell] = "product:yield1000:key*count/key*count"      -- a recipe your characters know
    U[key]   = "spell,spell"                                 -- the recipes that use an item (cut loop edges left out)
    G.DE     = "key,key"                                     -- exported items one could disenchant
    G.order  = "key,key"                                     -- every graph item, inputs before what they make

In PortfolioPlan.lua (inside its checksum):

    portfolio = { [market] = "snapshotMs;riskTolerance1000;premiumBase1000;premiumPerDay1000;immediateHours;
                              todayLimit;weekLimit;horizonDays;configVersion;capitalBase;committed;capacity;allocated" }
    gold      = { [charId] = "copper,atMs" }
    reserved  = { [charId] = copper }
    positions = { [charId] = { "positionId;key;state;units;unitsLeft;costLeft;netNow;targetMs;nextAction", ... } }

An empty field is unavailable. Money is copper; expected values (net, netNow) are rounded half up.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from ahsync.engine.graph import Graph
from ahsync.engine.portfolio import Candidate, input_sources
from ahsync.engine.selection import reason_code
from ahsync.engine.views import round_half_up


def _field(value: object) -> str:
    """One field; "" when unavailable.

    Args:
        value: The value, or None.

    Returns:
        Its text.
    """
    return "" if value is None else str(value)


def candidate_text(c: Candidate, status: str) -> str:
    """One exported candidate (SPEC §3.5 `K`, EX17).

    Args:
        c: The candidate.
        status: "" when funded, else its D.6 status text.

    Returns:
        "cid;strategy;charId;outKey;outQty;inputs;gold;cashBasis;net;chance1000;saleMs;windowEndMs;conf;needs;status":
        inputs "key:count:source" joined by "/" (portfolio.input_sources), gold its gold needed and cashBasis the
        cost basis of bought stock it uses (copper), net rounded half up, chance in thousandths, needs
        "Profession:skill" or "", status "" when funded, else its reason code (selection.reason_code).
    """
    inputs = "/".join(f"{key}:{units}:{source}" for key, units, source in input_sources(c))
    needs = f"{c.needs.profession}:{c.needs.skill}" if c.needs.profession else ""
    return ";".join((c.cand_id, c.strategy, c.char_id, str(c.key), str(c.out_qty), inputs, str(c.gold_needed),
                     str(c.cost_basis), str(round_half_up(c.expected_net)), str(round_half_up(c.chance * 1000)),
                     _field(c.sale_ms), _field(c.window_end_ms), str(c.conf), needs, reason_code(status)))


def graph_strings(g: Graph) -> tuple[dict[int, str], dict[int, str], str, str]:
    """The graph's strings (SPEC §3.5 `G`, EX18).

    Args:
        g: The market's graph.

    Returns:
        (R: spell -> "product:yield1000:key*count/…", U: key -> "spell,…", DE "key,…", order "key,…").
    """
    recipes = {r.spell: f"{r.product}:{r.yield1000}:" + "/".join(f"{k}*{n}" for k, n in r.reagents) for r in g.recipes}
    uses = {key: ",".join(str(s) for s in spells) for key, spells in g.uses}
    return recipes, uses, ",".join(str(k) for k in g.disenchant), ",".join(str(k) for k in g.order)


def portfolio_text(snapshot: Mapping) -> str:
    """A market's portfolio snapshot (SPEC §3.5 `portfolio`, EX18).

    Args:
        snapshot: recommend.portfolio.snapshot_data as stored.

    Returns:
        "snapshotMs;riskTolerance1000;premiumBase1000;premiumPerDay1000;immediateHours;todayLimit;weekLimit;
        horizonDays;configVersion;capitalBase;committed;capacity;allocated".
    """
    fields = [snapshot["snapshot_ms"], *snapshot["settings"], snapshot["config_version"], snapshot["capital_base"],
              snapshot["committed"], snapshot["capacity"], snapshot["allocated"]]
    return ";".join(str(v) for v in fields)


def position_text(row: Sequence) -> str:
    """One position (SPEC §3.5 `positions`, EX18).

    Args:
        row: A snapshot_data position: (position_id, character, key, state, units, units left, cost left, net now
            float copper or None, target ms or None, next action or None).

    Returns:
        "positionId;key;state;units;unitsLeft;costLeft;netNow;targetMs;nextAction", netNow rounded half up.
    """
    position_id, _char, key, state, units, left, cost, net, target, action = row
    return ";".join((position_id, str(key), state, str(units), str(left), str(cost),
                     "" if net is None else str(round_half_up(net)), _field(target), _field(action)))


def k_line_bytes(position: int, text: str) -> int:
    """The bytes one candidate's line takes in MarketIntelligence.lua: `K[n]="…"` and its line break.

    Args:
        position: Its 0-based position in the market's K.
        text: Its string (it holds no quote or backslash, so it is written as it is).

    Returns:
        Bytes.
    """
    return len(text.encode("utf-8")) + len(str(position + 1)) + 7


def trim_candidates(markets: Mapping[str, Sequence[tuple[str, str, float]]], over_bytes: int) -> dict[str, list[str]]:
    """The candidates kept so a market file fits its budget (SPEC §3.5, FM40): the lowest alternatives go first.

    Args:
        markets: Market -> its exported candidates in export order, as (K string, status "" when funded, adjusted
            profit float copper).
        over_bytes: How many bytes the file is over its budget.

    Returns:
        Market -> the K strings kept, in export order: alternatives are dropped lowest adjusted profit first (ties:
        the later in export order first; across markets, by market id) until their lines add up to over_bytes, or
        none is left. A funded row is never dropped.
    """
    alternatives = sorted((adjusted, market, -pos, k_line_bytes(pos, text))
                          for market, rows in markets.items() for pos, (text, status, adjusted) in enumerate(rows)
                          if status)
    dropped: set[tuple[str, int]] = set()
    saved = 0
    for _adjusted, market, neg_pos, size in alternatives:
        if saved >= over_bytes:
            break
        dropped.add((market, -neg_pos))
        saved += size
    return {market: [text for pos, (text, _s, _a) in enumerate(rows) if (market, pos) not in dropped]
            for market, rows in markets.items()}
