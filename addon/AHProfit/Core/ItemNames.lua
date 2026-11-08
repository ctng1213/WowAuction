-- Core/ItemNames.lua: one itemNames row, "quality,name,sellPrice,classId" (SPEC §2.2.1; A92). A name may hold
-- commas, so a row is read from both ends: the quality before the first comma, the two numbers after the last two.
-- A row from before A92 is "quality,name" and still reads, with no sell price or class.

local AHProfit = AHProfit

local ItemNames = {}
AHProfit.ItemNames = ItemNames

--- Read one row.
---@param text string|nil  AHProfitDB.itemNames[key]
---@return number|nil      Quality 0-7; nil when the row isn't one
---@return string|nil      Name
---@return number|nil      What a merchant pays for one, copper (0: can't be sold to one); nil when unknown
---@return number|nil      Item class id (2 weapon, 4 armor, ...); nil when unknown
function ItemNames.Parse(text)
  local quality, rest = (text or ""):match("^(%d+),(.+)$")
  if not quality then return nil end
  local name, sell, class = rest:match("^(.+),(%d*),(%d*)$")
  if not name then return tonumber(quality), rest end
  return tonumber(quality), name, tonumber(sell), tonumber(class)
end

--- Write one row; an unknown number is written "" (never 0, which means "can't be sold to a merchant").
---@param quality number    0-7
---@param name string
---@param sell number|nil   Copper a merchant pays; nil when unknown
---@param class number|nil  Item class id; nil when unknown
---@return string
function ItemNames.Format(quality, name, sell, class)
  return quality .. "," .. name .. "," .. (sell and tostring(sell) or "") .. "," .. (class and tostring(class) or "")
end

--- The base item id of an item key, for the client's item cache (SPEC §2.1).
---@param key number  Item key, or -itemId for an unknown random suffix
---@return number
function ItemNames.BaseId(key)
  if key < 0 then return -key end
  return key % 2147483648
end
