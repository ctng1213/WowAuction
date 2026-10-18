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
---@param text string  "id;kind;key;qty;price;atMs;net;parentId;windowEndMs;status;chance"
---@return table       Fields by name; numbers as numbers, empty fields nil
function Plan.DecodeAction(text)
  local f, i = {}, 0
  for field in (text .. ";"):gmatch("([^;]*);") do
    i = i + 1
    f[i] = field
  end
  return { id = f[1], kind = f[2], key = tonumber(f[3]), qty = tonumber(f[4]), price = tonumber(f[5]),
           at = tonumber(f[6]), net = tonumber(f[7]), parent = Optional(f[8]), windowEnd = tonumber(f[9]),
           status = f[10], chance = tonumber(f[11]) }
end

--- A character's open plan actions, in time order.
---@param charId string  "Name-Realm"
---@return table[]       As Plan.DecodeAction; empty without a usable plan
function Plan.Actions(charId)
  local s = State()
  if s.status ~= "ok" then return {} end
  local out = {}
  for i, text in ipairs((s.file.actions or {})[charId] or {}) do out[i] = Plan.DecodeAction(text) end
  return out
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
    for _, a in ipairs(Plan.Actions(charId)) do
      a.char = charId
      out[#out + 1] = a
    end
  end
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

--- PLAYER_LOGIN: every addon is loaded, so check the plan and prune what AHSync committed (A4).
function Plan.OnLogin()
  local s = State()
  local db = DB.Get()
  if s.status == "ok" and db then DB.Prune(db, s.file.ingestedUntil, Adapter.NowMs()) end
end

--- Forget the checked file (tests).
function Plan.Reset()
  state = nil
end

Events.Register("PLAYER_LOGIN", Plan.OnLogin)
