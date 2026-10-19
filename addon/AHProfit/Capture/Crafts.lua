-- Capture/Crafts.lua: a `craft` event for each successful cast of one of your known recipes
-- (SPEC §2.2.1 events, A47).

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB
local Characters = AHProfit.Capture.Characters

local Crafts = {}
AHProfit.Capture.Crafts = Crafts

--- UNIT_SPELLCAST_SUCCEEDED: a craft when it is the player's and the spell is a known recipe.
---@param _ string        The event
---@param unit string     "player" for your own casts
---@param _guid string    The cast's GUID
---@param spellId number
function Crafts.OnCast(_, unit, _guid, spellId)
  local db = DB.Get()
  if unit ~= "player" or not (db and spellId) then return end
  local charId = Characters.CurrentId()
  local known = DB.DecodeIdSet((db.knownRecipes or {})[charId])
  if not known[spellId] then return end
  DB.AppendEvent(db, { type = "craft", char = charId, at = Adapter.NowMs(), spell = spellId, count = 1 })
end

Events.Register("UNIT_SPELLCAST_SUCCEEDED", Crafts.OnCast)
