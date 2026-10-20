--- A stub auction house window for the AH panel specs: a frame named AuctionHouseFrame with a
--- search bar that records what was searched.

local AHWorld = {}

--- Install the AH window (after Stubs.Reset, before opening the AH).
---@return table  The window; .searches lists the names searched
function AHWorld.Install()
  local frame = CreateFrame("Frame", "AuctionHouseFrame", UIParent)
  frame:SetSize(800, 450)
  frame:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 100, -100)
  frame.searches = {}
  frame.SearchBar = {
    SetSearchText = function(_, text) frame.pending = text end,
    StartSearch = function() frame.searches[#frame.searches + 1] = frame.pending end,
  }
  return frame
end

--- Give the AH window a Sell tab (retail's shape) that records what is put on it.
---@param frame table      From AHWorld.Install
---@param available number Units GetAvailablePostCount answers
---@return table           The commodities sell frame; .price and .quantity hold what was set
function AHWorld.InstallSell(frame, available)
  local Stubs = require("wow_stubs")
  local house = _G.C_AuctionHouse or {}
  house.GetItemCommodityStatus = function() return 1 end
  house.GetAvailablePostCount = function() return available end
  house.IsSellItemValid = function() return true end
  Stubs.Define("C_AuctionHouse", house)
  Stubs.Define("Enum", { ItemCommodityStatus = { Commodity = 1, Item = 2 } })
  Stubs.Define("ItemLocation", { CreateFromBagAndSlot = function(_, bag, slot) return { bag = bag, slot = slot } end })
  frame.SetPostItem = function(_, location) frame.posted = location end
  frame.CommoditiesSellFrame = {
    SetPrice = function(self, price) self.price = price end,
    SetQuantity = function(self, quantity) self.quantity = quantity end,
  }
  return frame.CommoditiesSellFrame
end

--- UI7's world with A82's unfunded elixir: five planned buys (Linen and the gloves found, Copper Bar gone, Silk and
--- Wool still checking), a post of 5 Copper Rods, and the portfolio's Elixir of Fortitude, which the plan left out
--- for liquidity (40 planned, 3 sold a day), with 40 of them in the bags. Opens the AH and runs the searches.
---@return table, table  The panel's frames, the AH window
function AHWorld.Unfunded()
  local Stubs, UIWorld = require("wow_stubs"), require("ui_world")
  local NOW = 1760000000000
  Stubs.Reset()
  Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 6217, count = 5 }, [2] = { id = 3825, count = 40 } } } }
  Stubs.ITEM_NAMES[6586], Stubs.ITEM_NAMES[3825] = "Scouting Gloves of the Monkey", "Elixir of Fortitude"
  UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48), [6586] = UIWorld.Item(800, 50, 3), [2840] = UIWorld.Item(500, 80, 40),
                   [4306] = UIWorld.Item(70, 80, 60), [2592] = UIWorld.Item(30, 80, 60),
                   [3825] = UIWorld.Item(300, 60, 3, { postPrice = 350 }) }, NOW - 3600000)
  UIWorld.Plan({ { id = "a", kind = "buy", key = 2589, qty = 20, price = 45, at = NOW, net = 200 },
                 { id = "b", kind = "buy", key = 6586, qty = 1, price = 900, at = NOW, net = 300 },
                 { id = "c", kind = "buy", key = 2840, qty = 6, price = 500, at = NOW, net = 50 },
                 { id = "d", kind = "buy", key = 4306, qty = 5, price = 70, at = NOW, net = 50 },
                 { id = "e", kind = "buy", key = 2592, qty = 5, price = 30, at = NOW, net = 50 },
                 { id = "p", kind = "post", key = 6217, qty = 5, price = 4100, at = NOW, net = 6306, chance = 58 } })
  UIWorld.Portfolio({ "buy_later:3825:Amaya-Grommash:-/now/now:1;buy_later;Amaya-Grommash;3825;40;;12000;0;900;350;"
                      .. (NOW + 8 * 3600000) .. ";;60;;liquidity" }, NOW - 3600000, 314000)
  Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse({ auctions = {} }))
  Stubs.world.money = 314000
  Stubs.Login()
  local ah = AHWorld.Install()
  Stubs.FireEvent("AUCTION_HOUSE_SHOW")
  C_AuctionHouse.GetBrowseResults = function()
    return { { itemKey = { itemID = 2589 }, minPrice = 38, totalQuantity = 20 },
             { itemKey = { itemID = 6586 }, minPrice = 800, totalQuantity = 1 },
             { itemKey = { itemID = 2840 }, minPrice = 900, totalQuantity = 6 } }
  end
  Stubs.FireEvent("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED")
  return AHProfit.AHPanel.Frames(), ah
end

--- Take AHWorld.Unfunded's names out of the client's cache again.
function AHWorld.Unname()
  local Stubs = require("wow_stubs")
  Stubs.ITEM_NAMES[6586], Stubs.ITEM_NAMES[3825] = nil, nil
end

return AHWorld
