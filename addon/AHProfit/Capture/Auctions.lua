-- Capture/Auctions.lua: your own active auctions, one snapshot per character, replaced whole
-- (SPEC §2.2.1 ownAuctions, A41). Read when the AH opens and after a post or a cancel. The
-- Craft tab counts them like what you hold, until each one's end passes (A42).

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB
local Characters = AHProfit.Capture.Characters
local Inventory = AHProfit.Capture.Inventory

local Auctions = {}
AHProfit.Capture.Auctions = Auctions

--- Classic's time-left band of an auction (the replicate scan's bands, SPEC §4.2).
---@param seconds number  Time left
---@return number         1 short (< 30 min), 2 medium (< 2 h), 3 long (< 8 h, SPEC §3.3.1, A61), 4 very long
function Auctions.Band(seconds)
  if seconds < 1800 then return 1 end
  if seconds < 7200 then return 2 end
  if seconds < 28800 then return 3 end
  return 4
end

--- Encode auctions as the snapshot's rows and ends (SPEC §2.2.1).
---@param auctions table[]  { key, price, qty, leftSeconds|nil, band } from Adapter.ReadOwnedAuctions
---@param nowMs number
---@return string  rows "key,price,stack,band,count", grouped, ascending; the band from the seconds when known
---@return string  ends "key,units,end ms,unit price", one per auction whose seconds are known (A70), ascending
function Auctions.Encode(auctions, nowMs)
  local groups, ends = {}, {}
  for _, a in ipairs(auctions) do
    local group = { a.key, a.price, a.qty, a.leftSeconds and Auctions.Band(a.leftSeconds) or a.band or 0 }
    local id = table.concat(group, ",")
    groups[id] = groups[id] or { fields = group, count = 0 }
    groups[id].count = groups[id].count + 1
    if a.leftSeconds then ends[#ends + 1] = { a.key, a.qty, nowMs + a.leftSeconds * 1000, a.price } end
  end
  local rows = {}
  for _, g in pairs(groups) do rows[#rows + 1] = { g.fields[1], g.fields[2], g.fields[3], g.fields[4], g.count } end
  --- Order rows by every field, so the text never depends on pairs order.
  local function ByFields(a, b)
    for i = 1, #a do if a[i] ~= b[i] then return a[i] < b[i] end end
    return false
  end
  table.sort(rows, ByFields)
  table.sort(ends, ByFields)
  return DB.EncodeRows(rows), DB.EncodeRows(ends)
end

--- Units you have up on the AH per item, from a snapshot; auctions whose end has passed don't count.
---@param entry table|nil  ownAuctions[charId]
---@param nowMs number
---@return table           key -> { units, endsAt = the soonest end, ms }
function Auctions.Listed(entry, nowMs)
  local out = {}
  for _, a in ipairs(Auctions.Active(entry, nowMs)) do
    local listed = out[a.key] or { units = 0, endsAt = a.endsAt }
    listed.units, listed.endsAt = listed.units + a.units, math.min(listed.endsAt, a.endsAt)
    out[a.key] = listed
  end
  return out
end

--- Your auctions one by one, from a snapshot; those whose end has passed are left out (A50).
---@param entry table|nil  ownAuctions[charId]
---@param nowMs number
---@return table[]         { key, units, endsAt (ms), price (copper each, nil in an older snapshot) }, as stored
function Auctions.Active(entry, nowMs)
  local out = {}
  for row in (((entry or {}).ends or "") .. ";"):gmatch("([^;]*);") do
    local key, units, endsAt, price = row:match("^(%-?%d+),(%d+),(%d+),?(%d*)$")
    if key and tonumber(endsAt) > nowMs then
      out[#out + 1] = { key = tonumber(key), units = tonumber(units), endsAt = tonumber(endsAt), price = tonumber(price) }
    end
  end
  return out
end

local lastProbe = nil   -- "timeLeftSeconds=<type>" from the last owned-auctions read with an auction (A70)

--- What the last owned-auctions read showed of the time-left field, for the diagnostics (A70).
---@return string|nil  "timeLeftSeconds=number" or "timeLeftSeconds=nil"; nil when no auction was read this session
function Auctions.Describe()
  return lastProbe
end

--- Store the answer to the last owned-auctions query and tell the views.
local function CaptureLive()
  local db = DB.Get()
  local auctions, probe = Adapter.ReadOwnedAuctions()
  lastProbe = probe or lastProbe
  if not (db and auctions) then return end
  local now = Adapter.NowMs()
  local rows, ends = Auctions.Encode(auctions, now)
  db.ownAuctions[Characters.CurrentId()] = { at = now, rows = rows, ends = ends }
  Inventory.Changed("auctions")
end

--- AUCTION_HOUSE_SHOW, AUCTION_HOUSE_AUCTION_CREATED, AUCTION_CANCELED: ask for your auctions again.
local function Query()
  Adapter.QueryOwnedAuctions()
end

--- OWNED_AUCTIONS_UPDATED: the answer arrived; read it once it settles (debounced).
local function OnOwnedAuctions()
  Events.Debounce("auctions.owned", CaptureLive)
end

Events.Register("AUCTION_HOUSE_SHOW", Query)
Events.Register("AUCTION_HOUSE_AUCTION_CREATED", Query)
Events.Register("AUCTION_CANCELED", Query)
Events.Register("OWNED_AUCTIONS_UPDATED", OnOwnedAuctions)
