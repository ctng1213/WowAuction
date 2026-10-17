-- Specs for addon/AHProfit/Core/Diagnostics.lua: what is saved at logout to explain missing captures.

local Stubs = require("wow_stubs")

--- Fresh stubs, a logged-in addon, and a world with one header and one profession line.
local function Boot()
  Stubs.Reset()
  Stubs.world.skillLines = {
    { name = "Secondary Skills", header = true },
    { name = "Fishing", rank = 12, max = 75 },
  }
  Stubs.Login()
end

describe("Diagnostics", function()
  it("saves API presence, raw skill lines and caught errors at logout", function()
    Boot()
    AHProfit.Events.Register("UNIT_AURA", function() error("boom") end)
    Stubs.FireEvent("UNIT_AURA")
    Stubs.FireEvent("PLAYER_LOGOUT")
    local d = AHProfitDB.diagnostics
    assert_eq(d.at, 1760000000000)
    assert_eq(d.client, "unknown")   -- the stubs define no GetBuildInfo
    assert_eq(d.apis:find("GetNumSkillLines=function", 1, true) ~= nil, true)
    assert_eq(d.apis:find("C_MerchantFrame=nil", 1, true) ~= nil, true)
    assert_eq(d.skillLines, "Secondary Skills|true|true|0|0;Fishing|false|nil|12|75")
    assert_eq(#d.errors, 1)
    assert_eq(d.errors[1]:find("boom", 1, true) ~= nil, true)
    assert_eq(d.merchant, nil)
  end)

  it("turns a missing API into error text instead of raising", function()
    Boot()
    GetNumSkillLines = nil   -- as if Forever had removed it
    Stubs.FireEvent("PLAYER_LOGOUT")
    assert_eq(AHProfitDB.diagnostics.skillLines:sub(1, 7), "error: ")
  end)

  it("records the last merchant's raw items once they have loaded", function()
    Boot()
    Stubs.world.merchant = { { id = 2320, numAvailable = -1 }, { id = 2678, numAvailable = 5, extendedCost = true } }
    Stubs.FireEvent("MERCHANT_SHOW")
    Stubs.Advance(AHProfit.Events.DEBOUNCE_S)
    Stubs.FireEvent("PLAYER_LOGOUT")
    assert_eq(AHProfitDB.diagnostics.merchant, "2320|-1|false;2678|5|true")
  end)

  it("describes the client build when GetBuildInfo exists", function()
    Boot()
    GetBuildInfo = function() return "1.60.1", "70009", "Oct 1 2026", 16001 end
    local build = AHProfit.Adapter.ClientBuild()
    GetBuildInfo = nil
    assert_eq(build, "1.60.1 70009")
  end)

  it("records the last modern trade skill window's raw state", function()
    Stubs.Reset()
    Stubs.Define("C_TradeSkillUI", Stubs.ModernTradeSkill({ name = "Cooking", rank = 28, max = 75, recipes = {
      { id = 2538, out = 2679, learned = true, reagents = { { 2672, 1 } } },
      { id = 2539, out = 2680, learned = false, reagents = { { 2672, 1 }, { 2678, 1 } } },
    } }))
    Stubs.Login()
    Stubs.FireEvent("TRADE_SKILL_SHOW")
    Stubs.Advance(AHProfit.Events.DEBOUNCE_S)
    Stubs.FireEvent("PLAYER_LOGOUT")
    assert_eq(AHProfitDB.diagnostics.tradeSkill,
      "name=Cooking|parent=nil|level=28|max=75|ready=true|linked=false|ids=2|learned=1|first=2538:2679:1:1:1")
  end)
end)
