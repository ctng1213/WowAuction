-- UI/MainWindow.lua: the /ahp window (SPEC §4.3, docs/mockups/addon.html).
-- Frame code only: every text, colour flag and number comes from UI/ViewModel.lua. Frames
-- are built the first time the window opens (SPEC §5.1 lazy frames) and its rows are a fixed
-- pool that scrolls, so the window never grows past its size.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local DB = AHProfit.DB
local Market = AHProfit.Market
local Plan = AHProfit.Plan
local VM = AHProfit.ViewModel

local MainWindow = {}
AHProfit.MainWindow = MainWindow

MainWindow.WIDTH, MainWindow.HEIGHT = 640, 440
MainWindow.MIN_WIDTH, MainWindow.MIN_HEIGHT = 560, 360
MainWindow.ROWS, MainWindow.ROW_HEIGHT = 12, 20
MainWindow.GAP = 3
MainWindow.CHANCE_STEPS = { 0, 25, 50, 75 }
MainWindow.TABS = { { "today", "Today" }, { "week", "Week" } }
MainWindow.MONEY_FIELDS = { each = true, total = true, net = true }

-- { header, row field, width (nil = fills), justify }; columns after the filling one anchor from the right.
MainWindow.COLUMNS = {
  today = { { "", "action", 52, "LEFT" }, { "Item", "item", nil, "LEFT" }, { "Qty", "qty", 28, "RIGHT" },
            { "Each", "each", 52, "RIGHT" }, { "Total", "total", 56, "RIGHT" }, { "Net", "net", 70, "RIGHT" },
            { "Chance", "chance", 46, "RIGHT" }, { "Volume", "volume", 42, "RIGHT" }, { "Risk", "risk", 34, "RIGHT" },
            { "Who", "who", 60, "LEFT" } },
  week = { { "Time", "time", 34, "LEFT" }, { "", "action", 52, "LEFT" }, { "Item", "item", nil, "LEFT" },
           { "Qty", "qty", 28, "RIGHT" }, { "Total", "total", 56, "RIGHT" }, { "Net", "net", 70, "RIGHT" },
           { "Chance", "chance", 46, "RIGHT" }, { "Who", "who", 60, "LEFT" } },
}

-- The mockup's palette (docs/mockups/addon.html :root), as 0-1 RGB.
local COLORS = { pos = { 0.24, 0.86, 0.31 }, neg = { 1, 0.29, 0.24 }, muted = { 0.56, 0.54, 0.49 },
                 amber = { 1, 0.69, 0.18 }, text = { 0.91, 0.89, 0.82 }, title = { 1, 0.82, 0 },
                 border = { 0.42, 0.35, 0.2 }, panel = { 0.05, 0.05, 0.06 }, strip = { 0.13, 0.13, 0.09 },
                 line = { 0.13, 0.13, 0.13 }, hover = { 0.12, 0.11, 0.07 }, tabOn = { 0.16, 0.15, 0.09 },
                 tabOff = { 0.07, 0.07, 0.08 }, black = { 0, 0, 0 }, track = { 0.1, 0.1, 0.1 }, thumb = { 0.35, 0.29, 0.15 } }
local QUALITY = { [0] = { 0.62, 0.62, 0.62 }, [1] = { 1, 1, 1 }, [2] = { 0.12, 1, 0 }, [3] = { 0, 0.44, 0.87 },
                  [4] = { 0.64, 0.21, 0.93 }, [5] = { 1, 0.5, 0 } }
local BADGES = { buy = { 0.37, 0.71, 1 }, post = { 1, 0.82, 0 }, de = { 0.7, 0.48, 1 }, craft = { 1, 0.6, 0.24 },
                 vendor = { 0.67, 0.67, 0.67 }, mail = { 0.56, 0.83, 1 }, hold = { 1, 0.69, 0.18 } }
local DOTS = { high = COLORS.pos, medium = COLORS.amber, low = COLORS.muted }
local FLAT = "Interface\\Buttons\\WHITE8X8"
-- The Min chance box's arrow: the chat frame's scroll-down icon, which every client has.
local ARROW = "|TInterface\\ChatFrame\\UI-ChatIcon-ScrollDown-Up:14:14|t"

local ui = nil                     -- the frames, once built
local view = { tab = "today", offset = 0, minChance = 50 }

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
  --- Make and remember one column's cell.
  ---@param c table  The column
  ---@return table
  local function Make(c)
    cells[c[2]] = MakeCell(parent, c, kind)
    return cells[c[2]]
  end
  local left = nil
  for i = 1, fill - 1 do
    local cell = Make(columns[i])
    cell:SetWidth(columns[i][3])
    if left then cell:SetPoint("LEFT", left, "RIGHT", MainWindow.GAP, 0) else cell:SetPoint("LEFT", parent, "LEFT", 4, 0) end
    left = cell
  end
  local right = nil
  for i = #columns, fill + 1, -1 do
    local cell = Make(columns[i])
    cell:SetWidth(columns[i][3])
    if right then cell:SetPoint("RIGHT", right, "LEFT", -MainWindow.GAP, 0) else cell:SetPoint("RIGHT", parent, "RIGHT", -4, 0) end
    right = cell
  end
  local item = Make(columns[fill])
  item:SetPoint("TOPLEFT", left or parent, left and "TOPRIGHT" or "TOPLEFT", MainWindow.GAP, 0)
  item:SetPoint("BOTTOMRIGHT", right or parent, right and "BOTTOMLEFT" or "BOTTOMRIGHT", -MainWindow.GAP, 0)
  return cells
end

--- Show a column's tooltip while its header is hovered, and colour the headers gold (SPEC §4.3).
---@param cells table   field -> header button
---@param columns table The tab's columns
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

--- The coloured action badge behind each tab's action cell (the mockup's .act).
---@param row table
local function BuildBadges(row)
  row.badges = {}
  for _, t in ipairs(MainWindow.TABS) do
    local cell = row.cells[t[1]].action
    local badge = row:CreateTexture(nil, "ARTWORK")
    badge:SetSize(48, 12)
    badge:SetPoint("LEFT", cell, "LEFT", 0, 0)
    cell:SetJustifyH("CENTER")
    cell:SetWidth(48)
    row.badges[t[1]] = badge
  end
end

--- One pooled list line: a cell set per tab, an action badge, a confidence dot, a note line and a day header.
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
  row:SetScript("OnEnter", function(self) self.hover:SetShown(self.isRow == true) end)
  row:SetScript("OnLeave", function(self) self.hover:Hide() end)
  row.cells = {}
  for _, tab in ipairs(MainWindow.TABS) do row.cells[tab[1]] = BuildCells(row, MainWindow.COLUMNS[tab[1]], "FontString") end
  BuildBadges(row)
  row.note = row:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  row.note:SetPoint("TOPLEFT", row, "TOPLEFT", 59, 0)
  row.note:SetPoint("BOTTOMRIGHT", row, "BOTTOMRIGHT", -4, 0)
  row.note:SetWordWrap(false)
  row.note:SetJustifyH("LEFT")
  row.dayLabel = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  row.dayLabel:SetPoint("LEFT", row, "LEFT", 4, 0)
  row.dayTotal = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")
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
    button:SetSize(72, 22)
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

--- Build the list area: column headers per tab, the pooled rows, the scroll bar and the empty state.
---@param frame table
local function BuildList(frame)
  ui.headers, ui.headerCells = {}, {}
  for _, t in ipairs(MainWindow.TABS) do
    local tab, columns = t[1], MainWindow.COLUMNS[t[1]]
    local header = Adapter.CreateFrame("Frame", "AHProfitWindowHeader" .. tab, frame)
    header:SetHeight(MainWindow.ROW_HEIGHT)
    header:SetPoint("TOPLEFT", frame, "TOPLEFT", 10, -60)
    header:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -24, -60)
    ui.headerCells[tab] = BuildCells(header, columns, "Button")
    HookHeaders(ui.headerCells[tab], columns)
    ui.headers[tab] = header
  end
  local list = Adapter.CreateFrame("Frame", "AHProfitWindowList", frame)
  list:SetHeight(MainWindow.ROWS * MainWindow.ROW_HEIGHT)
  list:SetPoint("TOPLEFT", frame, "TOPLEFT", 10, -82)
  list:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -10, -82)
  list:EnableMouseWheel(true)
  list:SetScript("OnMouseWheel", function(_, delta) MainWindow.Scroll(-delta * 3) end)
  ui.list, ui.rows = list, {}
  for i = 1, MainWindow.ROWS do ui.rows[i] = BuildRow(list, i) end
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

--- An item's look for the view model: saved name, cache name and market activity.
---@param key number
---@return table
local function LookOf(key)
  local saved = (DB.Get() or {}).itemNames
  local name, quality = Adapter.ItemInfo(VM.BaseItemId(key))
  return VM.Look(key, Market.Item(key), saved and saved[key], name, quality)
end

--- Show only what a kind of line uses: action cells, a note, or a day header.
---@param row table
---@param kind string  "row" | "note" | "day"
local function ShowParts(row, kind)
  for _, t in ipairs(MainWindow.TABS) do
    local active = t[1] == view.tab and kind == "row"
    for _, cell in pairs(row.cells[t[1]]) do cell:SetShown(active) end
    row.badges[t[1]]:SetShown(active)
  end
  row.note:SetShown(kind == "note")
  row.dayLabel:SetShown(kind == "day")
  row.dayTotal:SetShown(kind == "day")
  row.dayRule:SetShown(kind == "day")
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

--- Fill an action line's cells, badge and dot.
---@param row table
---@param line table  A view-model row
local function FillRow(row, line)
  local cells = row.cells[view.tab]
  for _, c in ipairs(MainWindow.COLUMNS[view.tab]) do
    local text = line[c[2]]
    cells[c[2]]:SetText(MainWindow.MONEY_FIELDS[c[2]] and VM.WithCoins(text) or text)
  end
  Tint(cells.action, COLORS.black)
  local badge = BADGES[line.kind] or COLORS.muted
  row.badges[view.tab]:SetColorTexture(badge[1], badge[2], badge[3])
  Tint(cells.item, QUALITY[line.quality] or QUALITY[1])
  Tint(cells.net, COLORS[line.netColor])
  if cells.risk then Tint(cells.risk, line.riskAmber and COLORS.amber or COLORS.muted) end
  if line.dot then cells.chance:SetText(line.chance .. Dot(line.dot)) end
end

--- Fill one pooled line from the list: an action row, its note, or a Week day header.
---@param row table
---@param line table|nil
local function Fill(row, line)
  row:SetShown(line ~= nil)
  if not line then return end
  if line.noteLine then
    ShowParts(row, "note")
    row.note:SetText(line.text)
  elseif line.header then
    ShowParts(row, "day")
    row.dayLabel:SetText(line.label)
    row.dayTotal:SetText(VM.WithCoins(line.total))
    Tint(row.dayTotal, COLORS[line.netColor])
  else
    ShowParts(row, "row")
    FillRow(row, line)
  end
end

--- The lines the current tab shows, its footer and its empty-state text.
---@param nowMs number
---@param gold number  All your gold, copper
---@return table[], string, string
local function Lines(nowMs, gold)
  local offset = Adapter.LocalOffsetMs(nowMs)
  local actions = Plan.AllActions()
  if view.tab == "today" then
    local today = VM.Today(actions, nowMs, offset, view.minChance, LookOf, gold)
    return VM.WithNotes(today.rows), VM.TodayFooter(today, view.minChance), "Nothing to do in the next hour."
  end
  local week = VM.Week(actions, nowMs, offset, LookOf, gold)
  return VM.WithNotes(VM.WeekLines(week)), "Week total " .. week.total, "Nothing planned this week."
end

--- Draw the title, tabs and Min chance box for the current state.
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
    local on = t[1] == view.tab
    Flat(ui.tabs[t[1]], on and COLORS.tabOn or COLORS.tabOff, COLORS.border)
    ui.headers[t[1]]:SetShown(on)
  end
  ui.chance:SetText("Min chance: " .. view.minChance .. "% " .. ARROW)
  ui.chance:SetShown(view.tab == "today")
end

--- Redraw the window from the view model.
function MainWindow.Render()
  if not ui then return end
  local nowMs = Adapter.NowMs()
  RenderChrome(nowMs)
  local gold = VM.TotalGold((DB.Get() or {}).characters)
  local problem = Market.Problem()
  local lines, footer, emptyText = {}, "", problem
  if not problem then lines, footer, emptyText = Lines(nowMs, gold) end
  local page = VM.Page(lines, view.offset, MainWindow.ROWS)
  view.offset = page.offset
  for i, row in ipairs(ui.rows) do Fill(row, page.lines[i]) end
  ui.empty:SetShown(#lines == 0)
  ui.empty:SetText(emptyText)
  ui.footer:SetText(Plan.Problem() or VM.WithCoins(footer))
  ui.budget:SetText("Budget " .. VM.WithCoins(VM.Money(gold)))
  ui.track:SetShown(page.scrollable)
  ui.thumb:SetShown(page.scrollable)
  if page.scrollable then
    local track = MainWindow.ROWS * MainWindow.ROW_HEIGHT
    ui.thumb:ClearAllPoints()
    ui.thumb:SetHeight(track * page.thumbSize)
    ui.thumb:SetPoint("TOPRIGHT", ui.list, "TOPRIGHT", -1, -track * page.thumbTop)
  end
end

--- Switch tabs and redraw from the top.
---@param tab string  "today" | "week"
function MainWindow.ShowTab(tab)
  view.tab, view.offset = tab, 0
  MainWindow.Render()
end

--- Step the Min chance filter: 0 → 25 → 50 → 75 → 0 (SPEC §4.3).
function MainWindow.NextMinChance()
  for i, step in ipairs(MainWindow.CHANCE_STEPS) do
    if step == view.minChance then
      view.minChance = MainWindow.CHANCE_STEPS[i % #MainWindow.CHANCE_STEPS + 1]
      break
    end
  end
  MainWindow.Render()
end

--- Scroll the list by some lines.
---@param lines number  Positive scrolls down
function MainWindow.Scroll(lines)
  view.offset = view.offset + lines
  MainWindow.Render()
end

--- Open or close the window; /ahp week opens the Week tab (SPEC §4.3).
---@param args string  The rest of the slash command line
function MainWindow.Toggle(args)
  if not ui then Build() end
  if args == "week" or args == "today" then
    view.tab, view.offset = args, 0
    ui.frame:Show()
    MainWindow.Render()
  elseif ui.frame:IsShown() then
    ui.frame:Hide()
  else
    ui.frame:Show()
    MainWindow.Render()
  end
end

--- The window's frames, for the layout snapshots (tests).
---@return table|nil
function MainWindow.Frames()
  return ui
end

--- Forget the view state (tests).
function MainWindow.Reset()
  ui, view = nil, { tab = "today", offset = 0, minChance = 50 }
end

--- /ahp: open the window; a failure becomes a chat line, never a Lua error popup (SPEC §4.3, A5).
---@param args string  The rest of the command line
function MainWindow.OnSlash(args)
  local ok, err = pcall(MainWindow.Toggle, args)
  if not ok then Adapter.Print("the window failed: " .. tostring(err)) end
end

Adapter.RegisterSlash("AHPROFIT", { "/ahp", "/ahprofit" }, function(args) MainWindow.OnSlash(args) end)
