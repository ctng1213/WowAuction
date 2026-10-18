-- Specs for addon/AHProfit/Engine/Prices.lua: the shared post-price scenarios (tests/scenarios/prices.json).

local Stubs = require("wow_stubs")

--- A JSON null from the generated fixtures as nil.
---@param v any
---@return any
local function Value(v)
  if v == JSON_NULL then return nil end
  return v
end

describe("Prices shared scenarios", function()
  for _, s in ipairs(dofile("tests/addon/generated/prices.lua")) do
    it(s.id .. " " .. s.name, function()
      Stubs.Reset()
      Stubs.LoadAddon({ "Core/Adapter.lua", "Engine/Prices.lua" })
      assert_eq(AHProfit.Engine.Prices.PostPrice(Value(s.args.cur_others), Value(s.args.med7)), Value(s.expect), s.why)
    end)
  end
end)
