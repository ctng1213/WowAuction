"""Tests for ahsync/export/portfolio_lua.py: the portfolio's strings in the addon files (SPEC §3.5, M11)."""
from __future__ import annotations

from dataclasses import replace

from ahsync.engine.portfolio import Candidate, Requirements, config_version
from ahsync.export import portfolio_lua
from ahsync.export.market_lua import market_lua
from ahsync.intel.contract import MarketIntelligence
from tests.support import mockup_export as mx

NAMES = {mx.ROD: "Copper Rod", 6338: "Silver Rod", 7005: "Scouting Gloves of the Monkey", 4239: "Embossed Leather Gloves",
         2589: "Linen Cloth", 3577: "Transmute: Iron to Gold", 6218: "Runed Copper Rod", 4304: "Thick Leather",
         4305: "Bolt of Silk Cloth", 4306: "Silk Cloth", 7969: "Mithril Spurs", 3486: "Heavy Grinding Stone",
         3825: "Elixir of Fortitude", 11082: "Greater Magic Essence", mx.BRACERS: "Grunt's Bracers of Defense",
         3385: "Lesser Mana Potion"}


def key_of(text: str) -> int:
    """A K string's output key.

    Args:
        text: A K string.

    Returns:
        Its 4th field.
    """
    return int(text.split(";")[3])


def test_EX17_the_candidate_universe_funded_first_in_funding_order_then_alternatives_by_adjusted_profit():
    cands, result, adjusted = mx.selection()
    k = mx.k_strings(150)
    assert len(k) == 16 and len(result.funded) == 12
    by_id = {c.cand_id: c for c in cands}
    assert [text.split(";")[0] for text in k[:12]] == list(result.funded)              # funding order
    assert [NAMES[key_of(t)] for t in k[12:]] == ["Elixir of Fortitude", "Greater Magic Essence",
                                                   "Grunt's Bracers of Defense", "Lesser Mana Potion"]
    # 9 s today, 12 s at Sun 19:00 (wait 6.35 d, premium 46.75%: 8 s 18 c), 6 s and 80 c today.
    assert [round(adjusted[t.split(";")[0]]) for t in k[12:]] == [900, 818, 600, 80]
    assert [t.rsplit(";", 1)[1] for t in k] == [""] * 12 + ["liquidity", "uncertain", "profession", "risk"]
    rod = next(t for t in k if key_of(t) == mx.ROD)
    assert rod == (f"{by_id[rod.split(';')[0]].cand_id};sell_stock;{mx.BRUGAN};{mx.ROD};5;{mx.ROD}:5:own;100;5000;6306;"
                   f"900;{mx.TONIGHT};;80;;")
    runed = next(t for t in k if key_of(t) == mx.RUNED).split(";")
    assert runed[5] == f"{mx.ROD}:1:craft/{mx.ESSENCE}:1:ah/{mx.DUST}:1:ah"
    assert (runed[10], runed[11], runed[13]) == (str(mx.at(6, 18)), str(mx.at(6, 21)), "Enchanting:1")
    assert all(len(t.split(";")) == 15 for t in k)                       # no history, no 168-slot profile
    limited = mx.k_strings(13)
    assert limited == k[:13] and NAMES[key_of(limited[-1])] == "Elixir of Fortitude"


def test_EX17_the_checksum_counts_every_candidate_and_graph_string():
    text = mx.market_file()
    k, g = mx.k_strings(), portfolio_lua.graph_strings(mx.graph())
    strings = [*k, *g[0].values(), *g[1].values(), g[2], g[3]]
    assert f'checksum = "{len(strings)}:{sum(len(s) for s in strings)}"' in text
    assert all(f'K[{n}]="{t}"' in text for n, t in enumerate(k, start=1))
    assert ", K = K1, G = G1, coverage30 = 0 }," in text and "exportVersion = 6," in text
    empty = market_lua([MarketIntelligence(mx.M, mx.NOW, None, 1, {})], {}, {}, mx.NOW)
    assert 'checksum = "0:0"' in empty and 'G.DE=""' in empty and 'G.order=""' in empty


def test_EX18_the_graph_strings_name_the_copper_chain():
    recipes, uses, de, order = portfolio_lua.graph_strings(mx.graph())
    assert recipes == {2657: "2840:1000:2770*1", 7421: "6218:1000:6217*1/10938*1/10940*1", 7818: "6217:1000:2840*1"}
    assert uses[mx.BAR] == "7818" and de == str(mx.BRACERS)
    keys = [int(k) for k in order.split(",")]
    assert keys.index(mx.ORE) < keys.index(mx.BAR) < keys.index(mx.ROD) < keys.index(mx.RUNED)


def test_EX18_the_portfolio_snapshot_gold_reservations_and_positions():
    fields = mx.portfolio_fields()
    cv = config_version(mx.PS)
    assert fields.portfolio == {mx.M: f"{mx.NOW};300;150;50;8;20;20;7;{cv};355800;41800;64940;62500"}
    assert fields.gold == {mx.AMAYA: f"210000,{mx.MON + 10 * mx.HOUR}", mx.BRUGAN: f"104000,{mx.MON + 10 * mx.HOUR}"}
    assert fields.reserved == {mx.AMAYA: 25200, mx.BRUGAN: 37300}
    names = {7920: "Mithril Coif", 2840: "Copper Bar", 2457: "Elixir of Agility", 2772: "Iron Ore", 4338: "Mageweave Cloth",
             2319: "Medium Leather"}
    assert {c: sorted(names[int(t.split(";")[1])] for t in rows) for c, rows in fields.positions.items()} == {
        mx.BRUGAN: ["Copper Bar", "Elixir of Agility", "Iron Ore", "Mithril Coif"],
        mx.AMAYA: ["Mageweave Cloth", "Medium Leather"]}
    coif, leather = (v.position_id for v in mx.positions() if v.key in (mx.COIF, mx.LEATHER))
    assert coif == f"{mx.BRUGAN}:{mx.NOW - 30 * mx.HOUR}:1"           # a position's id is its opening event's (M14)
    assert fields.positions[mx.BRUGAN][0] == f"{coif};7920;Listed;1;1;30000;4500;;undercut: relist at 4g 49s 99c"
    assert fields.positions[mx.AMAYA][1] == \
        f"{leather};2319;Needs reconciliation;20;20;4000;;;check bags and mail: 14 of 20 found"
    plan = mx.plan_file()
    strings = fields.strings()
    assert len(strings) == 1 + 2 + 2 + 6
    assert f'checksum = "{len(strings)}:{sum(len(s.encode()) for s in strings)}"' in plan    # bytes, as Lua's #
    assert f'    ["{mx.AMAYA}"] = 25200,' in plan


def test_FM40_the_lowest_alternatives_go_first_and_a_funded_row_never():
    rows = [("f1", "", 500.0), ("f2", "", 10.0), ("a1", "Exceeds risk tolerance", 900.0), ("a2", "x", 300.0),
            ("a3", "x", 300.0)]
    one = portfolio_lua.k_line_bytes(4, "a3")
    assert one == len('K[5]="a3"\n')
    assert portfolio_lua.trim_candidates({"m": rows}, one) == {"m": ["f1", "f2", "a1", "a2"]}     # a3: tie, later
    assert portfolio_lua.trim_candidates({"m": rows}, one + 1) == {"m": ["f1", "f2", "a1"]}
    assert portfolio_lua.trim_candidates({"m": rows}, 10_000) == {"m": ["f1", "f2"]}             # funded rows stay
    assert portfolio_lua.trim_candidates({"m": rows}, 0) == {"m": ["f1", "f2", "a1", "a2", "a3"]}
    two = {"m": [("x", "r", 5.0)], "n": [("y", "r", 4.0), ("z", "r", 6.0)]}
    assert portfolio_lua.trim_candidates(two, 1) == {"m": ["x"], "n": ["z"]}                     # lowest across markets


def test_a_candidate_without_needs_or_times_has_empty_fields():
    c = Candidate("flip", 2589, mx.AMAYA, "-/now/now", 2, 20, expected_net=12.5, chance=0.4555)
    assert portfolio_lua.candidate_text(c, "") == f"flip:2589:{mx.AMAYA}:-/now/now:2;flip;{mx.AMAYA};2589;20;;0;0;13;456;;;0;;"
    needs = replace(c, needs=Requirements("Enchanting", 150))
    assert portfolio_lua.candidate_text(needs, "Recommendation limit reached (20 of 20 today)").endswith(
        ";Enchanting:150;limit")
    assert portfolio_lua.position_text(["p", "c", 1, "Holding", 2, 2, 30, 10.5, None, None]) == "p;1;Holding;2;2;30;11;;"
