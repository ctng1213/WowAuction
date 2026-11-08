-- UI/MainWindow.lua: the /ahp window (SPEC §4.3, docs/mockups/addon.html). It is about the
-- character you are playing: it shows that character's actions only, so it has no Who column
-- (the dashboard shows every character, with one).
-- Frame code only: UI/WindowLines.lua gathers each tab's lines and every text, colour flag and number comes from the
-- view models (UI/ViewModel.lua, UI/PortfolioView.lua). Frames are built the first time the window opens (SPEC §5.1
-- lazy frames) and its rows are a fixed pool that scrolls, so the window never grows past its size. The Portfolio
-- tab's lines have mixed heights (a wrapped Status, the Adjusted sub-line), so each redraw re-anchors the pooled rows.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local DB = AHProfit.DB
local Market = AHProfit.Market
local Plan = AHProfit.Plan
local VM = AHProfit.ViewModel
local Events = AHProfit.Events
local WindowLines = AHProfit.WindowLines
local Inventory = AHProfit.Capture.Inventory

local MainWindow = {}
AHProfit.MainWindow = MainWindow

MainWindow.WIDTH, MainWindow.HEIGHT = 640, 440
MainWindow.MIN_WIDTH, MainWindow.MIN_HEIGHT = 560, 360
MainWindow.ROWS, MainWindow.ROW_HEIGHT = 12, 20
MainWindow.GAP = 3
MainWindow.CHANCE_STEPS = { 0, 25, 50, 75 }
-- The mockup's order. Portfolio replaces Today and Week (D76). Disenchant is always there: an alt may be the
-- enchanter (A23). Reservations lists what you keep for yourself, to change or undo it (A26). Each tab is as wide as
-- its label, so all of them fit beside the Min chance box at the minimum width.
MainWindow.TABS = { { "portfolio", "Portfolio", 62 }, { "disenchant", "Disenchant", 68 }, { "craft", "Craft", 46 },
                    { "flip", "Flip", 40 }, { "inventory", "Inventory", 62 }, { "reservations", "Reservations", 78 } }
MainWindow.MONEY_FIELDS = { each = true, total = true, net = true, worth = true, buy = true, ev = true, profit = true,
                            mats = true, sell = true, adjusted = true }

-- Column sets: { header, row field, width (nil = fills), justify }; columns after the filling one anchor from the
-- right. "today" is the Portfolio tab without a snapshot (the plan's Today rows); pf* are its sections with one.
MainWindow.COLUMNS = {
  today = { { "", "action", 52, "LEFT" }, { "Item", "item", nil, "LEFT" }, { "Qty", "qty", 28, "RIGHT" },
            { "Each", "each", 52, "RIGHT" }, { "Total", "total", 56, "RIGHT" }, { "Net", "net", 88, "RIGHT" },
            { "Chance", "chance", 46, "RIGHT" }, { "Volume", "volume", 52, "RIGHT" }, { "Risk", "risk", 34, "RIGHT" } },
  pfPositions = { { "Item", "item", 150, "LEFT" }, { "Qty", "qty", 60, "RIGHT" }, { "Status", "state", 118, "LEFT" },
                  { "Net", "net", 56, "RIGHT" }, { "Next", "next", nil, "LEFT" } },
  pfToday = { { "", "action", 40, "LEFT" }, { "Item", "item", nil, "LEFT" }, { "Qty", "qty", 24, "RIGHT" },
              { "Each", "each", 40, "RIGHT" }, { "Total", "total", 44, "RIGHT" }, { "Net", "net", 54, "RIGHT" },
              { "Chance", "chance", 36, "RIGHT" }, { "Volume", "volume", 40, "RIGHT" }, { "Risk", "risk", 28, "RIGHT" },
              { "Status", "status", 150, "LEFT" } },
  pfWeek = { { "", "action", 40, "LEFT" }, { "Item", "item", nil, "LEFT" }, { "Plan", "plan", 154, "LEFT" },
             { "Net", "net", 56, "RIGHT" }, { "Adjusted", "adjusted", 84, "RIGHT" }, { "Status", "status", 150, "LEFT" } },
  disenchant = { { "Item", "item", nil, "LEFT" }, { "ilvl", "ilvl", 30, "RIGHT" }, { "Skill", "skill", 34, "RIGHT" },
                 { "Buy", "buy", 52, "RIGHT" }, { "EV", "ev", 56, "RIGHT" }, { "Profit", "profit", 70, "RIGHT" },
                 { "Chance", "chance", 46, "RIGHT" }, { "Volume", "volume", 52, "RIGHT" } },
  inventory = { { "Item", "item", nil, "LEFT" }, { "Where", "where", 44, "LEFT" }, { "Qty", "qty", 28, "RIGHT" },
                { "Worth", "worth", 88, "RIGHT" }, { "Chance", "chance", 46, "RIGHT" }, { "Volume", "volume", 52, "RIGHT" } },
  craft = { { "Item", "item", nil, "LEFT" }, { "Source", "source", 46, "LEFT" }, { "×", "crafts", 28, "RIGHT" },
            { "Mats", "mats", 56, "RIGHT" }, { "Profit", "profit", 70, "RIGHT" }, { "Chance", "chance", 46, "RIGHT" },
            { "Volume", "volume", 52, "RIGHT" }, { "Risk", "risk", 34, "RIGHT" } },
  flip = { { "Item", "item", nil, "LEFT" }, { "Qty", "qty", 28, "RIGHT" }, { "Buy", "buy", 46, "RIGHT" },
           { "Sell", "sell", 64, "RIGHT" }, { "When", "when", 62, "LEFT" }, { "Profit", "profit", 76, "RIGHT" },
           { "Chance", "chance", 46, "RIGHT" }, { "Risk", "risk", 34, "RIGHT" } },
  reservations = { { "Item", "item", nil, "LEFT" }, { "Keeping", "keeping", 52, "RIGHT" }, { "Held", "held", 44, "RIGHT" },
                   { "Free", "free", 44, "RIGHT" } },
}
-- Every set a pooled row carries cells for, in build order; the header strip above the list shows the fixed ones.
MainWindow.SETS = { "today", "pfPositions", "pfToday", "pfWeek", "disenchant", "craft", "flip", "inventory", "reservations" }
MainWindow.FIXED = { "today", "disenchant", "craft", "flip", "inventory", "reservations" }
MainWindow.PF_SETS = { "pfPositions", "pfToday", "pfWeek" }

-- The mockup's palette (docs/mockups/addon.html :root), as 0-1 RGB.
local COLORS = { pos = { 0.24, 0.86, 0.31 }, neg = { 1, 0.29, 0.24 }, muted = { 0.56, 0.54, 0.49 },
                 amber = { 1, 0.69, 0.18 }, text = { 0.91, 0.89, 0.82 }, title = { 1, 0.82, 0 },
                 border = { 0.42, 0.35, 0.2 }, panel = { 0.05, 0.05, 0.06 }, strip = { 0.13, 0.13, 0.09 },
                 line = { 0.13, 0.13, 0.13 }, hover = { 0.12, 0.11, 0.07 }, tabOn = { 0.16, 0.15, 0.09 },
                 tabOff = { 0.07, 0.07, 0.08 }, black = { 0, 0, 0 }, track = { 0.1, 0.1, 0.1 }, thumb = { 0.35, 0.29, 0.15 },
                 funded = { 0.24, 0.86, 0.31 }, white = { 1, 1, 1 } }
local QUALITY = { [0] = { 0.62, 0.62, 0.62 }, [1] = { 1, 1, 1 }, [2] = { 0.12, 1, 0 }, [3] = { 0, 0.44, 0.87 },
                  [4] = { 0.64, 0.21, 0.93 }, [5] = { 1, 0.5, 0 } }
local BADGES = { buy = { 0.37, 0.71, 1 }, post = { 1, 0.82, 0 }, de = { 0.7, 0.48, 1 }, craft = { 1, 0.6, 0.24 },
                 vendor = { 0.67, 0.67, 0.67 }, mail = { 0.56, 0.83, 1 }, hold = { 1, 0.69, 0.18 },
                 decide = { 1, 0.29, 0.24 } }
-- A position's stage badge (the mockup's .badge.*).
local STAGES = { acq = { 0.37, 0.71, 1 }, proc = { 1, 0.6, 0.24 }, hold = { 1, 0.69, 0.18 }, ready = { 0.24, 0.86, 0.31 },
                 listed = { 1, 0.82, 0 }, partial = { 0.56, 0.83, 1 }, done = { 0.56, 0.54, 0.49 }, recon = { 1, 0.29, 0.24 } }
local DOTS = { high = COLORS.pos, medium = COLORS.amber, low = COLORS.muted }
local FLAT = "Interface\\Buttons\\WHITE8X8"
-- The Min chance box's arrow: the chat frame's scroll-down icon, which every client has.
local ARROW = "|TInterface\\ChatFrame\\UI-ChatIcon-ScrollDown-Up:14:14|t"
local MUTED_ESCAPE = "|cff8f8a7c"

local ui = nil                     -- the frames, once built
local computed = nil               -- what the tab shows (WindowLines.Lines), until something changes
local lastGood = nil               -- the last computed lines without an error (A5)
local view = { tab = "portfolio", offset = 0, minChance = 50 }

--- Colour a font string from an { r, g, b } triple.
---@param region table
---@param rgb table
local function Tint(region, rgb)
  region:SetTextColor(rgb[1], rgb[2], rgb[3])
end

--- A solid-colour texture on a frame.
---@param parent table
---@param rgb table
---@param layer string  Draw layer
---@return table
local function Solid(parent, rgb, layer)
  local texture = parent:CreateTexture(nil, layer or "BACKGROUND")
  texture:SetColorTexture(rgb[1], rgb[2], rgb[3])
  return texture
end

--- A flat panel look: dark fill and a thin coloured border (the mockup's .frame, .tab, .dropdown).
---@param frame table  A frame made with BackdropTemplate
---@param fill table   RGB
---@param edge table   RGB
local function Flat(frame, fill, edge)
  frame:SetBackdrop({ bgFile = FLAT, edgeFile = FLAT, edgeSize = 1 })
  frame:SetBackdropColor(fill[1], fill[2], fill[3], 0.96)
  frame:SetBackdropBorderColor(edge[1], edge[2], edge[3])
end

--- A 1-px horizontal rule across a frame at some distance from its top.
---@param parent table
---@param y number     Pixels down from the top
---@param rgb table
---@return table
local function Rule(parent, y, rgb)
  local rule = Solid(parent, rgb, "BORDER")
  rule:SetHeight(1)
  rule:SetPoint("TOPLEFT", parent, "TOPLEFT", 2, -y)
  rule:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -2, -y)
  return rule
end

--- One cell: a font string justified like its column. A header cell is a gold label with an
--- invisible button over it, so it aligns like its column and still takes hover for its tooltip.
---@param parent table  The row or header
---@param c table       Its column { header, field, width, justify }
---@param kind string   "FontString" | "Button"
---@return table        The font string (a header's has .hit, its button)
local function MakeCell(parent, c, kind)
  local cell = parent:CreateFontString(nil, "OVERLAY", kind == "Button" and "GameFontNormalSmall" or "GameFontHighlightSmall")
  cell:SetWordWrap(false)
  cell:SetJustifyH(c[4])
  cell:SetHeight(MainWindow.ROW_HEIGHT)
  if kind == "Button" then
    cell:SetText(c[1])
    cell.hit = Adapter.CreateFrame("Button", nil, parent)
    cell.hit:SetAllPoints(cell)
  end
  return cell
end

--- One cell per column across a parent row, anchored left before the filling column and right after it.
---@param parent table     The row (or header) frame
---@param columns table    A MainWindow.COLUMNS list
---@param kind string      "FontString" for rows, "Button" for headers (they take hover)
---@return table           field -> cell
local function BuildCells(parent, columns, kind)
  local cells, fill = {}, nil
  for i, c in ipairs(columns) do if not c[3] then fill = i end end
  local left = nil
  for i = 1, fill - 1 do
    local cell = MakeCell(parent, columns[i], kind)
    cells[columns[i][2]] = cell
    cell:SetWidth(columns[i][3])
    if left then cell:SetPoint("LEFT", left, "RIGHT", MainWindow.GAP, 0) else cell:SetPoint("LEFT", parent, "LEFT", 4, 0) end
    left = cell
  end
  local right = nil
  for i = #columns, fill + 1, -1 do
    local cell = MakeCell(parent, columns[i], kind)
    cells[columns[i][2]] = cell
    cell:SetWidth(columns[i][3])
    if right then cell:SetPoint("RIGHT", right, "LEFT", -MainWindow.GAP, 0) else cell:SetPoint("RIGHT", parent, "RIGHT", -4, 0) end
    right = cell
  end
  local item = MakeCell(parent, columns[fill], kind)
  cells[columns[fill][2]] = item
  item:SetPoint("TOPLEFT", left or parent, left and "TOPRIGHT" or "TOPLEFT", MainWindow.GAP, 0)
  item:SetPoint("BOTTOMRIGHT", right or parent, right and "BOTTOMLEFT" or "BOTTOMRIGHT", -MainWindow.GAP, 0)
  return cells
end

--- Show a column's tooltip while its header is hovered, and colour the headers gold (SPEC §4.3).
---@param cells table   field -> header button
---@param columns table The set's columns
local function HookHeaders(cells, columns)
  for _, c in ipairs(columns) do
    local help = VM.COLUMN_HELP[c[1]]
    if help then
      local hit = cells[c[2]].hit
      hit:SetScript("OnEnter", function(self) Adapter.ShowTooltip(self, c[1], help) end)
      hit:SetScript("OnLeave", function() Adapter.HideTooltip() end)
    end
  end
end

--- The coloured action badge behind each set's action cell (the mockup's .act).
---@param row table
local function BuildBadges(row)
  row.badges = {}
  for _, set in ipairs(MainWindow.SETS) do
    local cell = row.cells[set].action
    if cell then
      local width = MainWindow.COLUMNS[set][1][3] - 4
      local badge = row:CreateTexture(nil, "ARTWORK")
      badge:SetSize(width, 12)
      badge:SetPoint("LEFT", cell, "LEFT", 0, 0)
      cell:SetJustifyH("CENTER")
      cell:SetWidth(width)
      row.badges[set] = badge
    end
  end
end

--- The Portfolio sections' cells top-aligned where a row may be tall: Status wraps within 150 px, and a week row's
--- Adjusted has its wait and premium under it (UI16).
---@param row table
local function AnchorTall(row)
  for _, set in ipairs({ "pfToday", "pfWeek" }) do
    local status = row.cells[set].status
    status:ClearAllPoints()
    status:SetPoint("TOPRIGHT", row, "TOPRIGHT", -4, -1)
    status:SetWordWrap(true)
  end
  local c = row.cells.pfWeek
  c.adjusted:ClearAllPoints()
  c.adjusted:SetPoint("BOTTOMRIGHT", c.status, "LEFT", -MainWindow.GAP, 0)
  c.adjusted:SetHeight(13)
  c.net:ClearAllPoints()
  c.net:SetPoint("RIGHT", c.status, "LEFT", -(2 * MainWindow.GAP + MainWindow.COLUMNS.pfWeek[5][3]), 0)
  row.sub = row:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  row.sub:SetSize(84, 13)
  row.sub:SetJustifyH("RIGHT")
  row.sub:SetPoint("TOPRIGHT", c.adjusted, "BOTTOMRIGHT", 0, 0)
end

--- A row's hover: the excluded-row tooltip on an unfunded Portfolio row (UI19), else the hover tint.
---@param row table
local function OnRowEnter(row)
  row.hover:SetShown(row.isRow == true)
  if row.tip then MainWindow.ShowTip(row, row.tip) end
end

--- A row's click: right-click lists the section's excluded rows on an unfunded row (UI19), else opens the keep
--- menu; on Reservations a left click opens it too.
---@param row table
---@param button string
local function OnRowClick(row, button)
  if row.menuTip and button == "RightButton" then return MainWindow.ShowTip(row, row.menuTip) end
  if row.isRow and row.key and (button == "RightButton" or view.tab == "reservations") then
    AHProfit.RowMenu.Open(row, row.key, row.itemName)
  end
end

--- One pooled list line: a cell set per column set, an action badge, a confidence dot, a note line and a heading.
---@param list table  The list frame
---@param i number    Line number, from 1
---@return table      The row
local function BuildRow(list, i)
  local row = Adapter.CreateFrame("Frame", "AHProfitWindowRow" .. i, list)
  row:SetHeight(MainWindow.ROW_HEIGHT)
  row:SetPoint("TOPLEFT", list, "TOPLEFT", 0, -(i - 1) * MainWindow.ROW_HEIGHT)
  row:SetPoint("TOPRIGHT", list, "TOPRIGHT", -14, -(i - 1) * MainWindow.ROW_HEIGHT)
  row.rule = Rule(row, 0, COLORS.line)
  row.hover = Solid(row, COLORS.hover, "BACKGROUND")
  row.hover:SetAllPoints(row)
  row.hover:Hide()
  row:EnableMouse(true)
  row:SetScript("OnMouseUp", function(self, button) OnRowClick(self, button) end)
  row:SetScript("OnEnter", function(self) OnRowEnter(self) end)
  row:SetScript("OnLeave", function(self) self.hover:Hide(); if self.tip then Adapter.HideTooltip() end end)
  row.cells = {}
  for _, set in ipairs(MainWindow.SETS) do row.cells[set] = BuildCells(row, MainWindow.COLUMNS[set], "FontString") end
  BuildBadges(row)
  AnchorTall(row)
  row.note = row:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  row.note:SetWordWrap(false)
  row.note:SetJustifyH("LEFT")
  row.dayLabel = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  row.dayTotal = row:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  row.dayTotal:SetPoint("RIGHT", row, "RIGHT", -4, 0)
  row.dayRule = Solid(row, COLORS.border, "BORDER")
  row.dayRule:SetHeight(1)
  row.dayRule:SetPoint("BOTTOMLEFT", row, "BOTTOMLEFT", 2, 0)
  row.dayRule:SetPoint("BOTTOMRIGHT", row, "BOTTOMRIGHT", -2, 0)
  return row
end

--- Build the title strip: name, realm and faction, the age badge pill and the close button.
---@param frame table
local function BuildTitle(frame)
  local strip = Solid(frame, COLORS.strip, "BORDER")
  strip:SetHeight(28)
  strip:SetPoint("TOPLEFT", frame, "TOPLEFT", 2, -2)
  strip:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -2, -2)
  Rule(frame, 30, COLORS.border)
  ui.title = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  ui.title:SetPoint("TOPLEFT", frame, "TOPLEFT", 12, -10)
  ui.title:SetText("AHProfit")
  Tint(ui.title, COLORS.title)
  ui.sub = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  ui.sub:SetPoint("LEFT", ui.title, "RIGHT", 10, 0)
  ui.close = Adapter.CreateFrame("Button", "AHProfitWindowClose", frame, "UIPanelCloseButton")
  ui.close:SetSize(24, 24)
  ui.close:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -4, -4)
  ui.close:SetScript("OnClick", function() frame:Hide() end)
  ui.badge = Adapter.CreateFrame("Frame", "AHProfitWindowAge", frame, "BackdropTemplate")
  ui.badge:SetSize(96, 18)
  ui.badge:SetPoint("RIGHT", ui.close, "LEFT", -6, 0)
  ui.age = ui.badge:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  ui.age:SetPoint("CENTER", ui.badge, "CENTER", 0, 0)
end

--- Build the tab buttons and the Min chance box, above a rule (the mockup's .tabs).
---@param frame table
local function BuildTabs(frame)
  ui.tabs = {}
  local previous = nil
  for _, tab in ipairs(MainWindow.TABS) do
    local button = Adapter.CreateFrame("Button", "AHProfitWindowTab" .. tab[2], frame, "BackdropTemplate")
    button:SetSize(tab[3] or 72, 22)
    button:SetNormalFontObject("GameFontNormalSmall")
    button:SetText(tab[2])
    if previous then button:SetPoint("LEFT", previous, "RIGHT", 2, 0) else button:SetPoint("TOPLEFT", frame, "TOPLEFT", 10, -36) end
    button:SetScript("OnClick", function() MainWindow.ShowTab(tab[1]) end)
    ui.tabs[tab[1]] = button
    previous = button
  end
  Rule(frame, 58, COLORS.border)
  ui.chance = Adapter.CreateFrame("Button", "AHProfitWindowMinChance", frame, "BackdropTemplate")
  ui.chance:SetSize(130, 20)
  ui.chance:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -10, -36)
  ui.chance:SetNormalFontObject("GameFontHighlightSmall")
  Flat(ui.chance, COLORS.black, COLORS.border)
  ui.chance:SetScript("OnClick", function() MainWindow.NextMinChance() end)
end

--- A header strip of one column set: gold labels, each explaining itself on hover.
---@param parent table
---@param set string
---@param name string  The frame's name
---@return table       The strip
local function HeaderStrip(parent, set, name)
  local header = Adapter.CreateFrame("Frame", name, parent)
  header:SetHeight(MainWindow.ROW_HEIGHT)
  ui.headerCells[set] = BuildCells(header, MainWindow.COLUMNS[set], "Button")
  HookHeaders(ui.headerCells[set], MainWindow.COLUMNS[set])
  return header
end

--- Build the list area: the fixed header strips, the Portfolio top line, the pooled rows, the Portfolio sections'
--- header strips (placed over a column-heading line when it is on the page), the scroll bar and the empty state.
---@param frame table
local function BuildList(frame)
  ui.headers, ui.headerCells, ui.pfHeaders = {}, {}, {}
  for _, set in ipairs(MainWindow.FIXED) do
    local header = HeaderStrip(frame, set, "AHProfitWindowHeader" .. set)
    header:SetPoint("TOPLEFT", frame, "TOPLEFT", 10, -60)
    header:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -24, -60)
    ui.headers[set] = header
  end
  ui.top = Adapter.CreateFrame("Button", "AHProfitWindowTop", frame)
  ui.top:SetHeight(MainWindow.ROW_HEIGHT)
  ui.top:SetPoint("TOPLEFT", frame, "TOPLEFT", 10, -60)
  ui.top:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -24, -60)
  ui.topText = ui.top:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  ui.topText:SetPoint("LEFT", ui.top, "LEFT", 4, 0)
  ui.top:SetScript("OnEnter", function(self) Adapter.ShowTooltip(self, "Snapshot", AHProfit.PortfolioView.HOVER) end)
  ui.top:SetScript("OnLeave", function() Adapter.HideTooltip() end)
  local list = Adapter.CreateFrame("Frame", "AHProfitWindowList", frame)
  list:SetHeight(MainWindow.ROWS * MainWindow.ROW_HEIGHT)
  list:SetPoint("TOPLEFT", frame, "TOPLEFT", 10, -82)
  list:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -10, -82)
  list:EnableMouseWheel(true)
  list:SetScript("OnMouseWheel", function(_, delta) MainWindow.Scroll(-delta * 3) end)
  ui.list, ui.rows = list, {}
  for i = 1, MainWindow.ROWS do ui.rows[i] = BuildRow(list, i) end
  for _, set in ipairs(MainWindow.PF_SETS) do
    local header = HeaderStrip(list, set, "AHProfitWindowHeader" .. set)
    header:SetPoint("TOPLEFT", list, "TOPLEFT", 0, 0)             -- moved over its column-heading line when shown
    header:SetPoint("TOPRIGHT", list, "TOPRIGHT", -14, 0)
    header:Hide()
    ui.pfHeaders[set] = header
  end
  ui.track = Solid(list, COLORS.track, "BACKGROUND")
  ui.track:SetWidth(8)
  ui.track:SetPoint("TOPRIGHT", list, "TOPRIGHT", 0, 0)
  ui.track:SetPoint("BOTTOMRIGHT", list, "BOTTOMRIGHT", 0, 0)
  ui.thumb = Solid(list, COLORS.thumb, "ARTWORK")
  ui.thumb:SetWidth(6)
  ui.empty = list:CreateFontString(nil, "OVERLAY", "GameFontDisable")
  ui.empty:SetPoint("CENTER", list, "CENTER", 0, 0)
end

--- Build the footer: a rule, the summary on the left and the budget on the right (the mockup's .footer).
---@param frame table
local function BuildFooter(frame)
  local rule = Solid(frame, COLORS.border, "BORDER")
  rule:SetHeight(1)
  rule:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 2, 28)
  rule:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -2, 28)
  ui.footer = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  ui.footer:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 12, 10)
  ui.budget = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  ui.budget:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -12, 10)
  ui.footer:SetPoint("BOTTOMRIGHT", ui.budget, "BOTTOMLEFT", -12, 0)   -- wraps rather than run into the right (0.1.6)
  ui.footer:SetJustifyH("LEFT")
end

--- Build the window the first time it opens.
local function Build()
  local frame = Adapter.CreateFrame("Frame", "AHProfitWindow", UIParent, "BackdropTemplate")
  frame:SetSize(MainWindow.WIDTH, MainWindow.HEIGHT)
  frame:SetPoint("CENTER", UIParent, "CENTER", 0, 0)
  frame:SetBackdrop({ bgFile = FLAT, edgeFile = FLAT, edgeSize = 2 })
  frame:SetBackdropColor(COLORS.panel[1], COLORS.panel[2], COLORS.panel[3], 0.96)
  frame:SetBackdropBorderColor(COLORS.border[1], COLORS.border[2], COLORS.border[3])
  frame:SetFrameStrata("HIGH")
  frame:SetMovable(true)
  frame:SetResizable(true)
  -- UNVERIFIED: which of the two Forever has; newer clients replaced SetMinResize with SetResizeBounds.
  if frame.SetResizeBounds then
    frame:SetResizeBounds(MainWindow.MIN_WIDTH, MainWindow.MIN_HEIGHT)
  elseif frame.SetMinResize then
    frame:SetMinResize(MainWindow.MIN_WIDTH, MainWindow.MIN_HEIGHT)
  end
  frame:SetClampedToScreen(true)
  frame:EnableMouse(true)
  frame:RegisterForDrag("LeftButton")
  frame:SetScript("OnDragStart", function(self) self:StartMoving() end)
  frame:SetScript("OnDragStop", function(self) self:StopMovingOrSizing() end)
  ui = { frame = frame }
  BuildTitle(frame)
  BuildTabs(frame)
  BuildList(frame)
  BuildFooter(frame)
  frame:Hide()                     -- a new frame is shown; the window opens with /ahp
  Adapter.CloseWithEscape("AHProfitWindow")
end

--- GET_ITEM_INFO_RECEIVED: an item the window asked for has arrived; redraw once, debounced,
--- so the row shows its name instead of "Item <key>". The redraw stops listening when no
--- shown name is missing any more.
function MainWindow.OnItemInfo()
  if ui and ui.frame:IsShown() then Events.Debounce("window.names", MainWindow.Refresh) end
end

--- A new bags, bank or mail snapshot was stored: what you carry changed, so the addon's own options
--- do too. The capture is already debounced, so this redraws straight away (A31).
function MainWindow.OnBags()
  if ui and ui.frame:IsShown() then MainWindow.Refresh() end
end

--- The column set the current tab's plain rows use: the Portfolio tab without a snapshot uses Today's.
---@return string
local function TabSet()
  if view.tab == "portfolio" then return "today" end
  return view.tab
end

--- Show only what a kind of line uses: one set's cells, a note, a heading or the empty block.
---@param row table
---@param kind string  "row" | "note" | "foot" | "section" | "colhead" | "empty"
---@param set string   The row's column set
local function ShowParts(row, kind, set)
  for _, s in ipairs(MainWindow.SETS) do
    local active = s == set and kind == "row"
    for _, cell in pairs(row.cells[s]) do cell:SetShown(active) end
    if row.badges[s] then row.badges[s]:SetShown(active) end
  end
  row.sub:SetShown(kind == "row" and set == "pfWeek")
  row.note:SetShown(kind == "note" or kind == "foot" or kind == "empty")
  row.dayLabel:SetShown(kind == "section" or kind == "empty")
  row.dayTotal:SetShown(kind == "section")
  row.dayRule:SetShown(kind == "section")
  row.rule:SetShown(kind == "row")
  row.isRow = kind == "row"
end

--- The confidence dot as an inline texture after the chance text, in its level's colour.
---@param level string  "high" | "medium" | "low"
---@return string       A |T...|t escape, 7 px square
local function Dot(level)
  local rgb = DOTS[level]
  return string.format(" |T%s:7:7:0:0:8:8:0:8:0:8:%d:%d:%d|t", FLAT, rgb[1] * 255, rgb[2] * 255, rgb[3] * 255)
end

--- Fill a row line's cells, badge and dot.
---@param row table
---@param line table  A view-model row
---@param set string
local function FillRow(row, line, set)
  row.key, row.itemName, row.tip, row.menuTip = line.key, line.item, line.tip, line.menuTip
  local cells = row.cells[set]
  for _, c in ipairs(MainWindow.COLUMNS[set]) do
    local text = line[c[2]]
    if set == "pfWeek" and c[2] == "adjusted" then text = line.adjustedArrow end
    cells[c[2]]:SetText(MainWindow.MONEY_FIELDS[c[2]] and VM.WithCoins(text) or text)
  end
  if cells.action then
    Tint(cells.action, COLORS.black)
    local badge = BADGES[line.kind] or COLORS.muted
    row.badges[set]:SetColorTexture(badge[1], badge[2], badge[3])
  end
  Tint(cells.item, line.muted and COLORS.muted or QUALITY[line.quality] or QUALITY[1])
  if set == "pfWeek" then
    cells.item:SetText(line.item .. " " .. MUTED_ESCAPE .. line.qty .. "|r")
    row.sub:SetText(line.wait .. " · " .. line.premium)
  end
  if cells.status then
    cells.status:SetHeight((line.height or MainWindow.ROW_HEIGHT) - 2)
    Tint(cells.status, COLORS[line.statusKind] or COLORS.muted)
  end
  if cells.state then Tint(cells.state, STAGES[line.stateKind] or COLORS.muted) end
  if cells.net then Tint(cells.net, line.muted and COLORS.muted or COLORS[line.netColor] or COLORS.muted) end
  if cells.profit then Tint(cells.profit, COLORS[line.netColor] or COLORS.pos) end
  if cells.risk then Tint(cells.risk, line.riskAmber and COLORS.amber or COLORS.muted) end
  if line.dot and cells.chance then cells.chance:SetText(line.chance .. Dot(line.dot)) end
end

--- Place a row's note: under the Item column for a note line (UI14), the full width for a totals line, centred under
--- the title for the empty block.
---@param row table
---@param kind string
---@param set string
local function PlaceNote(row, kind, set)
  row.note:ClearAllPoints()
  row.dayLabel:ClearAllPoints()
  if kind == "note" then
    row.note:SetPoint("TOPLEFT", row.cells[set].item, "TOPLEFT", 0, 0)
    row.note:SetPoint("BOTTOMRIGHT", row, "BOTTOMRIGHT", -4, 0)
  elseif kind == "empty" then
    row.dayLabel:SetPoint("TOP", row, "TOP", 0, -6)
    row.note:SetPoint("TOPLEFT", row, "TOPLEFT", 4, -24)
    row.note:SetPoint("BOTTOMRIGHT", row, "BOTTOMRIGHT", -4, 2)
  else
    row.note:SetPoint("TOPLEFT", row, "TOPLEFT", 4, 0)
    row.note:SetPoint("BOTTOMRIGHT", row, "BOTTOMRIGHT", -4, 0)
    row.dayLabel:SetPoint("LEFT", row, "LEFT", 4, 0)
  end
  row.note:SetWordWrap(kind == "empty")
  row.note:SetJustifyH(kind == "empty" and "CENTER" or "LEFT")
end

--- Fill one pooled line: a row, a note, a totals line, a section heading, a column-heading line or the empty block.
---@param row table
---@param line table|nil
local function Fill(row, line)
  row:SetShown(line ~= nil)
  row.tip, row.menuTip = nil, nil
  if not line then return end
  local kind = line.lineKind or (line.noteLine and "note") or (line.header and "section") or "row"
  local set = line.set or TabSet()
  ShowParts(row, kind, set)
  PlaceNote(row, kind, set)
  if kind == "row" then
    FillRow(row, line, set)
  elseif kind == "note" then
    row.note:SetText(line.text)
  elseif kind == "foot" then
    row.note:SetText(VM.WithCoins(line.text))
  elseif kind == "section" then
    row.dayLabel:SetText(line.label)
    row.dayTotal:SetText(line.right or VM.WithCoins(line.total or ""))
    Tint(row.dayTotal, line.right and COLORS.muted or COLORS[line.netColor] or COLORS.muted)
  elseif kind == "empty" then
    row.dayLabel:SetText(line.title)
    row.note:SetText(VM.WithCoins(line.text))
  end
end

--- Draw the title, tabs, Min chance box, the fixed header strip or the Portfolio top line.
---@param nowMs number
local function RenderChrome(nowMs)
  local info = Adapter.PlayerInfo()
  ui.sub:SetText(info.realm .. " · " .. info.faction)
  local age = VM.Age(Market.Times(), nowMs)
  local tone = age.amber and COLORS.amber or COLORS.pos
  ui.age:SetText(age.text)
  Tint(ui.age, tone)
  Flat(ui.badge, COLORS.black, tone)
  for _, t in ipairs(MainWindow.TABS) do
    Flat(ui.tabs[t[1]], t[1] == view.tab and COLORS.tabOn or COLORS.tabOff, COLORS.border)
  end
  local top = computed and computed.top
  for _, set in ipairs(MainWindow.FIXED) do ui.headers[set]:SetShown(set == TabSet() and top == nil) end
  ui.top:SetShown(top ~= nil)
  if top then
    ui.topText:SetText(VM.WithCoins(top.text))
    Tint(ui.topText, top.amber and COLORS.amber or COLORS.text)
  end
  ui.chance:SetText("Min chance: " .. view.minChance .. "% " .. ARROW)
  ui.chance:SetShown(view.tab == "portfolio")
end

--- Compute the tab's lines; an engine error keeps the last good lines and says so in the footer (SPEC §4.3, A5).
---@param nowMs number
---@param gold number  All characters' gold, copper
---@return table       WindowLines.Lines' result
local function Compute(nowMs, gold)
  local problem = Market.Problem()
  if problem then return { lines = {}, footer = "", empty = problem } end
  local ok, result = pcall(WindowLines.Lines, view.tab, nowMs, gold, view.minChance)
  if ok then
    lastGood = result
    return result
  end
  local why = tostring(result):gsub("^.-:%d+: ", "")
  return { lines = lastGood and lastGood.lines or {}, footer = "couldn't update: " .. why .. " (showing the last good list)",
           empty = "couldn't update: " .. why, top = lastGood and lastGood.top, right = lastGood and lastGood.right }
end

--- Lay the page's lines over the pooled rows, each at its own height, and put a section's header strip over its
--- column-heading line.
---@param page table  ViewModel.PageByHeight's
local function LayOut(page)
  for _, set in ipairs(MainWindow.PF_SETS) do ui.pfHeaders[set]:Hide() end
  local y = 0
  for i, row in ipairs(ui.rows) do
    local line = page.lines[i]
    local h = line and line.height or MainWindow.ROW_HEIGHT
    row:ClearAllPoints()
    row:SetPoint("TOPLEFT", ui.list, "TOPLEFT", 0, -y)
    row:SetPoint("TOPRIGHT", ui.list, "TOPRIGHT", -14, -y)
    row:SetHeight(h)
    Fill(row, line)
    if line and line.lineKind == "colhead" then
      local header = ui.pfHeaders[line.set]
      header:ClearAllPoints()
      header:SetPoint("TOPLEFT", row, "TOPLEFT", 0, 0)
      header:SetPoint("TOPRIGHT", row, "TOPRIGHT", 0, 0)
      header:Show()
    end
    y = y + h
  end
end

--- Redraw the window from the view model.
function MainWindow.Render()
  if not ui then return end
  local nowMs = Adapter.NowMs()
  local gold = VM.TotalGold((DB.Get() or {}).characters)
  if not computed then
    -- Computing is the costly part (the addon's own options); scrolling only re-pages it.
    WindowLines.missingName = false
    computed = Compute(nowMs, gold)
  end
  RenderChrome(nowMs)
  local lines = computed.lines
  local track = MainWindow.ROWS * MainWindow.ROW_HEIGHT
  local page = VM.PageByHeight(lines, view.offset, track, MainWindow.ROWS, MainWindow.ROW_HEIGHT)
  view.offset = page.offset
  LayOut(page)
  if WindowLines.missingName then
    Events.Register("GET_ITEM_INFO_RECEIVED", MainWindow.OnItemInfo)   -- listen only while a name is missing
  else
    Events.Unregister("GET_ITEM_INFO_RECEIVED", MainWindow.OnItemInfo)
  end
  ui.empty:SetShown(#lines == 0)
  ui.empty:SetText(computed.empty)
  ui.footer:SetText(VM.FooterText(DB.SetupError(), Market.Problem(), Plan.Problem() or computed.problem, computed.footer))
  ui.budget:SetText(computed.right and VM.WithCoins(computed.right) or ("Budget " .. VM.WithCoins(VM.Money(gold))))
  ui.track:SetShown(page.scrollable)
  ui.thumb:SetShown(page.scrollable)
  if page.scrollable then
    ui.thumb:ClearAllPoints()
    ui.thumb:SetHeight(track * page.thumbSize)
    ui.thumb:SetPoint("TOPRIGHT", ui.list, "TOPRIGHT", -1, -track * page.thumbTop)
  end
end

--- Show a Portfolio tooltip (an excluded row's, UI19) on a row: names and reasons coloured as the mockup does.
---@param owner table
---@param tip table  PortfolioView.ExcludedTooltip's
function MainWindow.ShowTip(owner, tip)
  local lines = {}
  for i, line in ipairs(tip.lines) do
    if line.text then
      lines[i] = { text = line.text, rgb = COLORS[line.colour] or COLORS.white, wrap = line.wrap }
    else
      lines[i] = { left = line.left, right = VM.WithCoins(line.right), leftRgb = COLORS.muted, rightRgb = COLORS.white }
    end
  end
  Adapter.ShowTooltipLines(owner, tip.title, lines)
end

--- This character's Today rows (the plan's posts due now and the addon's own options), for the AH panel's post list,
--- with the window's Min chance filter (50% until changed).
---@param charId string
---@return table[]  ViewModel.Today rows
function MainWindow.TodayRows(charId)
  return WindowLines.TodayRows(charId, view.minChance)
end

--- This character's Today list with its count and expected total at the window's Min chance (the minimap tooltip, UI20).
---@param charId string
---@return table  WindowLines.Today's result
function MainWindow.Today(charId)
  return WindowLines.Today(charId, view.minChance)
end

--- Switch tabs and redraw from the top.
---@param tab string  A MainWindow.TABS key
function MainWindow.ShowTab(tab)
  view.tab, view.offset = tab, 0
  MainWindow.Refresh()
end

--- Step the Min chance filter: 0 → 25 → 50 → 75 → 0 (SPEC §4.3).
function MainWindow.NextMinChance()
  for i, step in ipairs(MainWindow.CHANCE_STEPS) do
    if step == view.minChance then
      view.minChance = MainWindow.CHANCE_STEPS[i % #MainWindow.CHANCE_STEPS + 1]
      break
    end
  end
  MainWindow.Refresh()
end

--- Scroll the list by some lines.
---@param lines number  Positive scrolls down
function MainWindow.Scroll(lines)
  view.offset = view.offset + lines
  MainWindow.Render()
end

--- Recompute what the tab shows, then redraw (something it depends on changed).
function MainWindow.Refresh()
  computed = nil
  MainWindow.Render()
end

--- Open or close the window; /ahp portfolio opens the Portfolio tab, /ahp minimap hides or shows the minimap button
--- (SPEC §4.3).
---@param args string  The rest of the slash command line
function MainWindow.Toggle(args)
  if args == "perf" then return MainWindow.Perf() end
  if args == "minimap" then return AHProfit.MinimapButton.ToggleShown() end   -- UI23
  if not ui then Build() end
  if args == "portfolio" then
    view.tab, view.offset = args, 0
    ui.frame:Show()
    MainWindow.Refresh()
  elseif ui.frame:IsShown() then
    ui.frame:Hide()
  else
    ui.frame:Show()
    MainWindow.Refresh()
  end
end

--- /ahp perf: the real numbers against the budgets (SPEC §5.1): the last scan's slowest steps and the live
--- portfolio's last repricing and reselection.
function MainWindow.Perf()
  local s = AHProfit.Scan.Replicate.Stats()
  Adapter.Print(string.format("scan: slowest step %.2f ms, redraw %.2f ms (budget 4 ms)", s.maxStepMs or 0, s.notifyMs or 0))
  for _, line in ipairs(AHProfit.Adapt.PerfLines()) do Adapter.Print(line .. " (budget 2 ms)") end
  Adapter.Print(AHProfit.Capture.Gather.PerfLine() .. " (one table write per window)")
end

--- The window's frames, for the layout snapshots (tests).
---@return table|nil
function MainWindow.Frames()
  return ui
end

--- Forget the view state (tests).
function MainWindow.Reset()
  ui, view, computed, lastGood = nil, { tab = "portfolio", offset = 0, minChance = 50 }, nil, nil
end

--- /ahp: open the window; a failure becomes a chat line, never a Lua error popup (SPEC §4.3, A5).
---@param args string  The rest of the command line
function MainWindow.OnSlash(args)
  local ok, err = pcall(MainWindow.Toggle, args)
  if not ok then Adapter.Print("the window failed: " .. tostring(err)) end
end

Adapter.RegisterSlash("AHPROFIT", { "/ahp", "/ahprofit" }, function(args) MainWindow.OnSlash(args) end)
-- A finished AH scan re-prices and re-ranks the open window (SPEC §4.0, A2).
AHProfit.Scan.Replicate.OnBook(function() if ui and ui.frame:IsShown() then MainWindow.Refresh() end end)
AHProfit.RowMenu.OnChange(function() if ui and ui.frame:IsShown() then MainWindow.Refresh() end end)
Inventory.OnChange(function() MainWindow.OnBags() end)
