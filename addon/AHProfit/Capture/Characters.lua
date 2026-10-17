-- Capture/Characters.lua: the logged-in character's identity, level and gold (SPEC §2.2, §2.2.1).

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB

local Characters = {}
AHProfit.Capture = AHProfit.Capture or {}
AHProfit.Capture.Characters = Characters

--- The logged-in character's id (SPEC §2.2.1: `Name-Realm`).
---@return string  e.g. "Amaya-Grommash"
function Characters.CurrentId()
  local info = Adapter.PlayerInfo()
  return info.name .. "-" .. info.realm
end

--- Write the logged-in character's row, replacing the old one.
---@param db table  The database; db.characters[charId] is replaced
---@return string   The character id
function Characters.Capture(db)
  local info = Adapter.PlayerInfo()
  local charId = info.name .. "-" .. info.realm
  db.characters[charId] = {
    name = info.name,
    realm = info.realm,
    faction = info.faction,
    class = info.class,
    level = info.level,
    gold = Adapter.Money(),        -- copper
    updated = Adapter.NowMs(),
  }
  return charId
end

--- Capture into the live database, if it is usable.
local function CaptureLive()
  local db = DB.Get()
  if db then Characters.Capture(db) end
end

--- PLAYER_LOGIN: capture right away.
local function OnLogin()
  CaptureLive()
end

--- PLAYER_MONEY and PLAYER_LEVEL_UP: bursty while looting, and UnitLevel lags
--- the level-up event, so capture once after the debounce (SPEC §5.1).
local function OnChange()
  Events.Debounce("characters", CaptureLive)
end

Events.Register("PLAYER_LOGIN", OnLogin)
Events.Register("PLAYER_MONEY", OnChange)
Events.Register("PLAYER_LEVEL_UP", OnChange)
