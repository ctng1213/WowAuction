-- Specs for addon/AHProfit/UI/Tooltip.lua: the AHProfit block on item tooltips (SPEC §4.3, A21).

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")

local HOUR = 3600000
local NOW = 1760000000000

--- Log in with Linen in the market file (written 2 h ago, its price seen 30 min before that).
---@param med7 number|nil     Linen's 7-day median (default 49c, the same as med30)
---@param trend10 number|nil  Its trend in tenths of a percent a day (default 0)
---@return table  The tooltip stub
local function Boot(med7, trend10, p90)
  Stubs.Reset()
  local linen = "45,20,30," .. (med7 or 49) .. ",49,40," .. (p90 or 60) .. "," .. (trend10 or 0) .. ",480,,93,86,72,45,20,42,0,900;138:4:139:1120:7;"
  _G.AHProfitMarket = { schemaVersion = 1, exportVersion = 4, generatedAt = NOW - 2 * HOUR, checksum = "1:" .. #linen,
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

  it("TT6 the price at best time includes the trend up to the window's best hour", function()
    local tip = Boot(nil, 20)                                       -- +2%/day; Sat 19:00 UTC is 2.42 days away
    Stubs.HoverItem(2589)
    assert_eq(tip.doubles[4][2], "58c (+29%)")                     -- floor(49 × 1.12 × (1 + 0.02 × 2.42) + 0.5)
    local Prices = AHProfit.Engine.Prices
    assert_eq({ Prices.SlotPrice(49, 1.12, 2, 2), Prices.SlotPrice(49, 1.12, 9, 2), Prices.SlotPrice(49, 1.12, -9, 2) },
              { 57, 60, 49 })                                       -- clamped to ±5%/day
    assert_eq(Prices.DaysUntilSlot(80, NOW - (NOW % HOUR)), 0)     -- Thu 08:00 UTC is this hour's start
  end)

  it("R21 the price at best time starts from the 7-day median", function()
    local tip = Boot(60, nil, 80)
    Stubs.HoverItem(2589)
    assert_eq(tip.doubles[4][2], "67c (+49%)")                     -- 60c × 1.12 = 67.2c, (67.2 − 45) ÷ 45
  end)

  it("TT22 the price at best time stays within what the last 30 days traded at", function()
    local tip = Boot(60)                                            -- p90 60c: 67.2c is clamped to max(60c, 60c)
    Stubs.HoverItem(2589)
    assert_eq(tip.doubles[4][2], "60c (+33%)")
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
