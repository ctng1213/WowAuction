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
  _G.AHProfitMarket = { schemaVersion = 1, exportVersion = 6, generatedAt = NOW - 2 * HOUR, checksum = "1:" .. #linen,
    charMarkets = { ["Amaya-Grommash"] = UIWorld.MARKET },
    markets = { [UIWorld.MARKET] = { observationsUntil = NOW - 2 * HOUR, curveHour = 0, I = { [2589] = linen }, C = {} } } }
  Stubs.Login()
  return Stubs.tooltip
end

--- The block's lines for an item now, as tooltip_world's TW.Texts writes them ("---" a divider, TT42).
---@param key number
---@return string[]
local function Texts(key)
  local out = {}
  for i, l in ipairs(AHProfit.Tooltip.Lines(key, NOW)) do
    if l.divider then out[i] = "---"
    elseif l.joined or l.indent then out[i] = AHProfit.TooltipView.JoinedText(l)
    else out[i] = l.label .. " | " .. l.value end
  end
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

--- The value of the line under the one with a label (a title's values line, SPEC §4.3 M33b).
---@param key number
---@param label string
---@return string|nil
local function ValueUnder(key, label)
  local lines = AHProfit.Tooltip.Lines(key, NOW)
  for i, l in ipairs(lines) do
    if l.label == label then return lines[i + 1] and lines[i + 1].value end
  end
  return nil
end

describe("Tooltip", function()
  it("A21 hovering Linen in the bags adds the AHProfit block from the market file, the addon's own option first", function()
    local tip = Boot()
    assert_eq(Texts(2589), { "AHProfit | data 2 h old", "---", "Market Price  45c (2 h) · steady",
      "   (7d) 49c · (14d) — · (30d) 49c", "Market Inventory  93 listed now", "   (7d) — · (14d) — · (30d) —",
      "Sell Chance Now  — at 45c", "   (7d) — · (14d) — · (30d) —", "---", "Best Sell Time  established",
      "   55c · Saturday Evening · —", "---", "Vendor Price  3c" })   -- (45 − 49) ÷ 49 = −8.2%: steady; a file from before M33b; the stub sells Linen at 3c
    Stubs.world.containers = { [0] = { size = 1, slots = { [1] = { id = 2589, count = 20 } } } }
    Stubs.FireEvent("BAG_UPDATE_DELAYED")
    Stubs.Advance(1)
    local texts = Texts(2589)
    assert_eq({ texts[3], texts[4], texts[#texts] },
              { "Do: POST 20 now at 45c | +8s 40c · 90% chance", " | then nothing", "Inventory  Total: 20 · Bags: 20" })
    Stubs.HoverItem(2589)
    local header, market = tip.doubles[1], tip.doubles[6]
    assert_eq({ header[1]:match("AHProfit"), header[2]:match("data 2 h old|r |T.-WHITE8X8:7:7:0:0:8:8:0:8:0:8:61:219:79|t|c") ~= nil },
              { "AHProfit", true })                                        -- a green dot: the data is fresh
    assert_eq({ tip.doubles[2][1], tip.doubles[2][2] }, { " ", " " })            -- a divider: a spacer line (TT42)
    assert_eq(tip.doubles[3][1]:match("^|c%x+Do: |r|c(%x+)POST|r"), "ffffd100")   -- the verb in POST's gold
    assert_eq({ market[1]:match("^|cffffd100Market Price|r  "), market[1]:match("steady$"), market[2] },
              { "|cffffd100Market Price|r  ", "steady", "" })   -- joined: the title in price gold, steady plain (TT42)
    assert_eq(header[2]:match("|t|c%x+ · — coverage|r$") ~= nil, true)   -- the dot follows the age, then the coverage (TT40)
    assert_eq(tip.doubles[4][1], " ")                                              -- the value-only line's label (TT24)
  end)

  it("TT6 the price at best time includes the trend up to the window's best hour", function()
    Boot(nil, 20)                                                   -- +2%/day; Sat 19:00 UTC is 2.42 days away
    assert_eq(ValueUnder(2589, "Best Sell Time"), "58c · Saturday Evening · —")   -- floor(49 × 1.12 × 1.0484 + 0.5)
    local Prices = AHProfit.Engine.Prices
    assert_eq({ Prices.SlotPrice(49, 1.12, 2, 2), Prices.SlotPrice(49, 1.12, 9, 2), Prices.SlotPrice(49, 1.12, -9, 2) },
              { 57, 60, 49 })                                       -- clamped to ±5%/day
    assert_eq(Prices.DaysUntilSlot(80, NOW - (NOW % HOUR)), 0)     -- Thu 08:00 UTC is this hour's start
  end)

  it("R21 the price at best time starts from the 7-day median", function()
    Boot(60, nil, 80)
    assert_eq(ValueUnder(2589, "Best Sell Time"), "67c · Saturday Evening · —")   -- 60c × 1.12 = 67.2c
  end)

  it("TT22 the price at best time stays within what the last 30 days traded at", function()
    Boot(60)                                                        -- p90 60c: 67.2c is clamped to max(60c, 60c)
    assert_eq(ValueUnder(2589, "Best Sell Time"), "60c · Saturday Evening · —")
  end)

  it("A21 a newer in-game scan's price replaces the file's, and the deltas follow it", function()
    Boot()
    AHProfit.Scan.Replicate.LastBook = function() return { at = NOW, book = { [2589] = { [40] = { [101] = 1 } } } } end
    assert_eq(ValueOf(2589, "Market Price"), "40c now · low")                     -- (40 − 49) ÷ 49 = −18.4%
    assert_eq(ValueOf(2589, "Market Inventory"), "20 listed now")                     -- the scan's 20 units (TT27)
    assert_eq(ValueOf(2589, "Sell Chance Now"), "— at 45c")       -- the price it was made at, not the scan's (M33b)
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
    assert_eq(ValueOf(6585, "Disenchant Value"), nil)
    AHProfitDB.professions["Amaya-Grommash"] = { Enchanting = { skill = 100, max = 150 } }
    assert_eq(ValueOf(6585, "Disenchant Value") ~= nil, true)
    Stubs.ITEM_NAMES[6585], Stubs.ITEM_COLORS[6585], Stubs.ITEM_CLASSES[6585] = "Scouting Gloves", nil, nil
  end)

  it("TT43 Disenchant Value always shows for a Bind on Equip item not yet bound; soulbound ones need your Enchanting", function()
    Boot()
    Stubs.ITEM_NAMES[6585], Stubs.ITEM_COLORS[6585] = "Scouting Gloves", "1eff00"
    Stubs.ITEM_CLASSES[6585], Stubs.ITEM_BIND[6585] = 4, 2
    local gloves = "800,1,30,1100,1100,,,0,,,1,50,,,1,,0,500;;"
    local dust = "300,20,30,300,300,,,0,,,1,50,,,20,285,0,900;;"
    AHProfitMarket.markets[UIWorld.MARKET].I[6585], AHProfitMarket.markets[UIWorld.MARKET].I[10940] = gloves, dust
    AHProfitMarket.checksum = "3:" .. (#AHProfitMarket.markets[UIWorld.MARKET].I[2589] + #gloves + #dust)
    AHProfit.Market.Reset()
    --- Whether the block has a Disenchant Value line, and the line under it.
    ---@param bound boolean|nil
    ---@return boolean, string|nil
    local function Shown(bound)
      local lines = AHProfit.Tooltip.Lines(6585, NOW, bound)
      for i, l in ipairs(lines) do if l.label == "Disenchant Value" then return true, lines[i + 1].label end end
      return false, nil
    end
    assert_eq({ Shown() }, { true, "75% · 2.5 Strange Dust @ 3s" })      -- BoE, nobody has Enchanting: shown
    assert_eq(Shown(true), false)                                         -- worn, so soulbound: Enchanting needed
    Stubs.ITEM_BIND[6585] = 1
    assert_eq(Shown(), false)                                             -- Bind on Pickup
    AHProfitDB.professions["Amaya-Grommash"] = { Enchanting = { skill = 100, max = 150 } }
    assert_eq({ (Shown()), (Shown(true)) }, { true, true })                 -- with Enchanting, every binding (TT4)
    Stubs.ITEM_NAMES[6585], Stubs.ITEM_COLORS[6585], Stubs.ITEM_CLASSES[6585], Stubs.ITEM_BIND[6585] = "Scouting Gloves", nil, nil, nil
  end)

  it("the plan's action for this character comes before the addon's own option", function()
    Boot()
    UIWorld.Plan({ { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW + 26 * HOUR, net = 1046, chance = 92,
                     peak = NOW + 34 * HOUR } })
    AHProfit.Plan.Reset()
    local texts = Texts(2589)
    assert_eq({ texts[3], texts[4] }, { "Do: POST Fri 10:53 | 55c · +22% vs now · 92%", " | for the 18:53 peak" })
  end)
end)
