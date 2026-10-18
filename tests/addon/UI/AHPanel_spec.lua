-- Specs for addon/AHProfit/UI/AHPanel.lua: the panel at the auction house (SPEC §4.3, A24, FM24).

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")
local AHWorld = require("ah_world")

local NOW = 1760000000000

--- Log in with three planned buys (Linen ≤ 45c, Copper Bar ≤ 5s, Silk ≤ 70c) and a post, then open the AH.
---@return table, table  The panel's frames, the AH window
local function OpenAH()
  Stubs.Reset()
  UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48), [2840] = UIWorld.Item(500, 80, 40), [4306] = UIWorld.Item(70, 80, 60) },
                 NOW - 3600000)
  UIWorld.Plan({ { id = "a", kind = "buy", key = 2589, qty = 20, price = 45, at = NOW, net = 200 },
                 { id = "b", kind = "buy", key = 2840, qty = 6, price = 500, at = NOW, net = 300 },
                 { id = "c", kind = "buy", key = 4306, qty = 5, price = 70, at = NOW, net = 50 },
                 { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1000, chance = 92 } })
  Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse({ auctions = {} }))
  Stubs.Login()
  local ah = AHWorld.Install()
  Stubs.FireEvent("AUCTION_HOUSE_SHOW")
  return AHProfit.AHPanel.Frames(), ah
end

describe("AHPanel", function()
  it("A24 opens docked to the AH with every target still checking, and closes with it", function()
    local ui = OpenAH()
    assert_eq({ ui.frame:IsShown(), ui.checked:GetText(), ui.shop[1].item:GetText(), ui.shop[1].detail:GetText() },
              { true, "0 of 3", "Linen Cloth", "checking" })
    assert_eq({ ui.post[1].item:GetText(), ui.post[1].qty:GetText(), ui.postCount:GetText() }, { "Linen Cloth", "×20", "1" })
    Stubs.FireEvent("AUCTION_HOUSE_CLOSED")
    assert_eq(ui.frame:IsShown(), false)
  end)

  it("A24 a search result turns its row found; clicking a row searches it; Next search takes the next one", function()
    local ui, ah = OpenAH()
    ui.shop[2]:Click()
    assert_eq(ah.searches, { "Copper Bar" })
    C_AuctionHouse.GetBrowseResults = function() return { { itemKey = { itemID = 2589 }, minPrice = 38, totalQuantity = 20 } } end
    Stubs.FireEvent("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED")
    assert_eq({ ui.shop[1].detail:GetText(), ui.checked:GetText() },
              { AHProfit.ViewModel.WithCoins("38c ≤ 45c"), "1 of 3" })
    ui.next:Click()                                   -- Linen is found and Copper Bar was searched: Silk is next
    assert_eq(ah.searches, { "Copper Bar", "Silk Cloth" })
    ui.next:Click()
    assert_eq(ui.status:GetText(), "all searched")
  end)

  it("FM24 closing the AH drops the search queue; unsearched targets stay checking", function()
    local ui, ah = OpenAH()
    ui.next:Click()
    Stubs.FireEvent("AUCTION_HOUSE_CLOSED")
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    ui.next:Click()
    assert_eq(ah.searches, { "Linen Cloth", "Linen Cloth" })      -- searched again: the queue was rebuilt
    assert_eq(ui.shop[2].detail:GetText(), "checking")
  end)

  it("A24 Full scan asks for a ReplicateItems scan at most every 20 minutes", function()
    local ui = OpenAH()
    ui.scan:Click()
    assert_eq(ui.status:GetText(), "scanning…")
    ui.scan:Click()
    assert_eq(ui.status:GetText():match("^next scan in %d+ min$") ~= nil, true)
  end)

  it("without the AH search bar a search says so instead of failing", function()
    local ui, ah = OpenAH()
    ah.SearchBar = nil
    ui.shop[1]:Click()
    assert_eq(ui.status:GetText(), "search unavailable")
  end)
end)
