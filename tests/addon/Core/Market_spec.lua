-- Specs for addon/AHProfit/Core/Market.lua: reading MarketIntelligence.lua (SPEC §3.5, §5.1).
-- The files under tests/addon/generated/market are written by ahsync/export/market_lua.py
-- (tools/gen_lua_fixtures.py), so these specs read exactly what AHSync exports.

local Stubs = require("wow_stubs")

local EX2 = "tests/addon/generated/market/ex2.lua"
local BIG = "tests/addon/generated/market/big.lua"
local EX17 = "tests/addon/generated/market/ex17.lua"     -- the mockup world's K and G (tests/support/mockup_export.py)

--- Log in with a MarketIntelligence.lua loaded (or none), as WoW loads AHProfit_Data.
---@param path string|nil  A generated file, or nil for none
---@param change function|nil  Edits AHProfitMarket before the addon reads it
---@return table           AHProfit.Market
local function Boot(path, change)
  Stubs.Reset()
  _G.AHProfitMarket = nil
  if path then dofile(path) end
  if change then change(_G.AHProfitMarket) end
  Stubs.Login()
  return AHProfit.Market
end

describe("Market", function()
  it("EX2 decodes an item, its windows and its curve", function()
    local Market = Boot(EX2)
    assert_eq(Market.Problem(), nil)
    local linen = Market.Item(2589)
    assert_eq({ linen.cur, linen.curQty, linen.curAgeMin, linen.med30, linen.p10, linen.p90, linen.trend },
              { 45, 20, 60, 49, 40, 60, 0.4 })
    assert_eq({ linen.soldPerDay, linen.supply, linen.conf, linen.seasonality, linen.sellValue, linen.volumeUnknown,
                linen.chance }, { 48, 93, 86, 72, 42, false, 0.9 })
    assert_eq(linen.sellWindows, { { start = 138, hours = 4, best = 139, how = 1.12, weeks = 7 } })
    assert_eq(linen.buyWindows, {})
    local curve = Market.Curve(2589)
    assert_eq({ #curve.rows, #curve.rows[1], curve.aheadPoints[3] }, { 1, 3, 20 })     -- tooltip-only (EX19)
    assert_eq(curve.rows[1], { 0, 0.06, 0.1 })            -- row 0's columns 0, 3 and 5: (0 + c) % 50 steps of 2%
    local empty = Market.Item(6948)
    assert_eq({ empty.cur, empty.refStack, empty.volumeUnknown }, { nil, 1, true })
    assert_eq({ Market.Item(1), Market.Curve(6948) }, {})
    local generated, newest = Market.Times()
    assert_eq(newest, generated - 3600000)
  end)

  it("EX19 a tooltip-only item's one curve row is used at any hour; a shortlisted item keeps 24 rows", function()
    local Market = Boot(BIG)
    local SellProb = AHProfit.Engine.SellProb
    local tooltip, shortlisted = Market.Curve(11), Market.Curve(10)
    assert_eq({ #tooltip.rows, #tooltip.rows[1], #shortlisted.rows, #shortlisted.rows[1] }, { 1, 3, 24, 8 })
    local at = function(curve, hours)
      return SellProb.Lookup(curve.rows, curve.refreshMs, curve.refreshMs + hours * 3600000, 5, curve.aheadPoints)
    end
    assert_eq({ at(tooltip, 0), at(tooltip, 9), at(tooltip, 33) }, { tooltip.rows[1][2], tooltip.rows[1][2], tooltip.rows[1][2] })
    assert_eq({ at(shortlisted, 9) }, { SellProb.FromCurve(shortlisted.rows[10], 5) })   -- IU8 still holds
  end)

  it("EX6 reads vendor prices; a file without them has none", function()
    local Market = Boot(EX2)
    assert_eq({ Market.VendorPrice(4470), Market.VendorPrice(2320), Market.VendorPrice(2589) }, { 38, 10, nil })
    Market = Boot(EX2, function(file) file.vendor = nil end)
    assert_eq(Market.VendorPrice(4470), nil)
    Market = Boot(nil)
    assert_eq(Market.VendorPrice(4470), nil)
  end)

  it("with no MarketIntelligence.lua the addon says to run AHSync", function()
    local Market = Boot(nil)
    assert_eq(Market.Problem(), "No market data yet. Run AHSync, then /reload.")
    assert_eq({ Market.Item(2589), Market.Curve(2589), Market.Times() }, {})
  end)

  it("FM17 a file from another schema version is refused with a message, not an error", function()
    local Market = Boot(EX2, function(file) file.schemaVersion = 2 end)
    assert_eq(Market.Problem(), "Market data and addon versions differ. Update AHProfit or AHSync, then /reload.")
    assert_eq(Market.Item(2589), nil)
  end)

  it("FM34 a file from an older AHSync (export version 0 or none) has no items and says restart AHSync", function()
    for _, version in ipairs({ 0, false, 3 }) do
      local Market = Boot(EX2, function(file) file.exportVersion = version or nil end)
      assert_eq(Market.Problem(), "AHSync is older than this addon: restart AHSync, then /reload")
      assert_eq({ Market.Item(2589), Market.Curve(2589), Market.Times() }, {})
      assert_eq({ Market.Disenchantable(), Market.FlipOffers() }, { {}, {} })
    end
    local Market = Boot(EX2, function(file) file.exportVersion, file.schemaVersion = 0, 2 end)        -- schema first
    assert_eq(Market.Problem(), "Market data and addon versions differ. Update AHProfit or AHSync, then /reload.")
    Market = Boot(EX2, function(file)
      file.exportVersion, file.markets["forever.pvp.horde.us"].I[2589] = 0, "45;;"                   -- checksum last
    end)
    assert_eq(Market.Problem(), "AHSync is older than this addon: restart AHSync, then /reload")
  end)

  it("FM35 a file from a newer AHSync says to deploy the addon", function()
    local Market = Boot(EX2, function(file) file.exportVersion = 5 end)
    assert_eq({ Market.Problem(), Market.Item(2589) }, { "This addon is older than AHSync's files: run tools/deploy.py, then /reload", nil })
  end)

  it("EX17 decodes the exported candidates lazily: the funded first, then the alternatives with their reason", function()
    local Market = Boot(EX17)
    assert_eq({ Market.Problem(), Market.CandidateCount() }, { nil, 16 })
    local first = Market.Candidate(1)
    assert_eq({ first.strategy, first.key, first.qty, first.gold, first.net, first.chance, first.conf, first.funded },
              { "craft", 3486, 1, 0, 134, 0.9, 80, true })
    assert_eq({ first.inputs, first.needs, first.status }, { { { key = 2838, count = 3, source = "own" } },
                                                            { profession = "Blacksmithing", skill = 125 }, nil })
    assert_eq(Market.Candidate(1) == first, true)                   -- decoded once, then kept
    local rod = Market.Candidate(2)
    assert_eq({ rod.id, rod.char, rod.cashBasis, rod.windowEnd, rod.needs }, {
      "sell_stock:6217:Brugan-Grommash:-/-/now:1", "Brugan-Grommash", 5000, nil, nil })
    local statuses = {}
    for i = 12, 16 do statuses[#statuses + 1] = Market.Candidate(i).status or "funded" end
    assert_eq(statuses, { "funded", "liquidity", "uncertain", "profession", "risk" })
    assert_eq(Market.Candidate(17), nil)
    local runed = Market.DecodeCandidate("multistage:6218:A:6217c/-/h1:1;multistage;A;6218;1;6217:1:craft/10940:1:own+ah;"
                                         .. "3700;0;1630;900;3600000;14400000;80;Enchanting:1;")
    assert_eq({ runed.inputs[2].source, runed.sale, runed.windowEnd, runed.funded }, { "own+ah", 3600000, 14400000, true })
    assert_eq(#Market.DecodeCandidate("flip:1:A:-/now/now:1;flip;A;1;1;;5;0;2;500;;;10;;").inputs, 0)
  end)

  it("EX18 decodes the dependency graph: the copper chain, its reverse index and its order", function()
    local Market = Boot(EX17)
    local g = Market.Graph()
    assert_eq(g.recipes[7818], { product = 6217, yield = 1, reagents = { { key = 2840, count = 1 } } })
    assert_eq(#g.recipes[7421].reagents, 3)
    assert_eq({ g.uses[2840], g.disenchant, g.order }, { { 7818 }, { 6187 }, { 2770, 2840, 6187, 6217, 10938, 10940, 6218 } })
    assert_eq(Market.Graph() == g, true)
    local file = Boot(EX2)
    assert_eq({ file.Graph().order, file.CandidateCount() }, { {}, 0 })             -- a market without K or G content
    local tampered = Boot(EX17, function(f) f.markets["forever.pvp.horde.us"].G.order = "2840,2770" end)
    assert_eq({ tampered.Problem(), tampered.Graph(), tampered.Candidate(1), tampered.CandidateCount() }, {
      "Market data is damaged. Let AHSync write it again, then /reload.", nil, nil, 0 })
    local noGraph = Boot(EX2, function(f) f.markets["forever.pvp.horde.us"].G = nil end)
    assert_eq(noGraph.Graph(), nil)
  end)

  it("a damaged file fails its checksum", function()
    local Market = Boot(EX2, function(file) file.markets["forever.pvp.horde.us"].I[2589] = "45;;" end)
    assert_eq(Market.Problem(), "Market data is damaged. Let AHSync write it again, then /reload.")
  end)

  it("S12 a character the file doesn't name uses the only market of its faction", function()
    local Market = Boot(EX2, function(file) file.charMarkets = {} end)
    assert_eq(Market.Item(2589).cur, 45)
    Stubs.Reset()
    _G.AHProfitMarket = nil
    dofile(EX2)
    AHProfitMarket.charMarkets = {}
    Stubs.world.player.faction = "Alliance"
    Stubs.Login()
    assert_eq(AHProfit.Market.Problem(), "No market data for this character's realm. Check AHSync's Status page.")
  end)

  it("A3 a 20,000-item file loads in Lua 5.1 and decodes lazily from a small cache", function()
    local Market = Boot(BIG)
    assert_eq(Market.Problem(), nil)
    Market.CACHE_LIMIT = 2
    assert_eq({ Market.Item(1).cur, Market.Item(2).cur, Market.Item(20000).cur }, { 2, 3, 20000 % 997 + 1 })
    assert_eq(Market.Item(1).cur, 2)                       -- decoded again after the cache started over
    assert_eq({ #Market.Curve(10).rows[1], #Market.Curve(11).rows[1] }, { 8, 3 })   -- every 10th is shortlisted
    Market.CACHE_LIMIT = 500
  end)
end)
