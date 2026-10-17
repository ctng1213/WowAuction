-- Specs for addon/AHProfit/Engine/SellProb.lua: the shared scenarios (tests/scenarios/sellprob.json)
-- and the Python-generated property cases (tests/properties/sellprob.json, LP1), with identical numbers.

local Stubs = require("wow_stubs")

--- Fresh stubs with the engine loaded.
---@return table  AHProfit.Engine.SellProb
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "Engine/Fees.lua", "Engine/SellProb.lua" })
  return AHProfit.Engine.SellProb
end

-- Scenario "call" name -> function and its argument order (as in the Python signatures).
local CALLS = {
  expected_post = { "ExpectedPost", { "p", "qty", "price", "fallback", "deposit" } },
  blend = { "Blend", { "p_queue", "tier_sell_through", "tier_n" } },
  with_record = { "WithRecord", { "p", "sold", "expired", "weight" } },
  from_curve = { "FromCurve", { "row", "ahead", "ahead_points" } },
  lookup = { "Lookup", { "curve", "refresh_ms", "now_ms", "ahead", "ahead_points" } },
  curve_row = { "CurveRow", { "refresh_ms", "now_ms" } },
  quantize = { "Quantize", { "p" } },
  tier_of = { "TierOf", { "price", "cur_others" } },
}

--- Call a ported function with a scenario's named arguments.
---@param SellProb table  The module
---@param call string     Python function name
---@param args table      Named arguments (JSON_NULL for None)
---@return any            The result
local function Run(SellProb, call, args)
  local spec = CALLS[call]
  local values = {}
  for i, name in ipairs(spec[2]) do
    local v = args[name]
    if v == JSON_NULL then v = nil end
    values[i] = v
  end
  return SellProb[spec[1]](unpack(values, 1, #spec[2]))
end

--- Fail unless two numbers agree to 1e-9 (scenario values are written by hand).
---@param actual number    Computed
---@param expected number  Expected
---@param why string       The scenario's explanation
local function AssertNear(actual, expected, why)
  if math.abs(actual - expected) > 1e-9 then
    error(tostring(actual) .. " ~= " .. tostring(expected) .. "\nwhy: " .. why, 2)
  end
end

describe("SellProb shared scenarios", function()
  for _, s in ipairs(dofile("tests/addon/generated/sellprob.lua")) do
    it(s.id .. " " .. s.name, function()
      AssertNear(Run(Boot(), s.call, s.args), s.expect, s.why)
    end)
  end
end)

describe("SellProb LP1: identical to Python on generated cases", function()
  it("LP1 Python and Lua give identical results for sellprob", function()
    local SellProb = Boot()
    local data = dofile("tests/addon/generated/properties/sellprob.lua")
    for i, case in ipairs(data.cases) do
      local actual = Run(SellProb, case.call, case.args)
      if actual ~= case.result then
        error(string.format("seed %d case %d (%s): Lua %.17g ~= Python %.17g", data.seed, i, case.call,
          actual, case.result))
      end
    end
  end)
end)

describe("SellProb", function()
  it("decodes a curve string into rows of 2% steps", function()
    local SellProb = Boot()
    local text = string.char(35 + 50, 35 + 43, 35 + 0, 35 + 25)
    assert_eq(SellProb.DecodeCurve(text, 2), { { 1, 0.86 }, { 0, 0.5 } })
  end)

  it("tiers and curve clamping", function()
    local SellProb = Boot()
    assert_eq(SellProb.TierOf(100, nil), 1)
    assert_eq(SellProb.TierOf(120, 100), 4)
    assert_eq(SellProb.FromCurve({ 0.9, 0.5, 0.1 }, 50, { 0, 5, 20 }), 0.1)
  end)
end)
