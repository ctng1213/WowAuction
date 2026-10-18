-- Recommend/Reserves.lua: items you keep for yourself (A25). Per character, because what you keep
-- (food, potions) belongs to the character that uses it: AHProfitDB.reserves[charId] =
-- "key,qty;key,qty", where qty -1 keeps them all. Reserved units are never suggested for selling
-- or crafting; only what you hold above the reserve is.

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
function Reserves.Set(db, charId, key, qty)
  local reserves = Reserves.Get(db, charId)
  reserves[key] = qty
  local keys, parts = {}, {}
  for k in pairs(reserves) do keys[#keys + 1] = k end
  table.sort(keys)
  for i, k in ipairs(keys) do parts[i] = k .. "," .. reserves[k] end
  db.reserves = db.reserves or {}
  db.reserves[charId] = #parts > 0 and table.concat(parts, ";") or nil
end

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

--- AHSync's sale actions trimmed to what you hold above the reserve (until AHSync's next plan
--- knows the reserve): a post or vendor sale of more is cut down, its net scaled to match.
---@param actions table[]  Plan actions
---@param reserves table   key -> units kept
---@param held table       key -> units held
---@return table[]         The actions; dropped ones are gone, trimmed ones are copies
function Reserves.Trim(actions, reserves, held)
  local out, left = {}, {}
  for _, a in ipairs(actions) do
    local kept = reserves[a.key]
    if kept == nil or not (a.kind == "post" or a.kind == "vendor" or a.kind == "de") then
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
