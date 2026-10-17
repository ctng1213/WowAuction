-- Specs for addon/AHProfit/Engine/Craft.lua: LP1 cases identical to Python, plus scenario numbers.

local Stubs = require("wow_stubs")

--- Fresh stubs with the engine loaded.
---@return table  AHProfit.Engine.Craft
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "Engine/Craft.lua" })
  return AHProfit.Engine.Craft
end

--- JSON_NULL as nil.
---@param v any  A generated value
---@return any   nil for JSON_NULL, else v
local function Value(v)
  if v == JSON_NULL then return nil end
  return v
end

describe("Craft LP1: identical to Python on generated cases", function()
  it("LP1 Python and Lua give identical results for craft", function()
    local Craft = Boot()
    local data = dofile("tests/addon/generated/properties/craft.lua")
    for i, case in ipairs(data.cases) do
      local a = case.args
      local actual
      if case.call == "walk_book" then
        actual = Craft.WalkBook(a.book, a.qty)
      else
        actual = Craft.Fill(a.need, a.owned, a.hold_value, Value(a.vendor_price), a.book, Value(a.crafted_cost),
          a.crafted_gold)
      end
      local ok, err = pcall(assert_eq, actual, Value(case.result))
      if not ok then error(string.format("seed %d case %d (%s): %s", data.seed, i, case.call, tostring(err))) end
    end
  end)
end)

describe("Craft", function()
  it("C5 book walk: two bars at 10 s, then two at 15 s", function()
    local Craft = Boot()
    local book = { { 2500, 2 }, { 1000, 2 }, { 1500, 2 } }
    assert_eq(Craft.WalkBook(book, 2), 2000)
    assert_eq(Craft.WalkBook(book, 4), 5000)
    assert_eq(Craft.WalkBook(book, 7), nil)
  end)

  it("C3 fill: owned bars first, then the AH", function()
    local Craft = Boot()
    local f = Craft.Fill(6, 4, 950, nil, { { 1000, 2 } }, nil, 0)
    assert_eq(f.used, { owned = 4, vendor = 0, ah = 2, crafted = 0 })
    assert_eq(f.gold, 2000)
    assert_eq(f.cost, 4 * 950 + 2000)
  end)
end)
