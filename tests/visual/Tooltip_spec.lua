-- UI8: the tooltip block's lines fit the 320 px the SPEC allows (SPEC §4.3, §8.4).

local Stubs = require("wow_stubs")
local Layout = require("layout")

describe("Tooltip layout", function()
  it("UI8 a disenchantable item's block: header and six lines in order, none wider than 320 px", function()
    Stubs.Reset()
    Stubs.Login()
    local lines = AHProfit.ViewModel.Tooltip({ cur = 800, curAgeText = "2 h ago", med30 = 1100, deValue = 1425,
      canDisenchant = true, bestWindow = AHProfit.ViewModel.WindowLabel({ start = 167, hours = 3, weeks = 2 }, 0), bestPrice = 1230, chance = 0.46 })
    local labels = {}
    for i, line in ipairs(lines) do
      labels[i] = line.label
      local width = Layout.TextWidth(line.label, 12) + 16 + Layout.TextWidth(line.value, 12)
      assert_eq(width <= 320, true, line.label .. " is " .. width .. " px wide")
    end
    assert_eq(labels, { "Current AH price", "Historical price", "Disenchant value", "Best time to sell",
                        "Price at best time", "Chance to sell" })
  end)
end)
