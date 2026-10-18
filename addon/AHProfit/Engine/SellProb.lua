-- Engine/SellProb.lua: the live half of the sell probability (SPEC §3.3.2, §3.3.3, §3.1.1).
-- Port of the ported functions of ahsync/engine/sellprob.py; keep them formula-for-formula
-- identical, including the order of floating-point operations (CLAUDE.md, LP1).
-- The queue simulation itself never runs in game: the addon reads AHSync's curves.

local AHProfit = AHProfit
AHProfit.Engine = AHProfit.Engine or {}
local Fees = AHProfit.Engine.Fees

local SellProb = {}
AHProfit.Engine.SellProb = SellProb

SellProb.TIER_LIMITS = { 1.00, 1.05, 1.15 }
SellProb.TIER_FULL_WEIGHT_N = 30
SellProb.TIER_MAX_WEIGHT = 0.5
SellProb.CURVE_AHEAD = { 0, 1, 2, 5, 10, 20, 50, 100 }
SellProb.CURVE_AHEAD_TOOLTIP = { 0, 5, 20 }
SellProb.CURVE_ROWS = 24
SellProb.CURVE_STEPS = 50
SellProb.CURVE_CHAR_BASE = 35
local HOUR_MS = 3600000

--- Price tier of a post relative to the cheapest competitor (SPEC §3.3.2).
---@param price number          Your unit price, copper
---@param curOthers number|nil  Cheapest competing buyout; nil when there is none
---@return number               1 (cheapest) to 4; 1 when there is no competitor
function SellProb.TierOf(price, curOthers)
  if not curOthers or curOthers == 0 then return 1 end
  local ratio = price / curOthers
  for tier, limit in ipairs(SellProb.TIER_LIMITS) do
    if ratio <= limit then return tier end
  end
  return 4
end

--- Blend the simulation with the tier's history (SPEC §3.3.2 step 3).
---@param pQueue number                 Queue simulation result
---@param tierSellThrough number|nil    ST_tier; nil without history
---@param tierN number                  Auctions behind ST_tier
---@return number                       (1 − e) × P_queue + e × ST_tier, e = 0.5 × min(1, n/30)
function SellProb.Blend(pQueue, tierSellThrough, tierN)
  if tierSellThrough == nil then return pQueue end
  local e = SellProb.TIER_MAX_WEIGHT * math.min(1.0, tierN / SellProb.TIER_FULL_WEIGHT_N)
  return (1 - e) * pQueue + e * tierSellThrough
end

--- Blend P with your own sell record (SPEC §3.3.3): P' = (k·P + s) ÷ (k + s + x).
---@param p number        Market-model probability
---@param sold number     Your sold posts in the record window
---@param expired number  Your expired posts
---@param weight number   own_record_weight k
---@return number         P'
function SellProb.WithRecord(p, sold, expired, weight)
  return (weight * p + sold) / (weight + sold + expired)
end

--- Expected net of posting q units at price p (SPEC §3.3.2).
---@param p number         Chance the units sell (P')
---@param qty number       Units posted
---@param price number     Unit price, copper
---@param fallback number  Value per unsold unit, float copper
---@param deposit number   Deposit, copper; refunded on a sale
---@return number          P·q·p·(1 − cut) + (1 − P)·(q·fallback − deposit), float copper
function SellProb.ExpectedPost(p, qty, price, fallback, deposit)
  return p * qty * price * (1 - Fees.AH_CUT) + (1 - p) * (qty * fallback - deposit)
end

--- Which curve row applies now (SPEC §3.1.1).
---@param refreshMs number  Time of the refresh the curve came from, UTC ms
---@param nowMs number      Now, UTC ms
---@return number           (current hour − refresh hour) mod 24, 0-based
function SellProb.CurveRow(refreshMs, nowMs)
  return (math.floor(nowMs / HOUR_MS) - math.floor(refreshMs / HOUR_MS)) % SellProb.CURVE_ROWS
end

--- P for a number of units ahead, interpolating linearly between curve points (SPEC §3.1.1).
---@param row number[]          One curve row, one P per ahead point
---@param ahead number          Units ahead now
---@param aheadPoints number[]  The row's units-ahead points, ascending
---@return number               Interpolated P, clamped outside the points' range
function SellProb.FromCurve(row, ahead, aheadPoints)
  aheadPoints = aheadPoints or SellProb.CURVE_AHEAD
  if ahead <= aheadPoints[1] then return row[1] end
  for i = 2, #aheadPoints do
    if ahead <= aheadPoints[i] then
      local lo, hi = aheadPoints[i - 1], aheadPoints[i]
      return row[i - 1] + (row[i] - row[i - 1]) * ((ahead - lo) / (hi - lo))
    end
  end
  return row[#row]
end

--- P from a curve for now and the units ahead (SPEC §3.1.1).
---@param curve number[][]      24 rows
---@param refreshMs number      Refresh time, UTC ms
---@param nowMs number          Now, UTC ms
---@param ahead number          Units ahead now
---@param aheadPoints number[]  The curve's units-ahead points
---@return number               P_queue for posting now
function SellProb.Lookup(curve, refreshMs, nowMs, ahead, aheadPoints)
  return SellProb.FromCurve(curve[SellProb.CurveRow(refreshMs, nowMs) + 1], ahead, aheadPoints)
end

--- A probability in 2% steps, as exported (SPEC §3.1.1).
---@param p number  Probability in [0, 1]
---@return number   floor(p × 50 + 0.5), 0-50
function SellProb.Quantize(p)
  return math.floor(p * SellProb.CURVE_STEPS + 0.5)
end

--- Decode a curve string from MarketIntelligence.lua into rows of 2%-step probabilities (SPEC §3.1.1).
---@param text string           One character per point, row by row
---@param pointsPerRow number   Units-ahead points per row (8 or 3)
---@return number[][]           Rows of n ÷ 50
function SellProb.DecodeCurve(text, pointsPerRow)
  local rows, row = {}, nil
  for i = 1, #text do
    if (i - 1) % pointsPerRow == 0 then
      row = {}
      rows[#rows + 1] = row
    end
    row[#row + 1] = (text:byte(i) - SellProb.CURVE_CHAR_BASE) / SellProb.CURVE_STEPS
  end
  return rows
end
