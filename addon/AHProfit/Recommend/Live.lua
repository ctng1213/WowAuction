-- Recommend/Live.lua: live prices from the last complete in-game scan (SPEC §4.0). A scan newer
-- than the market file's newest observation overrides its current prices for this session:
-- each item's listings come straight from the scanner's in-memory book, a table lookup.

local AHProfit = AHProfit
local Replicate = AHProfit.Scan.Replicate

local Live = {}
AHProfit.Live = Live

local searched = {}            -- item id -> { price, units, at } from AH search results this session (A24)
local candidates = {}          -- { at, margin, list }: Live.FlipCandidates for the last book it was worked out for

--- Units listed at one price of a book.
---@param slots table  stack * 5 + band -> count
---@return number
local function LevelUnits(slots)
  local units = 0
  for slot, count in pairs(slots) do units = units + (slot - slot % 5) / 5 * count end
  return units
end

--- The units listed per unit price for an item, cheapest first, from a book.
---@param prices table|nil  unit price -> stack * 5 + band -> count
---@return table[]          { price, units } ascending; empty when the item isn't listed
function Live.Levels(prices)
  local levels = {}
  for price, slots in pairs(prices or {}) do
    levels[#levels + 1] = { price = price, units = LevelUnits(slots) }
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

--- Forget search results and worked-out flip candidates (tests).
function Live.Reset()
  searched, candidates = {}, {}
end

--- The two cheapest prices of one item's book, without building its levels.
---@param prices table  unit price -> slots
---@return number|nil, number|nil  Cheapest and second cheapest price
local function TwoCheapest(prices)
  local first, second
  for price in pairs(prices) do
    if not first or price < first then
      first, second = price, first
    elseif not second or price < second then
      second = price
    end
  end
  return first, second
end

--- Items in the live book whose cheapest level could be bought and relisted now just under the next one (A69):
--- (second − 1) × (1 − cut) ≥ (1 + margin) × cheapest, the test with a chance of 1, so the Flip tab prices only
--- these. Worked out once per book, not per redraw.
---@param observationsUntil number|nil  The market file's newest observation, ms
---@param margin number                 Config min_flip_margin
---@return table[]  { key, cheapest, second, units (at the cheapest) } by key; empty without a newer book
function Live.FlipCandidates(observationsUntil, margin)
  local last = Live.Book(observationsUntil)
  if not last then return {} end
  if candidates.at == last.at and candidates.margin == margin then return candidates.list end
  local keep, list = (1 + margin) / (1 - AHProfit.Engine.Fees.AH_CUT), {}
  for key, prices in pairs(last.book) do
    local first, second = TwoCheapest(prices)
    if second and key > 0 and second - 1 >= keep * first then
      list[#list + 1] = { key = key, cheapest = first, second = second, units = LevelUnits(prices[first]) }
    end
  end
  table.sort(list, function(a, b) return a.key < b.key end)
  candidates = { at = last.at, margin = margin, list = list }
  return list
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
