-- UI/MinimapButton.lua: the minimap button (SPEC §4.3 Minimap button, UI20-UI23; docs/mockups/addon.html §5).
-- Frame code only: what it shows and where it sits come from UI/MinimapView.lua. One frame, built at login;
-- nothing runs until it is clicked, dragged or hovered.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local DB = AHProfit.DB
local Events = AHProfit.Events
local Market = AHProfit.Market
local Plan = AHProfit.Plan
local VM = AHProfit.ViewModel
local View = AHProfit.MinimapView

local MinimapButton = {}
AHProfit.MinimapButton = MinimapButton

-- UNVERIFIED: the default UI's minimap textures exist on Forever as on Classic and retail (LibDBIcon uses them).
local BORDER = "Interface\\Minimap\\MiniMap-TrackingBorder"
local BACKGROUND = "Interface\\Minimap\\UI-Minimap-Background"
local HIGHLIGHT = "Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight"
-- UNVERIFIED: Forever loads an uncompressed 32-bit TGA (64 x 64) from an addon folder, as WoW always has.
local ICON = "Interface\\AddOns\\AHProfit\\Media\\MinimapIcon"
local AMBER, MUTED, HINT = { 1, 0.69, 0.18 }, { 0.56, 0.54, 0.49 }, { 0.67, 0.67, 0.67 }

local button, dot = nil, nil

--- The saved setting, to read: an empty one until a drag or /ahp minimap first writes it.
---@return table  AHProfitDB.minimap ({ angle, hidden }), or a fresh {} (not stored)
local function SavedSetting()
  local db = DB.Get()
  return db and type(db.minimap) == "table" and db.minimap or {}
end

--- The saved setting, to write: created in the database on first use.
---@return table|nil  AHProfitDB.minimap; nil while the database is unusable
local function Setting()
  local db = DB.Get()
  if not db then return nil end
  if type(db.minimap) ~= "table" then db.minimap = {} end
  return db.minimap
end

--- Put the button on the minimap's rim.
---@param angle number  Degrees, 0 = east, counter-clockwise
local function Place(angle)
  local minimap = Adapter.Minimap()
  local x, y = View.Offset(angle, View.Radius(minimap:GetWidth()))
  button:ClearAllPoints()
  button:SetPoint("CENTER", minimap, "CENTER", x, y)
end

--- What the tooltip and the dot need, from the loaded files; the Today list only when asked (it is the costly part).
---@param withToday boolean  Also compute the Today list (on hover)
---@return table             MinimapView.Tip's input
local function TipInput(withToday)
  local info = Adapter.PlayerInfo()
  local input = { age = VM.Age(Market.DataMs(), Adapter.NowMs()), marketProblem = Market.Problem(),
                  planStale = Plan.Problem() ~= nil, name = info.name or "" }
  input.inCombat = Adapter.InCombat()                -- the Today list is real work: never in combat (SPEC §5.1)
  if withToday and not input.inCombat and not input.marketProblem and Plan.HasPlan() then
    local ok, today = pcall(AHProfit.MainWindow.Today, (info.name or "") .. "-" .. (info.realm or ""))
    input.today, input.failed = ok and today or nil, not ok
  end
  return input
end

--- Hover: the data age, this character's Today count and expected net, and the hint (UI20).
---@param owner table  The button
function MinimapButton.ShowTip(owner)
  local tip = View.Tip(TipInput(true))
  local lines = {}
  if tip.problem then lines[1] = { text = tip.problem, rgb = AMBER, wrap = true } end
  for _, line in ipairs(tip.lines) do
    lines[#lines + 1] = { left = VM.WithCoins(line.left), right = VM.WithCoins(line.right), leftRgb = MUTED,
                          rightRgb = { 1, 1, 1 } }
  end
  lines[#lines + 1] = { text = tip.hint, rgb = HINT }
  Adapter.ShowTooltipLines(owner, tip.title, lines, { text = tip.age, rgb = tip.ageAmber and AMBER or MUTED })
  dot:SetShown(tip.dot)
end

--- Show or hide the amber dot from the loaded files (no Today list needed).
function MinimapButton.UpdateDot()
  if dot then dot:SetShown(View.Tip(TipInput(false)).dot) end
end

--- Drag start: follow the cursor freely (the frame's own moving; no OnUpdate).
---@param self table  The button
function MinimapButton.OnDragStart(self)
  Adapter.HideTooltip()
  self:StartMoving()
end

--- Drag end: snap to the rim at the angle of the button's centre seen from the minimap's, and keep it (UI21).
---@param self table  The button
function MinimapButton.OnDragStop(self)
  self:StopMovingOrSizing()
  self:SetUserPlaced(false)                     -- StartMoving made it user-placed; the layout cache must not own its place
  local bx, by = self:GetCenter()
  local mx, my = Adapter.Minimap():GetCenter()
  local setting = Setting() or {}
  if bx and mx then setting.angle = View.AngleOf(bx - mx, by - my) end
  Place(View.SavedAngle(setting))
end

--- Build the button: icon, round border, highlight, dot, clicks, drag and hover.
local function Build()
  button = Adapter.CreateFrame("Button", "AHProfitMinimapButton", Adapter.Minimap())
  button:SetSize(31, 31)
  button:SetFrameStrata("MEDIUM")
  button:SetFrameLevel(8)
  if button.SetFixedFrameStrata then button:SetFixedFrameStrata(true) end   -- a parent's level change can't move it
  if button.SetFixedFrameLevel then button:SetFixedFrameLevel(true) end
  button:SetMovable(true)
  button:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  button:RegisterForDrag("LeftButton")
  button:SetHighlightTexture(HIGHLIGHT)
  -- UNVERIFIED: the retail layout (LibDBIcon's, from memory): ring 50 from TOPLEFT, background 24 and icon 18 centred 1 up.
  local background = button:CreateTexture(nil, "BACKGROUND")
  background:SetTexture(BACKGROUND)
  background:SetSize(24, 24)
  background:SetPoint("CENTER", button, "CENTER", 0, 1)
  local icon = button:CreateTexture(nil, "ARTWORK")
  icon:SetTexture(ICON)
  icon:SetSize(18, 18)
  icon:SetPoint("CENTER", button, "CENTER", 0, 1)
  local border = button:CreateTexture(nil, "OVERLAY", nil, 0)
  border:SetTexture(BORDER)
  border:SetSize(50, 50)
  border:SetPoint("TOPLEFT", button, "TOPLEFT", 0, 0)
  dot = button:CreateTexture(nil, "OVERLAY", nil, 7)                       -- above the ring
  dot:SetColorTexture(AMBER[1], AMBER[2], AMBER[3], 1)
  dot:SetSize(7, 7)
  dot:SetPoint("TOPRIGHT", button, "TOPRIGHT", -4, -4)
  button:SetScript("OnClick", function() AHProfit.MainWindow.OnSlash("") end)     -- either button (UI22)
  button:SetScript("OnDragStart", function(self) MinimapButton.OnDragStart(self) end)
  button:SetScript("OnDragStop", function(self) MinimapButton.OnDragStop(self) end)
  button:SetScript("OnEnter", function(self) MinimapButton.OnEnter(self) end)
  button:SetScript("OnLeave", function() Adapter.HideTooltip() end)
end

--- OnEnter: the tooltip; a failure hides it instead of raising a Lua error popup (SPEC §4.3, A5).
---@param self table  The button
function MinimapButton.OnEnter(self)
  if not pcall(MinimapButton.ShowTip, self) then Adapter.HideTooltip() end
end

--- PLAYER_LOGIN: build the button at its saved angle, hidden if /ahp minimap hid it (UI22, UI23).
function MinimapButton.OnLogin()
  if not button then Build() end
  local setting = SavedSetting()
  Place(View.SavedAngle(setting))
  button:SetShown(not setting.hidden)
  MinimapButton.UpdateDot()
  local seconds = View.SecondsUntilStale(Market.DataMs(),Adapter.NowMs(), VM.STALE_MS)
  if seconds then Adapter.After(seconds, MinimapButton.UpdateDot) end   -- the files only change at /reload: one timer
end

--- /ahp minimap: hide or show the button, kept for the account (UI23).
function MinimapButton.ToggleShown()
  local setting = Setting()
  if not setting then return Adapter.Print("the minimap button's setting can't be saved: " .. tostring(DB.SetupError())) end
  setting.hidden = not setting.hidden
  if not button then MinimapButton.OnLogin() end
  button:SetShown(not setting.hidden)
  Adapter.Print(setting.hidden and "minimap button hidden; /ahp minimap shows it again" or "minimap button shown")
end

--- The button and its dot, for tests.
---@return table|nil button, table|nil dot
function MinimapButton.Frame()
  return button, dot
end

Events.Register("PLAYER_LOGIN", MinimapButton.OnLogin)
