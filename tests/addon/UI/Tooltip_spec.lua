-- Specs for addon/AHProfit/UI/Tooltip.lua: the AHProfit block on item tooltips (SPEC §4.3, A21).

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")

local HOUR = 3600000
local NOW = 1760000000000

--- Log in with Linen in the market file (written 2 h ago, its price seen 30 min before that).
---@return table  The tooltip stub
local function Boot()
  Stubs.Reset()
  local linen = "45,20,30,49,49,40,60,0,480,,93,86,72,45,20,42,0,900;138:4:139:1120:7;"
  _G.AHProfitMarket = { schemaVersion = 1, generatedAt = NOW - 2 * HOUR, checksum = "1:" .. #linen,
    charMarkets = { ["Amaya-Grommash"] = UIWorld.MARKET },
    markets = { [UIWorld.MARKET] = { observationsUntil = NOW - 2 * HOUR, curveHour = 0, I = { [2589] = linen }, C = {} } } }
  Stubs.Login()
  return Stubs.tooltip
end

--- The added double lines as { label, value } pairs.
---@param tip table
---@return table
local function Pairs(tip)
  local out = {}
  for i, d in ipairs(tip.doubles) do out[i] = { d[1], d[2] } end
  return out
end

describe("Tooltip", function()
  it("A21 hovering an item adds the AHProfit block from the market file", function()
    local tip = Boot()
    Stubs.HoverItem(2589)
    assert_eq(tip.lines, { "AHProfit" })
    assert_eq(Pairs(tip), { { "Current AH price", "45c (2 h ago)" }, { "Historical price", "49c (30d, +9%)" },
      { "Best time to sell", "Sat 18:00–22:00" }, { "Price at best time", "55c (+22%)" }, { "Chance to sell", "90%" } })
    assert_eq({ tip.doubles[2][3], tip.doubles[2][4] }, { 0.24, 0.86 })           -- the green delta
  end)

  it("A21 a newer in-game scan's price replaces the file's, and the deltas follow it", function()
    local tip = Boot()
    AHProfit.Scan.Replicate.LastBook = function() return { at = NOW, book = { [2589] = { [40] = { [101] = 1 } } } } end
    Stubs.HoverItem(2589)
    assert_eq({ tip.doubles[1][2], tip.doubles[2][2] }, { "40c (now)", "49c (30d, +23%)" })
  end)

  it("A21 an item without market data, or a broken lookup, adds nothing", function()
    local tip = Boot()
    Stubs.HoverItem(2840)
    assert_eq({ tip.lines, tip.doubles }, { {}, {} })
    AHProfit.Tooltip.Lines = function() error("boom") end
    Stubs.HoverItem(2589)
    assert_eq(tip.doubles, {})
  end)

  it("TT4 the disenchant line shows only when one of your characters can disenchant the item", function()
    local tip = Boot()
    Stubs.ITEM_NAMES[6585], Stubs.ITEM_COLORS[6585] = "Scouting Gloves", "1eff00"
    Stubs.ITEM_CLASSES[6585] = 4
    local gloves = "800,1,30,1100,1100,,,0,,,1,50,,,1,,0,500;;"
    local dust = "300,20,30,300,300,,,0,,,1,50,,,20,285,0,900;;"
    AHProfitMarket.markets[UIWorld.MARKET].I[6585], AHProfitMarket.markets[UIWorld.MARKET].I[10940] = gloves, dust
    AHProfitMarket.checksum = "3:" .. (#AHProfitMarket.markets[UIWorld.MARKET].I[2589] + #gloves + #dust)
    AHProfit.Market.Reset()
    Stubs.HoverItem(6585)
    assert_eq(#tip.doubles, 5)
    AHProfitDB.professions["Amaya-Grommash"] = { Enchanting = { skill = 100, max = 150 } }
    Stubs.HoverItem(6585)
    assert_eq({ #tip.doubles, tip.doubles[3][1] }, { 6, "Disenchant value" })
    Stubs.ITEM_NAMES[6585], Stubs.ITEM_COLORS[6585], Stubs.ITEM_CLASSES[6585] = "Scouting Gloves", nil, nil
  end)
end)
