-- Core/Plan.lua: reads PortfolioPlan.lua, written by AHSync (SPEC §3.5, §2.2, §5.2).
-- At login, once both addons are loaded, the file is checked (schema version, export version, checksum)
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

--- Count and total length of every action, rule, portfolio, gold, reserved (its digits) and position string, the
--- file's checksum.
---@param file table  AHProfitPlan
---@return string     "<strings>:<total length>"
local function Checksum(file)
  local count, length = 0, 0
  for _, list in pairs(file.actions or {}) do
    for _, text in ipairs(list) do count, length = count + 1, length + #text end
  end
  for _, text in pairs(file.rules or {}) do count, length = count + 1, length + #text end
  for _, text in pairs(file.portfolio or {}) do count, length = count + 1, length + #text end
  for _, text in pairs(file.gold or {}) do count, length = count + 1, length + #text end
  for _, copper in pairs(file.reserved or {}) do count, length = count + 1, length + #tostring(copper) end
  for _, list in pairs(file.positions or {}) do
    for _, text in ipairs(list) do count, length = count + 1, length + #text end
  end
  return count .. ":" .. length
end

--- Check a PortfolioPlan.lua table: schema, then export version, then checksum (EX4, FM34, FM35).
---@param file table|nil  The AHProfitPlan global
---@return table          { status = "ok" | "missing" | "version" | "older" | "newer" | "checksum", file }
function Plan.Check(file)
  if type(file) ~= "table" or type(file.ingestedUntil) ~= "table" then return { status = "missing" } end
  if file.schemaVersion ~= Plan.SCHEMA_VERSION then return { status = "version" } end
  local stale = AHProfit.Market.ExportStatus(file)
  if stale then return { status = stale } end
  if file.checksum ~= Checksum(file) then return { status = "checksum" } end
  return { status = "ok", file = file }
end

--- The checked file, checking it the first time.
---@return table  As Plan.Check
local function State()
  if not state then state = Plan.Check(_G.AHProfitPlan) end
  return state
end

--- Whether a usable plan file was loaded (the minimap tooltip's "no plan yet", UI20).
---@return boolean  true when PortfolioPlan.lua passed its checks
function Plan.HasPlan()
  return State().status == "ok"
end

--- Why there is no plan, for the UI's status line. A missing plan is normal: the addon's
--- own recommendations work without it (SPEC §5.2).
---@return string|nil  nil when the plan is usable or simply absent; otherwise a message
function Plan.Problem()
  local status = State().status
  return AHProfit.Market.STALE[status] or ({
    version = "AHSync's plan and the addon versions differ; plan rows are hidden. Update AHProfit or AHSync.",
    checksum = "AHSync's plan file is damaged; plan rows are hidden until AHSync writes it again.",
  })[status]
end

--- An optional text field: nil when empty.
---@param s string  The field
---@return string|nil
local function Optional(s)
  if s == "" then return nil end
  return s
end

--- Split one action string (EX3).
---@param text string  "id;kind;key;qty;price;atMs;net;parentId;windowEndMs;status;chance;peakMs;source;durationH;toChar;paid"
---@return table       Fields by name; numbers as numbers, empty fields nil; vendor = true for a vendor buy (EX7);
---                    duration in hours for a post (EX9), nil from an older 13-field string; to = a mail's
---                    recipient (C22, EX16); paid = copper net takes off for the units, 0 when empty or from an
---                    older 15-field string (EX22)
function Plan.DecodeAction(text)
  local f, i = {}, 0
  for field in (text .. ";"):gmatch("([^;]*);") do
    i = i + 1
    f[i] = field
  end
  return { id = f[1], kind = f[2], key = tonumber(f[3]), qty = tonumber(f[4]), price = tonumber(f[5]),
           at = tonumber(f[6]), net = tonumber(f[7]), parent = Optional(f[8]), windowEnd = tonumber(f[9]),
           status = f[10], chance = tonumber(f[11]), peak = tonumber(f[12]), vendor = f[13] == "v",
           duration = tonumber(f[14]), to = Optional(f[15] or ""), paid = tonumber(f[16] or "") or 0 }
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

--- The open mail other characters' plans send to this one (C22, A74): the lines under its craft rows.
---@param charId string  "Name-Realm", the recipient
---@return table[]       As Plan.DecodeAction plus char (the sender), senders by id, each in time order
function Plan.IncomingMail(charId)
  local out = {}
  for _, a in ipairs(Plan.AllActions()) do
    if a.kind == "mail" and a.to == charId and a.char ~= charId then out[#out + 1] = a end
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

--- Split a text on ";", keeping empty fields.
---@param text string
---@return string[]
local function Fields(text)
  local out = {}
  for field in (text .. ";"):gmatch("([^;]*);") do out[#out + 1] = field end
  return out
end

--- Decode one market's portfolio snapshot (SPEC §3.5 `portfolio`, EX18).
---@param text string  "snapshotMs;riskTolerance1000;premiumBase1000;premiumPerDay1000;immediateHours;todayLimit;
---                    weekLimit;horizonDays;configVersion;capitalBase;committed;capacity;allocated"
---@return table       Those fields by name, numbers; riskTolerance, premiumBase and premiumPerDay also as fractions
function Plan.DecodePortfolio(text)
  local names = { "snapshotMs", "riskTolerance1000", "premiumBase1000", "premiumPerDay1000", "immediateHours",
                  "todayLimit", "weekLimit", "horizonDays", "configVersion", "capitalBase", "committed", "capacity",
                  "allocated" }
  local out = {}
  for i, field in ipairs(Fields(text)) do
    if names[i] then out[names[i]] = tonumber(field) end
  end
  out.riskTolerance = out.riskTolerance1000 and out.riskTolerance1000 / 1000
  out.premiumBase = out.premiumBase1000 and out.premiumBase1000 / 1000
  out.premiumPerDay = out.premiumPerDay1000 and out.premiumPerDay1000 / 1000
  return out
end

--- A market's portfolio snapshot, as the desktop's last plan left it (EX18).
---@param marketId string
---@return table|nil  As Plan.DecodePortfolio; nil without a usable plan or a snapshot for that market
function Plan.Portfolio(marketId)
  local s = State()
  local text = s.status == "ok" and (s.file.portfolio or {})[marketId]
  if not text then return nil end
  return Plan.DecodePortfolio(text)
end

--- A character's gold at its last snapshot (EX18).
---@param charId string
---@return number|nil  Copper; nil without a usable plan or a record
---@return number|nil  When it was seen, ms; nil when unknown
function Plan.Gold(charId)
  local s = State()
  local text = s.status == "ok" and (s.file.gold or {})[charId]
  if not text then return nil, nil end
  local copper, at = text:match("^(%d+),(%d*)$")
  return tonumber(copper), tonumber(at)
end

--- The gold the desktop's funded plan reserves on a character (EX18).
---@param charId string
---@return number  Copper; 0 without a usable plan or a reservation
function Plan.Reserved(charId)
  local s = State()
  return s.status == "ok" and (s.file.reserved or {})[charId] or 0
end

--- The characters the snapshot has gold for (EX18).
---@return string[]  Character ids, ascending; empty without a usable plan
function Plan.Characters()
  local s, out = State(), {}
  for charId in pairs(s.status == "ok" and s.file.gold or {}) do out[#out + 1] = charId end
  table.sort(out)
  return out
end

--- Decode one position string (SPEC §3.5 `positions`, EX18).
---@param text string  "positionId;key;state;units;unitsLeft;costLeft;netNow;targetMs;nextAction"
---@return table       { id, key, state, units, unitsLeft, costLeft, netNow, target, nextAction }; empty fields nil
function Plan.DecodePosition(text)
  local f = Fields(text)
  return { id = f[1], key = tonumber(f[2]), state = f[3], units = tonumber(f[4]), unitsLeft = tonumber(f[5]),
           costLeft = tonumber(f[6]), netNow = tonumber(f[7]), target = tonumber(f[8]), nextAction = Optional(f[9] or "") }
end

--- A character's open positions (EX18).
---@param charId string
---@return table[]  As Plan.DecodePosition, by item key; empty without a usable plan
function Plan.Positions(charId)
  local s, out = State(), {}
  for i, text in ipairs(s.status == "ok" and (s.file.positions or {})[charId] or {}) do out[i] = Plan.DecodePosition(text) end
  return out
end

-- ------------------------------------------------------------------ local positions (SPEC §4.0, §3.4D.9; A81, FM38)

Plan.LOCAL_NEXT = "local · AHSync adds it at its next read"   -- a local position's next action

--- Take an event's units off the first open plan action of its kind and item.
---@param actions table[]  Decoded actions with `left` (lowered)
---@param kind string      "buy" | "craft"
---@param key number
---@param units number
---@return table|nil, number  The action and the units it took; nil, 0 when none is open
local function TakeAction(actions, kind, key, units)
  for _, a in ipairs(actions) do
    if a.kind == kind and a.key == key and a.left > 0 then
      local take = math.min(a.left, units)
      a.left = a.left - take
      return a, take
    end
  end
  return nil, 0
end

--- Your confirmed purchases and crafts after the plan that matched one of its buy or craft actions, as positions
--- AHSync hasn't read yet (A81). Each matched action is one position whose id is its first event's id, the same id
--- AHSync gives it (the opening event's), so its next export replaces it; one the export already carries is left out.
--- The events are kept in SavedVariables until AHSync ingests them (DB.Prune, A4), so a /reload keeps them (FM38).
---@param charId string
---@return table[]  { id, key, kind ("buy" | "craft"), state = "Holding", units, unitsLeft, costLeft (a purchase's
---                 qty × price; a craft's 0 until AHSync costs its reagents), netNow (its share of the action's net),
---                 nextAction, isLocal = true }, in event order; empty without a usable plan
function Plan.LocalPositions(charId)
  local s = State()
  if s.status ~= "ok" then return {} end
  local since, db = (s.file.ingestedUntil or {})[charId] or 0, DB.Get() or {}
  local actions, exported, byAction, out = {}, {}, {}, {}
  for i, text in ipairs((s.file.actions or {})[charId] or {}) do
    local a = Plan.DecodeAction(text)
    a.left, actions[i] = a.qty, a
  end
  for _, p in ipairs(Plan.Positions(charId)) do exported[p.id] = true end
  for _, e in ipairs(db.events or {}) do
    local kind, key, units = EventDid(db, e)
    local a, take = nil, 0
    if (kind == "buy" or kind == "craft") and key and e.char == charId and e.at > since and (units or 0) > 0 then
      a, take = TakeAction(actions, kind, key, units)
    end
    if a and not byAction[a.id] then
      byAction[a.id] = { id = e.id, key = key, kind = kind, state = "Holding", units = 0, unitsLeft = 0, costLeft = 0,
                         netNow = 0, nextAction = Plan.LOCAL_NEXT, isLocal = true }
      if not exported[e.id] then out[#out + 1] = byAction[a.id] end
    end
    local p = a and byAction[a.id]
    if p then
      p.units, p.unitsLeft = p.units + units, p.unitsLeft + units
      p.costLeft = p.costLeft + (kind == "buy" and units * (e.price or 0) or 0)
      p.netNow = p.netNow + (a.net or 0) * take / math.max(a.qty, 1)
    end
  end
  return out
end

--- The gold your local positions commit (A81): the reselection treats it as committed, so the cap shrinks.
---@param charId string
---@return number  Copper
function Plan.LocalCommitted(charId)
  local sum = 0
  for _, p in ipairs(Plan.LocalPositions(charId)) do sum = sum + p.costLeft end
  return sum
end

--- A character's positions as the addon knows them now (SPEC §4.0 "never overwrite newer state with an older
--- snapshot", PO8, FM38): the exported ones, each changed by your events after the plan (a post of its item lists
--- it, a sold auction mail of it makes it Partially sold), then the local ones.
---@param charId string
---@return table[]  As Plan.DecodePosition (provisional = true when an event changed it), then Plan.LocalPositions'
function Plan.LivePositions(charId)
  local s, out = State(), Plan.Positions(charId)
  local since, db = s.status == "ok" and (s.file.ingestedUntil or {})[charId] or 0, DB.Get() or {}
  for _, e in ipairs(db.events or {}) do
    local state = (e.type == "post" and e.confirmed ~= false and "Listed")
      or (e.type == "auction_mail" and e.outcome == "sold" and "Partially sold") or nil
    for _, p in ipairs((state and e.char == charId and e.at > since) and out or {}) do
      if p.key == e.key and p.state ~= "Partially sold" then p.state, p.provisional = state, true end
    end
  end
  for _, p in ipairs(Plan.LocalPositions(charId)) do out[#out + 1] = p end
  return out
end

--- Forget the checked file (tests).
function Plan.Reset()
  state = nil
end

Events.Register("PLAYER_LOGIN", Plan.OnLogin)
