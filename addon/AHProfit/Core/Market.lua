-- Core/Market.lua: reads MarketIntelligence.lua, written by AHSync (SPEC §3.5, §5.1, §5.2).
-- The file is checked once (schema version, export version and checksum) the first time anything asks, and
-- items stay compact strings until one is needed; decoded items sit in a small cache, so
-- login never builds 9k tables (SPEC §5.1 lazy data decode). Candidates (K) decode the same way, one when asked;
-- the dependency graph (G) decodes once, when first asked. Formats: ahsync/export/market_lua.py and portfolio_lua.py.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter

local Market = {}
AHProfit.Market = Market

Market.SCHEMA_VERSION = 1          -- must equal ahsync/export/market_lua.py MARKET_SCHEMA_VERSION (FM17)
-- The build of both generated files' format; must equal ahsync/export/market_lua.py EXPORT_VERSION. A file of
-- another build says which side is stale (FM34, FM35). Core/Plan.lua, which loads after this file, reads it too.
AHProfit.EXPORT_VERSION = 6            -- 6: the tooltip's market lines (M33b); 5: market items end in salesDays (EX21); 4: K, G and the portfolio
                                       -- snapshot (EX17, EX18); 3: closes30 (TT23, PP4); 2: a mail's recipient (C22)
Market.CACHE_LIMIT = 500           -- decoded items kept before the cache starts again
Market.FIELDS = { "cur", "curQty", "curAgeMin", "med7", "med30", "p10", "p90", "trend10", "sold10", "listed10",
                  "supply", "conf", "seasonality", "postPrice", "refStack", "sellValue", "volumeUnknown",
                  "chance1000", "typicalSupply", "suspect", "closes30", "med14", "salesDays", "supply7", "supply14",
                  "sellChance1000", "st7", "st14", "st30", "stHow", "commodity" }   -- typicalSupply:
                                  -- median daily listed units (EX10); suspect: 1 when AHSync hasn't confirmed cur (A58);
                                  -- closes30: hourly closes in 30 days (TT23); med14: the 14-day median (EX20);
                                  -- salesDays: days of the last 7 with sales data behind sold10 (EX21, TT32);
                                  -- M33b (TT35-TT39): supply over 7 and 14 days, Sell Chance Now in thousandths,
                                  -- sell-through in thousandths per lookback, stHow its letters (m, e, c, -; text, not
                                  -- a number), commodity 1 or 0; trailing empty fields are left out

local state = nil                  -- { status, market = { I, C, observationsUntil, curveHour }, file } once checked
local cache, cached = {}, 0
local candidates, graph = {}, nil  -- decoded K strings by position; the decoded G

--- Count and total length of every item and curve string, the file's checksum.
---@param markets table  AHProfitMarket.markets
---@return string        "<strings>:<total length>"
local function Checksum(markets)
  local count, length = 0, 0
  for _, m in pairs(markets) do
    for _, text in pairs(m.I or {}) do count, length = count + 1, length + #text end
    for _, text in pairs(m.C or {}) do count, length = count + 1, length + #text end
    for _, text in pairs(m.D or {}) do count, length = count + 1, length + #text end
    for _, text in ipairs(m.K or {}) do count, length = count + 1, length + #text end
    local g = m.G or {}
    for _, text in pairs(g.R or {}) do count, length = count + 1, length + #text end
    for _, text in pairs(g.U or {}) do count, length = count + 1, length + #text end
    for _, text in ipairs({ g.DE or "", g.order or "" }) do
      if text ~= "" then count, length = count + 1, length + #text end   -- an empty one counts as no string
    end
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

--- Compare a generated file's export version with this addon's (FM34, FM35); shared with Core/Plan.lua.
---@param file table  AHProfitMarket or AHProfitPlan
---@return string|nil  "older" when AHSync wrote an older format (a missing field counts as 0), "newer" when a
---                    newer one; nil when they match
function Market.ExportStatus(file)
  local version = tonumber(file.exportVersion) or 0
  if version < AHProfit.EXPORT_VERSION then return "older" end
  if version > AHProfit.EXPORT_VERSION then return "newer" end
  return nil
end

--- Check a MarketIntelligence.lua table: schema, then export version, then checksum (FM17, FM34, FM35).
---@param file table|nil  The AHProfitMarket global
---@param charId string   This character
---@param faction string  Its faction
---@return table          { status = "ok" | "missing" | "version" | "older" | "newer" | "checksum" | "market", market,
---                       file }
function Market.Check(file, charId, faction)
  if type(file) ~= "table" or type(file.markets) ~= "table" then return { status = "missing" } end
  if file.schemaVersion ~= Market.SCHEMA_VERSION then return { status = "version" } end
  local stale = Market.ExportStatus(file)
  if stale then return { status = stale } end
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

-- What the player should do about a file of another build (FM34, FM35); Core/Plan.lua shows the same texts.
Market.STALE = {
  older = "AHSync is older than this addon: restart AHSync, then /reload",
  newer = "This addon is older than AHSync's files: run tools/deploy.py, then /reload",
}

--- Why there is no market data, for the UI's status line (UI4, FM17, FM34, FM35).
---@return string|nil  nil when the data is usable; otherwise a message for the player
function Market.Problem()
  local status = State().status
  if status == "ok" then return nil end
  return Market.STALE[status] or ({
    missing = "No market data yet. Run AHSync, then /reload.",
    version = "Market data and addon versions differ. Update AHProfit or AHSync, then /reload.",
    checksum = "Market data is damaged. Let AHSync write it again, then /reload.",
    market = "No market data for this character's realm. Check AHSync's Status page.",
  })[status]
end

--- Split "a:b:c:d:e:f/..." windows (f, the percentage, since M33b; empty when unknown).
---@param text string  Windows, best first
---@return table[]     { start, hours, best, how, weeks, pct } each; how as a ratio, pct in thousandths or nil
local function DecodeWindows(text)
  local out = {}
  for s, h, b, r, w, p in text:gmatch("(%d+):(%d+):(%d+):(%d+):(%d+):?(%d*)") do
    out[#out + 1] = { start = tonumber(s), hours = tonumber(h), best = tonumber(b), how = tonumber(r) / 1000,
                      weeks = tonumber(w), pct = tonumber(p) }
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
    local name = Market.FIELDS[i]                    -- a newer AHSync's extra fields: ignored
    if name == "stHow" then item.stHow = field ~= "" and field or nil
    elseif name then item[name] = tonumber(field) end
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
  item.supplyRow = s.market.S and s.market.S[key]             -- 24 supply factors for the shortlist (EX13)
  item.curveHour = s.market.curveHour
  cache[key], cached = item, cached + 1
  return item
end

--- One item's sell-probability curve (SPEC §3.1.1): 24 rows × 8 points for the shortlist; one row × 3 points (the
--- refresh hour's, 0, 5 and 20 ahead) for a tooltip-only item, which SellProb.Lookup uses at any hour (EX19).
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

--- The hours this market was observed in the last 30 days, for the tooltip header's coverage (TT40).
---@return number|nil  Hours of the last 720; nil without usable data or in a file from before M33b
function Market.Coverage30()
  local s = State()
  if s.status ~= "ok" then return nil end
  return s.market.coverage30
end

--- When the file was written and the newest observation it holds, for the age badge.
---@return number|nil  generatedAt, UTC ms
---@return number|nil  observationsUntil, UTC ms
function Market.Times()
  local s = State()
  if s.status ~= "ok" then return nil, nil end
  return s.file.generatedAt, s.market.observationsUntil
end

--- The time the age badge measures from (UI26): the newest auction snapshot behind this market's data, so
--- "data 32 min old" is how old the prices are, not when AHSync last exported; a file without it, its export time.
---@return number|nil  UTC ms; nil without usable data
function Market.DataMs()
  local generatedAt, newest = Market.Times()
  return newest or generatedAt
end

--- This character's market id, from the checked file.
---@return string|nil  nil without usable data
function Market.MarketId()
  local s = State()
  return s.status == "ok" and s.marketId or nil
end

--- Whether another character trades on this character's market, by the file's map (S12); a character the map
--- doesn't name counts as on it.
---@param charId string
---@return boolean
function Market.SameMarket(charId)
  local s = State()
  if s.status ~= "ok" then return false end
  local named = (s.file.charMarkets or {})[charId]
  return named == nil or named == s.marketId
end

--- Forget the checked file and the caches (tests, and a future reload of the data).
function Market.Reset()
  state, cache, cached = nil, {}, 0
  candidates, graph = {}, nil
end

--- Split a text on a separator, keeping empty fields.
---@param text string
---@param sep string  One character
---@return string[]
local function Split(text, sep)
  local out = {}
  for field in (text .. sep):gmatch("([^" .. sep .. "]*)" .. sep) do out[#out + 1] = field end
  return out
end

--- Decode one candidate string (SPEC §3.5 `K`, EX17).
---@param text string  "cid;strategy;charId;outKey;outQty;inputs;gold;cashBasis;net;chance1000;saleMs;windowEndMs;conf;needs;status"
---@return table       { id, strategy, char, key, qty, inputs = { { key, count, source } }, gold, cashBasis, net (copper),
---                    chance (0-1), sale, windowEnd (ms or nil), conf (%), needs = { profession, skill } or nil,
---                    status (nil when funded, else the reason code), funded (bool) }
function Market.DecodeCandidate(text)
  local f = Split(text, ";")
  local inputs = {}
  for _, part in ipairs(f[6] ~= "" and Split(f[6], "/") or {}) do
    local key, count, source = part:match("^(%d+):(%d+):(.*)$")
    inputs[#inputs + 1] = { key = tonumber(key), count = tonumber(count), source = source }
  end
  local profession, skill = (f[14] or ""):match("^(.+):(%d+)$")
  local status = f[15] or ""
  return { id = f[1], strategy = f[2], char = f[3], key = tonumber(f[4]), qty = tonumber(f[5]), inputs = inputs,
           gold = tonumber(f[7]), cashBasis = tonumber(f[8]), net = tonumber(f[9]),
           chance = tonumber(f[10]) and tonumber(f[10]) / 1000, sale = tonumber(f[11]), windowEnd = tonumber(f[12]),
           conf = tonumber(f[13]), needs = profession and { profession = profession, skill = tonumber(skill) } or nil,
           status = status ~= "" and status or nil, funded = status == "" }
end

--- How many candidates the market exports (EX17).
---@return number  0 without usable data or an older file
function Market.CandidateCount()
  local s = State()
  if s.status ~= "ok" then return 0 end
  return #(s.market.K or {})
end

--- One exported candidate, decoded on first use and kept (EX17): funded ones first, in funding order.
---@param index number  1-based position in the export
---@return table|nil    As Market.DecodeCandidate; nil past the end or without usable data
function Market.Candidate(index)
  local s = State()
  if s.status ~= "ok" then return nil end
  local found = candidates[index]
  if found then return found end
  local text = (s.market.K or {})[index]
  if not text then return nil end
  found = Market.DecodeCandidate(text)
  candidates[index] = found
  return found
end

--- Split "a,b,c" into numbers.
---@param text string|nil
---@return number[]
local function Numbers(text)
  local out = {}
  for n in (text or ""):gmatch("%d+") do out[#out + 1] = tonumber(n) end
  return out
end

--- Decode a G table (SPEC §3.5 `G`, EX18).
---@param g table  { R = { [spell] = "product:yield1000:key*count/…" }, U = { [key] = "spell,…" }, DE, order }
---@return table   { recipes = { [spell] = { product, yield, reagents = { { key, count } } } },
---                uses = { [key] = { spell, … } }, disenchant = { key, … }, order = { key, … } }
function Market.DecodeGraph(g)
  local out = { recipes = {}, uses = {}, disenchant = Numbers(g.DE), order = Numbers(g.order) }
  for spell, text in pairs(g.R or {}) do
    local product, yield, mats = text:match("^(%d+):(%d+):(.*)$")
    local reagents = {}
    for key, count in mats:gmatch("(%d+)%*(%d+)") do reagents[#reagents + 1] = { key = tonumber(key), count = tonumber(count) } end
    out.recipes[spell] = { product = tonumber(product), yield = tonumber(yield) / 1000, yield1000 = tonumber(yield),
                           reagents = reagents }
  end
  for key, text in pairs(g.U or {}) do out.uses[key] = Numbers(text) end
  return out
end

--- The market's dependency graph, decoded on first use (EX18).
---@return table|nil  As Market.DecodeGraph; nil without usable data or an older file
function Market.Graph()
  local s = State()
  if s.status ~= "ok" or not s.market.G then return nil end
  graph = graph or Market.DecodeGraph(s.market.G)
  return graph
end

--- What a merchant charges for an item it sells in unlimited supply (EX6).
---@param key number
---@return number|nil  Copper; nil when no vendor is known to sell it, or without usable data
function Market.VendorPrice(key)
  local s = State()
  if s.status ~= "ok" then return nil end
  return (s.file.vendor or {})[key]
end

--- The market's flip offers for the addon's own one-stack flips (EX11, A59).
---@return table  key -> { expected (copper a unit), price (at the best window, copper), slot (0-167) }; empty without
function Market.FlipOffers()
  local s = State()
  if s.status ~= "ok" then return {} end
  local out = {}
  for key, text in pairs(s.market.F or {}) do
    local expected, price, slot = text:match("^(%d+),(%d+),(%d+)$")
    if expected then out[key] = { expected = tonumber(expected), price = tonumber(price), slot = tonumber(slot) } end
  end
  return out
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
