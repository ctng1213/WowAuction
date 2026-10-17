-- Engine/Craft.lua: live craft-cost checks (SPEC §3.4B mat sourcing, §4.3 "re-checked live").
-- Port of walk_book and fill in ahsync/engine/craft.py; keep them identical, including the
-- order of floating-point operations and the offer sort order (LP1). Chains and the
-- "how many crafts" loop run in AHSync only.

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
