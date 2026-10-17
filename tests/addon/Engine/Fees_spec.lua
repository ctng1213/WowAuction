-- Specs for addon/AHProfit/Engine/Fees.lua: the same cases as tests/ahsync/engine/test_fees.py.

local Stubs = require("wow_stubs")

--- Fresh stubs with the Adapter and Fees loaded.
---@return table  AHProfit.Engine.Fees
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "Engine/Fees.lua" })
  return AHProfit.Engine.Fees
end

describe("Fees", function()
  it("deposit is floored basis points of vendor value", function()
    local Fees = Boot()
    assert_eq(Fees.DepositCopper(500, 20, 24), 1500)
    assert_eq(Fees.DepositCopper(500, 20, 8), 500)
    assert_eq(Fees.DepositCopper(7, 3, 2), 0)
  end)

  it("an unknown duration raises", function()
    local Fees = Boot()
    local ok, err = pcall(Fees.DepositCopper, 100, 1, 12)
    assert_eq(ok, false)
    assert_eq(tostring(err):find("12 h", 1, true) ~= nil, true)
  end)

  it("shortest duration covering a window", function()
    local Fees = Boot()
    assert_eq(Fees.ShortestDurationCovering(8), 8)
    assert_eq(Fees.ShortestDurationCovering(11), 24)
    assert_eq(Fees.ShortestDurationCovering(1), 2)
    assert_eq(Fees.ShortestDurationCovering(30), 24)
  end)

  it("sale net takes the cut", function()
    local Fees = Boot()
    assert_eq(Fees.SaleNet(10000, 20), 190000)
  end)
end)
