-- Core/AdapterDiagnostics.lua: the Adapter's diagnostics half (SPEC §2.2.1 diagnostics, §6), split off
-- Core/Adapter.lua for size. Like Adapter.lua it touches WoW globals; it adds to the same AHProfit.Adapter table.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter

--- Call a global function when it exists.
---@param name string  Global name
---@return any         Its results; nothing when it is missing
local function CallIfExists(name, ...)
  local fn = _G[name]
  if type(fn) == "function" then return fn(...) end
end

--- Pack results with their count, keeping nils.
---@return table  { n = count, ... }
local function Pack(...)
  return { n = select("#", ...), ... }
end

-- For tools/savedvars_report.py (SPEC §2.2.1 diagnostics, §6). These never raise:
-- a failure becomes part of the returned text, because they exist to explain failures.

--- The client's version and build.
---@return string  e.g. "1.60.1 70009"; "unknown" when GetBuildInfo is missing
function Adapter.ClientBuild()
  local ok, version, build = pcall(CallIfExists, "GetBuildInfo")
  if not ok or not version then return "unknown" end
  return tostring(version) .. " " .. tostring(build)
end

--- Look up a global by a dotted name, such as "C_Container.GetContainerItemInfo".
---@param name string  Global name, optionally with one or more ".field" parts
---@return any         The value; nil when any part is missing
local function LookupGlobal(name)
  local value = _G
  for part in name:gmatch("[^.]+") do
    if type(value) ~= "table" then return nil end
    value = value[part]
  end
  return value
end

--- Which of the given APIs exist in this client.
---@param names string[]  Global names, dotted for namespaced ones
---@return string         "name=type;name=type" in the given order (type is "function", "table", "nil", ...)
function Adapter.ProbeApis(names)
  local parts = {}
  for i = 1, #names do parts[i] = names[i] .. "=" .. type(LookupGlobal(names[i])) end
  return table.concat(parts, ";")
end

--- Make one diagnostic field safe for the compact text format.
---@param value any  A value returned by a WoW API
---@return string    tostring(value) with "," ";" "|" replaced by spaces
local function Field(value)
  return (tostring(value):gsub("[,;|]", " "))
end

--- Run a describer, turning an error into text.
---@param fn function  Returns a string
---@return string      Its result, or "error: <message>"
local function Describe(fn)
  local ok, result = pcall(fn)
  if ok then return result end
  return "error: " .. Field(result)
end

--- The raw Skills-tab lines, to see why professions were or weren't captured.
---@return string  "name|isHeader|isExpanded|rank|max;..." in game order, or "error: ..."
function Adapter.DescribeSkillLines()
  return Describe(function()
    local rows = {}
    for i = 1, GetNumSkillLines() do
      local name, isHeader, isExpanded, rank, _, _, maxRank = GetSkillLineInfo(i)
      rows[i] = table.concat({ Field(name), Field(isHeader), Field(isExpanded), Field(rank), Field(maxRank) }, "|")
    end
    return table.concat(rows, ";")
  end)
end

--- The open merchant's items as the API returns them, to check the vendor-reagent rule.
---@return string  "itemKey|numAvailable|extendedCost;..." in merchant order, or "error: ..."
function Adapter.DescribeMerchant()
  return Describe(function()
    local rows = {}
    for i = 1, GetMerchantNumItems() do
      local numAvailable, extendedCost = Adapter.MerchantItem(i)
      local key = Adapter.ItemKey(GetMerchantItemLink(i))
      rows[i] = table.concat({ Field(key), Field(numAvailable), Field(extendedCost) }, "|")
    end
    return table.concat(rows, ";")
  end)
end

--- The function names inside API namespaces, to write readers against what the client has.
---@param names string[]  Namespace globals, e.g. { "C_TradeSkillUI" }
---@return string         "C_X=FnA FnB;C_Y=nil" with function names sorted
function Adapter.DescribeNamespaces(names)
  local parts = {}
  for i = 1, #names do
    local namespace = LookupGlobal(names[i])
    local functions = {}
    if type(namespace) == "table" then
      for key, value in pairs(namespace) do
        if type(key) == "string" and type(value) == "function" then functions[#functions + 1] = key end
      end
      table.sort(functions)
    end
    parts[i] = names[i] .. "=" .. (type(namespace) == "table" and table.concat(functions, " ") or "nil")
  end
  return table.concat(parts, ";")
end

--- The raw professions as the modern API returns them.
---@return string  "slot|index|name|rank|max;..." for each GetProfessions slot, or "error: ..."
function Adapter.DescribeProfessions()
  return Describe(function()
    local rows = {}
    local indices = Pack(GetProfessions())
    for slot = 1, indices.n do
      local index = indices[slot]
      local name, rank, maxRank
      if index then
        local _
        name, _, rank, maxRank = GetProfessionInfo(index)
      end
      rows[slot] = table.concat({ slot, Field(index), Field(name), Field(rank), Field(maxRank) }, "|")
    end
    return table.concat(rows, ";")
  end)
end

--- The open modern trade skill window as the API reports it, to check the recipe reader.
---@return string  "name=..|parent=..|level=..|max=..|ready=..|linked=..|ids=..|learned=..|first=id:out:min:max:slots"
function Adapter.DescribeTradeSkill()
  return Describe(function()
    local T = C_TradeSkillUI
    local base = T.GetBaseProfessionInfo() or {}
    local ids = T.GetAllRecipeIDs() or {}
    local learned = 0
    for _, id in ipairs(ids) do
      local info = T.GetRecipeInfo(id)
      if info and info.learned then learned = learned + 1 end
    end
    local first = "none"
    if ids[1] then
      local sc = T.GetRecipeSchematic(ids[1], false) or {}
      first = table.concat({ ids[1], Field(sc.outputItemID), Field(sc.quantityMin), Field(sc.quantityMax),
                             #(sc.reagentSlotSchematics or {}) }, ":")
    end
    return table.concat({
      "name=" .. Field(base.professionName), "parent=" .. Field(base.parentProfessionName),
      "level=" .. Field(base.skillLevel), "max=" .. Field(base.maxSkillLevel),
      "ready=" .. Field(T.IsTradeSkillReady and T.IsTradeSkillReady()),
      "linked=" .. Field(T.IsTradeSkillLinked and T.IsTradeSkillLinked()),
      "ids=" .. #ids, "learned=" .. learned, "first=" .. first,
    }, "|")
  end)
end
