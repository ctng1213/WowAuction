-- Specs for addon/AHProfit/Core/AdapterDiagnostics.lua: what the diagnostics report (SPEC §2.2.1 diagnostics).

local Stubs = require("wow_stubs")

--- Fresh stubs with only the Adapter and its diagnostics loaded.
---@return table  AHProfit.Adapter
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "Core/AdapterDiagnostics.lua" })
  return AHProfit.Adapter
end

describe("AdapterDiagnostics", function()
  it("lists the functions inside API namespaces, sorted, and nil for a missing namespace", function()
    local Adapter = Boot()
    Stubs.Define("C_Fake", { Zeta = function() end, Alpha = function() end, value = 3 })
    assert_eq(Adapter.DescribeNamespaces({ "C_Fake", "C_Missing" }), "C_Fake=Alpha Zeta;C_Missing=nil")
  end)

  it("describes the raw GetProfessions slots, keeping empty ones", function()
    local Adapter = Boot()
    Stubs.Define("GetProfessions", function() return 3, nil end)
    Stubs.Define("GetProfessionInfo", function() return "Herbalism", "icon", 15, 75 end)
    assert_eq(Adapter.DescribeProfessions(), "1|3|Herbalism|15|75;2|nil|nil|nil|nil")
  end)
end)
