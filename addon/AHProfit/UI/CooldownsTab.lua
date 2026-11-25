-- UI/CooldownsTab.lua: the Cooldowns tab's own frames (SPEC §3.4F.8, UI31), split from UI/MainWindow.lua for size:
-- the character buttons and the bar above the list, and each pooled row's sub-lines and per-cell hovers.
-- Frame code only: every text, colour flag and number comes from UI/CooldownsView.lua. Frames are built once with
-- the window (SPEC §5.1 lazy frames); a render only re-labels, shows and hides them.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter

local CooldownsTab = {}
AHProfit.CooldownsTab = CooldownsTab

CooldownsTab.PER_LINE, CooldownsTab.BUTTONS = 6, 12        -- more than 6 characters wrap to a second line (CD23)
CooldownsTab.BUTTON_W, CooldownsTab.BUTTON_H = 84, 20
CooldownsTab.BAR_H = 18
CooldownsTab.ROW_H = 30                                     -- a row and its sub-line
CooldownsTab.TOP = 60                                       -- where the header strip sits on the other tabs
local SUBS = { item = "sub", skill = "skillSub", cooldown = "shared", price = "priceAge" }   -- cell -> the field under it
local TIPS = { cooldown = "cooldownTip", ready = "readyTip", mats = "matsTip", perDay = "perDayTip",
               canMake = "canMakeTip" }
local ITEM_CELLS = { item = true, price = true }            -- these show the normal market tooltip (CD26)

--- Build the character buttons and the bar, hidden until the tab shows.
---@param frame table     The window
---@param onPick function function(charId) when a character button is clicked
---@return table          { frame, buttons, bar }
function CooldownsTab.Build(frame, onPick)
  local t = { frame = frame, buttons = {} }
  for i = 1, CooldownsTab.BUTTONS do
    local button = Adapter.CreateFrame("Button", "AHProfitWindowCdChar" .. i, frame, "BackdropTemplate")
    local col, line = (i - 1) % CooldownsTab.PER_LINE, math.floor((i - 1) / CooldownsTab.PER_LINE)
    button:SetSize(CooldownsTab.BUTTON_W, CooldownsTab.BUTTON_H)
    button:SetPoint("TOPLEFT", frame, "TOPLEFT", 10 + col * (CooldownsTab.BUTTON_W + 2),
                    -(CooldownsTab.TOP + 2 + line * (CooldownsTab.BUTTON_H + 2)))
    button:SetNormalFontObject("GameFontNormalSmall")
    button:SetScript("OnClick", function(self) onPick(self.charId) end)
    button:Hide()
    t.buttons[i] = button
  end
  t.bar = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  t.bar:SetWordWrap(false)
  t.bar:SetJustifyH("LEFT")
  t.bar:SetHeight(CooldownsTab.BAR_H)
  t.bar:Hide()
  return t
end

--- Show the character buttons and the bar for the tab's data, or hide them on another tab.
---@param t table          CooldownsTab.Build's
---@param data table|nil   WindowLines' cooldowns block { characters, shown, bar }; nil hides everything
---@param paint function   function(button, selected) colours a button
---@return number          How far the header strip and the list move down, px (0 when hidden)
function CooldownsTab.Layout(t, data, paint)
  for i, button in ipairs(t.buttons) do
    local c = data and data.characters[i]
    button:SetShown(c ~= nil)
    if c then
      button.charId = c.id
      button:SetText(c.you and (c.name .. " · you") or c.name)
      paint(button, c.id == data.shown)
    end
  end
  t.bar:SetShown(data ~= nil)
  if not data then return 0 end
  local lines = math.ceil(math.min(#data.characters, CooldownsTab.BUTTONS) / CooldownsTab.PER_LINE)
  local y = CooldownsTab.TOP + 2 + lines * (CooldownsTab.BUTTON_H + 2)
  t.bar:ClearAllPoints()
  t.bar:SetPoint("TOPLEFT", t.frame, "TOPLEFT", 14, -y)
  t.bar:SetPoint("TOPRIGHT", t.frame, "TOPRIGHT", -14, -y)
  t.bar:SetText(data.bar)
  return y + CooldownsTab.BAR_H - CooldownsTab.TOP
end

--- A cell's hover: the market tooltip on the item and the price, else the cell's own text; the row's tint stays.
---@param hit table  The cell's hit frame
local function OnCellEnter(hit)
  local row, line = hit.row, hit.row.cdLine
  row.hover:Show()
  if not line then return end
  local tip = not ITEM_CELLS[hit.field] and line[TIPS[hit.field] or ""] or nil
  if tip == nil then return Adapter.ShowItemTooltip(hit, line.key) end   -- the item, its price, or nothing else to say
  if type(tip) == "table" then
    local lines = {}
    for i, text in ipairs(tip) do lines[i] = { text = text, rgb = { 1, 1, 1 } } end
    Adapter.ShowTooltipLines(hit, line.item, lines)
  else
    Adapter.ShowTooltip(hit, line.item, tip)
  end
end

--- A cell's mouse leaving: the tint and any tooltip go.
---@param hit table
local function OnCellLeave(hit)
  hit.row.hover:Hide()
  Adapter.HideTooltip()
end

--- A click on a cell does what a click on its row does (the keep menu).
---@param hit table
---@param button string
local function OnCellClick(hit, button)
  local handler = hit.row:GetScript("OnMouseUp")
  if handler then handler(hit.row, button) end
end

--- Give a pooled row the tab's parts: cells top-aligned over two lines, a sub-line under Item, Cooldown and AH price,
--- and a hit frame per hovered cell (built once, scripts set once).
---@param row table    A pooled list row
---@param cells table  row.cells.cooldowns
function CooldownsTab.BuildRow(row, cells)
  row.cd = { subs = {}, hits = {} }
  for _, column in ipairs(AHProfit.MainWindow.COLUMNS.cooldowns) do   -- column order: the frame tree is stable
    local field, cell = column[2], cells[column[2]]
    cell:SetHeight(CooldownsTab.ROW_H - 2)
    cell:SetJustifyV("TOP")
    if SUBS[field] then
      local sub = row:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
      sub:SetWordWrap(false)
      sub:SetJustifyH(field == "item" and "LEFT" or "RIGHT")
      sub:SetHeight(12)
      sub:SetPoint("BOTTOMLEFT", cell, "BOTTOMLEFT", 0, 2)
      sub:SetPoint("BOTTOMRIGHT", cell, "BOTTOMRIGHT", 0, 2)
      row.cd.subs[field] = sub
    end
    do
      local hit = Adapter.CreateFrame("Button", nil, row)
      hit:SetAllPoints(cell)
      hit.row, hit.field = row, field
      hit:SetScript("OnEnter", OnCellEnter)
      hit:SetScript("OnLeave", OnCellLeave)
      hit:SetScript("OnMouseUp", OnCellClick)
      row.cd.hits[field] = hit
    end
  end
end

--- Show or hide a row's tab parts with its cells.
---@param row table
---@param active boolean  The row shows a Cooldowns line
function CooldownsTab.ShowRow(row, active)
  for _, sub in pairs(row.cd.subs) do sub:SetShown(active) end
  for _, hit in pairs(row.cd.hits) do hit:SetShown(active) end
  if not active then row.cdLine = nil end
end

--- Fill a row's sub-lines and colours after MainWindow has set its cells' text.
---@param row table
---@param line table     A CooldownsView row
---@param tint function  function(region, rgb)
---@param colors table   MainWindow's palette
function CooldownsTab.FillRow(row, line, tint, colors)
  row.cdLine = line
  for field, sub in pairs(row.cd.subs) do sub:SetText(line[SUBS[field]] or "") end
  tint(row.cd.subs.skill, colors.amber)
  local cells = row.cells.cooldowns
  tint(cells.skill, line.skillAmber and colors.amber or colors.text)
  tint(cells.cooldown, colors.text)
  tint(cells.ready, line.readyColor == "orange" and colors.orange or colors.text)
  tint(cells.price, line.priceAge and colors.muted or colors.text)
  tint(cells.mats, colors.text)
  tint(cells.perDay, line.perDayColor == "neg" and colors.neg or colors.text)
  tint(cells.canMake, line.canMakeColor == "pos" and colors.pos or colors.muted)
end
