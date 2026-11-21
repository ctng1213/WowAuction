-- Capture/Cooldowns.lua: when each character can use its catalog cooldown spells again (SPEC §3.4F.3,
-- §2.2.1 `cooldowns`). Three sources: the game's report (a recipe's time left while its own trade skill
-- window is open, a tool's item cooldown on BAG_UPDATE_COOLDOWN), the addon's own record of a successful
-- cast, and nothing yet ("Not read"). Each observation is stored per spell with its time, and the newest
-- wins; groups are resolved where the rows are read, never here. A reading of "no time left" counts as
-- ready only once this client has given a real time left (D15): before that it could be an API that
-- doesn't work, and would wipe a good cast record.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB
local Characters = AHProfit.Capture.Characters

local Cooldowns = {}
AHProfit.Capture.Cooldowns = Cooldowns

local catalog = nil   -- spell -> its row, decoded once from Data/Cooldowns.lua
local listeners = {}  -- called after a reading or a cast changed what is stored (the Cooldowns tab, D23)
local spells = {}     -- the catalog's spell ids, ascending (never rely on pairs order, SPEC §8.6)
local tradeOpen = false
local TRADE_UPDATE = Adapter.TradeUpdateEvent()

--- A catalog row's reagents, "key,count/key,count" by key.
---@param text string
---@return table[]  { key, count } each, in the string's order (by key)
local function DecodeReagents(text)
  local out = {}
  for key, count in string.gmatch(text or "", "(%d+),(%d+)") do out[#out + 1] = { key = tonumber(key), count = tonumber(count) } end
  return out
end

--- The catalog's spells, decoded on first use (SPEC §5.1 lazy decode).
---@return table  spell id -> { spell, name, profession, skill, cooldownS = seconds, group|nil, product,
---               yieldMin, yieldMax, reagents = { { key, count } }, tool = item id|nil, toolUse = bool }
function Cooldowns.Catalog()
  if catalog then return catalog end
  catalog = {}
  for _, row in ipairs(AHProfit.Data.COOLDOWNS or {}) do
    local f = {}
    for field in string.gmatch(row .. ";", "([^;]*);") do f[#f + 1] = field end
    local spell = tonumber(f[1])
    if spell then
      catalog[spell] = { spell = spell, name = f[2], profession = f[3], skill = tonumber(f[4]),
                         cooldownS = tonumber(f[5]), group = f[6] ~= "" and f[6] or nil, product = tonumber(f[7]),
                         yieldMin = tonumber(f[8]), yieldMax = tonumber(f[9]), reagents = DecodeReagents(f[10]),
                         tool = tonumber(f[11]), toolUse = f[12] == "1" }
      spells[#spells + 1] = spell
    end
  end
  table.sort(spells)
  return catalog
end

--- The catalog's spell ids in ascending order.
---@return number[]
function Cooldowns.Spells()
  Cooldowns.Catalog()
  return spells
end

--- Decode one character's stored ready times.
---@param text string|nil  "spell,readyAtMs,source,atMs;…"
---@return table           spell id -> { readyAt, source, at }
function Cooldowns.Decode(text)
  local out = {}
  for spell, ready, source, at in string.gmatch(text or "", "(%d+),(%d+),([gc]),(%d+)") do
    out[tonumber(spell)] = { readyAt = tonumber(ready), source = source, at = tonumber(at) }
  end
  return out
end

--- Encode a character's ready times, by spell id.
---@param byspell table  spell id -> { readyAt, source, at }
---@return string        "spell,readyAtMs,source,atMs;…"
function Cooldowns.Encode(byspell)
  local spells = {}
  for spell in pairs(byspell) do spells[#spells + 1] = spell end
  table.sort(spells)
  local parts = {}
  for i, spell in ipairs(spells) do
    local r = byspell[spell]
    parts[i] = string.format("%d,%s,%s,%s", spell, DB.FormatNumber(r.readyAt), r.source, DB.FormatNumber(r.at))
  end
  return table.concat(parts, ";")
end

--- Store one observation unless a newer one is already stored (SPEC §3.4F.3).
---@param db table        The database; db.cooldowns[charId] changes
---@param charId string   "Name-Realm"
---@param spell number    The catalog spell
---@param readyAt number  When it is ready, UTC ms
---@param source string   "g" (the game reported it) or "c" (the addon saw the cast)
---@param at number       When this was observed, UTC ms
function Cooldowns.Record(db, charId, spell, readyAt, source, at)
  db.cooldowns = db.cooldowns or {}
  local byspell = Cooldowns.Decode(db.cooldowns[charId])
  local old = byspell[spell]
  if old and old.at > at then return end
  byspell[spell] = { readyAt = readyAt, source = source, at = at }
  db.cooldowns[charId] = Cooldowns.Encode(byspell)
end

--- Store a game reading: a real time left always, "none left" only once the client is trusted (D15).
---@param db table        The database
---@param spell number    The catalog spell
---@param left number|nil Seconds left; 0 or nil = the client gave no time left
function Cooldowns.Reading(db, spell, left)
  local now = Adapter.NowMs()
  if left and left >= Adapter.MIN_COOLDOWN_S then
    db.cooldownReadsTrusted = true
    Cooldowns.Record(db, Characters.CurrentId(), spell, now + math.floor(left * 1000 + 0.5), "g", now)
  elseif db.cooldownReadsTrusted then
    Cooldowns.Record(db, Characters.CurrentId(), spell, now, "g", now)
  end
end

--- Read every catalog recipe the open, own trade skill window lists (SPEC §3.4F.3 source 1).
---@param db table  The database
function Cooldowns.ReadTradeWindow(db)
  if not Adapter.OwnTradeWindowReady() then return end
  local catalogRows, readings = Cooldowns.Catalog(), {}
  for _, spell in ipairs(Cooldowns.Spells()) do
    if not catalogRows[spell].toolUse then
      local learned, left = Adapter.RecipeCooldown(spell)
      Events.Trace("cd g " .. spell .. " " .. tostring(learned) .. " " .. tostring(left))
      if learned then readings[#readings + 1] = { spell, left } end
      -- one real time left in this window proves the API answers, for every reading of it (D15)
      if learned and left and left >= Adapter.MIN_COOLDOWN_S then db.cooldownReadsTrusted = true end
    end
  end
  for _, r in ipairs(readings) do Cooldowns.Reading(db, r[1], r[2]) end
end

--- Whether the character's last bag snapshot holds an item, bound or not (a tool may be soulbound).
---@param db table      The database
---@param charId string "Name-Realm"
---@param key number    Item key
---@return boolean
function Cooldowns.InBags(db, charId, key)
  local place = db.inventory and db.inventory[charId]
  local bags = place and place.bags
  return type(bags) == "string" and string.find(";" .. bags, ";" .. key .. ",", 1, true) ~= nil
end

--- Read the cooldown of every catalog tool in the bags (SPEC §3.4F.3 source 1); a banked tool is not read.
---@param db table  The database
function Cooldowns.ReadTools(db)
  local charId, catalogRows = Characters.CurrentId(), Cooldowns.Catalog()
  for _, spell in ipairs(Cooldowns.Spells()) do
    local row = catalogRows[spell]
    if row.toolUse and row.tool and Cooldowns.InBags(db, charId, row.tool) then
      local left = Adapter.ItemCooldownLeft(row.tool)
      Events.Trace("cd t " .. spell .. " " .. tostring(left))
      if left then Cooldowns.Reading(db, spell, left) end
    end
  end
end

--- Call a function whenever the stored ready times may have changed.
---@param fn function  Called with no arguments, out of combat
function Cooldowns.OnChange(fn)
  listeners[#listeners + 1] = fn
end

--- Tell the listeners (already debounced by the caller, so never in combat).
local function Notify()
  for _, fn in ipairs(listeners) do fn() end
end

--- UNIT_SPELLCAST_SUCCEEDED: a catalog spell cast by the player starts its cooldown (SPEC §3.4F.3 source 2).
-- Runs on every player cast, in combat too: one table lookup, no allocation for other spells.
---@param _ string      The event
---@param unit string   "player" for your own casts
---@param _guid string  The cast's GUID
---@param spellId number
function Cooldowns.OnCast(_, unit, _guid, spellId)
  if unit ~= "player" then return end
  local row = Cooldowns.Catalog()[spellId]
  local db = DB.Get()
  if not (row and db and row.cooldownS) then return end
  local now = Adapter.NowMs()
  Cooldowns.Record(db, Characters.CurrentId(), spellId, now + row.cooldownS * 1000, "c", now)
  Events.Debounce("cooldowns.changed", Notify)        -- a cast may come in combat: the redraw waits (SPEC §5.1)
end

--- Read the open trade skill window into the live database.
local function ReadTradeLive()
  local db = DB.Get()
  if db and tradeOpen then
    Cooldowns.ReadTradeWindow(db)
    Notify()
  end
end

--- Read the held tools into the live database.
local function ReadToolsLive()
  local db = DB.Get()
  if db then
    Cooldowns.ReadTools(db)
    Notify()
  end
end

--- The window's updates and BAG_UPDATE_COOLDOWN are bursty: debounce each.
local function OnTradeUpdate()
  Events.Debounce("cooldowns.trade", ReadTradeLive)
end

--- BAG_UPDATE_COOLDOWN fires on every global cooldown and potion: debounced, and only the catalog tools are read.
local function OnBagCooldown()
  Events.Debounce("cooldowns.tools", ReadToolsLive)
end

--- TRADE_SKILL_SHOW / TRADE_SKILL_CLOSE: read only while the window is open (SPEC §5.1).
---@param event string  The event name
local function OnTradeWindow(event)
  tradeOpen = event == "TRADE_SKILL_SHOW"
  if tradeOpen then
    Events.Register(TRADE_UPDATE, OnTradeUpdate)
    OnTradeUpdate()
  else
    Events.Unregister(TRADE_UPDATE, OnTradeUpdate)
  end
end

Events.Register("UNIT_SPELLCAST_SUCCEEDED", Cooldowns.OnCast)
Events.Register("TRADE_SKILL_SHOW", OnTradeWindow)
Events.Register("TRADE_SKILL_CLOSE", OnTradeWindow)
Events.Register("BAG_UPDATE_COOLDOWN", OnBagCooldown)
-- A tool moved into the bags fires no BAG_UPDATE_COOLDOWN (found in game, CD33), so the tools are also read at
-- login and after bag changes. Capture/Inventory loads first, so its bag snapshot is written before this read.
Events.Register("PLAYER_LOGIN", OnBagCooldown)
Events.Register("BAG_UPDATE_DELAYED", OnBagCooldown)
