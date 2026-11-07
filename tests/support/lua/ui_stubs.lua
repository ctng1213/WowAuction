--- UI frame stubs for the layout snapshots (SPEC §8.4 layer 2).
--- Frames, font strings and textures record what the addon asks of them: parent, size,
--- anchors, text, font size, wrapping and visibility. layout.lua resolves the anchors into
--- rectangles; nothing is drawn. Unknown methods are nil, so a typo in frame code fails a test.

local UI = {}

-- Font templates the addon may use -> font size in px (UI scale 1).
UI.FONT_SIZES = { GameFontNormal = 12, GameFontHighlight = 12, GameFontDisable = 12, GameFontNormalSmall = 10,
                  GameFontHighlightSmall = 10, GameFontDisableSmall = 10, GameFontNormalLarge = 16,
                  GameFontHighlightLarge = 16, NumberFontNormal = 12, NumberFontNormalSmall = 10 }

local Methods = {}
local Meta = { __index = Methods }     -- an unknown method is nil: calling it fails the test

--- A new region: frame, button, font string or texture.
---@param kind string         "Frame", "Button", "ScrollFrame", "FontString" or "Texture"
---@param name string|nil     Global name, if any
---@param parent table|nil    Parent region
---@return table              The region
function UI.New(kind, name, parent)
  local region = setmetatable({ kind = kind, name = name, parent = parent, children = {}, points = {}, shown = true,
                                events = {}, scripts = {}, fontSize = 12, wrap = true }, Meta)
  if parent then parent.children[#parent.children + 1] = region end
  if name then _G[name] = region end
  return region
end

-- ------------------------------------------------------------------ geometry

--- SetSize / SetWidth / SetHeight / GetWidth / GetHeight.
---@param w number  Width in px
---@param h number  Height in px
function Methods:SetSize(w, h) self.width, self.height = w, h end
--- Set the width.
---@param w number
function Methods:SetWidth(w) self.width = w end
--- Set the height.
---@param h number
function Methods:SetHeight(h) self.height = h end
--- The width set, or 0.
---@return number
function Methods:GetWidth() return self.width or 0 end
--- The height set, or 0.
---@return number
function Methods:GetHeight() return self.height or 0 end

--- SetPoint(point [, relativeTo [, relativePoint]] [, x, y]), as WoW accepts it.
---@param point string  e.g. "TOPLEFT"
function Methods:SetPoint(point, a, b, c, d)
  local rel, relPoint, x, y
  if type(a) == "number" then
    x, y = a, b
  else
    rel, relPoint, x, y = a, b, c, d
    if type(relPoint) == "number" then relPoint, x, y = nil, b, c end
  end
  self.points[#self.points + 1] = { point = point, rel = rel or self.parent, relPoint = relPoint or point,
                                    x = x or 0, y = y or 0 }
end

--- Anchor all four edges to another region (default: the parent).
---@param rel table|nil
function Methods:SetAllPoints(rel)
  self:SetPoint("TOPLEFT", rel or self.parent, "TOPLEFT", 0, 0)
  self:SetPoint("BOTTOMRIGHT", rel or self.parent, "BOTTOMRIGHT", 0, 0)
end

--- Remove every anchor.
function Methods:ClearAllPoints() self.points = {} end

--- Reparent the region.
---@param parent table
function Methods:SetParent(parent)
  if self.parent then
    for i, child in ipairs(self.parent.children) do
      if child == self then table.remove(self.parent.children, i) break end
    end
  end
  self.parent = parent
  parent.children[#parent.children + 1] = self
end
--- The parent region.
---@return table|nil
function Methods:GetParent() return self.parent end
--- The global name.
---@return string|nil
function Methods:GetName() return self.name end

-- ------------------------------------------------------------------ visibility

--- Show the region.
function Methods:Show() self.shown = true end
--- Hide the region.
function Methods:Hide() self.shown = false end
--- Show or hide.
---@param shown boolean
function Methods:SetShown(shown) self.shown = shown and true or false end
--- Whether the region itself is shown.
---@return boolean
function Methods:IsShown() return self.shown end
--- Whether it and every parent are shown.
---@return boolean
function Methods:IsVisible()
  local r = self
  while r do
    if not r.shown then return false end
    r = r.parent
  end
  return true
end

-- ------------------------------------------------------------------ text

--- Set the text (font strings and buttons).
---@param text string|nil
function Methods:SetText(text) self.text = text end
--- The text.
---@return string|nil
function Methods:GetText() return self.text end
--- Use a font template; its size is recorded.
---@param template string|table  e.g. "GameFontNormalSmall"
function Methods:SetFontObject(template) self.fontSize = UI.FONT_SIZES[template] or 12 end
--- Set the font file, size and flags; only the size is recorded.
---@param size number
function Methods:SetFont(_, size) self.fontSize = size end
--- Whether text may wrap onto more lines.
---@param wrap boolean
function Methods:SetWordWrap(wrap) self.wrap = wrap end
--- Record the horizontal justification.
---@param justify string  "LEFT", "CENTER" or "RIGHT"
function Methods:SetJustifyH(justify) self.justifyH = justify end
--- Record the vertical justification.
---@param justify string
function Methods:SetJustifyV(justify) self.justifyV = justify end
--- Record the text colour.
function Methods:SetTextColor(r, g, b, a) self.textColor = { r, g, b, a } end

-- ------------------------------------------------------------------ children and textures

--- Create a font string child.
---@param name string|nil
---@param _ any               Draw layer
---@param template string|nil Font template
---@return table
function Methods:CreateFontString(name, _, template)
  local fs = UI.New("FontString", name, self)
  if template then fs:SetFontObject(template) end
  return fs
end
--- Create a texture child.
---@param name string|nil
---@return table
function Methods:CreateTexture(name, layer, _, sublevel)
  local texture = UI.New("Texture", name, self)
  texture.layer, texture.sublevel = layer, sublevel
  return texture
end
--- Record a texture file.
---@param file string|number
function Methods:SetTexture(file) self.texture = file end
--- Record a solid colour.
function Methods:SetColorTexture(r, g, b, a) self.color = { r, g, b, a } end
--- Record a tint.
function Methods:SetVertexColor(r, g, b, a) self.color = { r, g, b, a } end
--- Record the alpha.
---@param a number
function Methods:SetAlpha(a) self.alpha = a end

-- ------------------------------------------------------------------ behaviour (recorded, not simulated)

local function Record(name)
  return function(self, ...) self[name] = { ... } end
end
Methods.EnableMouse = Record("mouse")
Methods.EnableMouseWheel = Record("mouseWheel")
Methods.SetMovable = Record("movable")
Methods.SetResizable = Record("resizable")
Methods.SetResizeBounds = Record("resizeBounds")
Methods.SetClampedToScreen = Record("clamped")
Methods.RegisterForDrag = Record("drag")
Methods.RegisterForClicks = Record("clicks")
Methods.SetFrameStrata = Record("strata")
Methods.SetFrameLevel = Record("level")
Methods.SetFixedFrameStrata = Record("fixedStrata")
Methods.SetFixedFrameLevel = Record("fixedLevel")
Methods.SetUserPlaced = Record("userPlaced")
Methods.SetBackdrop = Record("backdrop")
Methods.SetBackdropColor = Record("backdropColor")
Methods.SetBackdropBorderColor = Record("backdropBorderColor")
Methods.SetHighlightTexture = Record("highlight")
Methods.SetNormalFontObject = Record("normalFont")
Methods.SetEnabled = Record("enabled")
Methods.SetChecked = Record("checked")
Methods.SetVerticalScroll = Record("verticalScroll")
Methods.SetScrollChild = function(self, child) self.scrollChild = child end
Methods.SetNumeric = Record("numeric")
Methods.SetAutoFocus = Record("autoFocus")
Methods.SetMaxLetters = Record("maxLetters")
--- EditBox:SetFocus: recorded only.
function Methods:SetFocus() self.focused = true end
--- EditBox:ClearFocus: recorded only.
function Methods:ClearFocus() self.focused = false end

--- The frame's centre, as a test placed it (centerX, centerY); nil, nil until then, as for a frame not laid out.
---@return number|nil x, number|nil y
function Methods:GetCenter() return self.centerX, self.centerY end

--- Start moving (dragging) the frame: recorded only.
function Methods:StartMoving() self.moving = true end
--- Stop moving or sizing: recorded only.
function Methods:StopMovingOrSizing() self.moving = false end

-- Events and scripts: shared with the event stubs in wow_stubs.lua.
--- Frame:RegisterEvent.
---@param event string
function Methods:RegisterEvent(event)
  if UI.world and UI.world.unknownEvents[event] then
    error('Frame:RegisterEvent(): Attempt to register unknown event "' .. event .. '"', 2)
  end
  self.events[event] = true
end
--- Frame:UnregisterEvent.
---@param event string
function Methods:UnregisterEvent(event) self.events[event] = nil end
--- Frame:IsEventRegistered.
---@param event string
---@return boolean
function Methods:IsEventRegistered(event) return self.events[event] == true end
--- Frame:SetScript; OnUpdate is forbidden (SPEC §5.1: no per-frame polling).
---@param name string
---@param fn function|nil
function Methods:SetScript(name, fn)
  if name == "OnUpdate" and fn ~= nil then
    error("OnUpdate scripts are forbidden (SPEC §5.1); use C_Timer.After", 2)
  end
  self.scripts[name] = fn
end
--- Frame:GetScript.
---@param name string
---@return function|nil
function Methods:GetScript(name) return self.scripts[name] end
--- Run the frame's OnClick script, as a mouse click would.
---@param button string|nil  Mouse button, default "LeftButton"
function Methods:Click(button)
  local fn = self.scripts.OnClick
  if fn then fn(self, button or "LeftButton") end
end

return UI
