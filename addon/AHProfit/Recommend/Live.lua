-- Recommend/Live.lua: live prices from the last complete in-game scan (SPEC §4.0). A scan newer
-- than the market file's newest observation overrides its current prices for this session:
-- each item's listings come straight from the scanner's in-memory book, a table lookup.

local AHProfit = AHProfit
local Replicate = AHProfit.Scan.Replicate

local Live = {}
AHProfit.Live = Live

local searched = {}            -- item id -> { price, units, at } from AH search results this session (A24)
local bought = {}              -- item id -> units bought on the AH this session (A39)
local pending = {}             -- item id -> units of a commodity purchase not yet confirmed

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
  -- A search result is per item id; a random-suffix key matches it by its item id (A38).
  local itemId = key < 0 and -key or key % 2147483648
  local last, found = Live.Book(observationsUntil), searched[key] or searched[itemId]
  if found and (not observationsUntil or found.at > observationsUntil) and (not last or found.at > last.at) then
    if not found.price then return {} end                         -- searched, and none listed (A38)
    return { { price = found.price, units = found.units } }      -- a search newer than any scan (A24)
  end
  if not last then return nil end
  return Live.Levels(last.book[key])
end

--- Remember an AH search result: an item's cheapest price and units listed, seen now (A24).
--- Only keys without a random suffix match a search result's item id.
---@param itemId number
---@param price number|nil  Cheapest unit price, copper; nil when the search listed none (A38)
---@param units number
---@param atMs number
function Live.NoteSearch(itemId, price, units, atMs)
  searched[itemId] = { price = price, units = units, at = atMs }
end

--- Note an AH purchase (A39): a commodity purchase waits for its confirmation; a buyout counts at once.
---@param itemId number
---@param units number
---@param confirmed boolean  false for a commodity purchase still to be confirmed
function Live.NoteBought(itemId, units, confirmed)
  if confirmed then bought[itemId] = (bought[itemId] or 0) + units else pending[itemId] = (pending[itemId] or 0) + units end
end

--- The pending commodity purchases succeeded (true) or failed (false).
---@param succeeded boolean
function Live.SettlePurchases(succeeded)
  for itemId, units in pairs(pending) do
    if succeeded then bought[itemId] = (bought[itemId] or 0) + units end
  end
  pending = {}
end

--- Units of an item bought on the AH this session.
---@param key number  Item key; a random-suffix key counts by its item id
---@return number
function Live.Bought(key)
  return bought[key < 0 and -key or key % 2147483648] or 0
end

--- Forget search results and purchases (tests).
function Live.Reset()
  searched, bought, pending = {}, {}, {}
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
