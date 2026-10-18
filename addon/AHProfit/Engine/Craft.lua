-- Engine/Craft.lua: live craft-cost checks (SPEC §3.4B mat sourcing, §4.3 "re-checked live").
-- Port of walk_book, fill and craft_option in ahsync/engine/craft.py; keep them identical,
-- including the order of floating-point operations and the offer sort order (LP1, shared
-- scenarios C1-C3). Crafted intermediates (chains) are planned by AHSync only: the addon's
-- Option sources raw mats (owned, vendor, AH) and never crafts a reagent first.

local AHProfit = AHProfit
AHProfit.Engine = AHProfit.Engine or {}

local Craft = {}
AHProfit.Engine.Craft = Craft

Craft.KINDS = { "owned", "vendor", "ah", "crafted" }   -- tie order at an equal unit cost

--- Cost of buying qty units from the AH, cheapest listing first (SPEC §3.4 reagent cost).
---@param book table[]  { { price, units }, ... } in any order
---@param qty number    Units to buy
---@return number|nil   Total copper; nil when the book doesn't hold enough units
function Craft.WalkBook(book, qty)
  local levels = {}
  for i, level in ipairs(book) do levels[i] = level end
  table.sort(levels, function(a, b) return a[1] < b[1] or (a[1] == b[1] and a[2] < b[2]) end)
  local total, need = 0, qty
  for _, level in ipairs(levels) do
    local take = math.min(need, level[2])
    total = total + level[1] * take
    need = need - take
    if need <= 0 then return total end
  end
  if need > 0 then return nil end
  return total
end

--- Offers for one reagent, sorted like Python's tuples: (unit cost, kind rank, book index).
---@return table[]  { cost, rank, index, units } per offer
local function Offers(owned, holdValue, vendorPrice, book, craftedCost)
  local offers = { { holdValue, 0, -1, owned } }
  if vendorPrice ~= nil then offers[#offers + 1] = { vendorPrice, 1, -1, math.huge } end
  for i, level in ipairs(book) do offers[#offers + 1] = { level[1], 2, i - 1, level[2] } end
  if craftedCost ~= nil then offers[#offers + 1] = { craftedCost, 3, -1, math.huge } end
  table.sort(offers, function(a, b)
    if a[1] ~= b[1] then return a[1] < b[1] end
    if a[2] ~= b[2] then return a[2] < b[2] end
    return a[3] < b[3]
  end)
  return offers
end

--- Meet one reagent's need from its cheapest sources (SPEC §3.4B mat sourcing).
---@param need number              Units needed
---@param owned number             Units you own and may use
---@param holdValue number         Opportunity cost per owned unit, float copper
---@param vendorPrice number|nil   Vendor buy price per unit; nil when no vendor sells it
---@param book table[]             AH { price, units } levels, in book order
---@param craftedCost number|nil   Cost per unit when crafted by you; nil when it can't be
---@param craftedGold number|nil   Copper spent per crafted unit (default 0)
---@return table                   { cost, gold, used = { owned, vendor, ah, crafted }, book_taken = {...}, short }
function Craft.Fill(need, owned, holdValue, vendorPrice, book, craftedCost, craftedGold)
  craftedGold = craftedGold or 0.0
  local used = { owned = 0.0, vendor = 0.0, ah = 0.0, crafted = 0.0 }
  local taken = {}
  for i = 1, #book do taken[i] = 0.0 end
  local cost, gold, left = 0.0, 0.0, need
  for _, offer in ipairs(Offers(owned, holdValue, vendorPrice, book, craftedCost)) do
    if left <= 0 then break end
    local take = math.min(left, offer[4])
    if take > 0 then
      local kind = Craft.KINDS[offer[2] + 1]
      used[kind] = used[kind] + take
      cost = cost + offer[1] * take
      local unitGold = offer[1]
      if kind == "crafted" then unitGold = craftedGold end
      if kind == "owned" then gold = gold + 0.0 else gold = gold + unitGold * take end
      if offer[3] >= 0 then taken[offer[3] + 1] = taken[offer[3] + 1] + take end
      left = left - take
    end
  end
  return { cost = cost, gold = gold, used = used, book_taken = taken, short = math.max(0.0, left) }
end

Craft.MAX_CRAFTS = 100     -- craft.MAX_CRAFTS: a backstop for flat product values

--- A copy of a book's levels that crafts can use up.
---@param books table  key -> { { price, units }, ... }
---@return table       A deep copy
local function CopyBooks(books)
  local out = {}
  for key, levels in pairs(books) do
    out[key] = {}
    for i, level in ipairs(levels) do out[key][i] = { level[1], level[2] } end
  end
  return out
end

--- One craft's fills from what is left: each reagent is used up only when it can be met in full.
---@param recipe table  { reagents = { { key, count } } }
---@param market table  { owned, hold, vendor, books }
---@param left table    { owned = key -> units, books = key -> levels }; used up in place
---@return table[]      { key, fill } per reagent
local function TakeAll(recipe, market, left)
  local fills = {}
  for i, reagent in ipairs(recipe.reagents) do
    local key, count = reagent[1], reagent[2]
    local book = left.books[key] or {}
    local f = Craft.Fill(count, left.owned[key] or 0.0, market.hold[key] or 0.0, market.vendor[key], book, nil, 0.0)
    if f.short <= 0 then
      left.owned[key] = (left.owned[key] or 0.0) - f.used.owned
      for j, level in ipairs(book) do level[2] = level[2] - f.book_taken[j] end
    end
    fills[i] = { key = key, fill = f }
  end
  return fills
end

--- Add a craft's fills to the option's tallies (craft._record without crafted reagents).
---@param option table
---@param fills table[]  From TakeAll
local function Record(option, fills)
  for _, kf in ipairs(fills) do
    local f = kf.fill
    if f.used.owned > 0 then option.ownedUsed[kf.key] = (option.ownedUsed[kf.key] or 0.0) + f.used.owned end
    if f.used.ah > 0 or f.used.vendor > 0 then
      option.bought[kf.key] = (option.bought[kf.key] or 0.0) + f.used.ah + f.used.vendor
    end
    if f.used.vendor > 0 then option.fromVendor[kf.key] = (option.fromVendor[kf.key] or 0.0) + f.used.vendor end
  end
end

--- How many crafts of a recipe pay, and what they need (craft.craft_option, SPEC §3.4B). Crafts are
--- added one at a time while each still makes a profit.
---@param recipe table      { reagents = { { key, count } }, crafters = n, cooldown = s or nil }
---@param market table      { owned, hold, vendor, books } (key -> units, value each, buy price, levels)
---@param productNet function  n -> expected net of n crafts' output, float copper
---@param budget number|nil Most copper to spend on mats
---@param maxCrafts number|nil Most crafts (craft_option's max_crafts); default MAX_CRAFTS
---@return table            { crafts, matGold, profit, profits, ownedUsed, bought, fromVendor, missing }
function Craft.Option(recipe, market, productNet, budget, maxCrafts)
  local option = { crafts = 0, matGold = 0.0, profit = 0.0, profits = {}, ownedUsed = {}, bought = {}, fromVendor = {} }
  local limit = (recipe.cooldown and recipe.cooldown > 0) and (recipe.crafters or 1) or (maxCrafts or Craft.MAX_CRAFTS)
  local left = { owned = {}, books = CopyBooks(market.books) }
  for key, units in pairs(market.owned) do left.owned[key] = units end
  while option.crafts < limit do
    local saved = { owned = {}, books = CopyBooks(left.books) }
    for key, units in pairs(left.owned) do saved.owned[key] = units end
    local fills = TakeAll(recipe, market, left)
    local short, gold, cost = {}, 0.0, 0.0
    for _, kf in ipairs(fills) do
      if kf.fill.short > 0 then short[#short + 1] = { kf.key, kf.fill.short } end
      gold, cost = gold + kf.fill.gold, cost + kf.fill.cost
    end
    local marginal = productNet(option.crafts + 1) - productNet(option.crafts) - cost
    if #short > 0 or marginal <= 0 or (budget and option.matGold + gold > budget) then
      left = saved
      if #short > 0 and option.crafts == 0 then option.missing = short end
      break
    end
    Record(option, fills)
    option.matGold = option.matGold + gold
    option.profits[#option.profits + 1] = marginal
    option.profit = option.profit + marginal
    option.crafts = option.crafts + 1
  end
  return option
end

--- The option's Source label (craft.CraftOption.source without crafted reagents, C17, C18).
---@param option table  From Option
---@return string       "Owned", "AH", "Vendor" or "Mixed" (more than one of them)
function Craft.Source(option)
  local fromVendor = option.fromVendor or {}
  local owned, vendor, ah = next(option.ownedUsed) ~= nil, next(fromVendor) ~= nil, false
  for key, units in pairs(option.bought) do
    if units > (fromVendor[key] or 0.0) then ah = true end
  end
  local kinds = (owned and 1 or 0) + (vendor and 1 or 0) + (ah and 1 or 0)
  if kinds > 1 then return "Mixed" end
  if owned then return "Owned" end
  if vendor then return "Vendor" end
  return "AH"
end
