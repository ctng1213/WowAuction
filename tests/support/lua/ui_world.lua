--- Builders for the window specs: MarketIntelligence.lua and PortfolioPlan.lua tables in the
--- exported format (ahsync/export/market_lua.py, plan_lua.py), checksums included.

local UIWorld = {}

UIWorld.MARKET = "forever.pvp.horde.us"
UIWorld.HOUR = 3600000

--- Install AHProfitMarket with these items.
---@param items table   key -> item string ("numbers;sellWindows;buyWindows")
---@param generatedAt number  ms
---@param flips table|nil     key -> "expected,price,slot" flip offers (EX11); default none
function UIWorld.Market(items, generatedAt, flips)
  local count, length = 0, 0
  for _, text in pairs(items) do count, length = count + 1, length + #text end
  _G.AHProfitMarket = { schemaVersion = 1, exportVersion = 4, generatedAt = generatedAt, checksum = count .. ":" .. length,
                        charMarkets = { ["Amaya-Grommash"] = UIWorld.MARKET },
                        markets = { [UIWorld.MARKET] = { observationsUntil = generatedAt, curveHour = 0, I = items, C = {},
                                                         F = flips or {} } } }
end

--- An item string with a price, confidence and sales per day, and optionally its sell options.
---@param cur number          Copper
---@param conf number         Confidence %
---@param sold number         Units sold per day
---@param sell table|nil      { postPrice, sellValue, chance (0-1), refStack, curQty (default 20), supply (default 90) }
---@return string
function UIWorld.Item(cur, conf, sold, sell)
  sell = sell or {}
  -- cur, curQty, curAgeMin, med7, med30, p10, p90, trend10, sold10, listed10, supply, conf, seasonality,
  -- postPrice, refStack, sellValue, volumeUnknown, chance1000
  local fields = { cur, sell.curQty or 20, 30, "", "", "", "", 0, sold * 10, "", sell.supply or 90, conf, "", sell.postPrice or "", sell.refStack or 20,
                   sell.sellValue or "", 0, sell.chance and math.floor(sell.chance * 1000 + 0.5) or "" }
  return table.concat(fields, ",") .. ";;"
end

--- Install AHProfitPlan with these actions for Amaya (and any other characters named in them).
--- AHSync only plans sales of stock you hold, so when the test set no bags, Amaya's posts, vendor sales
--- and disenchants that start no chain (no parent) put their units in her bags (A49).
---@param actions table[]  { id, kind, key, qty, price, at, net, parent, chance, peak, char, vendor, duration, to }
function UIWorld.Plan(actions)
  local Stubs = require("wow_stubs")
  if next(Stubs.world.containers) == nil then UIWorld.StockFor(actions) end
  local byChar, count, length = {}, 0, 0
  for _, a in ipairs(actions) do
    local text = table.concat({ a.id, a.kind, a.key, a.qty, a.price or "", a.at, a.net or "", a.parent or "", "",
                                a.status or "pending", a.chance or "", a.peak or "", a.vendor and "v" or "",
                                a.duration or "", a.to or "" }, ";")
    local char = a.char or "Amaya-Grommash"
    byChar[char] = byChar[char] or {}
    table.insert(byChar[char], text)
    count, length = count + 1, length + #text
  end
  _G.AHProfitPlan = { schemaVersion = 1, exportVersion = 4, generatedAt = 0, checksum = count .. ":" .. length,
                      ingestedUntil = {}, actions = byChar, rules = {} }
end

--- Put the units of Amaya's planned sales (no parent) in her bags, one stack per item.
---@param actions table[]  As UIWorld.Plan
function UIWorld.StockFor(actions)
  local Stubs = require("wow_stubs")
  local units, keys = {}, {}
  for _, a in ipairs(actions) do
    local mine = (a.char or "Amaya-Grommash") == "Amaya-Grommash"
    if mine and not a.parent and (a.kind == "post" or a.kind == "vendor" or a.kind == "de") and a.key > 0
       and a.key < 2147483648 then
      if not units[a.key] then keys[#keys + 1] = a.key end
      units[a.key] = (units[a.key] or 0) + a.qty
    end
  end
  table.sort(keys)
  local slots = {}
  for i, key in ipairs(keys) do slots[i] = { id = key, count = units[key] } end
  if #slots > 0 then Stubs.world.containers[0] = { size = math.max(16, #slots), slots = slots } end
end

return UIWorld
