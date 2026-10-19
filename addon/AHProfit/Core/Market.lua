-- Core/Market.lua: reads MarketIntelligence.lua, written by AHSync (SPEC §3.5, §5.1, §5.2).
-- The file is checked once (schema version and checksum) the first time anything asks, and
-- items stay compact strings until one is needed; decoded items sit in a small cache, so
-- login never builds 9k tables (SPEC §5.1 lazy data decode). Format: ahsync/export/market_lua.py.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter

local Market = {}
AHProfit.Market = Market

Market.SCHEMA_VERSION = 1          -- must equal ahsync/export/market_lua.py MARKET_SCHEMA_VERSION (FM17)
Market.CACHE_LIMIT = 500           -- decoded items kept before the cache starts again
Market.FIELDS = { "cur", "curQty", "curAgeMin", "med7", "med30", "p10", "p90", "trend10", "sold10", "listed10",
                  "supply", "conf", "seasonality", "postPrice", "refStack", "sellValue", "volumeUnknown",
                  "chance1000", "typicalSupply" }   -- typicalSupply: median daily listed units (EX10)

local state = nil                  -- { status, market = { I, C, observationsUntil, curveHour }, file } once checked
local cache, cached = {}, 0

--- Count and total length of every item and curve string, the file's checksum.
---@param markets table  AHProfitMarket.markets
---@return string        "<strings>:<total length>"
local function Checksum(markets)
  local count, length = 0, 0
  for _, m in pairs(markets) do
    for _, text in pairs(m.I or {}) do count, length = count + 1, length + #text end
    for _, text in pairs(m.C or {}) do count, length = count + 1, length + #text end
    for _, text in pairs(m.D or {}) do count, length = count + 1, length + #text end
  end
  return count .. ":" .. length
end

--- The market of this character: the file's map, else the only market of its faction (S12).
---@param file table      AHProfitMarket
---@param charId string   "Name-Realm"
---@param faction string  "Horde" | "Alliance"
---@return string|nil     Market id
local function MarketOf(file, charId, faction)
  local named = file.charMarkets and file.charMarkets[charId]
  if named and file.markets[named] then return named end
  local suffix, found, count = "." .. string.lower(faction or "") .. ".us", nil, 0
  for id in pairs(file.markets) do
    if id:sub(-#suffix) == suffix then found, count = id, count + 1 end
  end
  return count == 1 and found or nil
end

--- Check a MarketIntelligence.lua table (FM17).
---@param file table|nil  The AHProfitMarket global
---@param charId string   This character
---@param faction string  Its faction
---@return table          { status = "ok" | "missing" | "version" | "checksum" | "market", market, file }
function Market.Check(file, charId, faction)
  if type(file) ~= "table" or type(file.markets) ~= "table" then return { status = "missing" } end
  if file.schemaVersion ~= Market.SCHEMA_VERSION then return { status = "version" } end
  if file.checksum ~= Checksum(file.markets) then return { status = "checksum" } end
  local id = MarketOf(file, charId, faction)
  if not id then return { status = "market" } end
  return { status = "ok", market = file.markets[id], marketId = id, file = file }
end

--- The checked file, checking it the first time.
---@return table  As Market.Check
local function State()
  if not state then
    local info = Adapter.PlayerInfo()
    state = Market.Check(_G.AHProfitMarket, info.name .. "-" .. info.realm, info.faction)
  end
  return state
end

--- Why there is no market data, for the UI's status line (UI4, FM17).
---@return string|nil  nil when the data is usable; otherwise a message for the player
function Market.Problem()
  local status = State().status
  if status == "ok" then return nil end
  return ({
    missing = "No market data yet. Run AHSync, then /reload.",
    version = "Market data and addon versions differ. Update AHProfit or AHSync, then /reload.",
    checksum = "Market data is damaged. Let AHSync write it again, then /reload.",
    market = "No market data for this character's realm. Check AHSync's Status page.",
  })[status]
end

--- Split "a:b:c:d:e/..." windows.
---@param text string  Windows, best first
---@return table[]     { start, hours, best, how, weeks } each; how as a ratio
local function DecodeWindows(text)
  local out = {}
  for s, h, b, r, w in text:gmatch("(%d+):(%d+):(%d+):(%d+):(%d+)") do
    out[#out + 1] = { start = tonumber(s), hours = tonumber(h), best = tonumber(b), how = tonumber(r) / 1000,
                      weeks = tonumber(w) }
  end
  return out
end

--- Decode one item string (format in ahsync/export/market_lua.py, EX2).
---@param text string  "numbers;sellWindows;buyWindows"
---@return table       Fields by name (nil = unavailable), sold/listed per day as numbers, trend in %/day
function Market.Decode(text)
  local numbers, sell, buy = text:match("^([^;]*);([^;]*);(.*)$")
  local item, i = {}, 0
  for field in (numbers .. ","):gmatch("([^,]*),") do
    i = i + 1
    item[Market.FIELDS[i]] = tonumber(field)
  end
  item.trend = item.trend10 and item.trend10 / 10
  item.soldPerDay = item.sold10 and item.sold10 / 10
  item.listedPerDay = item.listed10 and item.listed10 / 10
  item.volumeUnknown = item.volumeUnknown == 1
  item.chance = item.chance1000 and item.chance1000 / 1000      -- nil in a file from before this field
  item.sellWindows, item.buyWindows = DecodeWindows(sell), DecodeWindows(buy)
  return item
end

--- One item's market data, decoded on first use.
---@param key number   Item key
---@return table|nil   As Market.Decode; nil when the file has no such item or isn't usable
function Market.Item(key)
  local s = State()
  if s.status ~= "ok" then return nil end
  local item = cache[key]
  if item then return item end
  local text = s.market.I[key]
  if not text then return nil end
  if cached >= Market.CACHE_LIMIT then cache, cached = {}, 0 end
  item = Market.Decode(text)
  cache[key], cached = item, cached + 1
  return item
end

--- One item's sell-probability curve (SPEC §3.1.1).
---@param key number  Item key
---@return table|nil  { rows, aheadPoints, refreshMs }; nil without a curve
function Market.Curve(key)
  local s = State()
  if s.status ~= "ok" then return nil end
  local text = s.market.C[key]
  if not text then return nil end
  local SellProb = AHProfit.Engine.SellProb
  local full = #text == 24 * #SellProb.CURVE_AHEAD
  return { rows = SellProb.DecodeCurve(text, full and #SellProb.CURVE_AHEAD or 3),
           aheadPoints = full and SellProb.CURVE_AHEAD or { 0, 5, 20 }, refreshMs = s.market.curveHour * 3600000 }
end

--- When the file was written and the newest observation it holds, for the age badge.
---@return number|nil  generatedAt, UTC ms
---@return number|nil  observationsUntil, UTC ms
function Market.Times()
  local s = State()
  if s.status ~= "ok" then return nil, nil end
  return s.file.generatedAt, s.market.observationsUntil
end

--- Forget the checked file and the cache (tests, and a future reload of the data).
function Market.Reset()
  state, cache, cached = nil, {}, 0
end

--- What a merchant charges for an item it sells in unlimited supply (EX6).
---@param key number
---@return number|nil  Copper; nil when no vendor is known to sell it, or without usable data
function Market.VendorPrice(key)
  local s = State()
  if s.status ~= "ok" then return nil end
  return (s.file.vendor or {})[key]
end

--- Items one could disenchant, with what that takes (A23).
---@return table  key -> { skill, quality, ilvl, kind = "armor" | "weapon" }; empty without usable data
function Market.Disenchantable()
  local s = State()
  if s.status ~= "ok" then return {} end
  local out = {}
  for key, text in pairs(s.market.D or {}) do
    local skill, quality, ilvl, kind = text:match("^(%d+),(%d+),(%d+),([aw])$")
    if skill then
      out[key] = { skill = tonumber(skill), quality = tonumber(quality), ilvl = tonumber(ilvl),
                   kind = kind == "w" and "weapon" or "armor" }
    end
  end
  return out
end
