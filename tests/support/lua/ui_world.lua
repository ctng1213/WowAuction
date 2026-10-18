--- Builders for the window specs: MarketIntelligence.lua and PortfolioPlan.lua tables in the
--- exported format (ahsync/export/market_lua.py, plan_lua.py), checksums included.

local UIWorld = {}

UIWorld.MARKET = "forever.pvp.horde.us"
UIWorld.HOUR = 3600000

--- Install AHProfitMarket with these items.
---@param items table   key -> item string ("numbers;sellWindows;buyWindows")
---@param generatedAt number  ms
function UIWorld.Market(items, generatedAt)
  local count, length = 0, 0
  for _, text in pairs(items) do count, length = count + 1, length + #text end
  _G.AHProfitMarket = { schemaVersion = 1, generatedAt = generatedAt, checksum = count .. ":" .. length,
                        charMarkets = { ["Amaya-Grommash"] = UIWorld.MARKET },
                        markets = { [UIWorld.MARKET] = { observationsUntil = generatedAt, curveHour = 0, I = items, C = {} } } }
end

--- An item string with a price, confidence and sales per day, and optionally its sell options.
---@param cur number          Copper
---@param conf number         Confidence %
---@param sold number         Units sold per day
---@param sell table|nil      { postPrice, sellValue, chance (0-1), refStack }
---@return string
function UIWorld.Item(cur, conf, sold, sell)
  sell = sell or {}
  -- cur, curQty, curAgeMin, med7, med30, p10, p90, trend10, sold10, listed10, supply, conf, seasonality,
  -- postPrice, refStack, sellValue, volumeUnknown, chance1000
  local fields = { cur, 20, 30, "", "", "", "", 0, sold * 10, "", 90, conf, "", sell.postPrice or "", sell.refStack or 20,
                   sell.sellValue or "", 0, sell.chance and math.floor(sell.chance * 1000 + 0.5) or "" }
  return table.concat(fields, ",") .. ";;"
end

--- Install AHProfitPlan with these actions for Amaya (and any other characters named in them).
---@param actions table[]  { id, kind, key, qty, price, at, net, parent, chance, peak, char }
function UIWorld.Plan(actions)
  local byChar, count, length = {}, 0, 0
  for _, a in ipairs(actions) do
    local text = table.concat({ a.id, a.kind, a.key, a.qty, a.price or "", a.at, a.net or "", a.parent or "", "",
                                a.status or "pending", a.chance or "", a.peak or "" }, ";")
    local char = a.char or "Amaya-Grommash"
    byChar[char] = byChar[char] or {}
    table.insert(byChar[char], text)
    count, length = count + 1, length + #text
  end
  _G.AHProfitPlan = { schemaVersion = 1, generatedAt = 0, checksum = count .. ":" .. length, ingestedUntil = {},
                      actions = byChar, rules = {} }
end

return UIWorld
