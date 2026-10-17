--- WoW client stubs for the addon's Lua tests (SPEC §8.1: no real WoW in tests).
--- Usage: local Stubs = require("wow_stubs"); Stubs.Reset(); <shape Stubs.world>; Stubs.Login().
--- The world table is the fake game state; the installed globals read it on
--- every call, so a test can change it at any time. Events are fired with
--- Stubs.FireEvent, timers run with Stubs.Advance, and setting an OnUpdate
--- script raises (SPEC §5.1). Stubs.Serialize writes a table the way WoW
--- writes a SavedVariables file.

local Stubs = {}

local ADDON_DIR = "addon/AHProfit/"
local TOC = ADDON_DIR .. "AHProfit.toc"

-- Item names for links; cosmetic, only so links look real.
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

--- A fresh fake game state: a level 60 Horde mage with nothing, out of combat.
---@return table  The world; see the field comments
function Stubs.NewWorld()
  return {
    player = { name = "Amaya", realm = "Grommash", faction = "Horde",
               className = "Mage", class = "MAGE", level = 60 },
    money = 0,                -- copper
    serverTime = 1760000000,  -- seconds; GetServerTime floors it
    inCombat = false,
    skillLines = {},          -- { name, header = bool, collapsed = bool, rank, max }
    containers = {},          -- [bagId] = { size = n, slots = { [slot] = { id, count, bound, suffix, noLink } } }
    inbox = {},               -- one array of { id, count, noLink = bool } attachments per mail
    inboxTotal = nil,         -- mails on the server; default #inbox (all loaded)
    merchant = {},            -- { id, numAvailable (-1 = unlimited), extendedCost = bool }
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
  return "|cffffffff|Hitem:" .. table.concat(fields, ":") .. "|h[" .. name .. "]|h|r"
end

--- A recipe (enchant) link as the trade skill and craft windows print it.
---@param spell number  Spell id
---@param name string   Recipe name
---@return string       "|cffffd000|Henchant:<spell>|h[Name]|h|r"
function Stubs.EnchantLink(spell, name)
  return "|cffffd000|Henchant:" .. spell .. "|h[" .. name .. "]|h|r"
end

-- ------------------------------------------------------------------ frames, events, timers

local FrameMethods = {}

--- Frame:RegisterEvent.
---@param event string  WoW event name
function FrameMethods:RegisterEvent(event)
  -- Like the real client: an event it doesn't know raises (Forever lacks some Classic events).
  if Stubs.world.unknownEvents[event] then
    error('Frame:RegisterEvent(): Attempt to register unknown event "' .. event .. '"', 2)
  end
  self.events[event] = true
end

--- Frame:UnregisterEvent.
---@param event string  WoW event name
function FrameMethods:UnregisterEvent(event) self.events[event] = nil end

--- Frame:IsEventRegistered.
---@param event string  WoW event name
---@return boolean      true when registered
function FrameMethods:IsEventRegistered(event) return self.events[event] == true end

--- Frame:SetScript; OnUpdate is forbidden (SPEC §5.1: no per-frame polling).
---@param name string       Script name, e.g. "OnEvent"
---@param fn function|nil   Handler
function FrameMethods:SetScript(name, fn)
  if name == "OnUpdate" and fn ~= nil then
    error("OnUpdate scripts are forbidden (SPEC §5.1); use C_Timer.After", 2)
  end
  self.scripts[name] = fn
end

--- Frame:GetScript.
---@param name string  Script name
---@return function|nil
function FrameMethods:GetScript(name) return self.scripts[name] end

--- Frame:Show / Hide / IsShown.
function FrameMethods:Show() self.shown = true end
--- Hide the frame.
function FrameMethods:Hide() self.shown = false end
--- Whether the frame is shown.
---@return boolean
function FrameMethods:IsShown() return self.shown end

local FrameMeta = { __index = FrameMethods }

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
               isBound = item.bound or false, itemID = item.id }
    end,
  })
  Define("GetContainerNumSlots", numSlots)
  Define("GetContainerItemInfo", function(bag, slot)
    local item = SlotItem(bag, slot)
    if item then
      return "icon", item.count, false, 1, false, false, not item.noLink and Stubs.ItemLink(item.id, item.suffix) or nil
    end
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

--- Install player, clock, combat, timer and frame globals.
local function InstallCore()
  Define("UnitName", function(unit) if unit == "player" then return Stubs.world.player.name end end)
  Define("UnitClass", function() local p = Stubs.world.player; return p.className, p.class, 8 end)
  Define("UnitLevel", function() return Stubs.world.player.level end)
  Define("UnitFactionGroup", function() local p = Stubs.world.player; return p.faction, p.faction end)
  Define("GetRealmName", function() return Stubs.world.player.realm end)
  Define("GetMoney", function() return Stubs.world.money end)
  Define("GetServerTime", function() return math.floor(Stubs.world.serverTime) end)
  Define("InCombatLockdown", function() return Stubs.world.inCombat end)
  Define("C_Timer", { After = function(seconds, fn)
    timerSeq = timerSeq + 1
    timers[#timers + 1] = { due = clock + seconds, seq = timerSeq, fn = fn }
  end })
  Define("CreateFrame", function(frameType)
    local frame = setmetatable({ type = frameType, events = {}, scripts = {}, shown = true }, FrameMeta)
    frames[#frames + 1] = frame
    return frame
  end)
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
  installed, frames, timers, timerSeq, clock = {}, {}, {}, 0, 0
  _G.AHProfit = nil
  if not (options and options.keepSaved) then _G.AHProfitDB = nil end
  Stubs.world = Stubs.NewWorld()
  InstallCore()
  InstallRecipeWindows()
  InstallItems()
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
return Stubs
