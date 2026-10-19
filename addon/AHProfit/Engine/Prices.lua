-- Engine/Prices.lua: the post price (SPEC §3.4), a window's expected price and a live flip's value. Ports of
-- post_price in ahsync/engine/prices.py, slot_price in ahsync/engine/plan.py and flip_now_offer in
-- ahsync/engine/flips.py; keep them identical (CLAUDE.md, PP1, PP2, TT6, TT7, FN1).

local AHProfit = AHProfit
AHProfit.Engine = AHProfit.Engine or {}

local Prices = {}
AHProfit.Engine.Prices = Prices

--- Post price p = min(curOthers − 1, max(med7, p90)) (SPEC §3.4, PP1, PP2).
---@param curOthers number|nil  Lowest competing buyout, copper
---@param med7 number|nil       7-day median, copper
---@param p90 number|nil        30-day 90th percentile of hourly closes, copper
---@return number|nil           With a competitor, the undercut capped at the larger of med7 and p90 (uncapped
---                             when both are nil); without one, med7, else p90; nil when nothing is known
function Prices.PostPrice(curOthers, med7, p90)
  local cap = med7
  if p90 and (not cap or p90 > cap) then cap = p90 end
  if not curOthers then return med7 or p90 end
  local undercut = curOthers - 1
  if cap then return math.min(undercut, cap) end
  return undercut
end

--- A flip seen in a live book: buy the cheapest level, relist now under the next (SPEC §4.0, FN1).
---@param p number          Chance the units sell posted now at relist with nothing ahead
---@param units number      Units bought: the cheapest level's, at most one reference stack
---@param cheapest number   The cheapest level's unit price, copper
---@param relist number     The relist unit price, Prices.PostPrice(second level, med7, p90), copper
---@param fallback number   Value per unsold unit, float copper
---@param deposit number    Deposit for the posting-now window, copper
---@param margin number     Config min_flip_margin
---@return number|nil       E_post(p, units, relist, fallback, deposit) when expected − cost ≥ margin × cost; else nil
function Prices.FlipNow(p, units, cheapest, relist, fallback, deposit, margin)
  local expected = AHProfit.Engine.SellProb.ExpectedPost(p, units, relist, fallback, deposit)
  local cost = cheapest * units
  if expected - cost >= margin * cost then return expected end
  return nil
end

Prices.TREND_CLAMP_PCT = 5         -- SPEC §3.4C.1: trend7 is clamped to ±5% per day
-- Config max_trend_total (SPEC §3.6, TT7). The addon has no config channel (min_flip_margin is hard-coded the
-- same way in UI/FlipView.lua), so this mirrors AHSync's default; change both together.
Prices.MAX_TREND_TOTAL = 0.15

--- Expected price at a window's best hour: level × how × trend factor (SPEC §3.4C.1, TT6, TT7).
--- The factor is 1 + trend7 ÷ 100 × days ahead, trend7 clamped to ±5%/day and the factor to 1 ± maxTotal.
---@param level number          Price level (med7, else med30), copper
---@param how number            The best hour's price ratio
---@param trend7 number         Trend, % per day; clamped to ±5
---@param daysAhead number      Days until that hour
---@param maxTotal number|nil   Config max_trend_total, a fraction; nil for Prices.MAX_TREND_TOTAL
---@return number               Copper, half up
function Prices.SlotPrice(level, how, trend7, daysAhead, maxTotal)
  maxTotal = maxTotal or Prices.MAX_TREND_TOTAL
  local trend = math.max(-Prices.TREND_CLAMP_PCT, math.min(Prices.TREND_CLAMP_PCT, trend7))
  local factor = math.max(1 - maxTotal, math.min(1 + maxTotal, 1 + trend / 100 * daysAhead))
  return math.floor(level * how * factor + 0.5)
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
