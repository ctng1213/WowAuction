-- Self-test of the layout snapshot harness (tests/support/lua/layout.lua, SPEC §8.4 layer 2):
-- anchors resolve to the right rectangles, truncation adds "…", and each automatic check fires.

local Stubs = require("wow_stubs")
local Layout = require("layout")

--- A 200 × 100 panel at (10, 20) from UIParent's top left.
---@return table
local function Panel()
  Stubs.Reset()
  local panel = CreateFrame("Frame", "TestPanel", UIParent)
  panel:SetSize(200, 100)
  panel:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 10, -20)
  return panel
end

describe("Layout", function()
  it("resolves one-point, two-point and centred anchors", function()
    local panel = Panel()
    local fill = CreateFrame("Frame", nil, panel)
    fill:SetPoint("TOPLEFT", 4, -4)
    fill:SetPoint("BOTTOMRIGHT", -4, 4)
    local button = CreateFrame("Button", nil, panel)
    button:SetSize(60, 22)
    button:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -5, 5)
    local label = panel:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    label:SetPoint("CENTER")
    label:SetText("Hi")                                  -- 2 × 12 × 0.55 = 13.2 px wide
    local rects = Layout.Resolve(UIParent)
    local function Tenth(v) return math.floor(v * 10 + 0.5) / 10 end
    local function R(r) local x = rects[r] return { Tenth(x.x), Tenth(x.y), Tenth(x.w), Tenth(x.h) } end
    assert_eq(R(panel), { 10, 20, 200, 100 })
    assert_eq(R(fill), { 14, 24, 192, 92 })
    assert_eq(R(button), { 145, 93, 60, 22 })
    assert_eq(R(label), { 103.4, 64, 13.2, 12 })
    assert_eq(Layout.Check(panel, rects), {})
  end)

  it("cuts fixed-width text with an ellipsis and strips colour codes and links", function()
    local panel = Panel()
    local name = panel:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    name:SetPoint("TOPLEFT")
    name:SetWidth(40)                                    -- 6.6 px per glyph: "[Scou" + "…" fit in 40 px
    name:SetWordWrap(false)
    name:SetText("|cnIQ2:|Hitem:6585|h[Scouting Gloves]|h|r")
    assert_eq(Layout.ShownText(name), "[Scou…")       -- links show in brackets
    assert_eq(Layout.Visible("|cff1eff00Green|r and |TInterface\\Icon:0|t"), "Green and   ")
    local rects = Layout.Resolve(UIParent)
    assert_eq(Layout.Snapshot(panel, rects), 'Frame TestPanel 10,20 200x100\n  FontString - 10,20 40x12 "[Scou…"\n')
  end)

  it("flags regions outside their parent, overlapping rows, short buttons and missing anchors", function()
    local panel = Panel()
    local outside = CreateFrame("Frame", "Outside", panel)
    outside:SetSize(50, 50)
    outside:SetPoint("TOPLEFT", 180, 0)
    for i = 1, 2 do
      local row = CreateFrame("Frame", "TestRow" .. i, panel)
      row:SetSize(100, 20)
      row:SetPoint("TOPLEFT", 0, -10 * i)              -- 20 px tall rows 10 px apart overlap
    end
    local button = CreateFrame("Button", "Short", panel)
    button:SetSize(40, 16)
    button:SetPoint("BOTTOMLEFT")
    CreateFrame("Frame", "Loose", panel)
    local problems = Layout.Check(panel, Layout.Resolve(UIParent))
    assert_eq(problems, { "Frame Outside lies outside its parent", "Button Short is under 20 px tall",
                          "Frame Loose has no anchor", "Frame TestPanel: rows 1 and 2 overlap" })
  end)

  it("hidden frames are listed but not walked or checked; hidden text is left out", function()
    local panel = Panel()
    local hidden = CreateFrame("Frame", "Hidden", panel)
    hidden:Hide()
    CreateFrame("Frame", "Child", hidden)
    local text = panel:CreateFontString()
    text:SetText("gone")
    text:Hide()
    local rects = Layout.Resolve(UIParent)
    assert_eq(Layout.Check(panel, rects), {})
    assert_eq(Layout.Snapshot(panel, rects), "Frame TestPanel 10,20 200x100\n  Frame Hidden 0,0 0x0 (hidden)\n")
  end)

  it("compares a snapshot with its golden file", function()
    local panel = Panel()
    local label = panel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    label:SetPoint("TOPLEFT", 8, -8)
    label:SetText("Golden")
    assert_eq(Layout.Golden("layout_selftest", panel), 'Frame TestPanel 10,20 200x100\n  FontString - 18,28 33x10 "Golden"\n')
    if os.getenv("AHP_UPDATE_VISUAL") == "1" then return end      -- updating would accept the change below
    label:SetText("Changed")
    local ok, err = pcall(Layout.Golden, "layout_selftest", panel)
    assert_eq(ok, false)
    assert_eq(err:find("differs at line 2", 1, true) ~= nil, true)
  end)
end)
