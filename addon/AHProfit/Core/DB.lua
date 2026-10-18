-- Core/DB.lua: AHProfitDB setup, migrations, event ids, pruning and the
-- compact-string codecs of the SavedVariables layout (SPEC §2.2, §2.2.1, §5.2).

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events

local DB = {}
AHProfit.DB = DB

DB.FORMAT_VERSION = 1
DB.ADDON_VERSION = "0.1.0"
DB.SAVED_NAME = "AHProfitDB"
DB.ADDON_NAME = "AHProfit"
DB.UNCONFIRMED_KEEP_MS = 3600000   -- SPEC §2.2, §5.2: unconfirmed attempts are kept for 1 h

--- Migrations: DB.migrations[n] upgrades a version-n table to version n + 1 in place.
--- Version 1 is the first layout, so there are none yet (FM23 adds the v1 -> v2 one).
DB.migrations = {}

-- Top-level tables every version-1 database has (SPEC §2.2.1).
local TABLE_FIELDS = { "characters", "professions", "recipes", "knownRecipes",
                       "inventory", "ownAuctions", "scans", "events", "rules" }

local current = nil        -- the live database once set up; nil before or on error
local setupError = nil     -- why setup failed, for the status line
local seqSecond = {}       -- charId -> the second (ms / 1000) of its last event id
local seqNext = {}         -- charId -> next seq within that second

-- ------------------------------------------------------------------ codecs

--- Format a number the way the compact strings store it: integers without a decimal point.
---@param value number  Any finite number
---@return string       "1760000000000" for integers (no exponent), else up to 14 significant digits
function DB.FormatNumber(value)
  if value == math.floor(value) and value > -2^53 and value < 2^53 then
    return string.format("%.0f", value)   -- %d is 32-bit on Windows Lua 5.1
  end
  return string.format("%.14g", value)
end

--- Encode rows as a compact string: rows joined by ";", fields by "," (SPEC §2.2.1).
---@param rows table[]  Arrays of numbers, in output order
---@return string       e.g. "2840,20,0;6948,1,1"; "" for no rows
function DB.EncodeRows(rows)
  local parts, fields = {}, {}
  for i, row in ipairs(rows) do
    for j = 1, #row do fields[j] = DB.FormatNumber(row[j]) end
    for j = #row + 1, #fields do fields[j] = nil end
    parts[i] = table.concat(fields, ",")
  end
  return table.concat(parts, ";")
end

--- Encode a set of integer ids as a compact string, ascending (SPEC §2.2.1).
---@param set table  id -> true
---@return string|nil  e.g. "2662;2663"; nil when the set is empty
function DB.EncodeIdSet(set)
  local ids = {}
  for id in pairs(set) do ids[#ids + 1] = id end
  if #ids == 0 then return nil end
  table.sort(ids)
  local rows = {}
  for i, id in ipairs(ids) do rows[i] = { id } end
  return DB.EncodeRows(rows)
end

--- Decode a compact id list into a set.
---@param text string|nil  e.g. "2662;2663"; nil or "" for none
---@return table           id -> true
function DB.DecodeIdSet(text)
  local set = {}
  for id in string.gmatch(text or "", "[^;]+") do
    local n = tonumber(id)
    if n then set[n] = true end
  end
  return set
end

-- ------------------------------------------------------------------ setup and migration

--- A fresh, empty version-1 database.
---@return table  AHProfitDB with formatVersion, addonVersion and empty tables
function DB.NewDB()
  local db = { formatVersion = DB.FORMAT_VERSION, addonVersion = DB.ADDON_VERSION }
  for _, field in ipairs(TABLE_FIELDS) do db[field] = {} end
  return db
end

--- Copy a table and everything in it (no shared or cyclic tables in AHProfitDB).
---@param value any  Value to copy
---@return any       A deep copy; non-tables are returned as-is
function DB.DeepCopy(value)
  if type(value) ~= "table" then return value end
  local copy = {}
  for k, v in pairs(value) do copy[k] = DB.DeepCopy(v) end
  return copy
end

--- Upgrade a database step by step; the original is never modified (SPEC §5.2, FM23).
---@param saved table        Database at saved.formatVersion
---@param migrations table   n -> function(db) that upgrades version n to n + 1 in place
---@param target number      Version to reach
---@return table|nil         The upgraded copy; nil on failure
---@return string|nil        Why it failed
function DB.Migrate(saved, migrations, target)
  local copy = DB.DeepCopy(saved)
  for version = saved.formatVersion, target - 1 do
    local step = migrations[version]
    if not step then
      return nil, "no migration from AHProfitDB version " .. version
    end
    local ok, err = pcall(step, copy)
    if not ok then
      return nil, "migration " .. version .. " -> " .. (version + 1) .. " failed: " .. tostring(err)
    end
    copy.formatVersion = version + 1
  end
  return copy
end

--- Fill in any top-level table a database is missing and stamp the addon version.
---@param db table  A database at the current format version; changed in place
---@return table    The same table
local function EnsureShape(db)
  for _, field in ipairs(TABLE_FIELDS) do
    if type(db[field]) ~= "table" then db[field] = {} end
  end
  db.addonVersion = DB.ADDON_VERSION
  return db
end

--- Turn whatever SavedVariables held into a usable current-version database.
---@param saved any    The loaded AHProfitDB; nil on first run
---@return table|nil   The database to use; nil when it can't be used
---@return string|nil  Why not (a newer format, a failed migration, a damaged file)
function DB.Setup(saved)
  if saved == nil then return DB.NewDB() end
  if type(saved) ~= "table" or type(saved.formatVersion) ~= "number" then
    return nil, "AHProfitDB is damaged (no formatVersion); left untouched"
  end
  if saved.formatVersion > DB.FORMAT_VERSION then
    return nil, "AHProfitDB is format " .. saved.formatVersion .. ", newer than this addon ("
      .. DB.FORMAT_VERSION .. "); update AHProfit"
  end
  if saved.formatVersion < DB.FORMAT_VERSION then
    local migrated, err = DB.Migrate(saved, DB.migrations, DB.FORMAT_VERSION)
    if not migrated then return nil, err end
    saved = migrated
  end
  return EnsureShape(saved)
end

--- ADDON_LOADED handler: sets up AHProfitDB once our SavedVariables are loaded.
--- On failure the saved table stays untouched and capture is disabled (DB.Get returns nil).
---@param _ string        "ADDON_LOADED"
---@param addonName string The addon that finished loading
function DB.OnAddonLoaded(_, addonName)
  if addonName ~= DB.ADDON_NAME then return end
  Events.Unregister("ADDON_LOADED", DB.OnAddonLoaded)
  local db, err = DB.Setup(Adapter.LoadSaved(DB.SAVED_NAME))
  current, setupError = db, err
  if db then
    DB.SeedEventIds(db)
    Adapter.StoreSaved(DB.SAVED_NAME, db)
  end
end

--- The live database.
---@return table|nil  AHProfitDB; nil before ADDON_LOADED or when setup failed
function DB.Get()
  return current
end

--- Why setup failed, for the status line.
---@return string|nil  The error; nil when the database is usable
function DB.SetupError()
  return setupError
end

-- ------------------------------------------------------------------ events

--- A unique event id `charId:timeMs:seq` (SPEC §2.2.1); seq restarts at 0 each second per character.
---@param charId string  "Name-Realm"
---@param timeMs number  Event time, UTC ms (whole seconds × 1000)
---@return string        e.g. "Amaya-Grommash:1760000000000:0"
function DB.NewEventId(charId, timeMs)
  local second = math.floor(timeMs / 1000)
  if seqSecond[charId] ~= second then
    seqSecond[charId], seqNext[charId] = second, 0
  end
  local seq = seqNext[charId]
  seqNext[charId] = seq + 1
  return charId .. ":" .. DB.FormatNumber(timeMs) .. ":" .. seq
end

--- Continue the seq counter after the newest saved event and scan ids, so ids
--- stay unique after a /reload within the same second.
---@param db table  The database; read only
function DB.SeedEventIds(db)
  for _, rows in ipairs({ db.scans, db.events }) do
    local last = rows[#rows]
    local id = type(last) == "table" and type(last.id) == "string" and last.id or ""
    local charId, timeMs, seq = id:match("^(.+):(%d+):(%d+)$")
    if charId then
      local second = math.floor(tonumber(timeMs) / 1000)
      local nextSeq = tonumber(seq) + 1
      if seqSecond[charId] ~= second or seqNext[charId] < nextSeq then
        seqSecond[charId], seqNext[charId] = second, nextSeq
      end
    end
  end
end

--- Append an event, giving it an id (SPEC §2.2.1 event table).
---@param db table     The database
---@param event table  Event fields; must have char (charId) and at (ms); id is set here
---@return table       The same event table, now with id
function DB.AppendEvent(db, event)
  event.id = DB.NewEventId(event.char, event.at)
  db.events[#db.events + 1] = event
  return event
end

-- ------------------------------------------------------------------ pruning

--- Whether a scan or event row survives pruning (SPEC §2.2 pruning handshake).
---@param row table              A scan or event with char, at (ms) and optional confirmed
---@param ingestedUntil table    charId -> newest time AHSync has committed for that character, ms
---@param nowMs number           Current time, ms
---@return boolean               true to keep the row; a non-table row is junk and dropped
local function KeepRow(row, ingestedUntil, nowMs)
  if type(row) ~= "table" then return false end
  local untilMs = ingestedUntil[row.char]
  if type(untilMs) ~= "number" or type(row.at) ~= "number" or row.at > untilMs then return true end
  return row.confirmed == false and nowMs - row.at < DB.UNCONFIRMED_KEEP_MS
end

--- Keep only the rows of an array that pass KeepRow, preserving order.
---@param rows table[]               Array changed in place
---@param ingestedUntil table         charId -> newest committed time, ms
---@param nowMs number                Current time, ms
---@return number                     Rows removed
local function PruneArray(rows, ingestedUntil, nowMs)
  local kept = 0
  local total = #rows
  for i = 1, total do
    local row = rows[i]
    rows[i] = nil
    if KeepRow(row, ingestedUntil, nowMs) then
      kept = kept + 1
      rows[kept] = row
    end
  end
  return total - kept
end

--- Delete scans and events AHSync has committed (SPEC §2.2, §5.2 rule 5, A4).
--- A character's rows at or before its ingestedUntil go, except unconfirmed attempts younger
--- than 1 h; a character AHSync hasn't committed anything for keeps every row.
-- SPEC GAP: §2.2 lists posts, purchases, crafts and mail; `skip` events are pruned by the same rule.
---@param db table               The database; scans and events are changed in place
---@param ingestedUntil table    `ingestedUntil` from PortfolioPlan.lua: charId -> ms
---@param nowMs number           Current time, ms
---@return number                Rows removed
function DB.Prune(db, ingestedUntil, nowMs)
  return PruneArray(db.scans, ingestedUntil, nowMs) + PruneArray(db.events, ingestedUntil, nowMs)
end

Events.Register("ADDON_LOADED", DB.OnAddonLoaded)
