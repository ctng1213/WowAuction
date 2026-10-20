-- Engine/Timing.lua: the timing premium, profit later is worth less than profit now (SPEC §3.4D.2).
-- Port of ahsync/engine/timing.py; keep the two formula-for-formula identical (CLAUDE.md). The wait is kept in
-- whole hundredths of a day and the premium in millionths, so both languages get the same doubles (shared
-- scenarios: tests/scenarios/timing.json, PF17-PF19). Not in the .toc yet: the addon reselects from M12 on.

local AHProfit = AHProfit
AHProfit.Engine = AHProfit.Engine or {}

local Timing = {}
AHProfit.Engine.Timing = Timing

local MINUTE_MS = 60000
local MINUTES_PER_DAY = 1440
local MICRO = 1000000

--- The wait from now to the expected sale hour, in hundredths of a day, half up (timing.wait_hundredths).
---@param nowMs number        Now, UTC ms
---@param saleMs number|nil   The expected sale hour, UTC ms; nil for now
---@return number             floor(minutes × 100 ÷ 1440 + 0.5), never below 0
function Timing.WaitHundredths(nowMs, saleMs)
  if saleMs == nil then return 0 end
  local minutes = math.max(0, math.floor((saleMs - nowMs) / MINUTE_MS))
  return math.floor((minutes * 200 + MINUTES_PER_DAY) / (2 * MINUTES_PER_DAY))
end

--- Whether a wait is inside the immediate window: a Today row with no premium (timing.immediate).
---@param hundredths number      The wait, hundredths of a day
---@param immediateHours number  Config immediate_hours (8 or 24)
---@return boolean               hundredths × 24 ≤ hours × 100
function Timing.Immediate(hundredths, immediateHours)
  return hundredths * 24 <= immediateHours * 100
end

--- The timing premium in millionths (timing.premium_micro).
---@param hundredths number      The wait, hundredths of a day
---@param premiumBase number     Config premium_base, a fraction
---@param premiumPerDay number   Config premium_per_day, a fraction per day
---@param immediateHours number  Config immediate_hours
---@return number                0 when immediate, else floor(base × 1e6 + 0.5) + floor(perDay × 1e4 + 0.5) × hundredths
function Timing.PremiumMicro(hundredths, premiumBase, premiumPerDay, immediateHours)
  if Timing.Immediate(hundredths, immediateHours) then return 0 end
  return math.floor(premiumBase * MICRO + 0.5) + math.floor(premiumPerDay * 10000 + 0.5) * hundredths
end

--- Adjusted profit = expected net ÷ (1 + premium) (timing.adjusted).
---@param expectedNet number  Expected net, float copper
---@param premium number      The premium in millionths
---@return number             expectedNet × 1e6 ÷ (1e6 + premium); expectedNet itself when the premium is 0
function Timing.Adjusted(expectedNet, premium)
  if premium == 0 then return expectedNet end
  return expectedNet * MICRO / (MICRO + premium)
end
