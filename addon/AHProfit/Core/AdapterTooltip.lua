-- Core/AdapterTooltip.lua: the Adapter's tooltip half (SPEC §4.3 Tooltip; TT24, TT33, TT42), split off
-- Core/Adapter.lua for size: header and cell tooltips, the item tooltip hook, and the AHProfit block's lines with
-- their divider rules. Like Adapter.lua it touches WoW globals; it adds to the same AHProfit.Adapter table.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter

--- The item link a tooltip shows: its tooltip data's hyperlink, else the tooltip's GetItem (TT33).
--- On the live Forever client GetItem raises inside the client for some tooltips (the comparison tooltips of
--- CompareItem, loot); the hook calls this under pcall, so a failure reads as no link.
-- UNVERIFIED: that Forever's item tooltip data carries `hyperlink`, as retail's does.
---@param tooltip table     The tooltip being shown
---@param data table|nil    TooltipDataProcessor's data for it; nil from OnTooltipSetItem
---@return string|nil       The item link; nil when none can be read
local function TooltipItemLink(tooltip, data)
  if type(data) == "table" and type(data.hyperlink) == "string" then return data.hyperlink end
  if type(tooltip.GetItem) ~= "function" then return nil end
  local _, link = tooltip:GetItem()
  return type(link) == "string" and link or nil
end

--- Call a function whenever the game shows an item tooltip.
--- UNVERIFIED: which hook Forever has. Modern clients have TooltipDataProcessor; Classic has
--- GameTooltip's OnTooltipSetItem script. Whichever exists is used.
---@param fn function  Called with (tooltip, item link)
---@return boolean     true when a hook was installed
function Adapter.HookItemTooltip(fn)
  --- The hook itself: fn with the tooltip's item link, when it has one. Reading the link and fn both run under
  --- pcall, so nothing the client's tooltips do can raise a Lua error from here (TT33).
  ---@param tooltip table   The tooltip being shown
  ---@param data table|nil  Its tooltip data; nil from OnTooltipSetItem
  local function OnItem(tooltip, data)
    if tooltip.IsForbidden and tooltip:IsForbidden() then return end        -- nothing can be added to it
    local ok, link = pcall(TooltipItemLink, tooltip, data)
    if ok and link then pcall(fn, tooltip, link) end
  end
  if TooltipDataProcessor and TooltipDataProcessor.AddTooltipPostCall and Enum and Enum.TooltipDataType then
    TooltipDataProcessor.AddTooltipPostCall(Enum.TooltipDataType.Item, OnItem)
    return true
  end
  if GameTooltip and GameTooltip.HookScript then
    GameTooltip:HookScript("OnTooltipSetItem", function(tooltip) OnItem(tooltip, nil) end)
    return true
  end
  return false
end

-- ------------------------------------------------------------------ the AHProfit block's divider rules (TT42)

local DIVIDER_RGB = { 0.33, 0.39, 0.54 }
local FLAT = "Interface\\Buttons\\WHITE8X8"
local SIDES = { "TextLeft", "TextRight" }
-- Tooltip -> { textures, used }: its pooled divider rules. A tooltip in here already has its clear and hide hooks.
-- (Weak keys only by habit: the textures point back at their tooltip, and the client never drops a frame.)
local dividerPools = setmetatable({}, { __mode = "k" })

--- Hide every divider rule a tooltip shows and free them for reuse (its next item, a refresh, a clear).
---@param tooltip table  A tooltip
local function HideDividers(tooltip)
  local pool = dividerPools[tooltip]
  if not pool then return end
  for _, texture in ipairs(pool.textures) do texture:Hide() end
  pool.used = 0
end

--- A tooltip's divider pool, made the first time with hooks that hide its rules whenever the tooltip is cleared or
--- hidden, so a rule never shows on another item's tooltip.
--- UNVERIFIED: that Forever's tooltips have the OnTooltipCleared script, as Classic's do; OnHide is standard. A
--- script a tooltip lacks is skipped (HookScript raises on one), and AddTooltipLines also hides every rule first.
---@param tooltip table  A tooltip
---@return table         { textures = { texture }, used = rules in use }
local function DividerPool(tooltip)
  local pool = dividerPools[tooltip]
  if pool then return pool end
  pool = { textures = {}, used = 0 }
  dividerPools[tooltip] = pool
  for _, script in ipairs({ "OnTooltipCleared", "OnHide" }) do
    if tooltip.HasScript and tooltip.HookScript and tooltip:HasScript(script) then
      tooltip:HookScript(script, HideDividers)
    end
  end
  return pool
end

--- A 1 px rule across a tooltip's spacer line n, from its left text to its right text (TT42).
--- UNVERIFIED: that a texture on the tooltip's ARTWORK layer draws above Forever's tooltip backdrop (try OVERLAY if
--- it doesn't show), and that TextRight<n> is right-aligned to the tooltip's inner edge, as on Classic.
--- SetColorTexture is seen working on Forever (UI/MainWindow.lua); without it, a flat texture tinted.
---@param tooltip table  The tooltip
---@param name string    Its global name
---@param n number       The spacer's line number
---@return boolean       true when a rule was placed; false without the line's font strings or CreateTexture
local function PlaceDivider(tooltip, name, n)
  local leftText, rightText = _G[name .. "TextLeft" .. n], _G[name .. "TextRight" .. n]
  if not (leftText and rightText and tooltip.CreateTexture) then return false end
  local pool = DividerPool(tooltip)
  local texture = pool.textures[pool.used + 1]
  if not texture then
    texture = tooltip:CreateTexture(nil, "ARTWORK")
    pool.textures[pool.used + 1] = texture            -- pooled at once: a failure below leaves no gap in the list
    texture:Hide()
    local r, g, b = DIVIDER_RGB[1], DIVIDER_RGB[2], DIVIDER_RGB[3]
    if texture.SetColorTexture then
      texture:SetColorTexture(r, g, b, 1)
    else
      texture:SetTexture(FLAT)
      texture:SetVertexColor(r, g, b, 1)
    end
  end
  pool.used = pool.used + 1
  texture:ClearAllPoints()
  texture:SetPoint("LEFT", leftText, "LEFT", 0, 0)
  texture:SetPoint("RIGHT", rightText, "RIGHT", 0, 0)
  texture:SetHeight(1)
  texture:Show()
  return true
end

--- A tooltip's global name when rules may be drawn on it: named and not forbidden.
---@param tooltip table
---@return string|nil
local function RuleTarget(tooltip)
  if tooltip.IsForbidden and tooltip:IsForbidden() then return nil end
  return tooltip.GetName and tooltip:GetName() or nil
end

--- Switch a tooltip line's two font strings to the small tooltip font.
--- UNVERIFIED: GameTooltip's TextLeft<n>/TextRight<n> font strings and GameTooltipTextSmall exist on Forever, as on
--- Classic; without them the lines keep the tooltip's own font.
---@param name string|nil  The tooltip's global name
---@param n number|nil     The line number
local function SmallFont(name, n)
  if not (name and n and GameTooltipTextSmall) then return end
  for _, side in ipairs(SIDES) do
    local text = _G[name .. side .. n]
    if text and text.SetFontObject then text:SetFontObject(GameTooltipTextSmall) end
  end
end

--- Add the AHProfit block's lines to a tooltip (SPEC §4.3), in the small tooltip font so a line fits 320 px: two
--- columns each, a joined line all in its left text, and a divider as a spacer line with a rule across it (TT42).
--- An empty label (a value-only line) is added as one space: on the live Forever client a line whose left text is ""
--- has no height, so its value drew over the line above and the next line overlapped it (TT24).
--- A divider whose rule can't be drawn (an unnamed or forbidden tooltip, a missing font string or texture call, an
--- error) stays a plain spacer line.
---@param tooltip table  The tooltip shown
---@param lines table[]  { left, right, leftRgb = { r, g, b }, rightRgb, kind } each, text already coloured; kind
---                      "divider" for a divider
function Adapter.AddTooltipLines(tooltip, lines)
  local name = RuleTarget(tooltip)
  HideDividers(tooltip)                    -- a refresh of the same tooltip may come without a clear
  for _, line in ipairs(lines) do
    local l, r = line[3], line[4]
    local left = line[1] == "" and " " or line[1]
    tooltip:AddDoubleLine(left, line[2], l[1], l[2], l[3], r[1], r[2], r[3])
    local n = tooltip.NumLines and tooltip:NumLines()
    SmallFont(tooltip.GetName and tooltip:GetName() or nil, n)
    if line[5] == "divider" and name and n then pcall(PlaceDivider, tooltip, name, n) end
  end
  tooltip:Show()
end

-- ------------------------------------------------------------------ header and cell tooltips

--- Show a header or cell tooltip next to a frame (GameTooltip), without an item block's rules (TT42).
--- UNVERIFIED: that SetOwner on a tooltip already shown clears it on Forever; its rules are hidden first anyway.
---@param owner table   The frame hovered
---@param title string  First line
---@param text string|nil  Wrapped body text
function Adapter.ShowTooltip(owner, title, text)
  HideDividers(GameTooltip)
  GameTooltip:SetOwner(owner, "ANCHOR_TOP")
  GameTooltip:SetText(title)
  if text then GameTooltip:AddLine(text, 1, 1, 1, true) end
  GameTooltip:Show()
end

--- Show a tooltip of several lines on a frame: wrapped text lines and two-column lines (the Portfolio tab's excluded
--- rows, UI19), each already coloured as RGB; without an item block's rules (TT42).
---@param owner table           The frame hovered
---@param title string          First line
---@param lines table[]         { text, rgb, wrap } or { left, right, leftRgb, rightRgb }
---@param titleRight table|nil  { text, rgb }: a value on the title's line, right-aligned (the minimap's data age, UI20)
function Adapter.ShowTooltipLines(owner, title, lines, titleRight)
  HideDividers(GameTooltip)
  GameTooltip:SetOwner(owner, "ANCHOR_RIGHT")
  if titleRight then
    local rgb = titleRight.rgb
    GameTooltip:AddDoubleLine(title, titleRight.text, 1, 0.82, 0, rgb[1], rgb[2], rgb[3])
  else
    GameTooltip:SetText(title)
  end
  for _, line in ipairs(lines) do
    if line.text then
      GameTooltip:AddLine(line.text, line.rgb[1], line.rgb[2], line.rgb[3], line.wrap == true)
    else
      local l, r = line.leftRgb, line.rightRgb
      GameTooltip:AddDoubleLine(line.left, line.right, l[1], l[2], l[3], r[1], r[2], r[3])
    end
  end
  GameTooltip:Show()
end
