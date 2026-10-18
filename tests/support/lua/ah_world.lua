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

return AHWorld
