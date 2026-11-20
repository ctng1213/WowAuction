-- Core/AdapterCooldowns.lua: the Adapter's cooldown half (SPEC §3.4F.3, §6 item 7), split off Core/Adapter.lua
-- for size. Every call here is UNVERIFIED on Forever: the diagnostics' apis list says which exist, and the
-- traces record their raw returns so tools/savedvars_report.py shows what the client gives.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter

-- Readings under this many seconds are the global cooldown (about 1.5 s) or noise: every catalog cooldown
-- is at least 600 s.
Adapter.MIN_COOLDOWN_S = 2
-- GetTime() wraps after 2^32 ms of system uptime; a cooldown that started before a reboot reads start > now.
-- UNVERIFIED: that subtracting 2^32 ms puts such a start back on the GetTime clock.
local GETTIME_WRAP_S = 4294967.296

--- Seconds of a cooldown left from GetTime()-based start and duration (both seconds).
---@param start number     When it started, GetTime() seconds
---@param duration number  Its length, seconds
---@return number          Seconds left, 0 when it has passed
function Adapter.SecondsLeft(start, duration)
  local now = GetTime()
  if start > now then start = start - GETTIME_WRAP_S end
  local left = start + duration - now
  return left > 0 and left or 0
end

--- A recipe's cooldown in the open trade skill window (SPEC §3.4F.3 source 1).
-- UNVERIFIED: C_TradeSkillUI.GetRecipeCooldown(id) returns cooldown (seconds left, nil off cooldown),
-- isDayCooldown, charges, maxCharges, as on retail; only the first is read.
---@param spell number   The recipe's spell id
---@return boolean|nil   Whether the character knows it; nil when the window has no such recipe
---@return number|nil    Seconds left; nil when the client gave no number (off cooldown, or no API)
function Adapter.RecipeCooldown(spell)
  local T = C_TradeSkillUI
  if type(T) ~= "table" or type(T.GetRecipeInfo) ~= "function" then return nil, nil end
  local info = T.GetRecipeInfo(spell)
  if type(info) ~= "table" then return nil, nil end
  local left = type(T.GetRecipeCooldown) == "function" and T.GetRecipeCooldown(spell) or nil
  return info.learned == true, type(left) == "number" and left or nil
end

--- An item's cooldown, for a tool such as the Salt Shaker (SPEC §3.4F.3 source 1).
-- UNVERIFIED: C_Container.GetItemCooldown(itemId) returns start, duration, enable on the GetTime() clock,
-- enable a boolean (retail) or 1/0 (legacy); enable false/0 means the cooldown hasn't started yet.
---@param itemId number  The tool's item id
---@return number|nil    Seconds left (0 = ready); nil when the client can't say (no API, or pending)
function Adapter.ItemCooldownLeft(itemId)
  local C = C_Container
  if type(C) ~= "table" or type(C.GetItemCooldown) ~= "function" or type(GetTime) ~= "function" then return nil end
  local start, duration, enable = C.GetItemCooldown(itemId)
  if type(start) ~= "number" or type(duration) ~= "number" or enable == false or enable == 0 then return nil end
  if duration < Adapter.MIN_COOLDOWN_S then return 0 end          -- none, or only the global cooldown
  return Adapter.SecondsLeft(start, duration)
end
