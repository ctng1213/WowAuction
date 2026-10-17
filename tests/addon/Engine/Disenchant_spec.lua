-- Specs for addon/AHProfit/Engine/Disenchant.lua: shared scenarios and LP1 cases, identical to Python.

local Stubs = require("wow_stubs")

--- Fresh stubs with the DE data and engine loaded.
---@return table  AHProfit.Engine.Disenchant
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "Data/Disenchant.lua", "Engine/Disenchant.lua" })
  return AHProfit.Engine.Disenchant
end

--- Replace JSON_NULL with nil.
---@param v any  A generated value
---@return any   nil for JSON_NULL, else v
local function Value(v)
  if v == JSON_NULL then return nil end
  return v
end

--- Run one ported call with named arguments, as the Python function would.
---@param De table     The module
---@param call string  Python function name
---@param args table   Named arguments
---@return any         The result, shaped like Python's JSON (outcomes_for -> { skill, outcomes } or nil)
local function Run(De, call, args)
  if call == "expected_value" then return De.ExpectedValue(args.outcomes, args.sell_values) end
  if call == "can_disenchant" then return De.CanDisenchant(args.skill_required, Value(args.best_skill)) end
  local skill, outcomes = De.OutcomesFor(De.Table(), args.quality, args.ilvl, args.kind)
  if skill == nil then return nil end
  return { skill, outcomes }
end

describe("Disenchant shared scenarios", function()
  for _, s in ipairs(dofile("tests/addon/generated/disenchant.lua")) do
    it(s.id .. " " .. s.name, function()
      local actual = Run(Boot(), s.call, s.args)
      if type(actual) == "number" then
        if math.abs(actual - s.expect) > 1e-9 then error(actual .. " ~= " .. s.expect .. "\nwhy: " .. s.why) end
      else
        assert_eq(actual, s.expect, s.why)
      end
    end)
  end
end)

describe("Disenchant LP1: identical to Python on generated cases", function()
  it("LP1 Python and Lua give identical results for disenchant", function()
    local De = Boot()
    local data = dofile("tests/addon/generated/properties/disenchant.lua")
    for i, case in ipairs(data.cases) do
      local ok, err = pcall(assert_eq, Run(De, case.call, case.args), Value(case.result))
      if not ok then error(string.format("seed %d case %d (%s): %s", data.seed, i, case.call, tostring(err))) end
    end
  end)
end)

describe("Disenchant", function()
  it("decodes the generated table once, in Python's row order", function()
    local De = Boot()
    local rows = De.Table()
    assert_eq(#rows, 38)
    assert_eq(rows[1][1], 2)
    assert_eq(rows[1][2], "armor")
    assert_eq(rows[1][6][1], { 10940, 0.8, 1, 2 })
    assert_eq(De.Table() == rows, true)
  end)
end)
