-- Core/Plan.lua: reads PortfolioPlan.lua, written by AHSync (SPEC §3.5, §2.2, §5.2).
-- At login, once both addons are loaded, the file is checked (schema version and checksum)
-- and SavedVariables rows AHSync has committed are pruned per character (A4). A file that
-- fails its checks prunes nothing and shows no plan (EX4). Format: ahsync/export/plan_lua.py.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB

local Plan = {}
AHProfit.Plan = Plan

Plan.SCHEMA_VERSION = 1            -- must equal ahsync/export/plan_lua.py PLAN_SCHEMA_VERSION (EX4)

local state = nil                  -- { status, file } once checked

--- Count and total length of every action and rule string, the file's checksum.
---@param file table  AHProfitPlan
---@return string     "<strings>:<total length>"
local function Checksum(file)
  local count, length = 0, 0
  for _, list in pairs(file.actions or {}) do
    for _, text in ipairs(list) do count, length = count + 1, length + #text end
  end
  for _, text in pairs(file.rules or {}) do count, length = count + 1, length + #text end
  return count .. ":" .. length
end

--- Check a PortfolioPlan.lua table (EX4).
---@param file table|nil  The AHProfitPlan global
---@return table          { status = "ok" | "missing" | "version" | "checksum", file }
function Plan.Check(file)
  if type(file) ~= "table" or type(file.ingestedUntil) ~= "table" then return { status = "missing" } end
  if file.schemaVersion ~= Plan.SCHEMA_VERSION then return { status = "version" } end
  if file.checksum ~= Checksum(file) then return { status = "checksum" } end
  return { status = "ok", file = file }
end

--- The checked file, checking it the first time.
---@return table  As Plan.Check
local function State()
  if not state then state = Plan.Check(_G.AHProfitPlan) end
  return state
end

--- Why there is no plan, for the UI's status line. A missing plan is normal: the addon's
--- own recommendations work without it (SPEC §5.2).
---@return string|nil  nil when the plan is usable or simply absent; otherwise a message
function Plan.Problem()
  return ({
    version = "AHSync's plan and the addon versions differ; plan rows are hidden. Update AHProfit or AHSync.",
    checksum = "AHSync's plan file is damaged; plan rows are hidden until AHSync writes it again.",
  })[State().status]
end

--- An optional text field: nil when empty.
---@param s string  The field
---@return string|nil
local function Optional(s)
  if s == "" then return nil end
  return s
end

--- Split one action string (EX3).
---@param text string  "id;kind;key;qty;price;atMs;net;parentId;windowEndMs;status;chance;peakMs;source;durationH"
---@return table       Fields by name; numbers as numbers, empty fields nil; vendor = true for a vendor buy (EX7);
---                    duration in hours for a post (EX9), nil from an older 13-field string
function Plan.DecodeAction(text)
  local f, i = {}, 0
  for field in (text .. ";"):gmatch("([^;]*);") do
    i = i + 1
    f[i] = field
  end
  return { id = f[1], kind = f[2], key = tonumber(f[3]), qty = tonumber(f[4]), price = tonumber(f[5]),
           at = tonumber(f[6]), net = tonumber(f[7]), parent = Optional(f[8]), windowEnd = tonumber(f[9]),
           status = f[10], chance = tonumber(f[11]), peak = tonumber(f[12]), vendor = f[13] == "v",
           duration = tonumber(f[14]) }
end

--- A recipe's product, from the recipes the addon has seen.
---@param db table
---@param spell number
---@return number|nil  Item key; nil when unknown
local function ProductOf(db, spell)
  for _, definitions in pairs(db.recipes or {}) do
    local r = definitions[spell]
    if r and r.product then return r.product end
  end
  return nil
end

--- What one of your events did, as the plan action it would match.
---@param db table
---@param e table   An event
---@return string|nil, number|nil, number|nil  Action kind, item key, units; nil for anything else
local function EventDid(db, e)
  if e.confirmed == false then return nil end              -- an attempt is not a done action (FM20)
  if e.type == "purchase" then return "buy", e.key, e.qty end
  if e.type == "post" then return "post", e.key, e.stack end
  if e.type == "craft" then return "craft", ProductOf(db, e.spell), e.count end
  return nil
end

--- Mark the actions your events after the plan already did, locally and provisionally (SPEC §5.2, FM18):
--- each confirmed purchase, post or craft newer than the character's ingestedUntil takes its units off
--- the earliest open action of the same kind and item; an action with nothing left goes.
---@param charId string
---@param actions table[]   The character's decoded actions, in time order (their qty is lowered)
---@param since number|nil  The character's ingestedUntil, ms; nil = AHSync has read none of its events
---@return table[], number  The actions left, and the events applied
function Plan.DoneLocally(charId, actions, since)
  local db, applied = AHProfit.DB.Get() or {}, 0
  for _, e in ipairs(db.events or {}) do
    local kind, key, units = EventDid(db, e)
    if kind and key and e.char == charId and e.at > (since or 0) then
      for _, a in ipairs(actions) do
        if a.kind == kind and a.key == key and a.qty > 0 and (units or 0) > 0 then
          local take = math.min(a.qty, units)
          a.qty, units = a.qty - take, units - take
          applied = applied + 1
          break
        end
      end
    end
  end
  local left = {}
  for _, a in ipairs(actions) do
    if a.qty > 0 then left[#left + 1] = a end
  end
  return left, applied
end

--- A character's open plan actions, in time order, less what your newer events already did (FM18).
---@param charId string  "Name-Realm"
---@return table[]       As Plan.DecodeAction; empty without a usable plan
---@return number        Events applied locally: > 0 means the plan is provisional
function Plan.Actions(charId)
  local s = State()
  if s.status ~= "ok" then return {}, 0 end
  local out = {}
  for i, text in ipairs((s.file.actions or {})[charId] or {}) do out[i] = Plan.DecodeAction(text) end
  return Plan.DoneLocally(charId, out, (s.file.ingestedUntil or {})[charId])
end

--- How many of your events the plan doesn't include yet but were applied to it locally (FM18).
---@param charId string
---@return number  0 when the plan is up to date
function Plan.Provisional(charId)
  local _, applied = Plan.Actions(charId)
  return applied
end

--- Every character's open plan actions, each with its character id.
---@return table[]  As Plan.DecodeAction plus char; characters by id, actions in time order
function Plan.AllActions()
  local s = State()
  if s.status ~= "ok" then return {} end
  local chars, out = {}, {}
  for charId in pairs(s.file.actions or {}) do chars[#chars + 1] = charId end
  table.sort(chars)
  for _, charId in ipairs(chars) do
    for _, a in ipairs((Plan.Actions(charId))) do
      a.char = charId
      out[#out + 1] = a
    end
  end
  return out
end

--- One character's open plan actions, each with its character id (the window shows only these).
---@param charId string  "Name-Realm"
---@return table[]       As Plan.Actions, plus char
function Plan.CharacterActions(charId)
  local out = Plan.Actions(charId)
  for _, a in ipairs(out) do a.char = charId end
  return out
end

--- An item rule set on the dashboard (SPEC §3.4.2).
---@param key number    Item key
---@return string|nil   "no_buy" | "ignore" | "keep" | "none" (a clear)
---@return number|nil   When it was set, ms
function Plan.Rule(key)
  local s = State()
  local text = s.status == "ok" and (s.file.rules or {})[key]
  if not text then return nil, nil end
  local rule, setAt = text:match("^([^,]+),(%d+)$")
  return rule, tonumber(setAt)
end

--- Your sell record of an item, as AHSync wrote it (SPEC §3.3.3, EX8).
---@param key number
---@return table|nil  { notSelling = bool, line = "Your record (30 d): …" }; nil without one
function Plan.Record(key)
  local s = State()
  local text = s.status == "ok" and (s.file.records or {})[key]
  if not text then return nil end
  local flag, line = text:match("^([01]);(.*)$")
  if not flag then return nil end
  return { notSelling = flag == "1", line = line }
end

--- Every item AHSync flagged not selling (SPEC §3.3.3, A52).
---@return number[]  Item keys, ascending
function Plan.NotSelling()
  local s, out = State(), {}
  for key, text in pairs(s.status == "ok" and s.file.records or {}) do
    if text:sub(1, 2) == "1;" then out[#out + 1] = key end
  end
  table.sort(out)
  return out
end

--- PLAYER_LOGIN: every addon is loaded, so check the plan and prune what AHSync committed (A4).
function Plan.OnLogin()
  local s = State()
  local db = DB.Get()
  if s.status == "ok" and db then DB.Prune(db, s.file.ingestedUntil, Adapter.NowMs()) end
end

--- The reserves AHSync's plan carries for a character: dashboard and synced ones, clears included (EX12).
---@param charId string
---@return table  key -> { qty (−1 all, 0 cleared), at (ms) }; empty without a usable plan
function Plan.Reserves(charId)
  local s, out = State(), {}
  local text = s.status == "ok" and (s.file.reserves or {})[charId] or ""
  for key, qty, at in text:gmatch("(%-?%d+),(%-?%d+),(%d+)") do
    out[tonumber(key)] = { qty = tonumber(qty), at = tonumber(at) }
  end
  return out
end

--- Forget the checked file (tests).
function Plan.Reset()
  state = nil
end

Events.Register("PLAYER_LOGIN", Plan.OnLogin)
