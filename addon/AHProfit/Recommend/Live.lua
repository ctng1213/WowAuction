-- Recommend/Live.lua: live prices from the last complete in-game scan (SPEC §4.0). A scan newer
-- than the market file's newest observation overrides its current prices for this session:
-- each item's listings come straight from the scanner's in-memory book, a table lookup.

local AHProfit = AHProfit
local Replicate = AHProfit.Scan.Replicate

local Live = {}
AHProfit.Live = Live

--- The units listed per unit price for an item, cheapest first, from a book.
---@param prices table|nil  unit price -> stack * 5 + band -> count
---@return table[]          { price, units } ascending; empty when the item isn't listed
function Live.Levels(prices)
  local levels = {}
  for price, slots in pairs(prices or {}) do
    local units = 0
    for slot, count in pairs(slots) do units = units + (slot - slot % 5) / 5 * count end
    levels[#levels + 1] = { price = price, units = units }
  end
  table.sort(levels, function(a, b) return a.price < b.price end)
  return levels
end

--- The live book, when it is newer than the market data.
---@param observationsUntil number|nil  The market file's newest observation, ms
---@return table|nil  Replicate.LastBook(); nil when there is none or it is older
function Live.Book(observationsUntil)
  local last = Replicate.LastBook()
  if not last or (observationsUntil and last.at <= observationsUntil) then return nil end
  return last
end

--- An item's live listings, or nil without a newer scan.
---@param key number
---@param observationsUntil number|nil
---@return table[]|nil  As Live.Levels; nil means "use the market file"
function Live.Item(key, observationsUntil)
  local last = Live.Book(observationsUntil)
  if not last then return nil end
  return Live.Levels(last.book[key])
end

--- Units listed at or below a price.
---@param levels table[]  From Live.Levels
---@param price number
---@return number
function Live.Ahead(levels, price)
  local units = 0
  for _, level in ipairs(levels) do
    if level.price <= price then units = units + level.units end
  end
  return units
end
