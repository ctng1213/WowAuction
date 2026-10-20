-- Specs for addon/AHProfit/Engine/Timing.lua: the shared timing scenarios (tests/scenarios/timing.json: PF17-PF19).

local Stubs = require("wow_stubs")

--- A JSON null from the generated fixtures as nil.
---@param v any
---@return any
local function Value(v)
  if v == JSON_NULL then return nil end
  return v
end

--- Boot the engine module the scenarios use.
---@return table  AHProfit.Engine.Timing
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "Engine/Timing.lua" })
  return AHProfit.Engine.Timing
end

--- Run one scenario's call.
---@param Timing table
---@param s table  The scenario
---@return any
local function Run(Timing, s)
  local a = s.args
  if s.call == "wait_hundredths" then return Timing.WaitHundredths(a.now_ms, Value(a.sale_ms)) end
  if s.call == "immediate" then return Timing.Immediate(a.hundredths, a.immediate_hours) end
  if s.call == "premium_micro" then
    return Timing.PremiumMicro(a.hundredths, a.premium_base, a.premium_per_day, a.immediate_hours)
  end
  return Timing.Adjusted(a.expected_net, a.premium)
end

describe("Timing shared scenarios", function()
  for _, s in ipairs(dofile("tests/addon/generated/timing.lua")) do
    it(s.id .. " " .. s.name, function()
      assert_eq(Run(Boot(), s), s.expect, s.why)
    end)
  end
end)
