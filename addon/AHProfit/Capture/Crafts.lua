-- Capture/Crafts.lua: a `craft` event for each successful cast of one of your known recipes
-- (SPEC §2.2.1 events, A47), and a `disenchant` event with what each Disenchant gave (A55).

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB
local Characters = AHProfit.Capture.Characters

local Crafts = {}
AHProfit.Capture.Crafts = Crafts

Crafts.DISENCHANT = 13262        -- UNVERIFIED: Forever's Disenchant spell id (Classic's)
local disenchanting               -- { key, cast } from a Disenchant's start until its loot window

--- UNIT_SPELLCAST_START: note the item a Disenchant targets (its bag slot is locked while it casts).
---@param _ string
---@param unit string
---@param _guid string
---@param spellId number
function Crafts.OnCastStart(_, unit, _guid, spellId)
  if unit == "player" and spellId == Crafts.DISENCHANT then
    disenchanting = { key = Adapter.LockedBagItem(), cast = false }
    Events.Trace("de start " .. tostring(disenchanting.key))
  end
end

--- UNIT_SPELLCAST_INTERRUPTED / FAILED: a Disenchant that didn't happen.
---@param _ string
---@param unit string
---@param _guid string
---@param spellId number
function Crafts.OnCastStopped(_, unit, _guid, spellId)
  if unit == "player" and spellId == Crafts.DISENCHANT and disenchanting then
    disenchanting = nil
    Events.Trace("de stopped")
  end
end

--- Loot as a disenchant's results.
---@param loot table[]  { key, qty }
---@return string       "key,qty;..." by key, summed per key
function Crafts.EncodeResults(loot)
  local units, keys = {}, {}
  for _, item in ipairs(loot) do
    if not units[item.key] then
      keys[#keys + 1] = item.key
      units[item.key] = 0
    end
    units[item.key] = units[item.key] + item.qty
  end
  table.sort(keys)
  local parts = {}
  for _, key in ipairs(keys) do parts[#parts + 1] = key .. "," .. units[key] end
  return table.concat(parts, ";")
end

--- LOOT_OPENED: after a successful Disenchant, its loot is the results (A55).
function Crafts.OnLoot()
  local de, db = disenchanting, DB.Get()
  disenchanting = nil
  if not (de and de.cast and db) then return end
  local loot = Adapter.ReadLoot()
  Events.Trace("de loot " .. #loot .. " -> " .. ((de.key and #loot > 0) and "disenchant" or "none"))
  if not de.key or #loot == 0 then return end
  DB.AppendEvent(db, { type = "disenchant", char = Characters.CurrentId(), at = Adapter.NowMs(), key = de.key,
                       results = Crafts.EncodeResults(loot) })
end

--- UNIT_SPELLCAST_SUCCEEDED: a craft when it is the player's and the spell is a known recipe.
---@param _ string        The event
---@param unit string     "player" for your own casts
---@param _guid string    The cast's GUID
---@param spellId number
function Crafts.OnCast(_, unit, _guid, spellId)
  local db = DB.Get()
  if unit ~= "player" or not (db and spellId) then return end
  if spellId == Crafts.DISENCHANT and disenchanting then
    disenchanting.cast = true
    return
  end
  local charId = Characters.CurrentId()
  local known = DB.DecodeIdSet((db.knownRecipes or {})[charId])
  if not known[spellId] then return end
  DB.AppendEvent(db, { type = "craft", char = charId, at = Adapter.NowMs(), spell = spellId, count = 1 })
end

Events.Register("UNIT_SPELLCAST_SUCCEEDED", Crafts.OnCast)
Events.Register("UNIT_SPELLCAST_START", Crafts.OnCastStart)
Events.Register("UNIT_SPELLCAST_INTERRUPTED", Crafts.OnCastStopped)
Events.Register("UNIT_SPELLCAST_FAILED", Crafts.OnCastStopped)
Events.Register("LOOT_OPENED", Crafts.OnLoot)
