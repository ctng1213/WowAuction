-- Recommend/Reserves.lua: items you keep for yourself (A25). Per character, because what you keep
-- (food, potions) belongs to the character that uses it: AHProfitDB.reserves[charId] =
-- "key,qty;key,qty", where qty -1 keeps them all. Reserved units are never suggested for selling
-- or crafting; only what you hold above the reserve is. AHProfitDB.reserveTimes[charId] = "key,ms;…"
-- records when each was set or cleared, so the newer of the addon's and the dashboard's wins (A60, S15).

local AHProfit = AHProfit

local Reserves = {}
AHProfit.Reserves = Reserves

Reserves.ALL = -1

--- A character's reserves.
---@param db table      AHProfitDB
---@param charId string
---@return table        key -> units kept (Reserves.ALL keeps all)
function Reserves.Get(db, charId)
  local out = {}
  for key, qty in (((db.reserves or {})[charId]) or ""):gmatch("(%-?%d+),(%-?%d+)") do out[tonumber(key)] = tonumber(qty) end
  return out
end

--- Keep an item for yourself, or stop keeping it.
---@param db table          AHProfitDB; db.reserves[charId] changes
---@param charId string
---@param key number
---@param qty number|nil    Units to keep (Reserves.ALL: all); nil sells normally again
---@param atMs number|nil   When it was set (default now); recorded in reserveTimes (A60)
function Reserves.Set(db, charId, key, qty, atMs)
  local times = Reserves.Times(db, charId)
  times[key] = atMs or AHProfit.Adapter.NowMs()
  local timeKeys, timeParts = {}, {}
  for k in pairs(times) do timeKeys[#timeKeys + 1] = k end
  table.sort(timeKeys)
  for i, k in ipairs(timeKeys) do timeParts[i] = k .. "," .. string.format("%.0f", times[k]) end
  db.reserveTimes = db.reserveTimes or {}
  db.reserveTimes[charId] = table.concat(timeParts, ";")
  local reserves = Reserves.Get(db, charId)
  reserves[key] = qty
  local keys, parts = {}, {}
  for k in pairs(reserves) do keys[#keys + 1] = k end
  table.sort(keys)
  for i, k in ipairs(keys) do parts[i] = k .. "," .. reserves[k] end
  db.reserves = db.reserves or {}
  db.reserves[charId] = #parts > 0 and table.concat(parts, ";") or nil
end

--- When each of a character's reserves was last set or cleared in the addon (A60).
---@param db table      AHProfitDB
---@param charId string
---@return table        key -> ms
function Reserves.Times(db, charId)
  local out = {}
  for key, at in (((db.reserveTimes or {})[charId]) or ""):gmatch("(%-?%d+),(%d+)") do out[tonumber(key)] = tonumber(at) end
  return out
end

--- Apply the plan's reserves that are newer than the addon's own for the same item (A60, S15).
---@param db table        AHProfitDB; reserves and reserveTimes change
---@param charId string
---@param fromPlan table  key -> { qty (−1 all, 0 cleared), at (ms) } (Plan.Reserves)
---@return number         Reserves changed
function Reserves.Merge(db, charId, fromPlan)
  local times, changed = Reserves.Times(db, charId), 0
  local keys = {}
  for key in pairs(fromPlan) do keys[#keys + 1] = key end
  table.sort(keys)
  for _, key in ipairs(keys) do
    local r = fromPlan[key]
    if r.at > (times[key] or -1) then
      Reserves.Set(db, charId, key, r.qty ~= 0 and r.qty or nil, r.at)
      changed = changed + 1
    end
  end
  return changed
end

--- PLAYER_LOGIN: take the dashboard's newer reserves for this character from the plan file (A60).
local function OnLogin()
  local db = AHProfit.DB.Get()
  if not db then return end
  local info = AHProfit.Adapter.PlayerInfo()
  local charId = info.name .. "-" .. info.realm
  Reserves.Merge(db, charId, AHProfit.Plan.Reserves(charId))
end

AHProfit.Events.Register("PLAYER_LOGIN", OnLogin)

--- Units free to sell or craft with, once the reserves are kept.
---@param held number      Units held
---@param kept number|nil  The reserve
---@return number
function Reserves.Free(held, kept)
  if kept == nil then return held end
  if kept == Reserves.ALL then return 0 end
  return math.max(0, held - kept)
end

--- What you carry, less what you keep.
---@param units table     key -> units carried
---@param reserves table  key -> units kept
---@return table          key -> units free
function Reserves.Apply(units, reserves)
  local out = {}
  for key, qty in pairs(units) do
    local free = Reserves.Free(qty, reserves[key])
    if free > 0 then out[key] = free end
  end
  return out
end

--- AHSync's sale actions trimmed to what you hold now above the reserve (A25, A49): until AHSync's
--- next plan knows, a post, vendor sale or disenchant of more is cut down, its net scaled to match,
--- and one of an item you no longer hold (you just posted it) is gone. A chain's later step (an
--- action with a parent) is for units still to be bought or made, so it stays.
---@param actions table[]  Plan actions
---@param reserves table   key -> units kept
---@param held table       key -> units held, bags, bank and mail
---@return table[]         The actions; dropped ones are gone, trimmed ones are copies
function Reserves.Trim(actions, reserves, held)
  local out, left = {}, {}
  for _, a in ipairs(actions) do
    local kept = reserves[a.key]
    if a.parent or not (a.kind == "post" or a.kind == "vendor" or a.kind == "de") then
      out[#out + 1] = a
    else
      if left[a.key] == nil then left[a.key] = Reserves.Free(held[a.key] or 0, kept) end
      local qty = math.min(a.qty, left[a.key])
      if qty > 0 then
        local copy = {}
        for k, v in pairs(a) do copy[k] = v end
        copy.qty, copy.net = qty, a.net and a.net * qty / a.qty
        out[#out + 1] = copy
        left[a.key] = left[a.key] - qty
      end
    end
  end
  return out
end

--- The Inventory note for a reserved item.
---@param kept number|nil
---@return string|nil  nil when nothing is kept
function Reserves.Note(kept)
  if kept == nil then return nil end
  if kept == Reserves.ALL then return "Kept for yourself" end
  return "Keeping " .. kept .. " for yourself"
end
