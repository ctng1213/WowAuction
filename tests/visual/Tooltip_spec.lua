-- UI17: the tooltip's 14 variants fit the 320 px the SPEC allows (SPEC §4.3, §8.4), from TooltipView's lines. The
-- tooltip is the client's GameTooltip, not a frame tree of ours, so its snapshot is the lines with their widths.

local Stubs = require("wow_stubs")
local Layout = require("layout")
local TW = require("tooltip_world")

describe("Tooltip layout", function()
  it("UI17 the 14 variants: every line within 320 px, long Do values on a value-only line, hour ranges, amber when stale", function()
    Stubs.Reset()
    Stubs.Login()
    local TV, VM = AHProfit.TooltipView, AHProfit.ViewModel
    local out, continued = {}, {}
    for n, v in ipairs(TW.Variants(TV, VM)) do
      out[#out + 1] = "-- " .. v[1]
      local lines = TV.Lines(v[2])
      for i, l in ipairs(lines) do
        local width = TV.Width(VM.WithCoins(l.label), VM.WithCoins(l.value))
        assert_eq(width <= TV.WIDTH, true, v[1] .. ": " .. l.label .. " | " .. l.value .. " is " .. width .. " px")
        out[#out + 1] = string.format("%3d %s | %s%s", math.floor(width + 0.5), l.label, l.value, l.amber and " (amber)" or "")
        if l.label:match("^Best time") and not l.value:match("^now ·") then
          assert_eq(l.value:match("^%a%a%a %d%d–%d%d") ~= nil or l.value:match("^now %(%a%a%a %d%d–%d%d%)") ~= nil, true, l.value)
        end
        if i == 3 and lines[2].isDo and l.label == "" then continued[#continued + 1] = n end
      end
    end
    assert_eq(continued, { 1, 2, 4, 8, 9, 12, 14 }, "a long Do value continues on a line with an empty label")
    Layout.GoldenText("UI17_tooltip", table.concat(out, "\n") .. "\n", 2)
  end)
end)
