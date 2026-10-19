-- Engine/Prices.lua: the post price (SPEC §3.4) and a window's expected price. Ports of post_price in
-- ahsync/engine/prices.py and slot_price in ahsync/engine/plan.py; keep them identical (CLAUDE.md, PP1, TT6).

local AHProfit = AHProfit
AHProfit.Engine = AHProfit.Engine or {}

local Prices = {}
AHProfit.Engine.Prices = Prices

--- Post price p = min(curOthers − 1, med7) (SPEC §3.4).
---@param curOthers number|nil  Lowest competing buyout, copper
---@param med7 number|nil       7-day median, copper
---@return number|nil           Unit price; the available one when the other is nil; nil when both are
function Prices.PostPrice(curOthers, med7)
  local undercut = curOthers and curOthers - 1
  if undercut and med7 then return math.min(undercut, med7) end
  return undercut or med7
end

Prices.TREND_CLAMP_PCT = 5         -- SPEC §3.4C.1: trend7 is clamped to ±5% per day

--- Expected price at a window's best hour: level × how × (1 + trend7 ÷ 100 × days ahead) (SPEC §3.4C.1, TT6).
---@param level number      Price level (med7, else med30), copper
---@param how number        The best hour's price ratio
---@param trend7 number     Trend, % per day; clamped to ±5
---@param daysAhead number  Days until that hour
---@return number           Copper, half up
function Prices.SlotPrice(level, how, trend7, daysAhead)
  local trend = math.max(-Prices.TREND_CLAMP_PCT, math.min(Prices.TREND_CLAMP_PCT, trend7))
  return math.floor(level * how * (1 + trend / 100 * daysAhead) + 0.5)
end

--- Days from now until the next start of an hour of the week (UTC, 0 = Mon 00:00), as capacity.slot_of counts.
---@param slot number   0-167
---@param nowMs number
---@return number       Days, at least 0
function Prices.DaysUntilSlot(slot, nowMs)
  local hour = math.floor(nowMs / 3600000)
  local current = (math.floor(hour / 24) + 3) % 7 * 24 + hour % 24
  local hours = (slot - current) % 168
  return math.max(0, (hours * 3600000 - nowMs % 3600000) / 86400000)
end
