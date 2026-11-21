-- Capture/Realmless.lua: fold records saved under "Name-" into "Name-Realm" (SPEC §2.2.1, RL2, RL3).
-- Forever's client named no realm after a patch (found 2026-11-20), so sessions were saved as "Name-".
-- Capture/Characters runs Realmless.FoldAll at PLAYER_LOGIN, before anything is captured.

local AHProfit = AHProfit
local DB = AHProfit.DB

local Realmless = {}
AHProfit.Capture = AHProfit.Capture or {}
AHProfit.Capture.Realmless = Realmless

--- Move bags, bank and mail into the real id, each place the newer snapshot.
---@param inventory table  db.inventory
---@param old string       "Name-"
---@param new string       "Name-Realm"
local function FoldInventory(inventory, old, new)
  local from = inventory[old]
  if type(from) ~= "table" then return end
  local to = type(inventory[new]) == "table" and inventory[new] or {}
  for _, place in ipairs({ "bags", "bank", "mail" }) do
    local at, toAt = from[place .. "At"], to[place .. "At"]
    if type(at) == "number" and not (type(toAt) == "number" and toAt > at) then
      to[place], to[place .. "At"] = from[place], at
    end
  end
  inventory[new] = to
end

--- Known recipes only grow, one profession window at a time: keep both sets.
---@param known table  db.knownRecipes
---@param old string
---@param new string
local function FoldKnown(known, old, new)
  if type(known[old]) ~= "string" then return end
  local set = DB.DecodeIdSet(type(known[new]) == "string" and known[new] or nil)
  for id in pairs(DB.DecodeIdSet(known[old])) do set[id] = true end
  known[new] = DB.EncodeIdSet(set)
end

--- Reserves are kept per item with the time each was set (S15): the newer of each item wins.
---@param db table  The database; reserves and reserveTimes change
---@param old string
---@param new string
local function FoldReserves(db, old, new)
  local Reserves = AHProfit.Reserves
  local values, times, newTimes = Reserves.Get(db, old), Reserves.Times(db, old), Reserves.Times(db, new)
  local keys = {}
  for key in pairs(times) do keys[#keys + 1] = key end
  for key in pairs(values) do if times[key] == nil then keys[#keys + 1] = key end end
  table.sort(keys)
  for _, key in ipairs(keys) do
    local at = times[key] or 0
    if newTimes[key] == nil or newTimes[key] < at then Reserves.Set(db, new, key, values[key], at) end
  end
end

--- Cooldowns: the newer observation of each spell wins; a damaged string is dropped.
---@param db table  The database; cooldowns change
---@param old string
---@param new string
local function FoldCooldowns(db, old, new)
  local Cooldowns = AHProfit.Capture.Cooldowns
  if Cooldowns and type(db.cooldowns[old]) == "string" then
    for spell, c in pairs(Cooldowns.Decode(db.cooldowns[old])) do   -- per spell, so the order doesn't matter
      Cooldowns.Record(db, new, spell, c.readyAt, c.source, c.at)
    end
  end
end

--- Re-assign scans and events not yet pruned, their ids kept, so AHSync never stores one twice.
---@param db table   The database; scans and events changed in place
---@param old string
---@param new string
local function FoldRows(db, old, new)
  for _, rows in ipairs({ db.scans, db.events }) do
    for _, row in ipairs(rows) do
      if type(row) == "table" and row.char == old then row.char = new end
      if type(row) == "table" and row.to == old then row.to = new end
    end
  end
end

--- Fold one realm-less id into its real id (SPEC §2.2.1, RL2). Bags, bank, mail and own auctions keep the
--- newer snapshot; known recipes both sets; reserves and cooldowns the newer of each item or spell;
--- professions and the character row the copy updated later.
---@param db table   The database; changed in place, old removed everywhere
---@param old string "Name-"
---@param new string "Name-Realm"
function Realmless.Fold(db, old, new)
  local oldRow, newRow = db.characters[old], db.characters[new]
  local oldNewer = not (type(newRow) == "table" and (newRow.updated or 0) > (oldRow.updated or 0))
  if db.professions[old] ~= nil and (oldNewer or db.professions[new] == nil) then
    db.professions[new] = db.professions[old]
  end
  local fromAuctions, toAuctions = db.ownAuctions[old], db.ownAuctions[new]
  if type(fromAuctions) == "table" and not (type(toAuctions) == "table" and (toAuctions.at or 0) > (fromAuctions.at or 0)) then
    db.ownAuctions[new] = fromAuctions
  end
  FoldInventory(db.inventory, old, new)
  FoldKnown(db.knownRecipes, old, new)
  FoldReserves(db, old, new)
  FoldCooldowns(db, old, new)
  FoldRows(db, old, new)
  if oldNewer then
    oldRow.realm = new:sub(#old + 1)
    if oldRow.login == nil and type(newRow) == "table" then oldRow.login = newRow.login end
    db.characters[new] = oldRow
  end
  for _, field in ipairs({ "characters", "professions", "knownRecipes", "inventory", "ownAuctions",
                           "reserves", "reserveTimes", "cooldowns" }) do
    db[field][old] = nil
  end
  DB.NoteFold(db, old, new, AHProfit.Adapter.NowMs())
end

--- Fold every stored realm-less character whose real id can be told (RL2, RL3).
---@param db table           The database
---@param realmOf function   function(row) -> the realm of the one matching stored character, or nil
---@return string[]          The realm-less ids folded, sorted
function Realmless.FoldAll(db, realmOf)
  local ids = {}
  for id, row in pairs(db.characters) do
    if type(row) == "table" and row.realm == "" and id == tostring(row.name) .. "-" then ids[#ids + 1] = id end
  end
  table.sort(ids)                            -- collected first: the table changes while folding
  local folded = {}
  for _, old in ipairs(ids) do
    local realm = realmOf(db.characters[old])
    if realm then
      Realmless.Fold(db, old, old .. realm)
      folded[#folded + 1] = old
    end
  end
  return folded
end
