-- Engine/Fees.lua: the AH cut, deposits and auction durations (SPEC §3.4 Fees).
-- Port of ahsync/engine/fees.py; keep the two formula-for-formula identical (CLAUDE.md).
-- UNVERIFIED for Forever (SPEC §6 item 4): rates and durations are Classic Era seeds.

local AHProfit = AHProfit
AHProfit.Engine = AHProfit.Engine or {}

local Fees = {}
AHProfit.Engine.Fees = Fees

Fees.AH_CUT = 0.05                          -- faction AH cut on a sale
Fees.DURATIONS_H = { 2, 8, 24 }             -- shortest first
Fees.DEPOSIT_BP = { [2] = 125, [8] = 500, [24] = 1500 }   -- basis points of vendor sell × qty

--- Deposit for posting an auction, refunded when it sells (SPEC §3.3.2).
---@param vendorSellCopper number  Vendor sell price per unit, copper
---@param qty number               Units in the auction
---@param durationH number         Auction duration in hours (one of DURATIONS_H)
---@return number                  floor(vendor × qty × rate) in copper
function Fees.DepositCopper(vendorSellCopper, qty, durationH)
  local bp = Fees.DEPOSIT_BP[durationH]
  if not bp then error("unknown auction duration: " .. tostring(durationH) .. " h", 2) end
  return math.floor(vendorSellCopper * qty * bp / 10000)
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
