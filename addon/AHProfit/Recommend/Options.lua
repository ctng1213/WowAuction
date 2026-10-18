-- Recommend/Options.lua: the addon's own recommendations (SPEC §4.0), so it works without AHSync.
-- They choose between options AHSync precomputed in MarketIntelligence.lua, for the state the
-- addon sees right now; no history, no simulation. This first part sells what you carry: each
-- unbound item in your bags with market data gets its best option, posting now (its sell value
-- and chance) or a merchant, unless AHSync's plan already covers it (A15-A18).

local AHProfit = AHProfit

local Options = {}
AHProfit.Options = Options

--- Total units per item of the unbound stacks in a list of bag rows.
---@param bags table[]  { key, qty, bound } (Inventory.Decode)
---@return table        key -> units
local function Units(bags)
  local units = {}
  for _, row in ipairs(bags) do
    if not row.bound then units[row.key] = (units[row.key] or 0) + row.qty end
  end
  return units
end

--- The best way to sell one item you carry, or nil when neither pays (A15, A17).
---@param key number           Item key
---@param qty number           Units carried
---@param item table|nil       Market.Item(key)
---@param vendorEach number|nil  What a merchant pays for one, copper
---@param nowMs number
---@param charId string
---@return table|nil           A plan-shaped action (kind post | vendor), own = true
function Options.SellOne(key, qty, item, vendorEach, nowMs, charId)
  if not item then return nil end
  local postEach, vendor = item.sellValue, vendorEach or 0
  local action = { id = "own:" .. key, key = key, qty = qty, at = nowMs, status = "pending", char = charId, own = true }
  if postEach and item.postPrice and postEach >= vendor and postEach > 0 then
    action.kind, action.price, action.net = "post", item.postPrice, postEach * qty
    action.chance = item.chance and math.floor(item.chance * 100 + 0.5) or nil
    action.ownNote = "post now · AHSync has no plan for it"
    return action
  end
  if vendor > 0 then
    action.kind, action.price, action.net = "vendor", vendor, vendor * qty
    action.ownNote = "a merchant pays more than the AH"
    return action
  end
  return nil
end

--- The addon's own sell actions for what a character carries (A15-A18).
---@param bags table[]       { key, qty, bound } rows of the character's bags
---@param planned table      key -> true for items AHSync's plan already has an action for
---@param itemOf function    key -> Market.Item(key) or nil
---@param vendorOf function  key -> merchant price each, or nil
---@param nowMs number
---@param charId string
---@return table[]           Plan-shaped actions, by key
function Options.Sell(bags, planned, itemOf, vendorOf, nowMs, charId)
  local units, keys, out = Units(bags), {}, {}
  for key in pairs(units) do
    if not planned[key] then keys[#keys + 1] = key end
  end
  table.sort(keys)
  for _, key in ipairs(keys) do
    local action = Options.SellOne(key, units[key], itemOf(key), vendorOf(key), nowMs, charId)
    if action then out[#out + 1] = action end
  end
  return out
end
