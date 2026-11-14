-- UI17: the tooltip's 14 variants fit the 320 px the SPEC allows (SPEC §4.3, §8.4; mockup §3b), from TooltipView's lines. The
-- tooltip is the client's GameTooltip, not a frame tree of ours, so its snapshot is the lines with their widths: a
-- two-column line as "label | value", a joined or indented line as drawn, a divider as "---" (TT42).

local Stubs = require("wow_stubs")
local Layout = require("layout")
local TW = require("tooltip_world")

describe("Tooltip layout", function()
  it("UI17 the 14 variants: every line within 320 px, long Do values on a value-only line, days in words, amber when stale", function()
    Stubs.Reset()
    Stubs.Login()
    local TV, VM = AHProfit.TooltipView, AHProfit.ViewModel
    local out, continued = {}, {}
    for n, v in ipairs(TW.Variants(TV, VM)) do
      out[#out + 1] = "-- " .. v[1]
      local lines = TV.Lines(v[2])
      for i, l in ipairs(lines) do
        local width = TV.LineWidth(l)
        assert_eq(width <= TV.WIDTH, true, v[1] .. ": " .. l.label .. " | " .. l.value .. " is " .. width .. " px")
        local text = l.divider and "---" or (l.joined or l.indent) and TV.JoinedText(l) or (l.label .. " | " .. l.value)
        out[#out + 1] = string.format("%3d %s%s", math.floor(width + 0.5), text, l.amber and " (amber)" or "")
        if l.label:match("^Best %a+ Price$") then                          -- a day and time of day in words, or now (TT28)
          assert_eq(l.value:match(" · %a+day %a+") ~= nil or l.value:match(" · now") ~= nil, true, l.value)
        end
        if i == 4 and lines[3].isDo and l.label == "" and not l.divider then continued[#continued + 1] = n end   -- lines[2]: a divider
      end
    end
    assert_eq(continued, { 8, 9 }, "a long Do value continues on a line with an empty label")   -- the rest have line 2 (TT45)
    Layout.GoldenText("UI17_tooltip", table.concat(out, "\n") .. "\n", 2)
  end)
end)
