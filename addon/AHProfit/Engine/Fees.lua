-- Engine/Fees.lua: the AH cut, deposits and auction durations (SPEC §3.4 Fees).
-- Port of ahsync/engine/fees.py; keep the two formula-for-formula identical (CLAUDE.md).
-- Forever's durations are 2, 8 and 24 h (verified 2026-10-18). The deposit rule was checked in game on 2026-11-08
-- (SPEC §6 item 4): 5%, 20% and 60% of the vendor value, floored, at least 1c an auction (1s until 2026-11-11).

local AHProfit = AHProfit
AHProfit.Engine = AHProfit.Engine or {}

local Fees = {}
AHProfit.Engine.Fees = Fees

Fees.AH_CUT = 0.05                          -- faction AH cut on a sale
Fees.DURATIONS_H = { 2, 8, 24 }             -- shortest first
Fees.DEPOSIT_BP = { [2] = 500, [8] = 2000, [24] = 6000 }  -- basis points of vendor sell × qty
Fees.MIN_DEPOSIT_COPPER = 1                 -- the least an auction's deposit can be (1s until 2026-11-11)

--- Deposit for posting an auction, refunded when it sells, lost when it expires or is cancelled (SPEC §3.4 Fees).
---@param vendorSellCopper number  Vendor sell price per unit, copper; 0 when unknown (the minimum then)
---@param qty number               Units in the auction; 0 or fewer is no auction
---@param durationH number         Auction duration in hours (one of DURATIONS_H)
---@return number                  max(MIN_DEPOSIT_COPPER, floor(vendor × qty × rate)) in copper; 0 when qty <= 0
function Fees.DepositCopper(vendorSellCopper, qty, durationH)
  local bp = Fees.DEPOSIT_BP[durationH]
  if not bp then error("unknown auction duration: " .. tostring(durationH) .. " h", 2) end
  if qty <= 0 then return 0 end
  return math.max(Fees.MIN_DEPOSIT_COPPER, math.floor(vendorSellCopper * qty * bp / 10000))
end

--- The shortest auction duration that stays up for a whole sell window (SPEC §3.3.2).
---@param windowHours number  Sell window length in whole hours
---@return number             Duration in hours; the longest when none covers the window
function Fees.ShortestDurationCovering(windowHours)
  for _, duration in ipairs(Fees.DURATIONS_H) do
    if duration >= windowHours then return duration end
  end
  return Fees.DURATIONS_H[#Fees.DURATIONS_H]
end

--- What a sale pays after the AH cut.
---@param priceCopper number  Unit price, copper
---@param qty number          Units sold
---@return number             price × qty × (1 − cut), float copper
function Fees.SaleNet(priceCopper, qty)
  return priceCopper * qty * (1 - Fees.AH_CUT)
end
