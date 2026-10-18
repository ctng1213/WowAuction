-- Specs for addon/AHProfit/UI/AHPanel.lua: the panel at the auction house (SPEC §4.3, A24, FM24).

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")
local AHWorld = require("ah_world")

local NOW = 1760000000000

--- Log in with three planned buys (Linen ≤ 45c, Copper Bar ≤ 5s, Silk ≤ 70c) and a post of the 20 Linen in the bags,
--- then open the AH.
---@return table, table  The panel's frames, the AH window
local function OpenAH()
  Stubs.Reset()
  Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2589, count = 20 } } } }   -- the Linen to post
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

--- Log in with 3 Copper Rods in bag 0 slot 2 and 4 in bag 1, and a plan to post 5 at 41s now; open the AH.
---@return table, table  The panel's frames, the AH window
local function RodsAtAH()
  Stubs.Reset()
  Stubs.world.containers = { [0] = { size = 2, slots = { [2] = { id = 6217, count = 3 } } },
                             [1] = { size = 1, slots = { [1] = { id = 6217, count = 4 } } } }
  UIWorld.Market({ [6217] = UIWorld.Item(4200, 80, 6) }, NOW - 3600000)
  UIWorld.Plan({ { id = "p", kind = "post", key = 6217, qty = 5, price = 4100, at = NOW, net = 6306, chance = 58 } })
  Stubs.Login()
  local ah = AHWorld.Install()
  Stubs.FireEvent("AUCTION_HOUSE_SHOW")
  return AHProfit.AHPanel.Frames(), ah
end

describe("AHPanel", function()
  it("A24 opens docked to the AH with every target still checking, and closes with it", function()
    local ui = OpenAH()
    assert_eq({ ui.frame:IsShown(), ui.checked:GetText(), ui.shop[1].item:GetText(), ui.shop[1].colA:GetText() },
              { true, "0 of 3", "Linen Cloth", "checking" })
    assert_eq(ui.shop[1].colB:GetText(), AHProfit.ViewModel.WithCoins("45c"))
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
    assert_eq({ ui.shop[1].colA:GetText(), ui.shop[1].colB:GetText(), ui.checked:GetText() },
              { AHProfit.ViewModel.WithCoins("38c"), AHProfit.ViewModel.WithCoins("45c"), "1 of 3" })
    ui.next:Click()                                   -- one per click, top to bottom, skipping what was searched
    assert_eq({ ah.searches, ui.status:GetText() }, { { "Copper Bar", "Linen Cloth" }, "searched Linen Cloth · 2 of 3" })
    ui.next:Click()
    assert_eq(ah.searches, { "Copper Bar", "Linen Cloth", "Silk Cloth" })
    ui.next:Click()
    assert_eq(ui.status:GetText(), "all searched this visit")
    ui.next:GetScript("OnEnter")(ui.next)
    assert_eq(Stubs.tooltip.lines[1], "Next search")
  end)

  it("FM24 closing the AH drops the search queue; unsearched targets stay checking", function()
    local ui, ah = OpenAH()
    ui.next:Click()
    Stubs.FireEvent("AUCTION_HOUSE_CLOSED")
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    ui.next:Click()
    assert_eq(ah.searches, { "Linen Cloth", "Linen Cloth" })      -- searched again: the queue was rebuilt
    assert_eq(ui.shop[2].colA:GetText(), "checking")
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

  it("an empty shopping list says why; the post list keeps Today's Min chance filter", function()
    Stubs.Reset()
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2589, count = 20 } } } }   -- the Linen to post
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW - 3600000)
    UIWorld.Plan({ { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1000, chance = 92 },
                   { id = "q", kind = "post", key = 2589, qty = 2, price = 55, at = NOW, net = 1, chance = 10 } })
    Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse({ auctions = {} }))
    Stubs.Login()
    AHWorld.Install()
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    local ui = AHProfit.AHPanel.Frames()
    assert_eq({ ui.empty:IsShown(), ui.empty:GetText(), ui.postCount:GetText(), ui.post[1].qty:GetText() },
              { true, "Nothing to buy right now.", "1", "×20" })   -- the 10% post is filtered
  end)

  it("UI7 each section has labelled columns, and every header explains itself on hover", function()
    local ui = OpenAH()
    local shop, post = {}, {}
    for i, h in ipairs(ui.shopHeaders) do shop[i] = h:GetText() end
    for i, h in ipairs(ui.postHeaders) do post[i] = h:GetText() end
    assert_eq({ shop, post }, { { "Item", "Price", "Max", "Qty" }, { "Item", "List price", "Qty", "Est. profit", "Chance" } })
    for _, headers in ipairs({ ui.shopHeaders, ui.postHeaders }) do
      for _, h in ipairs(headers) do
        h.hit:GetScript("OnEnter")(h.hit)
        assert_eq(Stubs.tooltip.lines[2] ~= nil, true, h:GetText() .. " has no tooltip text")
      end
    end
    -- 20 Linen listed at 55c each; the post's own expected profit is 1000c.
    local row = ui.post[1]
    assert_eq({ row.item:GetText(), row.price:GetText(), row.qty:GetText(), row.profit:GetText(), row.chance:GetText():match("^%d+%%") },
              { "Linen Cloth", AHProfit.ViewModel.WithCoins("55c"), "×20", AHProfit.ViewModel.WithCoins("+10s"), "92%" })
  end)

  it("A29 clicking a Post row fills the AH's Sell tab; you press Post", function()
    local ui, ah = RodsAtAH()
    local sell = AHWorld.InstallSell(ah, 7)
    ui.post[1]:Click()
    assert_eq({ ah.posted.bag, ah.posted.slot, sell.quantity, sell.price, #ah.searches }, { 0, 2, 5, 4100, 0 })
    assert_eq(ui.status:GetText(), AHProfit.ViewModel.WithCoins("Sell tab: ×5 at 41s each · press Post"))
    sell.price = 4199                                     -- the AH puts its own price in after its search
    Stubs.FireEvent("COMMODITY_SEARCH_RESULTS_UPDATED")
    assert_eq(sell.price, 4100)
    sell.price = 4199
    Stubs.FireEvent("COMMODITY_SEARCH_RESULTS_UPDATED")   -- only once
    assert_eq(sell.price, 4199)
    ui.post[1]:Click()
    Stubs.Advance(11)                                     -- too late: the price you set by hand stays
    sell.price = 4199
    Stubs.FireEvent("ITEM_SEARCH_RESULTS_UPDATED")
    assert_eq(sell.price, 4199)
    Stubs.world.containers = {}
    ui.post[1]:Click()
    assert_eq(ui.status:GetText(), "no Copper Rod in your bags")
  end)

  it("FM30 without the Sell tab's API, or for an item the AH won't take, a click says so", function()
    local ui, ah = RodsAtAH()
    ui.post[1]:Click()
    assert_eq(ui.status:GetText(), "Sell tab unavailable")
    local sell = AHWorld.InstallSell(ah, 7)
    C_AuctionHouse.IsSellItemValid = function() return false end
    ui.post[1]:Click()
    assert_eq({ ui.status:GetText(), sell.price }, { "Copper Rod can't be sold on the AH", nil })
  end)

  it("A30 the Post list shows only what is in your bags; a chain's later post waits", function()
    Stubs.Reset()
    Stubs.ITEM_NAMES[4471] = "Basic Campfire Kit"
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2589, count = 20 } } } }
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48), [4471] = UIWorld.Item(99, 80, 10) }, NOW - 3600000)
    UIWorld.Plan({ { id = "w", kind = "buy", key = 4470, qty = 7, price = 38, at = NOW, net = 0 },
                   { id = "c", kind = "craft", key = 4471, qty = 7, at = NOW, net = 392, parent = "w" },
                   { id = "k", kind = "post", key = 4471, qty = 7, price = 99, at = NOW, net = 392, parent = "w", chance = 100 },
                   { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1000, chance = 92 } })
    Stubs.Login()
    AHWorld.Install()
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    local ui = AHProfit.AHPanel.Frames()
    assert_eq({ ui.postCount:GetText(), ui.post[1].item:GetText(), ui.post[2]:IsShown() }, { "1", "Linen Cloth", false })
    Stubs.ITEM_NAMES[4471] = nil
  end)
end)
