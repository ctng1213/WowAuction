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

local TRADE_UPDATE = Adapter.TradeUpdateEvent()   -- modern or Classic event name
local windowOpen = { trade = false, craft = false }
local merchantOpen = false
local learnedListeners = {}        -- called with recipes a character just learned (A83)

--- Write the character's professions and skill levels, replacing the old entry.
--- Skipped (old entry kept) when a collapsed Skills header hides lines (SPEC §2.2.1).
---@param db table      The database; db.professions[charId] is replaced
---@param charId string "Name-Realm"
---@return boolean      true when written
function Professions.CaptureSkills(db, charId)
  local lines = Adapter.ReadSkillLines()
  if not lines then return false end
  local skills = {}
  for _, line in ipairs(lines) do
    if Professions.NAMES[line.name] then
      skills[line.name] = { skill = line.rank, max = line.max }
    end
  end
  db.professions[charId] = skills
  return true
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
  local learned = {}
  for _, recipe in ipairs(window.recipes) do
    if recipe.learned and not known[recipe.spell] and recipe.product then learned[#learned + 1] = recipe end
    definitions[recipe.spell] = {
      product = recipe.product,
      yieldMin = recipe.yieldMin,
      yieldMax = recipe.yieldMax,
      skill = recipe.skill,         -- nil (omitted) when unknown (SPEC §2.2.1)
      cooldown = recipe.cooldown,   -- seconds; nil when unknown, 0 = no cooldown
      reagents = EncodeReagents(recipe.reagents),
    }
    -- The modern window also lists unlearned recipes: define them, but don't mark them known.
    if recipe.learned then known[recipe.spell] = true end
  end
  db.knownRecipes[charId] = DB.EncodeIdSet(known)
  if #learned > 0 then Professions.NotifyLearned(charId, learned) end
  return window.profession
end

--- Tell the listeners which recipes a character just learned (A83).
---@param charId string
---@param recipes table[]  The window's recipes newly known: { spell, product, yieldMin, yieldMax, cooldown,
---                        reagents = { { key, count } } }, in window order
function Professions.NotifyLearned(charId, recipes)
  local out = {}
  for i, r in ipairs(recipes) do
    local reagents = {}
    for j, m in ipairs(r.reagents) do reagents[j] = { m.key, m.count } end
    out[i] = { spell = r.spell, product = r.product, yieldMin = r.yieldMin or 1, yieldMax = r.yieldMax or r.yieldMin or 1,
               cooldown = r.cooldown, reagents = reagents, crafters = 1 }
  end
  table.sort(out, function(a, b) return a.spell < b.spell end)
  for _, fn in ipairs(learnedListeners) do fn(charId, out) end
end

--- Call a function when a captured window shows recipes the character didn't know before (A83).
--- A character's first capture counts every recipe as new; the listener decides what it needs.
---@param fn function  Called with (charId, recipes in Options.KnownRecipes' shape, by spell)
function Professions.OnLearned(fn)
  learnedListeners[#learnedListeners + 1] = fn
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

--- TRADE_SKILL_LIST_UPDATE (modern) / TRADE_SKILL_UPDATE (Classic) while open: debounced capture.
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
    Events.Register(TRADE_UPDATE, OnTradeUpdate)
    OnTradeUpdate()
  else
    Events.Unregister(TRADE_UPDATE, OnTradeUpdate)
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
if Adapter.HasCraftWindow() then   -- Classic only; unknown events on Forever
  Events.Register("CRAFT_SHOW", OnCraftWindow)
  Events.Register("CRAFT_CLOSE", OnCraftWindow)
end
Events.Register("MERCHANT_SHOW", OnMerchantWindow)
Events.Register("MERCHANT_CLOSED", OnMerchantWindow)
