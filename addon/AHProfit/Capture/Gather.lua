-- Capture/Gather.lua: one `gather` event per loot window of farming loot (SPEC §2.2.1 gather, §3.4E.3; A85-A91,
-- FM42), tagged by what opened it. The Disenchant pattern of A55: a successful Mining, Herb Gathering, Skinning
-- or Fishing cast by the player notes its source until the next LOOT_OPENED; a loot window on a creature's
-- corpse with no such cast pending is `monster`; every other window records nothing (a Disenchant, Pick Pocket,
-- Opening, a chest or a container item). No analytics here: sessions and rates are AHSync's (§4.1, §5.1).
--
-- Source rule (conservative: no event rather than a wrong one):
--   1. a gathering cast younger than its timeout is the source, unless the loot source contradicts it (a corpse
--      after a mining, herb or fishing cast, or an object after a skinning cast: the cast's own window never
--      opened, so it is dropped and rule 2 decides);
--   2. a loot window whose source is a creature (Adapter.LootSourceKind) is `monster`;
--   3. a loot window after another loot-opening cast (Pick Pocket, Disenchant, Opening) records nothing;
--   4. anything else, including a source the adapter can't tell, records nothing.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB
local Characters = AHProfit.Capture.Characters
local Crafts = AHProfit.Capture.Crafts

local Gather = {}
AHProfit.Capture.Gather = Gather

Gather.CAST_TIMEOUT_MS = 10000       -- UNVERIFIED: a node's or corpse's loot opens right after the cast succeeds
Gather.FISHING_TIMEOUT_MS = 35000    -- UNVERIFIED: the bobber is clicked within the 30 s Fishing channel

local pending = nil       -- { tag = "mining" | ... | nil, name = spell name, at = ms } from a cast to its loot window
local windowOpen = false  -- a LOOT_OPENED was handled and its LOOT_CLOSED hasn't come yet (A90)
local lastLoot = nil      -- what ReadIntoLastLoot read, so the read can run protected without a closure
local perf = { lastMs = 0, maxMs = 0, windows = 0 }

--- UNIT_SPELLCAST_SUCCEEDED / CHANNEL_START: note a gathering or other loot-opening cast of the player's.
---@param _ string
---@param unit string     "player" for your own casts
---@param _guid string    The cast's GUID
---@param spellId number
function Gather.OnCast(_, unit, _guid, spellId)
  if unit ~= "player" then return end
  local tag, name = Adapter.LootCast(spellId)
  if name then pending = { tag = tag, name = name, at = Adapter.NowMs() } end
end

--- The pending cast if it is still young enough to own this loot window.
---@param nowMs number
---@return table|nil  The pending cast; nil when there is none or it timed out
local function FreshCast(nowMs)
  if not pending then return nil end
  local limit = pending.tag == "fishing" and Gather.FISHING_TIMEOUT_MS or Gather.CAST_TIMEOUT_MS
  if nowMs - pending.at > limit then return nil end
  return pending
end

--- What opened this loot window (the source rule above).
---@param cast table|nil  The fresh pending cast
---@param kind string     Adapter.LootSourceKind(): "creature" | "object" | "item" | "unknown"
---@return string|nil     The gather source; nil records nothing
---@return string|nil     The other loot-opening cast's name when that is why
function Gather.SourceOf(cast, kind)
  if cast and cast.tag then
    local corpse = kind == "creature"
    if kind == "unknown" or corpse == (cast.tag == "skinning") then return cast.tag end
  elseif cast then
    return nil, cast.name
  end
  if kind == "creature" then return "monster" end
  return nil
end

--- Read the loot window into lastLoot; run through Events.Protected so an adapter error is caught (FM42).
local function ReadIntoLastLoot()
  lastLoot = Adapter.ReadLoot()
end

--- Record one loot window of farming loot as a gather event.
---@param db table        The database
---@param source string   The gather source
---@param nowMs number
local function Record(db, source, nowMs)
  lastLoot = nil
  if not Events.Protected(ReadIntoLastLoot) or not lastLoot then
    Events.Trace("gather " .. source .. " loot error -> none")
    return
  end
  local loot = lastLoot
  Events.Trace("gather " .. source .. " loot " .. #loot .. " -> " .. (#loot > 0 and "gather" or "none"))
  if #loot == 0 then return end
  local charId = Characters.CurrentId()
  DB.AppendEvent(db, { type = "gather", char = charId, at = nowMs, source = source,
                       login = Characters.LoginMs(db, charId), loot = Crafts.EncodeResults(loot) })
end

--- LOOT_OPENED: one event per loot window, tagged by what opened it; a second LOOT_OPENED adds none (A90).
function Gather.OnLoot()
  if windowOpen then return end
  windowOpen = true
  local start, nowMs, db = Adapter.ProfileMs(), Adapter.NowMs(), DB.Get()
  local cast = FreshCast(nowMs)
  pending = nil
  if not db then return end
  local source, other = Gather.SourceOf(cast, Adapter.LootSourceKind())
  if source then
    Record(db, source, nowMs)
  elseif other and other ~= "Disenchant" then           -- a Disenchant's window is traced by Capture/Crafts (A55)
    Events.Trace("loot after '" .. other .. "' -> none")
  end
  local ms = Adapter.ProfileMs() - start
  perf.lastMs, perf.maxMs, perf.windows = ms, math.max(perf.maxMs, ms), perf.windows + 1
end

--- LOOT_CLOSED: the window is gone; the next LOOT_OPENED is a new one.
function Gather.OnLootClosed()
  windowOpen = false
  pending = nil
end

--- /ahp perf's line for the loot capture (SPEC §5.1: a few microseconds per loot window).
---@return string
function Gather.PerfLine()
  return string.format("loot capture %.3f ms last window, %.3f ms slowest (%d windows)", perf.lastMs, perf.maxMs,
                       perf.windows)
end

--- Forget the pending cast and the open window (tests).
function Gather.Reset()
  pending, windowOpen, lastLoot = nil, false, nil
  perf = { lastMs = 0, maxMs = 0, windows = 0 }
end

Events.Register("UNIT_SPELLCAST_SUCCEEDED", Gather.OnCast)
Events.Register("UNIT_SPELLCAST_CHANNEL_START", Gather.OnCast)   -- UNVERIFIED: Fishing may only report this
Events.Register("LOOT_OPENED", Gather.OnLoot)
Events.Register("LOOT_CLOSED", Gather.OnLootClosed)
