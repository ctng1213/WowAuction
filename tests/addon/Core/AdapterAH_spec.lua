-- Specs for addon/AHProfit/Core/AdapterAH.lua: the replicate scan reader and the Sell tab.

local Stubs = require("wow_stubs")

--- Fresh stubs with only the Adapter loaded.
---@return table  AHProfit.Adapter
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "Core/AdapterAH.lua" })
  return AHProfit.Adapter
end

describe("AdapterAH", function()
  it("reads a replicate auction 0-based as key, unit price, stack and band, caching keys by link", function()
    local Adapter = Boot()
    local house = { auctions = { Stubs.Auction(2840, 100, 5, 1), { id = 2589, count = 3, buyout = 100, left = 4 } } }
    Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse(house))
    local cache = {}
    assert_eq({ Adapter.ReadReplicateItem(0, cache) }, { 2840, 100, 5, 1, false })
    assert_eq({ Adapter.ReadReplicateItem(1, cache) }, { 2589, 33, 3, 4, false }, "100c for 3: floor(33.3) per unit")
    assert_eq(cache[Stubs.ItemLink(2840)], 2840)
    assert_eq(Adapter.HasReplicate(), true)
    assert_eq(Adapter.NumReplicateItems(), 2)
  end)

  it("a secret replicate value reads as missing, and no C_AuctionHouse means no replicate", function()
    local Adapter = Boot()
    assert_eq(Adapter.HasReplicate(), false)
    Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse({ auctions = { Stubs.Auction(2840, 100, 5, 1) } }))
    Stubs.Define("issecretvalue", function(v) return v == 5 end)   -- the stack count is secret
    assert_eq({ Adapter.ReadReplicateItem(0, {}) }, { nil, "count" }, "left out, with the reason")
  end)

  it("an item whose class the client can't tell is keyed -itemId when it has no link", function()
    local Adapter = Boot()
    Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse({ auctions = { { id = 2589, count = 1, buyout = 5, left = 2, noLink = true } } }))
    GetItemInfoInstant = function() return nil end
    assert_eq({ Adapter.ReadReplicateItem(0, {}) }, { -2589, 5, 1, 2, true }, "unknown class: never priced as its base item")
  end)

  it("fills a single-item Sell tab through its price and quantity inputs, capped at what can be posted", function()
    local Adapter = Boot()
    local set = {}
    Stubs.Define("C_AuctionHouse", { GetItemCommodityStatus = function() return 2 end, GetAvailablePostCount = function() return 1 end })
    Stubs.Define("Enum", { ItemCommodityStatus = { Commodity = 1, Item = 2 } })
    Stubs.Define("ItemLocation", { CreateFromBagAndSlot = function(_, bag, slot) return { bag, slot } end })
    Stubs.Define("AuctionHouseFrame", { SetPostItem = function() end, ItemSellFrame = {
      PriceInput = { SetAmount = function(_, v) set.price = v end },
      QuantityInput = { SetQuantity = function(_, v) set.qty = v end } } })
    assert_eq(Adapter.RefillSellPrice(10), false)                     -- nothing filled yet
    assert_eq({ Adapter.FillSell(0, 1, 3, 800), set.qty, set.price }, { 1, 1, 800 })
    assert_eq({ Adapter.RefillSellPrice(900), set.price }, { true, 900 })
    AuctionHouseFrame.ItemSellFrame = nil
    assert_eq({ Adapter.FillSell(0, 1, 3, 800) }, { nil, "unavailable" })
    AuctionHouseFrame.ItemSellFrame = {}                               -- neither input: quietly sets nothing
    assert_eq({ Adapter.FillSell(0, 1, 3, 800), Adapter.RefillSellPrice(5) }, { 1, false })
  end)
end)
