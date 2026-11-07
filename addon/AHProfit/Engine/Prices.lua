-- Engine/Prices.lua: the post price (SPEC §3.4) and a window's expected price. Ports of post_price and relist_price
-- in ahsync/engine/prices.py and slot_price in ahsync/engine/plan.py; keep them identical (CLAUDE.md, PP1, PP2, PP4,
-- R28, TT6, TT7).

local AHProfit = AHProfit
AHProfit.Engine = AHProfit.Engine or {}

local Prices = {}
AHProfit.Engine.Prices = Prices

Prices.MIN_HOURLY_CLOSES = 24        -- prices.MIN_HOURLY_CLOSES: fewer hourly closes in 30 days is thin data

--- Post price p = min(curOthers − 1, max(med7, p90)) (SPEC §3.4, PP1, PP2, PP4).
---@param curOthers number|nil  Lowest competing buyout, copper
---@param med7 number|nil       7-day median, copper
---@param p90 number|nil        30-day 90th percentile of hourly closes, copper
---@param closes number|nil     Hourly closes in 30 days; under 24 (with med7 known) the cap is med7 alone (PP4)
---@return number|nil           With a competitor, the undercut capped at the larger of med7 and p90 (uncapped
---                             when both are nil); without one, med7, else p90; nil when nothing is known
function Prices.PostPrice(curOthers, med7, p90, closes)
  if closes ~= nil and closes < Prices.MIN_HOURLY_CLOSES and med7 ~= nil then p90 = nil end   -- thin data
  local cap = med7
  if p90 and (not cap or p90 > cap) then cap = p90 end
  if not curOthers then return med7 or p90 end
  local undercut = curOthers - 1
  if cap then return math.min(undercut, cap) end
  return undercut
end

--- The price to relist bought units now, only when grounded (SPEC §3.4C.2, R28; prices.relist_price).
--- With no listing left, the level alone grounds nothing on thin data.
---@param curOthers number|nil  Lowest competing buyout left after the buy, copper
---@param med7 number|nil       7-day median, copper
---@param p90 number|nil        30-day 90th percentile of hourly closes, copper
---@param closes number|nil     Hourly closes in 30 days; nil when unknown (treated as enough)
---@return number|nil           Prices.PostPrice(...), or nil when nothing is listed and closes < 24
function Prices.RelistPrice(curOthers, med7, p90, closes)
  if curOthers == nil and closes ~= nil and closes < Prices.MIN_HOURLY_CLOSES then return nil end
  return Prices.PostPrice(curOthers, med7, p90, closes)
end

Prices.TREND_CLAMP_PCT = 5         -- SPEC §3.4C.1: trend7 is clamped to ±5% per day
-- Config max_trend_total (SPEC §3.6, TT7). The addon has no config channel (min_flip_margin is hard-coded the
-- same way in UI/FlipView.lua), so this mirrors AHSync's default; change both together.
Prices.MAX_TREND_TOTAL = 0.15

--- Expected price at a window's best hour: level × how × trend factor, clamped (SPEC §3.4C.1, TT6, TT7, TT22, TT23).
--- The factor is 1 + trend7 ÷ 100 × days ahead, trend7 clamped to ±5%/day and the factor to 1 ± maxTotal.
---@param level number          Price level (med7, else med30), copper
---@param how number            The best hour's price ratio
---@param trend7 number         Trend, % per day; clamped to ±5
---@param daysAhead number      Days until that hour
---@param maxTotal number|nil   Config max_trend_total, a fraction; nil for Prices.MAX_TREND_TOTAL
---@param p10 number|nil        30-day 10th percentile, copper: the floor (TT22); nil for none
---@param med7 number|nil       7-day median, copper; with p90 the ceiling is the larger known one
---@param p90 number|nil        30-day 90th percentile, copper
---@param closes number|nil     Hourly closes in 30 days; under 24 the ceiling is the level (TT23); nil when unknown
---@return number               Copper, half up, then raised to p10 and lowered to max(med7, p90) where known
function Prices.SlotPrice(level, how, trend7, daysAhead, maxTotal, p10, med7, p90, closes)
  maxTotal = maxTotal or Prices.MAX_TREND_TOTAL
  local trend = math.max(-Prices.TREND_CLAMP_PCT, math.min(Prices.TREND_CLAMP_PCT, trend7))
  local factor = math.max(1 - maxTotal, math.min(1 + maxTotal, 1 + trend / 100 * daysAhead))
  local price = math.floor(level * how * factor + 0.5)
  if p10 ~= nil then price = math.max(price, p10) end
  local ceiling = med7
  if p90 ~= nil and (ceiling == nil or p90 > ceiling) then ceiling = p90 end
  if closes ~= nil and closes < Prices.MIN_HOURLY_CLOSES then ceiling = level end   -- thin data: no premium
  if ceiling ~= nil then price = math.min(price, ceiling) end
  return price
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
