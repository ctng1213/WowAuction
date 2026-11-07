--- WoW client stubs for the addon's Lua tests (SPEC §8.1: no real WoW in tests).
--- Usage: local Stubs = require("wow_stubs"); Stubs.Reset(); <shape Stubs.world>; Stubs.Login().
--- The world table is the fake game state; the installed globals read it on
--- every call, so a test can change it at any time. Events are fired with
--- Stubs.FireEvent, timers run with Stubs.Advance, and setting an OnUpdate
--- script raises (SPEC §5.1). Stubs.Serialize writes a table the way WoW
--- writes a SavedVariables file.

local UI = require("ui_stubs")
local Stubs = {}

local ADDON_DIR = "addon/AHProfit/"
local TOC = ADDON_DIR .. "AHProfit.toc"

-- Item class ids for GetItemInfoInstant (2 weapon, 4 armor, 7 trade goods); default 7.
Stubs.ITEM_CLASSES = { [6585] = 4, [2852] = 4 }

-- Item names for links; cosmetic, only so links look real.
-- Item levels (GetItemInfo's 4th return); others 1.
Stubs.ITEM_LEVELS = { [6585] = 18 }

-- What a merchant pays for some items, copper (GetItemInfo's 11th return); others 0.
Stubs.ITEM_SELL = { [2589] = 3, [3300] = 12 }

-- Link colours (quality) of some items; others print white (common).
Stubs.ITEM_COLORS = { [6948] = "ffffff", [3300] = "9d9d9d", [2851] = "1eff00" }

Stubs.ITEM_NAMES = {
  [2320] = "Coarse Thread", [2321] = "Fine Thread", [2589] = "Linen Cloth", [2592] = "Wool Cloth",
  [2835] = "Rough Stone", [2840] = "Copper Bar", [2852] = "Copper Chain Pants",
  [2862] = "Rough Sharpening Stone", [4306] = "Silk Cloth", [6217] = "Copper Rod",
  [6218] = "Runed Copper Rod", [6270] = "Pattern: Blue Linen Vest", [6585] = "Scouting Gloves", [6948] = "Hearthstone",
  [10938] = "Lesser Magic Essence", [10940] = "Strange Dust",
}

local installed = {}   -- names of globals the stubs defined, cleared by Reset
local frames = {}      -- every frame created, in creation order
local timers = {}      -- pending C_Timer.After callbacks { due, seq, fn }
local timerSeq = 0
local clock = 0        -- seconds since Reset, for timers
local profileMs = 0    -- debugprofilestop(): moves only when the fake AH is read, so budgets are counted deterministically

--- A fresh fake game state: a level 60 Horde mage with nothing, out of combat.
---@return table  The world; see the field comments
function Stubs.NewWorld()
  return {
    player = { name = "Amaya", realm = "Grommash", faction = "Horde",
               className = "Mage", class = "MAGE", level = 60 },
    money = 0,                -- copper
    serverTime = 1760000000,  -- seconds; GetServerTime floors it
    inCombat = false,
    localOffsetS = 0,         -- the machine's local time minus UTC, seconds (date() uses it)
    uncached = {},            -- [itemId] = true: GetItemInfo returns nil, as before the client caches it
    skillLines = {},          -- { name, header = bool, collapsed = bool, rank, max }
    containers = {},          -- [bagId] = { size = n, slots = { [slot] = { id, count, bound, suffix, noLink } } }
    inbox = {},               -- one array of { id, count, noLink = bool } attachments per mail
    sendMail = {},            -- [slot] = { id, count }: the attachments of the mail being written (A75)
    sent = {},                -- { recipient, subject, body } per SendMail call
    inboxTotal = nil,         -- mails on the server; default #inbox (all loaded)
    merchant = {},            -- { id, numAvailable (-1 = unlimited), extendedCost = bool }
    loot = {},                -- the open loot window's slots: { id, count } or { coin = copper } (Stubs.OpenLoot)
    lootSource = nil,         -- GetLootSourceInfo's GUID for every slot; nil = the client gives none
    unknownEvents = {},       -- [event] = true: RegisterEvent raises, as the real client does
    tradeSkill = nil,         -- { name, rank, max, recipes = { { header = "Name" } | recipe } }
    craft = nil,              -- same shape as tradeSkill
    -- recipe: { name, spell, product (item id|nil), min, max, reagents = { { id, count } } }
  }
end

-- ------------------------------------------------------------------ links

--- An item link as the client prints it.
---@param id number          Item id
---@param suffix number|nil  Classic random-suffix id, or nil
---@return string            "|cffffffff|Hitem:<id>::::::<suffix>::60::::|h[Name]|h|r"
function Stubs.ItemLink(id, suffix)
  local fields = { id, "", "", "", "", "", suffix or "", "", 60, "", "", "", "" }
  local name = Stubs.ITEM_NAMES[id] or ("Item " .. id)
  return "|cff" .. (Stubs.ITEM_COLORS[id] or "ffffff") .. "|Hitem:" .. table.concat(fields, ":") .. "|h[" .. name .. "]|h|r"
end

--- A recipe (enchant) link as the trade skill and craft windows print it.
---@param spell number  Spell id
---@param name string   Recipe name
---@return string       "|cffffd000|Henchant:<spell>|h[Name]|h|r"
function Stubs.EnchantLink(spell, name)
  return "|cffffd000|Henchant:" .. spell .. "|h[" .. name .. "]|h|r"
end

-- ------------------------------------------------------------------ frames, events, timers

-- Frames are ui_stubs regions: they record events and scripts as well as layout (SPEC §8.4).

--- Fire a WoW event at every frame registered for it, in frame creation order.
---@param event string  WoW event name
function Stubs.FireEvent(event, ...)
  for _, frame in ipairs(frames) do
    local handler = frame.scripts.OnEvent
    if frame.events[event] and handler then handler(frame, event, ...) end
  end
end

--- Whether any frame is registered for an event.
---@param event string  WoW event name
---@return boolean      true when at least one frame listens
function Stubs.IsRegistered(event)
  for _, frame in ipairs(frames) do
    if frame.events[event] then return true end
  end
  return false
end

--- Number of C_Timer.After callbacks still waiting.
---@return number
function Stubs.PendingTimers()
  return #timers
end

--- Let time pass: moves the server clock and runs timers that come due, in order.
---@param seconds number  Seconds to advance
function Stubs.Advance(seconds)
  local target = clock + seconds
  while true do
    table.sort(timers, function(a, b) return a.due < b.due or (a.due == b.due and a.seq < b.seq) end)
    local nextTimer = timers[1]
    if not nextTimer or nextTimer.due > target then break end
    table.remove(timers, 1)
    Stubs.world.serverTime = Stubs.world.serverTime + (nextTimer.due - clock)
    clock = nextTimer.due
    nextTimer.fn()
  end
  Stubs.world.serverTime = Stubs.world.serverTime + (target - clock)
  clock = target
end

--- Run the timers that are due now once, like one rendered frame; timers they start wait.
function Stubs.RunDueTimers()
  local due, later = {}, {}
  for _, timer in ipairs(timers) do
    if timer.due <= clock then due[#due + 1] = timer else later[#later + 1] = timer end
  end
  timers = later
  table.sort(due, function(a, b) return a.seq < b.seq end)
  for _, timer in ipairs(due) do timer.fn() end
end

--- Enter combat: sets the lockdown flag and fires PLAYER_REGEN_DISABLED.
function Stubs.EnterCombat()
  Stubs.world.inCombat = true
  Stubs.FireEvent("PLAYER_REGEN_DISABLED")
end

--- Leave combat: clears the lockdown flag and fires PLAYER_REGEN_ENABLED.
function Stubs.LeaveCombat()
  Stubs.world.inCombat = false
  Stubs.FireEvent("PLAYER_REGEN_ENABLED")
end

-- ------------------------------------------------------------------ globals

--- Define a global and remember it so Reset can remove it.
---@param name string  Global name
---@param value any    Its value
local function Define(name, value)
  installed[#installed + 1] = name
  _G[name] = value
end

--- Define a global for one test (e.g. a modern API); Stubs.Reset removes it again.
---@param name string  Global name
---@param value any    Its value
function Stubs.Define(name, value)
  Define(name, value)
end

-- The Classic globals the live Forever client (build 1.60.1, 2026-11-06) no longer has: its diagnostics read
-- each of them nil (SPEC §6 item 26).
Stubs.DROPPED_ON_LIVE = { "GetItemInfo", "GetItemInfoInstant", "GetSpellInfo", "GetNumSkillLines", "GetSkillLineInfo",
                          "GetTradeSkillLine", "GetNumTradeSkills", "GetTradeSkillInfo", "GetTradeSkillRecipeLink",
                          "GetCraftDisplaySkillLine", "GetNumCrafts", "GetCraftInfo", "GetMerchantItemInfo",
                          "GetContainerItemInfo", "QueryAuctionItems", "CanSendAuctionQuery", "GetAuctionItemInfo" }

--- Make the client look like the live Forever client: the dropped globals are gone, and what it has instead
--- answers: C_Item, C_Spell, GetProfessions from world.skillLines' non-header lines, and C_MerchantFrame from
--- world.merchant (A93, A94). Call once after Stubs.Reset (and any Stubs.Define of its own), before Stubs.Login.
function Stubs.LiveForeverClient()
  local itemInfo, instant, spellInfo = _G.GetItemInfo, _G.GetItemInfoInstant, _G.GetSpellInfo
  local merchantInfo = _G.GetMerchantItemInfo
  for _, name in ipairs(Stubs.DROPPED_ON_LIVE) do _G[name] = nil end
  Define("C_Item", { GetItemInfo = itemInfo, GetItemInfoInstant = instant })
  Define("C_Spell", { GetSpellName = function(id) return (spellInfo(id)) end, GetSpellInfo = function(id)
    local name = spellInfo(id)
    return name and { name = name, spellID = id, iconID = 136243 }
  end })
  local function Professions()
    local out = {}
    for _, line in ipairs(Stubs.world.skillLines) do
      if not line.header then out[#out + 1] = line end
    end
    return out
  end
  Define("GetProfessions", function()
    local indices = {}
    for i = 1, #Professions() do indices[i] = i end
    return unpack(indices)
  end)
  Define("GetProfessionInfo", function(i)
    local line = Professions()[i]
    if line then return line.name, "icon", line.rank, line.max end
  end)
  Define("C_MerchantFrame", { GetItemInfo = function(i)
    local name, _, price, stack, numAvailable, _, _, extendedCost = merchantInfo(i)
    return name and { name = name, price = price, stackCount = stack, numAvailable = numAvailable,
                      hasExtendedCost = extendedCost }
  end })
end

--- A fake modern C_TradeSkillUI (Dragonflight-era shape) showing one open profession window.
--- Define it with Stubs.Define("C_TradeSkillUI", Stubs.ModernTradeSkill{...}) before Stubs.Login().
---@param window table  { name, rank, max, linked = bool, ready = bool (default true),
---                      recipes = { { id, out (item id|nil), learned = bool,
---                                    reagents = { { itemId, count, optional = bool } } } } }
---@return table        The fake namespace
function Stubs.ModernTradeSkill(window)
  local byId = {}
  for _, recipe in ipairs(window.recipes) do byId[recipe.id] = recipe end
  local api = {}
  function api.IsTradeSkillReady() return window.ready ~= false end
  function api.IsDataSourceChanging() return false end
  function api.IsTradeSkillLinked() return window.linked == true end
  function api.IsTradeSkillGuild() return false end
  function api.IsNPCCrafting() return false end
  function api.GetBaseProfessionInfo()
    return { professionName = window.name, skillLevel = window.rank, maxSkillLevel = window.max }
  end
  function api.GetAllRecipeIDs()
    local ids = {}
    for i, recipe in ipairs(window.recipes) do ids[i] = recipe.id end
    return ids
  end
  function api.GetRecipeInfo(id) return { recipeID = id, learned = byId[id].learned } end
  function api.GetRecipeSchematic(id)
    local slots = {}
    for i, reagent in ipairs(byId[id].reagents) do
      slots[i] = { reagents = { { itemID = reagent[1] } }, quantityRequired = reagent[2],
                   required = not reagent.optional }
    end
    return { recipeID = id, outputItemID = byId[id].out, quantityMin = 1, quantityMax = 1,
             reagentSlotSchematics = slots }
  end
  return api
end

--- A fake C_AuctionHouse with the replicate API (SPEC §4.2). Define it with
--- Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse(house)) before Stubs.Login().
--- Each GetReplicateItemInfo call costs house.costMs on the profile clock (default 0.01).
---@param house table  { auctions = { { id, count, buyout (stack, 0 = none), left (1-4, as Forever), pet = bool,
---                      noLink = bool } }, answers = bool (default true: a call fires the update),
---                      calls = 0 (counted here) }
---@return table       The fake namespace
function Stubs.ReplicateHouse(house)
  house.calls = house.calls or 0
  local function auction(i) return house.auctions[i + 1] end   -- the API is 0-based
  local api = {}
  function api.ReplicateItems()
    house.calls = house.calls + 1
    if house.answers ~= false then Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE") end
  end
  function api.GetNumReplicateItems() return #house.auctions end
  function api.GetReplicateItemInfo(i)
    profileMs = profileMs + (house.costMs or 0.01)
    local a = auction(i)
    if not a then return nil end
    return Stubs.ITEM_NAMES[a.id], "icon", a.count, 1, true, 1, 0, a.buyout, 1, a.buyout, 0,
      false, nil, "Seller", "Seller-Grommash", 0, a.id
  end
  function api.GetReplicateItemLink(i)
    local a = auction(i)
    if a and not a.noLink then return Stubs.ItemLink(a.id) end
  end
  function api.GetReplicateItemTimeLeft(i) local a = auction(i); return a and a.left end
  function api.GetReplicateItemBattlePetInfo(i) local a = auction(i); return a and a.pet and 42 or 0 end
  return api
end

--- An auction of the fake replicate answer.
---@param id number      Item id
---@param price number   Unit price, copper
---@param count number   Stack size
---@param left number    Time left 1-4 (short..very long, as Forever returns it)
---@return table         { id, count, buyout, left }
function Stubs.Auction(id, price, count, left)
  return { id = id, count = count, buyout = price * count, left = left }
end

--- The recipe at an index of a fake recipe window.
---@param window table|nil  world.tradeSkill or world.craft
---@param i number          1-based index
---@return table|nil        The row
local function RecipeRow(window, i)
  return window and window.recipes[i]
end

--- Reagent j of recipe i, or nil.
---@param window table|nil  world.tradeSkill or world.craft
---@param i number          Recipe index
---@param j number          Reagent index
---@return table|nil        { id, count }
local function ReagentRow(window, i, j)
  local row = RecipeRow(window, i)
  return row and row.reagents and row.reagents[j]
end

--- Install the recipe-window globals for one window kind.
---@param prefix string     "TradeSkill" or "Craft"
---@param getWindow function Returns the window table from the world
local function InstallRecipeWindow(prefix, getWindow)
  Define("Get" .. prefix .. "NumMade", function(i)
    local row = RecipeRow(getWindow(), i)
    if row then return row.min or 1, row.max or row.min or 1 end
  end)
  Define("Get" .. prefix .. "NumReagents", function(i)
    local row = RecipeRow(getWindow(), i)
    return row and row.reagents and #row.reagents or 0
  end)
  Define("Get" .. prefix .. "ReagentInfo", function(i, j)
    local r = ReagentRow(getWindow(), i, j)
    if r then return Stubs.ITEM_NAMES[r[1]], "icon", r[2], 0 end
  end)
  Define("Get" .. prefix .. "ReagentItemLink", function(i, j)
    local r = ReagentRow(getWindow(), i, j)
    return r and Stubs.ItemLink(r[1])
  end)
  Define("Get" .. prefix .. "RecipeLink", function(i)
    local row = RecipeRow(getWindow(), i)
    if row and row.spell then return Stubs.EnchantLink(row.spell, row.name) end
  end)
  Define("Get" .. prefix .. "ItemLink", function(i)
    local row = RecipeRow(getWindow(), i)
    if not row or row.header then return nil end
    if row.product then return Stubs.ItemLink(row.product) end
    return Stubs.EnchantLink(row.spell, row.name)
  end)
end

--- Install the trade skill and craft window globals.
local function InstallRecipeWindows()
  local function trade() return Stubs.world.tradeSkill end
  local function craft() return Stubs.world.craft end
  Define("GetTradeSkillLine", function()
    local w = trade()
    if not w then return "UNKNOWN", 0, 0 end
    return w.name, w.rank, w.max
  end)
  Define("GetNumTradeSkills", function() local w = trade(); return w and #w.recipes or 0 end)
  Define("GetTradeSkillInfo", function(i)
    local row = RecipeRow(trade(), i)
    if not row then return nil end
    if row.header then return row.header, "header", 0, true end
    return row.name, "optimal", 0, nil
  end)
  Define("GetCraftDisplaySkillLine", function()
    local w = craft()
    if w then return w.name, w.rank, w.max end
  end)
  Define("GetNumCrafts", function() local w = craft(); return w and #w.recipes or 0 end)
  Define("GetCraftInfo", function(i)
    local row = RecipeRow(craft(), i)
    if not row then return nil end
    if row.header then return row.header, nil, "header", 0, true, 0, 0 end
    return row.name, nil, "optimal", 0, nil, 0, 0
  end)
  InstallRecipeWindow("TradeSkill", trade)
  InstallRecipeWindow("Craft", craft)
end

--- Container slot of the fake world, or nil.
---@param bag number   Container id
---@param slot number  Slot
---@return table|nil   { id, count, bound, suffix }
local function SlotItem(bag, slot)
  local container = Stubs.world.containers[bag]
  return container and container.slots and container.slots[slot]
end

--- Install container (bags, bank), mail and merchant globals.
local function InstallItems()
  local function numSlots(bag)
    local container = Stubs.world.containers[bag]
    return container and container.size or 0
  end
  Define("C_Container", {
    GetContainerNumSlots = numSlots,
    GetContainerItemInfo = function(bag, slot)
      local item = SlotItem(bag, slot)
      if not item then return nil end
      return { stackCount = item.count, hyperlink = not item.noLink and Stubs.ItemLink(item.id, item.suffix) or nil,
               isBound = item.bound or false, isLocked = item.locked or false, itemID = item.id }
    end,
  })
  Define("GetContainerNumSlots", numSlots)
  Define("GetContainerItemInfo", function(bag, slot)
    local item = SlotItem(bag, slot)
    if item then
      return "icon", item.count, item.locked or false, 1, false, false,
        not item.noLink and Stubs.ItemLink(item.id, item.suffix) or nil
    end
  end)
  Define("ATTACHMENTS_MAX_SEND", 12)
  Define("GetSendMailItem", function(i)
    local item = Stubs.world.sendMail[i]
    if item then return Stubs.ITEM_NAMES[item.id], item.id, "icon", item.count, 1, true end
  end)
  Define("GetSendMailItemLink", function(i)
    local item = Stubs.world.sendMail[i]
    return item and Stubs.ItemLink(item.id)
  end)
  Define("SendMail", function(recipient, subject, body)
    table.insert(Stubs.world.sent, { recipient = recipient, subject = subject, body = body })
  end)
  Define("GetInboxNumItems", function() return #Stubs.world.inbox, Stubs.world.inboxTotal or #Stubs.world.inbox end)
  Define("GetInboxItem", function(i, a)
    local mail = Stubs.world.inbox[i]
    local item = mail and mail[a]
    if item then return Stubs.ITEM_NAMES[item[1]], item[1], "icon", item[2], 1, true end
  end)
  Define("GetInboxItemLink", function(i, a)
    local mail = Stubs.world.inbox[i]
    local item = mail and mail[a]
    if item and not item.noLink then return Stubs.ItemLink(item[1]) end
  end)
  Define("GetItemInfo", function(id)
    local name = Stubs.ITEM_NAMES[id]
    if not name or Stubs.world.uncached[id] then return nil end
    local quality = ({ ["9d9d9d"] = 0, ffffff = 1, ["1eff00"] = 2 })[Stubs.ITEM_COLORS[id] or "ffffff"]
    return name, Stubs.ItemLink(id), quality, Stubs.ITEM_LEVELS[id] or 1, 1, "Type", "Subtype", 20, "", "icon",
      Stubs.ITEM_SELL[id] or 0
  end)
  Define("GetItemInfoInstant", function(id)
    return id, "Type", "Subtype", "", "icon", Stubs.ITEM_CLASSES[id] or 7, 0
  end)
  Define("GetMerchantNumItems", function() return #Stubs.world.merchant end)
  Define("GetMerchantItemInfo", function(i)
    local item = Stubs.world.merchant[i]
    if item then
      return Stubs.ITEM_NAMES[item.id], "icon", 100, 1, item.numAvailable, true, true, item.extendedCost or false
    end
  end)
  Define("GetMerchantItemLink", function(i)
    local item = Stubs.world.merchant[i]
    return item and Stubs.ItemLink(item.id)
  end)
end

-- Spell names for GetSpellInfo; other spell ids have none (nil), like a spell the client doesn't know.
Stubs.SPELL_NAMES = { [2575] = "Mining", [2366] = "Herb Gathering", [8613] = "Skinning", [7620] = "Fishing",
                      [13262] = "Disenchant", [921] = "Pick Pocket", [3365] = "Opening", [133] = "Fireball" }
Stubs.GUIDS = { creature = "Creature-0-4372-0-19-299-0000ABCDEF", object = "GameObject-0-4372-0-19-1731-0000ABCDEF",
                item = "Item-4372-0-4000000012345678" }

--- Open a loot window, as the client does before LOOT_OPENED.
---@param slots table[]     { id, count } per slot; { coin = copper } for money
---@param source string|nil "creature" | "object" | "item" (Stubs.GUIDS), or nil for a source GetLootSourceInfo lacks
function Stubs.OpenLoot(slots, source)
  Stubs.world.loot = slots
  Stubs.world.lootSource = source and Stubs.GUIDS[source]
end

--- Install the spell and loot window globals (A55, A85-A91).
local function InstallLoot()
  Define("GetSpellInfo", function(id) return Stubs.SPELL_NAMES[id], nil, "icon" end)
  Define("GetNumLootItems", function() return #Stubs.world.loot end)
  Define("GetLootSlotLink", function(i)
    local slot = Stubs.world.loot[i]
    return slot and slot.id and Stubs.ItemLink(slot.id) or nil
  end)
  Define("GetLootSlotInfo", function(i)
    local slot = Stubs.world.loot[i]
    if slot then return "icon", slot.id and "item" or "coin", slot.count or 0, nil, 1 end
  end)
  Define("GetLootSourceInfo", function(i)
    if Stubs.world.loot[i] and Stubs.world.lootSource then return Stubs.world.lootSource, 1 end
  end)
end

--- Install player, clock, combat, timer and frame globals.
local function InstallCore()
  -- hooksecurefunc([table,] name, fn): run fn after the original, with the same arguments.
  Define("hooksecurefunc", function(a, b, c)
    local tbl, name, fn = a, b, c
    if type(a) == "string" then tbl, name, fn = _G, a, b end
    local original = tbl[name]
    tbl[name] = function(...)
      local results = { original(...) }
      fn(...)
      return unpack(results)
    end
  end)
  Define("UnitName", function(unit) if unit == "player" then return Stubs.world.player.name end end)
  Define("UnitClass", function() local p = Stubs.world.player; return p.className, p.class, 8 end)
  Define("UnitLevel", function() return Stubs.world.player.level end)
  Define("UnitFactionGroup", function() local p = Stubs.world.player; return p.faction, p.faction end)
  Define("GetRealmName", function() return Stubs.world.player.realm end)
  Define("GetMoney", function() return Stubs.world.money end)
  Define("GetServerTime", function() return math.floor(Stubs.world.serverTime) end)
  Define("InCombatLockdown", function() return Stubs.world.inCombat end)
  -- date(): the client's, with the machine's time zone fixed by world.localOffsetS so tests are deterministic.
  Define("date", function(format, seconds)
    seconds = seconds or math.floor(Stubs.world.serverTime)
    if format:sub(1, 1) == "!" then return os.date(format, seconds) end
    return os.date("!" .. format, seconds + Stubs.world.localOffsetS)
  end)
  Define("debugprofilestop", function() return profileMs end)
  Define("C_Timer", { After = function(seconds, fn)
    timerSeq = timerSeq + 1
    timers[#timers + 1] = { due = clock + seconds, seq = timerSeq, fn = fn }
  end })
  local root = UI.New("Frame", nil, nil)
  root.name, root.width, root.height = "UIParent", 1920, 1080
  Define("UIParent", root)
  local minimap = UI.New("Frame", nil, root)          -- 140 px, centred near the top right, as the default UI's
  minimap.name, minimap.width, minimap.height, minimap.centerX, minimap.centerY = "Minimap", 140, 140, 1800, 960
  Define("Minimap", minimap)
  Define("CreateFrame", function(frameType, name, parent)
    local frame = UI.New(frameType or "Frame", nil, parent or root)
    frame.type = frameType
    if name then frame.name = name; Define(name, frame) end
    frames[#frames + 1] = frame
    return frame
  end)
  Define("SlashCmdList", {})
  Define("UISpecialFrames", {})
  Stubs.chat = {}
  Define("DEFAULT_CHAT_FRAME", { AddMessage = function(_, text) Stubs.chat[#Stubs.chat + 1] = text end })
  Stubs.tooltip = { shown = false, lines = {} }
  Stubs.tooltip.doubles, Stubs.tooltip.hooks = {}, {}
  Define("GameTooltip", {
    SetOwner = function(_, owner) Stubs.tooltip.owner, Stubs.tooltip.lines, Stubs.tooltip.doubles = owner, {}, {} end,
    SetText = function(_, text) Stubs.tooltip.lines[1] = text end,
    AddLine = function(_, text) Stubs.tooltip.lines[#Stubs.tooltip.lines + 1] = text end,
    AddDoubleLine = function(_, left, right, _, _, _, r, g, b)
      Stubs.tooltip.doubles[#Stubs.tooltip.doubles + 1] = { left, right, r, g, b }
    end,
    Show = function() Stubs.tooltip.shown = true end,
    Hide = function() Stubs.tooltip.shown = false end,
    GetItem = function() return Stubs.tooltip.itemName, Stubs.tooltip.itemLink end,
    HookScript = function(self, name, fn) Stubs.tooltip.hooks[name] = fn end,
  })
  Define("GetNumSkillLines", function() return #Stubs.world.skillLines end)
  Define("GetSkillLineInfo", function(i)
    local line = Stubs.world.skillLines[i]
    if not line then return nil end
    if line.header then return line.name, true, not line.collapsed, 0, 0, 0, 0 end
    return line.name, false, nil, line.rank, 0, 0, line.max
  end)
end

-- ------------------------------------------------------------------ lifecycle

--- Start a test from scratch: removes the addon and every stub global, then installs fresh ones.
---@param options table|nil  { keepSaved = true } keeps AHProfitDB, as across a logout and login
function Stubs.Reset(options)
  for _, name in ipairs(installed) do _G[name] = nil end
  for name in pairs(_G) do
    if type(name) == "string" and name:match("^SLASH_") then _G[name] = nil end
  end
  installed, frames, timers, timerSeq, clock, profileMs = {}, {}, {}, 0, 0, 0
  _G.AHProfit = nil
  if not (options and options.keepSaved) then _G.AHProfitDB = nil end
  Stubs.world = Stubs.NewWorld()
  UI.world = Stubs.world
  InstallCore()
  InstallRecipeWindows()
  InstallItems()
  InstallLoot()
end

--- The addon's files in .toc order.
---@return string[]  Paths relative to addon/AHProfit/, with forward slashes
function Stubs.TocFiles()
  local files = {}
  for line in io.lines(TOC) do
    local file = line:match("^%s*([^#%s][^\r]*%.lua)%s*$")
    if file then files[#files + 1] = (file:gsub("\\", "/")) end
  end
  return files
end

--- Load addon files the way WoW does: each chunk gets (addonName, privateTable).
---@param files string[]|nil  Paths relative to addon/AHProfit/; default: the .toc list
function Stubs.LoadAddon(files)
  local private = {}
  for _, file in ipairs(files or Stubs.TocFiles()) do
    local chunk, err = loadfile(ADDON_DIR .. file)
    if not chunk then error(err, 2) end
    chunk("AHProfit", private)
  end
end

--- Type a slash command, as the chat box would run it (e.g. "/ahp demo").
---@param line string  The whole command line
function Stubs.Slash(line)
  local command, rest = line:match("^(/%S+)%s*(.-)$")
  for name, value in pairs(_G) do
    if type(name) == "string" and value == command then
      local key = name:match("^SLASH_(.-)%d+$")
      if key and SlashCmdList[key] then return SlashCmdList[key](rest) end
    end
  end
  error("no slash command " .. tostring(command), 2)
end

--- Load the addon and log in: fires ADDON_LOADED and PLAYER_LOGIN.
function Stubs.Login()
  Stubs.LoadAddon()
  Stubs.FireEvent("ADDON_LOADED", "AHProfit")
  Stubs.FireEvent("PLAYER_LOGIN")
end

-- ------------------------------------------------------------------ SavedVariables writer

local ESCAPES = { ['"'] = '\\"', ["\\"] = "\\\\", ["\n"] = "\\n", ["\r"] = "\\r" }

--- Quote a string as WoW does in SavedVariables.
---@param s string  Any string
---@return string   Double-quoted, with \" \\ \n \r and \ddd for other control bytes
local function Quote(s)
  local body = s:gsub('[%c"\\]', function(c) return ESCAPES[c] or string.format("\\%03d", c:byte()) end)
  return '"' .. body .. '"'
end

--- Format a number as WoW does: integers without a decimal point or exponent.
---@param n number  Any finite number
---@return string
local function FormatNumber(n)
  if n == math.floor(n) and n > -2^53 and n < 2^53 then return string.format("%.0f", n) end
  return string.format("%.14g", n)
end

--- Format a scalar value.
---@param v any  string, number or boolean
---@return string
local function Scalar(v)
  if type(v) == "string" then return Quote(v) end
  if type(v) == "number" then return FormatNumber(v) end
  if type(v) == "boolean" then return tostring(v) end
  error("cannot save a " .. type(v))
end

--- Keys of a table that are not part of its 1..n sequence: numbers ascending, then strings.
---@param t table   Table to list
---@param n number  Length of the 1..n sequence
---@return any[]
local function ExplicitKeys(t, n)
  local keys = {}
  for k in pairs(t) do
    if not (type(k) == "number" and k >= 1 and k <= n and k == math.floor(k)) then keys[#keys + 1] = k end
  end
  table.sort(keys, function(a, b)
    if type(a) ~= type(b) then return type(a) == "number" end
    return a < b
  end)
  return keys
end

local WriteValue

--- Write a table's fields, one per line.
---@param out string[]   Output pieces, appended to
---@param t table        The table
---@param depth number   Indent depth of the fields
local function WriteTable(out, t, depth)
  local indent = string.rep("\t", depth)
  out[#out + 1] = "{\n"
  local n = 0
  while t[n + 1] ~= nil do n = n + 1 end
  for _, k in ipairs(ExplicitKeys(t, n)) do
    local key = type(k) == "string" and Quote(k) or FormatNumber(k)
    out[#out + 1] = indent .. "[" .. key .. "] = "
    WriteValue(out, t[k], depth)
    out[#out + 1] = ",\n"
  end
  for i = 1, n do
    out[#out + 1] = indent
    WriteValue(out, t[i], depth)
    out[#out + 1] = ", -- [" .. i .. "]\n"
  end
  out[#out + 1] = string.rep("\t", depth - 1) .. "}"
end

--- Write any value.
---@param out string[]  Output pieces
---@param v any         Value
---@param depth number  Indent depth of the line the value starts on
WriteValue = function(out, v, depth)
  if type(v) == "table" then WriteTable(out, v, depth + 1) else out[#out + 1] = Scalar(v) end
end

--- Write a SavedVariables global exactly as WoW writes it to WTF/.../SavedVariables/<Addon>.lua:
--- a blank line, `NAME = {`, tab-indented `["key"] = value,` lines (numeric keys first),
--- sequence items with `-- [n]` comments, and nil written as `NAME = nil`.
---@param name string  Global name
---@param value any    Its value
---@return string      File contents, "\n" line endings
function Stubs.Serialize(name, value)
  local out = { "\n", name, " = " }
  if value == nil then
    out[#out + 1] = "nil"
  else
    WriteValue(out, value, 0)
  end
  out[#out + 1] = "\n"
  return table.concat(out)
end

--- Read a whole file in binary mode.
---@param path string  File path
---@return string|nil  Contents; nil when the file does not exist
function Stubs.ReadFile(path)
  local file = io.open(path, "rb")
  if not file then return nil end
  local text = file:read("*a")
  file:close()
  return text
end

--- Write a whole file in binary mode.
---@param path string  File path
---@param text string  Contents
function Stubs.WriteFile(path, text)
  local file = assert(io.open(path, "wb"))
  file:write(text)
  file:close()
end

Stubs.Reset()

--- Show an item tooltip, as hovering an item does: runs the hooked OnTooltipSetItem script.
---@param id number  Item id
---@return table     Stubs.tooltip, with the lines the addon added
function Stubs.HoverItem(id)
  Stubs.tooltip.lines, Stubs.tooltip.doubles = {}, {}
  Stubs.tooltip.itemName, Stubs.tooltip.itemLink = Stubs.ITEM_NAMES[id], Stubs.ItemLink(id)
  local hook = Stubs.tooltip.hooks.OnTooltipSetItem
  if hook then hook(GameTooltip) end
  return Stubs.tooltip
end

return Stubs
