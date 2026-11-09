-- Specs for addon/AHProfit/Core/AdapterTooltip.lua: header and cell tooltips, the item tooltip hook, and the AHProfit
-- block's lines with their divider rules (SPEC §4.3 Tooltip; TT24, TT33, TT42).

local Stubs = require("wow_stubs")

--- Fresh stubs with the Adapter and its tooltip half loaded.
---@return table  AHProfit.Adapter
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "Core/AdapterTooltip.lua" })
  return AHProfit.Adapter
end

--- A tooltip as the client gives one: named font strings per line, scripts it can be hooked on and textures it makes,
--- recording what the Adapter does to them.
---@param name string|nil  Its global name; nil for an unnamed (embedded) tooltip
---@param opts table|nil   { forbidden, noCleared (no OnTooltipCleared script), noTextures (no CreateTexture) }
---@return table
local function FakeTooltip(name, opts)
  opts = opts or {}
  local tip = { n = 0, added = {}, textures = {}, hooked = {}, handlers = {} }
  function tip.GetName() return name end
  function tip.NumLines() return tip.n end
  function tip.Show() end
  function tip.IsForbidden() return opts.forbidden == true end
  function tip.HasScript(_, script) return script == "OnHide" or (script == "OnTooltipCleared" and not opts.noCleared) end
  function tip.HookScript(_, script, fn)
    tip.hooked[script] = (tip.hooked[script] or 0) + 1
    tip.handlers[script] = fn
  end
  function tip.AddDoubleLine(_, left, right)
    tip.n = tip.n + 1
    tip.added[#tip.added + 1] = { left, right }
    if name then
      Stubs.Define(name .. "TextLeft" .. tip.n, { side = "left", line = tip.n, SetFontObject = function() end,
                                                  GetText = function() return left end })
      Stubs.Define(name .. "TextRight" .. tip.n, { side = "right", line = tip.n, SetFontObject = function() end })
    end
  end
  if not opts.noTextures then
    function tip.CreateTexture(_, _, layer)
      local t = { layer = layer, shown = false, points = {} }
      function t.SetColorTexture(_, r, g, b, a) t.rgba = { r, g, b, a } end
      function t.ClearAllPoints() t.points = {} end
      function t.SetPoint(_, point, rel) t.points[#t.points + 1] = { point, rel.side, rel.line } end
      function t.SetHeight(_, h) t.height = h end
      function t.Show() t.shown = true end
      function t.Hide() t.shown = false end
      tip.textures[#tip.textures + 1] = t
      return t
    end
  end
  return tip
end

local W = { 1, 1, 1 }

--- Rules shown on a tooltip.
---@param tip table  A FakeTooltip
---@return number
local function Shown(tip)
  local n = 0
  for _, t in ipairs(tip.textures) do if t.shown then n = n + 1 end end
  return n
end

describe("AdapterTooltip", function()
  it("a tooltip's title line can carry a value on its right", function()
    local Adapter = Boot()
    Adapter.ShowTooltipLines(UIParent, "AHProfit", { { text = "hint", rgb = { 1, 1, 1 } } },
                             { text = "data 2 h old", rgb = { 0.5, 0.5, 0.5 } })
    assert_eq({ Stubs.tooltip.doubles[1], Stubs.tooltip.lines }, { { "AHProfit", "data 2 h old", 0.5, 0.5, 0.5 }, { "hint" } })
    Adapter.ShowTooltipLines(UIParent, "Plain", {})
    assert_eq({ Stubs.tooltip.lines[1], #Stubs.tooltip.doubles }, { "Plain", 0 })
  end)

  it("TT24 a tooltip line with an empty label gets a one-space left text, so it keeps its height", function()
    local Adapter = Boot()
    local added = {}
    local tooltip = { AddDoubleLine = function(_, left, right) added[#added + 1] = { left, right } end,
                      Show = function() end }
    local rgb = { 1, 1, 1 }
    Adapter.AddTooltipLines(tooltip, { { "Do: POST", "+23s · 50% chance", rgb, rgb }, { "", "then nothing", rgb, rgb } })
    assert_eq(added, { { "Do: POST", "+23s · 50% chance" }, { " ", "then nothing" } })
  end)

  it("TT33 the item tooltip hook reads the link from the tooltip's data, and a GetItem that raises adds nothing", function()
    local Adapter = Boot()
    local postCalls = {}
    Stubs.Define("Enum", { TooltipDataType = { Item = 0 } })
    Stubs.Define("TooltipDataProcessor", { AddTooltipPostCall = function(kind, fn) postCalls[kind] = fn end })
    local seen = {}
    local function OnItem(tooltip, link) seen[#seen + 1] = { tooltip.name, link } end
    assert_eq(Adapter.HookItemTooltip(OnItem), true)
    local linen = "|cffffffff|Hitem:2589::::::::|h[Linen Cloth]|h|r"
    local calls = 0
    local raises = function() calls = calls + 1; error("attempt to call a nil value") end   -- CompareItem's tooltips
    local compare = { name = "ShoppingTooltip1", GetItem = raises }
    postCalls[0](compare, { type = 0, id = 2589, hyperlink = linen })
    assert_eq(calls, 0, "with a hyperlink in the data, GetItem is never called")
    postCalls[0](compare, { type = 0, id = 2589 })                       -- no hyperlink, and GetItem raises
    assert_eq(calls, 1)
    postCalls[0]({ name = "GameTooltip", GetItem = function() return "Wool Cloth", "|Hitem:2592|h" end }, {})
    postCalls[0]({ name = "EmbeddedItemTooltip" }, { type = 0 })          -- neither a hyperlink nor a GetItem
    postCalls[0](compare, setmetatable({}, { __index = raises }))         -- data the client won't let us read
    assert_eq(seen, { { "ShoppingTooltip1", linen }, { "GameTooltip", "|Hitem:2592|h" } })
    postCalls[0](compare, { hyperlink = linen })                          -- fn itself failing raises nothing
    local broken = function() error("AddDoubleLine on a forbidden tooltip") end
    assert_eq(Adapter.HookItemTooltip(broken), true)
    postCalls[0](compare, { hyperlink = linen })
    TooltipDataProcessor = nil                                            -- Classic's OnTooltipSetItem hook
    seen = {}
    assert_eq(Adapter.HookItemTooltip(OnItem), true)
    Stubs.tooltip.hooks.OnTooltipSetItem({ name = "GameTooltip", GetItem = raises })
    assert_eq(seen, {})
  end)

  it("TT43 an item tooltip says Soulbound in one of its first lines; else, or unreadable, it is not bound", function()
    local Adapter = Boot()
    Stubs.Define("ITEM_SOULBOUND", "Soulbound")
    local tip = FakeTooltip("BoundTip")
    for _, text in ipairs({ "Scouting Gloves", "Soulbound", "Hands" }) do tip:AddDoubleLine(text, "") end
    local boe = FakeTooltip("BoeTip")
    for _, text in ipairs({ "Scouting Gloves", "Binds when equipped", "Soulbound" }) do boe:AddDoubleLine(text, "") end
    boe.n = 2                                                             -- a "Soulbound" past the tooltip's lines
    assert_eq({ Adapter.TooltipSaysSoulbound(tip), Adapter.TooltipSaysSoulbound(boe),
                Adapter.TooltipSaysSoulbound(FakeTooltip(nil)),
                Adapter.TooltipSaysSoulbound(FakeTooltip("Locked", { forbidden = true })) }, { true, false, false, false })
    Stubs.Define("ITEM_SOULBOUND", nil)
    assert_eq(Adapter.TooltipSaysSoulbound(tip), false)                   -- a client without the global
  end)

  it("TT42 a divider is a spacer line with a 1 px rule from its left text to its right text", function()
    local Adapter = Boot()
    local tip = FakeTooltip("FakeTip")
    Adapter.AddTooltipLines(tip, { { "AHProfit", "data 2 h old", W, W }, { " ", " ", W, W, "divider" },
                                   { "Market Price  19c now", "", W, W } })
    assert_eq(tip.added, { { "AHProfit", "data 2 h old" }, { " ", " " }, { "Market Price  19c now", "" } })
    local rule = tip.textures[1]
    assert_eq({ #tip.textures, rule.layer, rule.shown, rule.height, rule.points, rule.rgba },
              { 1, "ARTWORK", true, 1, { { "LEFT", "left", 2 }, { "RIGHT", "right", 2 } }, { 0.33, 0.39, 0.54, 1 } })
  end)

  it("TT42 rules are pooled per tooltip, hidden when it is cleared or hidden, and its hooks go in once", function()
    local Adapter = Boot()
    local tip = FakeTooltip("FakeTip")
    local block = { { "AHProfit", "x", W, W }, { " ", " ", W, W, "divider" }, { "a", "", W, W },
                    { " ", " ", W, W, "divider" }, { "b", "", W, W } }
    Adapter.AddTooltipLines(tip, block)
    assert_eq({ #tip.textures, Shown(tip) }, { 2, 2 })
    tip.handlers.OnTooltipCleared(tip)
    assert_eq(Shown(tip), 0)                                              -- never left on another item's tooltip
    tip.n = 0
    Adapter.AddTooltipLines(tip, block)                                   -- the next item: the same two textures
    assert_eq({ #tip.textures, Shown(tip), tip.textures[2].points[1] }, { 2, 2, { "LEFT", "left", 4 } })
    tip.handlers.OnHide(tip)
    assert_eq(Shown(tip), 0)
    Adapter.AddTooltipLines(tip, { { " ", " ", W, W, "divider" } })        -- a refresh without a clear
    assert_eq({ Shown(tip), tip.hooked }, { 1, { OnTooltipCleared = 1, OnHide = 1 } })
    local old = FakeTooltip("OldTip", { noCleared = true })               -- a client without OnTooltipCleared
    Adapter.AddTooltipLines(old, { { " ", " ", W, W, "divider" } })
    assert_eq(old.hooked, { OnHide = 1 })
  end)

  it("TT42 a rule that fails while it is coloured leaves no gap in the pool, so the next clear still hides every rule", function()
    local Adapter = Boot()
    local divider = { " ", " ", W, W, "divider" }
    local tip = FakeTooltip("GapTip")
    local create, first = tip.CreateTexture, true
    tip.CreateTexture = function(self, sub, layer)
      local texture = create(self, sub, layer)
      if first then texture.SetColorTexture, first = function() error("SetColorTexture") end, false end
      return texture
    end
    Adapter.AddTooltipLines(tip, { divider, divider, divider })   -- the 1st fails; the 2nd reuses its texture
    assert_eq({ #tip.textures, Shown(tip), #tip.added }, { 2, 2, 3 })
    tip.handlers.OnHide(tip)
    assert_eq(Shown(tip), 0)
  end)

  it("TT42 a forbidden, unnamed or textureless tooltip keeps the spacer without a rule, and a failure adds the rest", function()
    local Adapter = Boot()
    local divider = { " ", " ", W, W, "divider" }
    for _, tip in ipairs({ FakeTooltip("Forbidden", { forbidden = true }), FakeTooltip(nil),
                           FakeTooltip("Plain", { noTextures = true }) }) do
      Adapter.AddTooltipLines(tip, { divider, { "after", "", W, W } })
      assert_eq({ tip.added, #tip.textures }, { { { " ", " " }, { "after", "" } }, 0 })
    end
    local broken = FakeTooltip("Broken")
    broken.CreateTexture = function() error("CreateTexture on this tooltip") end
    Adapter.AddTooltipLines(broken, { divider, { "after", "", W, W } })
    assert_eq(broken.added, { { " ", " " }, { "after", "" } })
  end)
end)
