-- Specs for addon/AHProfit/Engine/Prices.lua: the shared post-price and live-flip scenarios
-- (tests/scenarios/prices.json: PP1, PP2, PP4, R28, FN1, TT7).

local Stubs = require("wow_stubs")

--- A JSON null from the generated fixtures as nil.
---@param v any
---@return any
local function Value(v)
  if v == JSON_NULL then return nil end
  return v
end

--- Boot the engine modules the scenarios use.
---@return table  AHProfit.Engine.Prices
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "Engine/Fees.lua", "Engine/SellProb.lua", "Engine/Prices.lua" })
  return AHProfit.Engine.Prices
end

--- Run one scenario's call.
---@param Prices table
---@param s table  The scenario
---@return any
local function Run(Prices, s)
  local a = s.args
  if s.call == "relist_price" then
    return Prices.RelistPrice(Value(a.cur_others), Value(a.med7), Value(a.p90), Value(a.closes))
  end
  if s.call == "post_price" then
    return Prices.PostPrice(Value(a.cur_others), Value(a.med7), Value(a.p90), Value(a.closes))
  end
  if s.call == "slot_price" then
    return Prices.SlotPrice(a.level, a.how_best, a.trend7, a.days_ahead, a.max_trend_total, Value(a.p10),
      Value(a.med7), Value(a.p90), Value(a.closes))
  end
  error("unknown shared call " .. tostring(s.call))
end

describe("Prices shared scenarios", function()
  for _, s in ipairs(dofile("tests/addon/generated/prices.lua")) do
    it(s.id .. " " .. s.name, function()
      local actual, expected = Run(Boot(), s), Value(s.expect)
      if type(expected) == "number" and type(actual) == "number" then
        assert_eq(math.abs(actual - expected) < 1e-9, true, s.why)
      else
        assert_eq(actual, expected, s.why)
      end
    end)
  end
end)
