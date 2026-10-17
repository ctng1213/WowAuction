-- Core/Adapter.lua: the only file that touches WoW globals (CLAUDE.md, SPEC §5).
-- Forever's API is partly unverified (SPEC §6), so every guess is marked
-- "-- UNVERIFIED:" and fixes stay in this one file. Everything here is cold
-- path (login, window opened, debounced bag change), so plain tables are fine.

local AHProfit = AHProfit or {}
_G.AHProfit = AHProfit

local Adapter = {}
AHProfit.Adapter = Adapter

local KEY_SUFFIX_FACTOR = 2147483648   -- SPEC §2.1: key = item + 2147483648 × v
local BAG_IDS = { 0, 1, 2, 3, 4 }      -- backpack + 4 bag slots
local BANK_IDS = { -1, 5, 6, 7, 8, 9, 10 }  -- UNVERIFIED: Classic bank container -1 plus 6 bank bags
local MAIL_ATTACHMENTS = 12            -- UNVERIFIED: ATTACHMENTS_MAX_RECEIVE in Classic
local SUFFIX_FIELD = 7                 -- itemID:enchant:gem1:gem2:gem3:gem4:suffixID:...

-- ------------------------------------------------------------------ basics

--- Current server time (SPEC §2.2.1: whole seconds × 1000).
---@return number  UTC Unix ms
function Adapter.NowMs()
  return GetServerTime() * 1000
end

--- Run a callback once after a delay; the only timer the addon uses (SPEC §5.1).
---@param seconds number      Delay in seconds
---@param callback function   Called with no arguments
function Adapter.After(seconds, callback)
  C_Timer.After(seconds, callback)
end

--- Create the invisible frame that receives events.
---@return table  A WoW Frame
function Adapter.CreateEventFrame()
  return CreateFrame("Frame")
end

--- Whether the player is in combat right now.
---@return boolean  true while in combat lockdown
function Adapter.InCombat()
  return InCombatLockdown() and true or false
end

--- Read a SavedVariables global.
---@param name string  Global name, e.g. "AHProfitDB"
---@return any         Its value; nil when the file had none
function Adapter.LoadSaved(name)
  return _G[name]
end

--- Replace a SavedVariables global; WoW writes it to disk at logout or /reload.
---@param name string  Global name, e.g. "AHProfitDB"
---@param value any    New value
function Adapter.StoreSaved(name, value)
  _G[name] = value
end

-- ------------------------------------------------------------------ player

--- Identity and level of the logged-in character.
---@return table  { name, realm, faction ("Horde"|"Alliance"), class (file token, e.g. "MAGE"), level }
function Adapter.PlayerInfo()
  local _, classToken = UnitClass("player")
  return {
    name = UnitName("player"),
    -- SPEC GAP: realm as shown by GetRealmName(); realms with spaces keep them.
    realm = GetRealmName(),
    faction = UnitFactionGroup("player"),
    class = classToken,
    level = UnitLevel("player"),
  }
end

--- Gold on the logged-in character.
---@return number  Integer copper
function Adapter.Money()
  return GetMoney()
end

-- ------------------------------------------------------------------ item keys

--- Split an item string's colon-separated fields; empty fields stay as "".
---@param itemString string  The part after "item:", e.g. "2840:0:0::::::60"
---@return string[]          Fields in order
local function SplitFields(itemString)
  local fields, start = {}, 1
  while true do
    local stop = itemString:find(":", start, true)
    if not stop then
      fields[#fields + 1] = itemString:sub(start)
      return fields
    end
    fields[#fields + 1] = itemString:sub(start, stop - 1)
    start = stop + 1
  end
end

--- AHF's suffix value `v` for an item string (SPEC §2.1, §2.2.1, §6 item 11).
--- UNVERIFIED: the mapping from a link to AHF's `v` is unknown. It is not the
--- classic suffix id (a recorded key has v = 28592, suffix ids are ~14,300).
--- Until it is known, a suffixed item returns nil and is written as key = -itemId
--- (SPEC §2.2.1). Fix it here and only here.
---@param fields string[]  Item string fields from SplitFields
---@return number|nil      v (0 for an item without a random suffix); nil when unknown
local function SuffixValue(fields)
  -- UNVERIFIED: field 7 holds the classic suffix id; Forever may use bonus ids instead.
  local suffixId = tonumber(fields[SUFFIX_FIELD] or "")
  if suffixId and suffixId ~= 0 then return nil end
  return 0
end

--- AHF item key from an item link or item string (SPEC §2.1, §2.2.1).
---@param link string|nil  "|cff...|Hitem:2840:...|h[Copper Bar]|h|r" or "item:2840:..."
---@return number|nil      key = itemId + 2147483648 × v; -itemId for a random-suffix
---                        item (v unknown); nil when not an item link
function Adapter.ItemKey(link)
  if type(link) ~= "string" then return nil end
  local itemString = link:match("|Hitem:([^|]*)|h") or link:match("^item:([^|]*)$")
  if not itemString then return nil end
  local fields = SplitFields(itemString)
  local itemId = tonumber(fields[1])
  if not itemId then return nil end
  local v = SuffixValue(fields)
  if not v then return -itemId end
  return itemId + KEY_SUFFIX_FACTOR * v
end

--- Spell id from a recipe link ("|Henchant:2663|h" or "|Hspell:2663|h").
---@param link string|nil  A recipe or enchant link
---@return number|nil      The spell id; nil when the link is not a spell link
local function SpellIdFromLink(link)
  if type(link) ~= "string" then return nil end
  local id = link:match("|Henchant:(%d+)") or link:match("|Hspell:(%d+)")
  return tonumber(id)
end

-- ------------------------------------------------------------------ skills

--- The character's skill lines (professions, weapon skills, languages, ...).
--- UNVERIFIED: Classic GetNumSkillLines/GetSkillLineInfo; collapsed headers hide their lines.
---@return table[]|nil  { name, rank, max } per non-header line, in game order; nil when a
---                     collapsed header hides lines (SPEC §2.2.1: complete snapshots only)
function Adapter.ReadSkillLines()
  local lines = {}
  for i = 1, GetNumSkillLines() do
    local name, isHeader, isExpanded, rank, _, _, maxRank = GetSkillLineInfo(i)
    if isHeader and not isExpanded then return nil end
    if name and not isHeader then
      lines[#lines + 1] = { name = name, rank = rank, max = maxRank }
    end
  end
  return lines
end

-- ------------------------------------------------------------------ recipe windows

-- Classic has two recipe windows: the trade skill window (most professions)
-- and the craft window (Enchanting). Both are read through the same table of
-- global function names. UNVERIFIED: every name here, for Forever.
local RECIPE_API = {
  trade = {
    line = "GetTradeSkillLine", count = "GetNumTradeSkills", info = "GetTradeSkillInfo",
    headerField = 2, recipeLink = "GetTradeSkillRecipeLink", itemLink = "GetTradeSkillItemLink",
    numMade = "GetTradeSkillNumMade", numReagents = "GetTradeSkillNumReagents",
    reagentInfo = "GetTradeSkillReagentInfo", reagentLink = "GetTradeSkillReagentItemLink",
  },
  craft = {
    line = "GetCraftDisplaySkillLine", count = "GetNumCrafts", info = "GetCraftInfo",
    headerField = 3, recipeLink = "GetCraftRecipeLink", itemLink = "GetCraftItemLink",
    numMade = "GetCraftNumMade", numReagents = "GetCraftNumReagents",
    reagentInfo = "GetCraftReagentInfo", reagentLink = "GetCraftReagentItemLink",
  },
}

--- Call a global function by name if it exists.
---@param name string  Global function name
---@return any         Its results; nothing when the function does not exist
local function CallIfExists(name, ...)
  local fn = _G[name]
  if type(fn) == "function" then return fn(...) end
end

--- Reagents of one recipe in a recipe window.
---@param api table     A RECIPE_API entry
---@param index number  1-based recipe index in the window
---@return table[]|nil  { key, count } per reagent; nil when a reagent link is missing (not loaded yet)
local function ReadReagents(api, index)
  local reagents = {}
  for j = 1, CallIfExists(api.numReagents, index) or 0 do
    local _, _, count = CallIfExists(api.reagentInfo, index, j)
    local key = Adapter.ItemKey(CallIfExists(api.reagentLink, index, j))
    if not key or not count then return nil end
    reagents[#reagents + 1] = { key = key, count = count }
  end
  return reagents
end

--- One recipe in a recipe window, or nil for headers and unreadable rows.
---@param api table     A RECIPE_API entry
---@param index number  1-based recipe index in the window
---@return table|nil    { spell, product (key|nil), yieldMin, yieldMax, skill (nil), cooldown (nil), reagents }
local function ReadRecipe(api, index)
  local info = { CallIfExists(api.info, index) }
  if info[1] == nil or info[api.headerField] == "header" then return nil end
  local itemLink = CallIfExists(api.itemLink, index)
  -- UNVERIFIED: the craft window may have no recipe link; its item link is then an enchant link.
  local spell = SpellIdFromLink(CallIfExists(api.recipeLink, index)) or SpellIdFromLink(itemLink)
  local reagents = ReadReagents(api, index)
  if not spell or not reagents then return nil end
  local yieldMin, yieldMax = CallIfExists(api.numMade, index)
  return {
    spell = spell,
    product = Adapter.ItemKey(itemLink),   -- nil for enchants, which make no item
    yieldMin = yieldMin or 1,
    yieldMax = yieldMax or yieldMin or 1,
    -- Classic has no API for the skill a recipe needs or for its cooldown period
    -- (only time remaining; §6 item 7), so both stay nil = unknown (SPEC §2.2.1).
    skill = nil,
    cooldown = nil,
    reagents = reagents,
  }
end

--- Everything in the open trade skill or craft window.
---@param kind string  "trade" (trade skill window) or "craft" (craft window, Enchanting)
---@return table|nil   { profession, rank, max, recipes = { ReadRecipe rows } }; nil when unreadable
function Adapter.ReadRecipeWindow(kind)
  local api = RECIPE_API[kind]
  local profession, rank, maxRank = CallIfExists(api.line)
  if not profession or profession == "UNKNOWN" then return nil end
  local recipes = {}
  for i = 1, CallIfExists(api.count) or 0 do
    local recipe = ReadRecipe(api, i)
    if recipe then recipes[#recipes + 1] = recipe end
  end
  return { profession = profession, rank = rank, max = maxRank, recipes = recipes }
end

-- ------------------------------------------------------------------ merchant

--- Items the open merchant sells in unlimited supply for gold (SPEC §3.4B vendor reagents).
--- UNVERIFIED: Classic GetMerchantNumItems/GetMerchantItemInfo/GetMerchantItemLink.
---@return number[]  Item keys, in merchant order
function Adapter.ReadMerchantKeys()
  local keys = {}
  for i = 1, GetMerchantNumItems() do
    local _, _, _, _, numAvailable, _, _, extendedCost = GetMerchantItemInfo(i)
    local key = Adapter.ItemKey(GetMerchantItemLink(i))
    if key and numAvailable == -1 and not extendedCost then keys[#keys + 1] = key end
  end
  return keys
end

-- ------------------------------------------------------------------ containers and mail

--- One container slot's item.
--- UNVERIFIED: C_Container (Classic Era 1.15+) or the legacy globals; legacy has no bound flag.
---@param bag number   Container id
---@param slot number  1-based slot
---@return string|nil link, number|nil count, boolean bound
local function ReadSlot(bag, slot)
  if C_Container and C_Container.GetContainerItemInfo then
    local info = C_Container.GetContainerItemInfo(bag, slot)
    if not info then return nil, nil, false end
    return info.hyperlink, info.stackCount, info.isBound and true or false
  end
  local _, count, _, _, _, _, link = GetContainerItemInfo(bag, slot)
  return link, count, false
end

--- Number of slots in a container.
---@param bag number  Container id
---@return number     Slot count; 0 when there is no bag
local function NumSlots(bag)
  if C_Container and C_Container.GetContainerNumSlots then
    return C_Container.GetContainerNumSlots(bag) or 0
  end
  return GetContainerNumSlots(bag) or 0
end

--- Items in a list of containers, one entry per occupied slot.
---@param bagIds number[]  Container ids
---@return table[]|nil     { key, qty, bound } per slot, in container then slot order; nil when
---                        an occupied slot has no item link yet (SPEC §2.2.1: complete snapshots only)
local function ReadContainers(bagIds)
  local items = {}
  for _, bag in ipairs(bagIds) do
    for slot = 1, NumSlots(bag) do
      local link, count, bound = ReadSlot(bag, slot)
      if count then
        local key = Adapter.ItemKey(link)
        if not key then return nil end
        items[#items + 1] = { key = key, qty = count, bound = bound }
      end
    end
  end
  return items
end

--- Items in the character's bags.
---@return table[]|nil  { key, qty, bound } per occupied slot; nil when not fully loaded
function Adapter.ReadBags()
  return ReadContainers(BAG_IDS)
end

--- Items in the bank; only readable while the bank is open.
---@return table[]|nil  { key, qty, bound } per occupied slot; nil when not fully loaded
function Adapter.ReadBank()
  return ReadContainers(BANK_IDS)
end

--- Items attached to mail in the inbox; only readable while the mailbox is open.
--- UNVERIFIED: Classic GetInboxNumItems/GetInboxItem/GetInboxItemLink. Soulbound
--- items can't be mailed, so every mail item is written as not bound.
---@return table[]|nil  { key, qty, bound = false } per attachment; nil when the inbox is not
---                     fully loaded (SPEC §2.2.1: complete snapshots only)
function Adapter.ReadInbox()
  local shown, total = GetInboxNumItems()
  -- UNVERIFIED: the 2nd return is the total on the server; fewer shown means not all mail is loaded.
  -- (Classic shows at most 50, so an inbox of more than 50 mails is never written.)
  if total and total > shown then return nil end
  local items = {}
  for i = 1, shown do
    for a = 1, MAIL_ATTACHMENTS do
      local _, _, _, count = GetInboxItem(i, a)
      if count then
        local key = Adapter.ItemKey(GetInboxItemLink(i, a))
        if not key then return nil end
        items[#items + 1] = { key = key, qty = count, bound = false }
      end
    end
  end
  return items
end
