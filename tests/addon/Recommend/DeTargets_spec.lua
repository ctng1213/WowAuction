-- Specs for addon/AHProfit/Recommend/DeTargets.lua: the AH panel's disenchant targets, worked out in steps
-- (SPEC §5.1, A96-A98).

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")
local AHWorld = require("ah_world")

local NOW = 1760000000000

--- Install a market file with items and disenchantable entries (Market.Disenchantable's D rows).
---@param items table  key -> item string
---@param de table     key -> "skill,quality,ilvl,a|w"
local function MarketWithDe(items, de)
  local count, length = 0, 0
  for _, text in pairs(items) do count, length = count + 1, length + #text end
  for _, text in pairs(de) do count, length = count + 1, length + #text end
  _G.AHProfitMarket = { schemaVersion = 1, exportVersion = 10, generatedAt = NOW - 3600000, checksum = count .. ":" .. length,
                        charMarkets = { ["Amaya-Grommash"] = UIWorld.MARKET },
                        markets = { [UIWorld.MARKET] = { observationsUntil = NOW - 3600000, curveHour = 0, I = items, C = {},
                                                         D = de } } }
end

--- A96's world: Amaya plans to buy 20 Linen; the account has an enchanter; the gloves (1 s 50 c) and the pants (2 s)
--- pay to disenchant at the dusts' and essences' 6 s, Silk Cloth at 99 g doesn't. A step does 2 items. Opens the AH.
---@return table, table  The panel's frames, the AH window
local function OpenWithTargets()
  Stubs.Reset()
  local mat = UIWorld.Item(600, 80, 20, { sellValue = 600 })
  MarketWithDe({ [2589] = UIWorld.Item(45, 86, 48), [6585] = UIWorld.Item(150, 50, 1), [2852] = UIWorld.Item(200, 50, 1),
                 [4306] = UIWorld.Item(990000, 50, 1), [10938] = mat, [10939] = mat, [10940] = mat },
               { [6585] = "1,2,18,a", [2852] = "1,2,10,a", [4306] = "1,2,12,a" })
  UIWorld.Plan({ { id = "a", kind = "buy", key = 2589, qty = 20, price = 45, at = NOW, net = 200 } })
  Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse({ auctions = {} }))
  Stubs.Login()
  AHProfitDB.professions["Amaya-Grommash"] = { Enchanting = { skill = 300, max = 300 } }
  AHProfit.DeTargets.STEP_ITEMS = 2
  local ah = AHWorld.Install()
  Stubs.FireEvent("AUCTION_HOUSE_SHOW")
  return AHProfit.AHPanel.Frames(), ah
end

--- Shopping's shown rows as "name WHY", top first.
---@param ui table  The panel's frames
---@return string[]
local function Shopping(ui)
  local out = {}
  for _, row in ipairs(ui.shop) do
    if row:IsShown() then out[#out + 1] = row.item:GetText() .. " " .. row.why:GetText() end
  end
  return out
end

--- The steps, finished jobs and panel redraws so far.
---@return table  { steps, jobs, redraws }
local function Counts()
  local de = AHProfit.DeTargets.Perf()
  return { de.steps, de.jobs, AHProfit.AHPanel.Perf().redraws }
end

describe("DeTargets", function()
  it("A96 opening the AH works out no disenchant target in its frame; they arrive in steps, then one redraw", function()
    local ui = OpenWithTargets()
    local linen = Shopping(ui)[1]
    assert_eq({ Shopping(ui), Counts() }, { { linen }, { 0, 0, 1 } })
    Stubs.Advance(0)
    assert_eq({ Shopping(ui), Counts() },
              { { linen, "Scouting Gloves DE", "Copper Chain Pants DE" }, { 2, 1, 2 } })
  end)

  it("A97 a second open reuses the targets; a search re-evaluates only its item, with no step", function()
    local ui = OpenWithTargets()
    Stubs.Advance(0)
    Stubs.FireEvent("AUCTION_HOUSE_CLOSED")
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.Advance(0)
    local linen = Shopping(ui)[1]
    assert_eq({ Shopping(ui), Counts() }, { { linen, "Scouting Gloves DE", "Copper Chain Pants DE" }, { 2, 1, 3 } })
    C_AuctionHouse.GetBrowseResults = function() return { { itemKey = { itemID = 6585 }, minPrice = 990000, totalQuantity = 1 } } end
    Stubs.FireEvent("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED")
    assert_eq({ Shopping(ui), Counts() }, { { linen, "Copper Chain Pants DE" }, { 2, 1, 4 } })
  end)

  it("A98 a finished scan works the targets out again in steps, the last ones shown until then", function()
    local ui = OpenWithTargets()
    Stubs.Advance(0)
    local linen = Shopping(ui)[1]
    local book = { [6585] = { [990000] = { [1 * 5 + 4] = 1 } }, [2852] = { [200] = { [1 * 5 + 4] = 1 } } }
    AHProfit.Scan.Replicate.LastBook = function() return { at = NOW, book = book } end
    AHProfit.Scan.Replicate.NotifyBook()
    assert_eq({ Shopping(ui), Counts() }, { { linen, "Scouting Gloves DE", "Copper Chain Pants DE" }, { 2, 1, 3 } })
    Stubs.Advance(0)
    assert_eq({ Shopping(ui), Counts() }, { { linen, "Copper Chain Pants DE" }, { 4, 2, 4 } })
  end)
end)
