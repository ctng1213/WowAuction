"""Tests for ahsync/state/diagnostics.py: the addon's diagnostics block (SPEC §2.2.1, §6)."""
from __future__ import annotations

from ahsync.state.savedvars import read_ahprofit
from tests.ahsync.state.test_savedvars import db_text


def test_diagnostics_block_is_read_for_the_report():
    data = read_ahprofit(db_text('''\t["diagnostics"] = {
\t\t["at"] = 1792205711000,
\t\t["client"] = "1.60.1 70009",
\t\t["apis"] = "GetNumSkillLines=nil;GetMerchantItemInfo=function",
\t\t["errors"] = {
\t\t\t"Interface/AddOns/AHProfit/Core/Adapter.lua:146: attempt to call global 'GetNumSkillLines' (a nil value)", -- [1]
\t\t},
\t\t["skillLines"] = "error: attempt to call global 'GetNumSkillLines' (a nil value)",
\t\t["namespaces"] = "C_MerchantFrame=GetBuybackItemInfo GetItemInfo;C_Missing=nil",
\t\t["professions"] = "1|3|Herbalism|15|75;2|nil|nil|nil|nil",
\t\t["tradeSkill"] = "name=Cooking|ids=2|learned=1",
\t\t["scan"] = "calls=0|n=3|skipped=0|steps=1|maxStepMs=0.03|kind=full|rows=2|dropped=0",
\t},'''))
    d = data.diagnostics
    assert (d.at_ms, d.client, d.merchant, d.setup_error) == (1792205711000, "1.60.1 70009", None, None)
    assert d.apis == (("GetNumSkillLines", "nil"), ("GetMerchantItemInfo", "function"))
    assert len(d.errors) == 1 and "GetNumSkillLines" in d.errors[0]
    assert d.skill_lines.startswith("error: ")
    assert d.namespaces == (("C_MerchantFrame", ("GetBuybackItemInfo", "GetItemInfo")), ("C_Missing", None))
    assert d.professions == "1|3|Herbalism|15|75;2|nil|nil|nil|nil"
    assert d.trade_skill == "name=Cooking|ids=2|learned=1"
    assert d.scan == "calls=0|n=3|skipped=0|steps=1|maxStepMs=0.03|kind=full|rows=2|dropped=0"
    assert data.problems == ()


def test_missing_or_malformed_diagnostics_never_break_the_read():
    assert read_ahprofit(db_text("")).diagnostics is None
    d = read_ahprofit(db_text('\t["diagnostics"] = { ["at"] = "soon", ["apis"] = 5, ["errors"] = "x" },')).diagnostics
    assert (d.at_ms, d.apis, d.errors, d.client) == (None, (), (), None)
