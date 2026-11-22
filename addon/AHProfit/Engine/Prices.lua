-- Engine/Prices.lua: the post price (SPEC §3.4) and a window's expected price from the price level (SPEC §3.3).
-- Ports of clamp_bounds, forecast, post_price and relist_price in ahsync/engine/prices.py and slot_price in
-- ahsync/engine/plan.py; keep them identical (CLAUDE.md, PP1, PP5, R28, TT6, TT7, TT51).

local AHProfit = AHProfit
AHProfit.Engine = AHProfit.Engine or {}

local Prices = {}
AHProfit.Engine.Prices = Prices

Prices.MIN_HOURLY_CLOSES = 24        -- prices.MIN_HOURLY_CLOSES: fewer hourly closes in 30 days is thin data

--- A forecast kept within the level's bounds (SPEC §3.3 Bounds, §3.3.4, TT51; prices.clamp_bounds).
---@param price number       Copper
---@param low number|nil     The low bound, copper; nil for none
---@param high number|nil    The high bound, copper; nil for none (never below low when both are known)
---@return number            The price raised to low, then lowered to high, as far as they are known
function Prices.ClampBounds(price, low, high)
  if low ~= nil then price = math.max(price, low) end
  if high ~= nil then price = math.min(price, high) end
  return price
end

--- The expected price of an hour: level × its pattern × a trend factor, within the bounds (SPEC §3.3.4, TT51, PP5;
--- prices.forecast).
---@param level number       The price level (SPEC §3.3), copper
---@param how number         The hour's price ratio
---@param factor number      The trend factor (1 for the current hour)
---@param low number|nil     The level's low bound, copper
---@param high number|nil    The level's high bound, copper
---@return number            floor(level × how × factor + 0.5), then ClampBounds
function Prices.Forecast(level, how, factor, low, high)
  return Prices.ClampBounds(math.floor(level * how * factor + 0.5), low, high)
end

--- Post price p = min(curOthers − 1, ceiling), the ceiling being this hour's forecast (SPEC §3.4, PP1, PP5;
--- prices.post_price). An ask level's high bound is itself, so asks never earn a premium (SPEC §3.3).
---@param curOthers number|nil  Lowest competing buyout, copper
---@param level number|nil      The price level, copper
---@param howNow number         The current hour's price ratio (1 without a pattern)
---@param low number|nil        The level's low bound, copper
---@param high number|nil       The level's high bound, copper
---@return number|nil           With a competitor, the undercut capped at the ceiling (uncapped without a level);
---                             without one, the ceiling; nil when there is neither
function Prices.PostPrice(curOthers, level, howNow, low, high)
  local ceiling = level ~= nil and Prices.Forecast(level, howNow, 1, low, high) or nil
  if curOthers == nil then return ceiling end
  local undercut = curOthers - 1
  if ceiling ~= nil then return math.min(undercut, ceiling) end
  return undercut
end

--- The price to relist bought units now, only when grounded (SPEC §3.4C.2, R28; prices.relist_price).
--- With no listing left, the ceiling alone grounds nothing on thin data.
---@param curOthers number|nil  Lowest competing buyout left after the buy, copper
---@param level number|nil      The price level, copper
---@param howNow number         The current hour's price ratio
---@param low number|nil        The level's low bound, copper
---@param high number|nil       The level's high bound, copper
---@param closes number|nil     Hourly closes in 30 days; nil when unknown (treated as enough)
---@return number|nil           Prices.PostPrice(...), or nil when nothing is listed and closes < 24
function Prices.RelistPrice(curOthers, level, howNow, low, high, closes)
  if curOthers == nil and closes ~= nil and closes < Prices.MIN_HOURLY_CLOSES then return nil end
  return Prices.PostPrice(curOthers, level, howNow, low, high)
end

Prices.TREND_CLAMP_PCT = 5         -- SPEC §3.4C.1: trend7 is clamped to ±5% per day
-- Config max_trend_total (SPEC §3.6, TT7). The addon has no config channel (min_flip_margin is hard-coded the
-- same way in UI/FlipView.lua), so this mirrors AHSync's default; change both together.
Prices.MAX_TREND_TOTAL = 0.15

--- Expected price at a window's best hour: level × how × trend factor, within the bounds (SPEC §3.4C.1, TT6, TT7,
--- TT51; plan.slot_price). The factor is 1 + trend7 ÷ 100 × days ahead, trend7 clamped to ±5%/day and the factor
--- to 1 ± maxTotal.
---@param level number          The price level (SPEC §3.3), copper
---@param how number            The best hour's price ratio
---@param trend7 number         Trend, % per day; clamped to ±5
---@param daysAhead number      Days until that hour
---@param maxTotal number|nil   Config max_trend_total, a fraction; nil for Prices.MAX_TREND_TOTAL
---@param low number|nil        The level's low bound, copper; nil for none
---@param high number|nil       The level's high bound, copper; nil for none
---@return number               Copper, half up, then raised to low and lowered to high where known
function Prices.SlotPrice(level, how, trend7, daysAhead, maxTotal, low, high)
  maxTotal = maxTotal or Prices.MAX_TREND_TOTAL
  local trend = math.max(-Prices.TREND_CLAMP_PCT, math.min(Prices.TREND_CLAMP_PCT, trend7))
  local factor = math.max(1 - maxTotal, math.min(1 + maxTotal, 1 + trend / 100 * daysAhead))
  return Prices.Forecast(level, how, factor, low, high)
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
