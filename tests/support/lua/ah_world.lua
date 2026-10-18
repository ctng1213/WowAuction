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

return AHWorld
