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

--- Write the logged-in character's row, replacing the old one; its initial-login time is kept.
---@param db table  The database; db.characters[charId] is replaced
---@return string   The character id
function Characters.Capture(db)
  local info = Adapter.PlayerInfo()
  local charId = info.name .. "-" .. info.realm
  local old = db.characters[charId]
  db.characters[charId] = {
    name = info.name,
    realm = info.realm,
    faction = info.faction,
    class = info.class,
    level = info.level,
    gold = Adapter.Money(),        -- copper
    updated = Adapter.NowMs(),
    login = type(old) == "table" and old.login or nil,   -- ms of the initial login, kept across /reload (§3.4E.4)
  }
  return charId
end

--- The time of a character's initial login, which every gather event carries so AHSync can tell a logout
--- from a pause (SPEC §3.4E.4, A91). Set at the first PLAYER_ENTERING_WORLD of a login; when none was seen
--- (the addon was updated mid-session), now, kept from then on.
---@param db table      The database
---@param charId string
---@return number       UTC ms
function Characters.LoginMs(db, charId)
  local row = db.characters[charId]
  if type(row) == "table" and type(row.login) == "number" then return row.login end
  local now = Adapter.NowMs()
  if type(row) == "table" then row.login = now end
  return now
end

local entered = false   -- PLAYER_ENTERING_WORLD already handled in this UI session (it fires on every zone change)

--- PLAYER_ENTERING_WORLD: the first one after a login starts a new login time; after a /reload it is kept.
--- UNVERIFIED: the event carries (isInitialLogin, isReloadingUi) on Forever, as on Classic 1.13+. Without the
--- flag a stored login is kept, so a relog continues it and only the 15-minute gap ends a session (§3.4E.4).
---@param _ string
---@param isInitialLogin boolean|nil
function Characters.OnEnteringWorld(_, isInitialLogin)
  if entered then return end
  entered = true
  local db = DB.Get()
  if not db then return end
  local row = db.characters[Characters.Capture(db)]
  if isInitialLogin == true or row.login == nil then row.login = Adapter.NowMs() end
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
Events.Register("PLAYER_ENTERING_WORLD", Characters.OnEnteringWorld)
Events.Register("PLAYER_MONEY", OnChange)
Events.Register("PLAYER_LEVEL_UP", OnChange)
