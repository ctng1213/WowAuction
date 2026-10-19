-- Specs for addon/AHProfit/Capture/Purchases.lua: purchase events (SPEC §2.2.1, A45, A46).

local Stubs = require("wow_stubs")

--- Log in with an AH whose purchase calls do nothing, and open it.
---@return table  The fake C_AuctionHouse
local function Boot()
  Stubs.Reset()
  local house = { StartCommoditiesPurchase = function() end, ConfirmCommoditiesPurchase = function() end,
                  PlaceBid = function() end,
                  GetAuctionInfoByID = function() return { itemKey = { itemID = 4240 }, quantity = 1, buyoutAmount = 200 } end }
  Stubs.Define("C_AuctionHouse", house)
  Stubs.Login()
  Stubs.FireEvent("AUCTION_HOUSE_SHOW")
  return house
end

--- The purchase events, as "key qty price source confirmed".
---@return string[]
local function Purchases()
  local out = {}
  for _, e in ipairs(AHProfitDB.events) do
    out[#out + 1] = table.concat({ e.key, e.qty, e.price, e.source, tostring(e.confirmed) }, " ")
  end
  return out
end

describe("Purchases", function()
  it("A45 a commodity purchase at the quoted price is confirmed when it succeeds; a buyout when it completes", function()
    local house = Boot()
    house.StartCommoditiesPurchase(3239, 4)
    Stubs.FireEvent("COMMODITY_PRICE_UPDATED", 10, 40)
    house.ConfirmCommoditiesPurchase(3239, 4)
    assert_eq(Purchases(), { "3239 4 10 ah false" })
    Stubs.FireEvent("COMMODITY_PURCHASE_SUCCEEDED")
    house.StartCommoditiesPurchase(3239, 3)
    house.ConfirmCommoditiesPurchase(3239, 3)
    Stubs.FireEvent("COMMODITY_PURCHASE_FAILED")
    house.PlaceBid(7, 150)                                     -- a bid: no purchase
    house.PlaceBid(8, 200)                                     -- the buyout
    Stubs.FireEvent("AUCTION_HOUSE_PURCHASE_COMPLETED", 8)
    Stubs.FireEvent("AUCTION_HOUSE_PURCHASE_COMPLETED", 99)    -- not ours
    assert_eq(Purchases(), { "3239 4 10 ah true", "3239 3 10 ah false", "4240 1 200 ah true" })
    assert_eq({ AHProfit.Live.Bought(3239), AHProfit.Live.Bought(4240) }, { 4, 1 })
  end)

  it("A46 a merchant purchase is a confirmed vendor purchase at the price per unit", function()
    Stubs.Reset()
    Stubs.ITEM_NAMES[4470] = "Simple Wood"
    Stubs.Define("BuyMerchantItem", function() end)
    Stubs.Login()
    Stubs.Define("GetMerchantItemLink", function() return Stubs.ItemLink(4470) end)
    Stubs.Define("GetMerchantItemInfo", function() return "Simple Wood", "icon", 38, 1, -1, true, true, false end)
    BuyMerchantItem(1, 9)
    assert_eq(Purchases(), { "4470 9 38 vendor true" })
    Stubs.ITEM_NAMES[4470] = nil
  end)
end)
