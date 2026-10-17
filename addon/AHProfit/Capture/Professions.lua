-- Capture/Professions.lua: professions and skill per character, account-wide
-- recipe definitions, known recipes per character, and vendor-sold reagents
-- (SPEC §2.2, §2.2.1, §4.1).

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB
local Characters = AHProfit.Capture.Characters

local Professions = {}
AHProfit.Capture.Professions = Professions

-- Skill lines that are professions (enUS client names; SPEC §2.2.1).
Professions.NAMES = {
  Alchemy = true, Blacksmithing = true, Enchanting = true, Engineering = true,
  Herbalism = true, Leatherworking = true, Mining = true, Skinning = true, Tailoring = true,
  Cooking = true, ["First Aid"] = true, Fishing = true,
}

local windowOpen = { trade = false, craft = false }
local merchantOpen = false

--- Write the character's professions and skill levels, replacing the old entry.
---@param db table      The database; db.professions[charId] is replaced
---@param charId string "Name-Realm"
function Professions.CaptureSkills(db, charId)
  local skills = {}
  for _, line in ipairs(Adapter.ReadSkillLines()) do
    if Professions.NAMES[line.name] then
      skills[line.name] = { skill = line.rank, max = line.max }
    end
  end
  db.professions[charId] = skills
end

--- Encode a recipe's reagents, ascending by key (SPEC §2.2.1 "key,count;key,count").
---@param reagents table[]  { key, count } per reagent
---@return string           e.g. "2840,2"; "" when the recipe has no reagents
local function EncodeReagents(reagents)
  local rows = {}
  for i, reagent in ipairs(reagents) do rows[i] = { reagent.key, reagent.count } end
  table.sort(rows, function(a, b) return a[1] < b[1] or (a[1] == b[1] and a[2] < b[2]) end)
  return DB.EncodeRows(rows)
end

--- Write every recipe in an open recipe window and add them to the character's known recipes.
--- Known recipes are only ever added: a filtered or collapsed window shows a subset,
--- and recipes are not unlearned one at a time.
---@param db table      The database; db.recipes[profession] and db.knownRecipes[charId] change
---@param charId string "Name-Realm"
---@param kind string   "trade" or "craft" (Adapter.ReadRecipeWindow)
---@return string|nil   The profession captured; nil when the window could not be read
function Professions.CaptureRecipes(db, charId, kind)
  local window = Adapter.ReadRecipeWindow(kind)
  if not window then return nil end
  local definitions = db.recipes[window.profession] or {}
  db.recipes[window.profession] = definitions
  local known = DB.DecodeIdSet(db.knownRecipes[charId])
  for _, recipe in ipairs(window.recipes) do
    definitions[recipe.spell] = {
      product = recipe.product,
      yieldMin = recipe.yieldMin,
      yieldMax = recipe.yieldMax,
      skill = recipe.skill,
      cooldown = recipe.cooldown,   -- seconds
      reagents = EncodeReagents(recipe.reagents),
    }
    known[recipe.spell] = true
  end
  db.knownRecipes[charId] = DB.EncodeIdSet(known)
  return window.profession
end

--- Add the open merchant's unlimited items to the vendor-reagent set (SPEC §3.4B).
-- SPEC GAP: "keys a merchant was seen selling" is read as every unlimited-supply
-- item bought with gold, not only reagents of known recipes; AHSync can filter.
---@param db table  The database; db.vendorReagents changes
function Professions.CaptureMerchant(db)
  local keys = DB.DecodeIdSet(db.vendorReagents)
  for _, key in ipairs(Adapter.ReadMerchantKeys()) do keys[key] = true end
  db.vendorReagents = DB.EncodeIdSet(keys)
end

--- Capture the character's skills into the live database.
local function CaptureSkillsLive()
  local db = DB.Get()
  if db then Professions.CaptureSkills(db, Characters.CurrentId()) end
end

--- Capture the open trade skill window into the live database.
local function CaptureTradeLive()
  local db = DB.Get()
  if db and windowOpen.trade then Professions.CaptureRecipes(db, Characters.CurrentId(), "trade") end
end

--- Capture the open craft window into the live database.
local function CaptureCraftLive()
  local db = DB.Get()
  if db and windowOpen.craft then Professions.CaptureRecipes(db, Characters.CurrentId(), "craft") end
end

--- Capture the open merchant into the live database.
local function CaptureMerchantLive()
  local db = DB.Get()
  if db and merchantOpen then Professions.CaptureMerchant(db) end
end

--- PLAYER_LOGIN: capture skills right away.
local function OnLogin()
  CaptureSkillsLive()
end

--- SKILL_LINES_CHANGED: debounced skill capture.
local function OnSkillsChanged()
  Events.Debounce("professions.skills", CaptureSkillsLive)
end

--- TRADE_SKILL_UPDATE while the window is open: debounced recipe capture.
local function OnTradeUpdate()
  Events.Debounce("professions.trade", CaptureTradeLive)
end

--- CRAFT_UPDATE while the window is open: debounced recipe capture.
local function OnCraftUpdate()
  Events.Debounce("professions.craft", CaptureCraftLive)
end

--- MERCHANT_UPDATE while the merchant is open: debounced merchant capture.
local function OnMerchantUpdate()
  Events.Debounce("professions.merchant", CaptureMerchantLive)
end

--- TRADE_SKILL_SHOW / TRADE_SKILL_CLOSE: listen to updates only while the window is open (SPEC §5.1).
---@param event string  The event name
local function OnTradeWindow(event)
  windowOpen.trade = event == "TRADE_SKILL_SHOW"
  if windowOpen.trade then
    Events.Register("TRADE_SKILL_UPDATE", OnTradeUpdate)
    OnTradeUpdate()
  else
    Events.Unregister("TRADE_SKILL_UPDATE", OnTradeUpdate)
  end
end

--- CRAFT_SHOW / CRAFT_CLOSE: listen to updates only while the window is open (SPEC §5.1).
---@param event string  The event name
local function OnCraftWindow(event)
  windowOpen.craft = event == "CRAFT_SHOW"
  if windowOpen.craft then
    Events.Register("CRAFT_UPDATE", OnCraftUpdate)
    OnCraftUpdate()
  else
    Events.Unregister("CRAFT_UPDATE", OnCraftUpdate)
  end
end

--- MERCHANT_SHOW / MERCHANT_CLOSED: listen to updates only while the merchant is open.
---@param event string  The event name
local function OnMerchantWindow(event)
  merchantOpen = event == "MERCHANT_SHOW"
  if merchantOpen then
    Events.Register("MERCHANT_UPDATE", OnMerchantUpdate)
    OnMerchantUpdate()
  else
    Events.Unregister("MERCHANT_UPDATE", OnMerchantUpdate)
  end
end

Events.Register("PLAYER_LOGIN", OnLogin)
Events.Register("SKILL_LINES_CHANGED", OnSkillsChanged)
Events.Register("TRADE_SKILL_SHOW", OnTradeWindow)
Events.Register("TRADE_SKILL_CLOSE", OnTradeWindow)
Events.Register("CRAFT_SHOW", OnCraftWindow)
Events.Register("CRAFT_CLOSE", OnCraftWindow)
Events.Register("MERCHANT_SHOW", OnMerchantWindow)
Events.Register("MERCHANT_CLOSED", OnMerchantWindow)
