-- Specs for addon/AHProfit/UI/Tooltip.lua: the AHProfit block on item tooltips, gathered from the addon files and
-- the live state and drawn as two-column lines (SPEC §4.3 Tooltip; A21, TT4, TT6, TT22, TT24, TT27, TT30, R21). The lines' words are
-- TooltipView's (tests/addon/UI/TooltipView_spec.lua).

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")

local HOUR = 3600000
local NOW = 1760000000000

--- Log in with Linen in the market file (written 2 h ago, its price seen 30 min before that).
---@param med7 number|nil     Linen's 7-day median (default 49c, the same as med30)
---@param trend10 number|nil  Its trend in tenths of a percent a day (default 0)
---@param p90 number|nil      Its 30-day 90th percentile (default 60c)
---@return table  The tooltip stub
local function Boot(med7, trend10, p90)
  Stubs.Reset()
  local linen = "45,20,30," .. (med7 or 49) .. ",49,40," .. (p90 or 60) .. "," .. (trend10 or 0) .. ",480,,93,86,72,45,20,42,0,900;138:4:139:1120:7;"
  _G.AHProfitMarket = { schemaVersion = 1, exportVersion = 5, generatedAt = NOW - 2 * HOUR, checksum = "1:" .. #linen,
    charMarkets = { ["Amaya-Grommash"] = UIWorld.MARKET },
    markets = { [UIWorld.MARKET] = { observationsUntil = NOW - 2 * HOUR, curveHour = 0, I = { [2589] = linen }, C = {} } } }
  Stubs.Login()
  return Stubs.tooltip
end

--- The block's lines for an item now, as "label | value".
---@param key number
---@return string[]
local function Texts(key)
  local out = {}
  for i, l in ipairs(AHProfit.Tooltip.Lines(key, NOW)) do out[i] = l.label .. " | " .. l.value end
  return out
end

--- The value of the line with a label.
---@param key number
---@param label string
---@return string|nil
local function ValueOf(key, label)
  for _, l in ipairs(AHProfit.Tooltip.Lines(key, NOW)) do
    if l.label == label then return l.value end
  end
  return nil
end

describe("Tooltip", function()
  it("A21 hovering Linen in the bags adds the AHProfit block from the market file, the addon's own option first", function()
    local tip = Boot()
    assert_eq(Texts(2589), { "AHProfit | data 2 h old", "Market Price | 45c (2 h) · 8% lower than 7d median",
      "Price history | steady", " | (30d) 49c · (14d) — · (7d) 49c", "Market Inventory | 93 listed",
      "Sales Rate | 48 sold/day",
      "Best Sell Price | 55c · Saturday Evening · established" })        -- (45 − 49) ÷ 49 = −8.2%; no med14 in this file
    Stubs.world.containers = { [0] = { size = 1, slots = { [1] = { id = 2589, count = 20 } } } }
    Stubs.FireEvent("BAG_UPDATE_DELAYED")
    Stubs.Advance(1)
    local texts = Texts(2589)
    assert_eq({ texts[2], texts[3], texts[#texts] },
              { "Do: POST 20 now at 45c | +8s 40c · 90% chance", " | then nothing", "Inventory | Total: 20 · Bags: 20" })
    Stubs.HoverItem(2589)
    local header, market = tip.doubles[1], tip.doubles[4]
    assert_eq({ header[1]:match("AHProfit"), header[2]:match("data 2 h old|r |T.-WHITE8X8:7:7:0:0:8:8:0:8:0:8:61:219:79|t$") ~= nil },
              { "AHProfit", true })                                        -- a green dot: the data is fresh
    assert_eq(tip.doubles[2][1]:match("^|c%x+Do: |r|c(%x+)POST|r"), "ffffd100")   -- the verb in POST's gold
    assert_eq(market[2]:match("|c(%x+)8%% lower than 7d median|r"), "ffff493d")   -- red: below the 7-day median
    assert_eq(tip.doubles[3][1], " ")                                              -- the value-only line's label (TT24)
  end)

  it("TT6 the price at best time includes the trend up to the window's best hour", function()
    Boot(nil, 20)                                                   -- +2%/day; Sat 19:00 UTC is 2.42 days away
    assert_eq(ValueOf(2589, "Best Sell Price"), "58c · Saturday Evening · established")   -- floor(49 × 1.12 × 1.0484 + 0.5)
    local Prices = AHProfit.Engine.Prices
    assert_eq({ Prices.SlotPrice(49, 1.12, 2, 2), Prices.SlotPrice(49, 1.12, 9, 2), Prices.SlotPrice(49, 1.12, -9, 2) },
              { 57, 60, 49 })                                       -- clamped to ±5%/day
    assert_eq(Prices.DaysUntilSlot(80, NOW - (NOW % HOUR)), 0)     -- Thu 08:00 UTC is this hour's start
  end)

  it("R21 the price at best time starts from the 7-day median", function()
    Boot(60, nil, 80)
    assert_eq(ValueOf(2589, "Best Sell Price"), "67c · Saturday Evening · established")   -- 60c × 1.12 = 67.2c
  end)

  it("TT22 the price at best time stays within what the last 30 days traded at", function()
    Boot(60)                                                        -- p90 60c: 67.2c is clamped to max(60c, 60c)
    assert_eq(ValueOf(2589, "Best Sell Price"), "60c · Saturday Evening · established")
  end)

  it("A21 a newer in-game scan's price replaces the file's, and the deltas follow it", function()
    Boot()
    AHProfit.Scan.Replicate.LastBook = function() return { at = NOW, book = { [2589] = { [40] = { [101] = 1 } } } } end
    assert_eq(ValueOf(2589, "Market Price"), "40c now · 18% lower than 7d median")   -- (40 − 49) ÷ 49 = −18.4%
    assert_eq(ValueOf(2589, "Market Inventory"), "20 listed")                         -- the scan's 20 units (TT27)
    assert_eq(ValueOf(2589, "Sales Rate"), "48 sold/day")                           -- no salesDays in this file (TT32)
  end)

  it("TT30 what you hold of an item across your characters, by place: bags, bank, mail, then the AH", function()
    Boot()
    local Inventory = AHProfit.Capture.Inventory
    AHProfitDB.inventory = { ["Amaya-Grommash"] = { bags = Inventory.Encode({ { key = 2589, qty = 4 } }),
                                                    bank = Inventory.Encode({ { key = 2589, qty = 1 } }) },
                             ["Brugan-Grommash"] = { mail = Inventory.Encode({ { key = 2589, qty = 24 } }) } }
    AHProfitDB.ownAuctions = { ["Brugan-Grommash"] = { ends = "2589,20," .. (NOW + HOUR) .. ",45" } }
    assert_eq(ValueOf(2589, "Inventory"), "Total: 49 · Bags: 4 · Bank: 1 · Mail: 24 · AH: 20")
    AHProfitDB.ownAuctions = { ["Brugan-Grommash"] = { ends = "2589,20," .. (NOW - HOUR) .. ",45" } }   -- ended: not held
    assert_eq(ValueOf(2589, "Inventory"), "Total: 29 · Bags: 4 · Bank: 1 · Mail: 24")
  end)

  it("A21 an item without market data shows two lines; a broken lookup adds nothing", function()
    local tip = Boot()
    assert_eq(Texts(2840), { "AHProfit | data 2 h old", "no market data yet | " })
    Stubs.HoverItem(2840)
    assert_eq(#tip.doubles, 2)
    AHProfit.Tooltip.Lines = function() error("boom") end
    Stubs.HoverItem(2589)
    assert_eq(tip.doubles, {})
  end)

  it("TT4 the disenchant line shows only when one of your characters can disenchant the item", function()
    Boot()
    Stubs.ITEM_NAMES[6585], Stubs.ITEM_COLORS[6585] = "Scouting Gloves", "1eff00"
    Stubs.ITEM_CLASSES[6585] = 4
    local gloves = "800,1,30,1100,1100,,,0,,,1,50,,,1,,0,500;;"
    local dust = "300,20,30,300,300,,,0,,,1,50,,,20,285,0,900;;"
    AHProfitMarket.markets[UIWorld.MARKET].I[6585], AHProfitMarket.markets[UIWorld.MARKET].I[10940] = gloves, dust
    AHProfitMarket.checksum = "3:" .. (#AHProfitMarket.markets[UIWorld.MARKET].I[2589] + #gloves + #dust)
    AHProfit.Market.Reset()
    assert_eq(ValueOf(6585, "Disenchant"), nil)
    AHProfitDB.professions["Amaya-Grommash"] = { Enchanting = { skill = 100, max = 150 } }
    assert_eq(ValueOf(6585, "Disenchant") ~= nil, true)
    Stubs.ITEM_NAMES[6585], Stubs.ITEM_COLORS[6585], Stubs.ITEM_CLASSES[6585] = "Scouting Gloves", nil, nil
  end)

  it("the plan's action for this character comes before the addon's own option", function()
    Boot()
    UIWorld.Plan({ { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW + 26 * HOUR, net = 1046, chance = 92,
                     peak = NOW + 34 * HOUR } })
    AHProfit.Plan.Reset()
    local texts = Texts(2589)
    assert_eq({ texts[2], texts[3] }, { "Do: POST Fri 10:53 | 55c · +22% vs now · 92%", " | for the 18:53 peak" })
  end)
end)
