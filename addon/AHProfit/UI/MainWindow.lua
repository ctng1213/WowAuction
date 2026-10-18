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

-- { header, row field, width (nil = fills), justify }; columns after the filling one anchor from the right.
MainWindow.COLUMNS = {
  today = { { "", "action", 44, "LEFT" }, { "Item", "item", nil, "LEFT" }, { "Qty", "qty", 30, "RIGHT" },
            { "Each", "each", 46, "RIGHT" }, { "Total", "total", 50, "RIGHT" }, { "Net", "net", 62, "RIGHT" },
            { "Chance", "chance", 48, "RIGHT" }, { "Volume", "volume", 44, "RIGHT" }, { "Risk", "risk", 36, "RIGHT" },
            { "Who", "who", 40, "LEFT" } },
  week = { { "Time", "time", 34, "LEFT" }, { "", "action", 44, "LEFT" }, { "Item", "item", nil, "LEFT" },
           { "Qty", "qty", 30, "RIGHT" }, { "Total", "total", 50, "RIGHT" }, { "Net", "net", 62, "RIGHT" },
           { "Chance", "chance", 48, "RIGHT" }, { "Who", "who", 40, "LEFT" } },
}
local COLORS = { pos = { 0.24, 0.86, 0.31 }, neg = { 1, 0.29, 0.24 }, muted = { 0.56, 0.54, 0.49 },
                 amber = { 1, 0.69, 0.18 }, text = { 0.91, 0.89, 0.82 }, gold = { 1, 0.82, 0 } }
local QUALITY = { [0] = { 0.62, 0.62, 0.62 }, [1] = { 1, 1, 1 }, [2] = { 0.12, 1, 0 }, [3] = { 0, 0.44, 0.87 },
                  [4] = { 0.64, 0.21, 0.93 }, [5] = { 1, 0.5, 0 } }
local DOTS = { high = COLORS.pos, medium = COLORS.amber, low = COLORS.muted }

local ui = nil                     -- the frames, once built
local view = { tab = "today", offset = 0, minChance = 50 }

--- Colour a font string or texture from an { r, g, b } triple.
---@param region table
---@param rgb table
local function Paint(region, rgb)
  if region.SetTextColor then region:SetTextColor(rgb[1], rgb[2], rgb[3]) else region:SetColorTexture(rgb[1], rgb[2], rgb[3]) end
end

--- One cell: a font string in a row, or a button in a header (headers take hover for tooltips).
---@param parent table  The row or header
---@param c table       Its column { header, field, width, justify }
---@param kind string   "FontString" | "Button"
---@return table
local function MakeCell(parent, c, kind)
  local cell
  if kind == "Button" then
    cell = Adapter.CreateFrame("Button", nil, parent)
    cell:SetNormalFontObject("GameFontNormalSmall")
    cell:SetText(c[1])
  else
    cell = parent:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    cell:SetWordWrap(false)
    cell:SetJustifyH(c[4])
  end
  cell:SetHeight(MainWindow.ROW_HEIGHT)
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

--- Show a column's tooltip while its header is hovered (SPEC §4.3).
---@param cells table   field -> header button
---@param columns table The tab's columns
local function HookHeaderTips(cells, columns)
  for _, c in ipairs(columns) do
    local help = VM.COLUMN_HELP[c[1]]
    if help then
      local cell = cells[c[2]]
      cell:SetScript("OnEnter", function(self) Adapter.ShowTooltip(self, c[1], help) end)
      cell:SetScript("OnLeave", function() Adapter.HideTooltip() end)
    end
  end
end

--- One pooled list row: a cell set per tab, a confidence dot and a day-header line (Week).
---@param list table  The list frame
---@param i number    Row number, from 1
---@return table      The row
local function BuildRow(list, i)
  local row = Adapter.CreateFrame("Frame", "AHProfitWindowRow" .. i, list)
  row:SetHeight(MainWindow.ROW_HEIGHT)
  row:SetPoint("TOPLEFT", list, "TOPLEFT", 0, -(i - 1) * MainWindow.ROW_HEIGHT)
  row:SetPoint("TOPRIGHT", list, "TOPRIGHT", -14, -(i - 1) * MainWindow.ROW_HEIGHT)
  row.cells = {}
  for _, tab in ipairs(MainWindow.TABS) do row.cells[tab[1]] = BuildCells(row, MainWindow.COLUMNS[tab[1]], "FontString") end
  row.dot = row:CreateTexture(nil, "OVERLAY")
  row.dot:SetSize(6, 6)
  row.dayLabel = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  row.dayLabel:SetPoint("LEFT", row, "LEFT", 4, 0)
  row.dayTotal = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  row.dayTotal:SetPoint("RIGHT", row, "RIGHT", -4, 0)
  return row
end

--- Build the title bar: name, realm and faction, the age badge and the close button.
---@param frame table
local function BuildTitle(frame)
  ui.title = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  ui.title:SetPoint("TOPLEFT", frame, "TOPLEFT", 12, -10)
  ui.title:SetText("AHProfit")
  ui.sub = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  ui.sub:SetPoint("LEFT", ui.title, "RIGHT", 8, 0)
  ui.close = Adapter.CreateFrame("Button", "AHProfitWindowClose", frame, "UIPanelCloseButton")
  ui.close:SetSize(24, 24)
  ui.close:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -4, -4)
  ui.close:SetScript("OnClick", function() frame:Hide() end)
  ui.age = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  ui.age:SetPoint("RIGHT", ui.close, "LEFT", -6, 0)
end

--- Build the tab buttons and the Min chance stepper.
---@param frame table
local function BuildTabs(frame)
  ui.tabs = {}
  local previous = nil
  for _, tab in ipairs(MainWindow.TABS) do
    local button = Adapter.CreateFrame("Button", "AHProfitWindowTab" .. tab[2], frame)
    button:SetSize(72, 22)
    button:SetNormalFontObject("GameFontNormalSmall")
    button:SetText(tab[2])
    if previous then button:SetPoint("LEFT", previous, "RIGHT", 2, 0) else button:SetPoint("TOPLEFT", frame, "TOPLEFT", 10, -32) end
    button:SetScript("OnClick", function() MainWindow.ShowTab(tab[1]) end)
    ui.tabs[tab[1]] = button
    previous = button
  end
  ui.chance = Adapter.CreateFrame("Button", "AHProfitWindowMinChance", frame)
  ui.chance:SetSize(120, 22)
  ui.chance:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -10, -32)
  ui.chance:SetNormalFontObject("GameFontNormalSmall")
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
    header:SetPoint("TOPLEFT", frame, "TOPLEFT", 10, -58)
    header:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -24, -58)
    ui.headerCells[tab] = BuildCells(header, columns, "Button")
    HookHeaderTips(ui.headerCells[tab], columns)
    ui.headers[tab] = header
  end
  local list = Adapter.CreateFrame("Frame", "AHProfitWindowList", frame)
  list:SetHeight(MainWindow.ROWS * MainWindow.ROW_HEIGHT)
  list:SetPoint("TOPLEFT", frame, "TOPLEFT", 10, -80)
  list:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -10, -80)
  list:EnableMouseWheel(true)
  list:SetScript("OnMouseWheel", function(_, delta) MainWindow.Scroll(-delta * 3) end)
  ui.list, ui.rows = list, {}
  for i = 1, MainWindow.ROWS do ui.rows[i] = BuildRow(list, i) end
  ui.track = list:CreateTexture(nil, "BACKGROUND")
  ui.track:SetWidth(8)
  ui.track:SetPoint("TOPRIGHT", list, "TOPRIGHT", 0, 0)
  ui.track:SetPoint("BOTTOMRIGHT", list, "BOTTOMRIGHT", 0, 0)
  ui.track:SetColorTexture(0.1, 0.1, 0.1)
  ui.thumb = list:CreateTexture(nil, "ARTWORK")
  ui.thumb:SetWidth(8)
  ui.thumb:SetColorTexture(0.35, 0.29, 0.15)
  ui.empty = list:CreateFontString(nil, "OVERLAY", "GameFontDisable")
  ui.empty:SetPoint("CENTER", list, "CENTER", 0, 0)
  ui.footer = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  ui.footer:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 12, 10)
end

--- Build the window the first time it opens.
local function Build()
  local frame = Adapter.CreateFrame("Frame", "AHProfitWindow", UIParent, "BackdropTemplate")
  frame:SetSize(MainWindow.WIDTH, MainWindow.HEIGHT)
  frame:SetPoint("CENTER", UIParent, "CENTER", 0, 0)
  frame:SetBackdrop({ bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background-Dark",
                      edgeFile = "Interface\\DialogFrame\\UI-DialogBox-Gold-Border", edgeSize = 16,
                      insets = { left = 4, right = 4, top = 4, bottom = 4 } })
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

--- Fill one pooled row from a line (a view-model row, or a Week day header).
---@param row table
---@param line table|nil
local function Fill(row, line)
  row:SetShown(line ~= nil)
  if not line then return end
  for _, t in ipairs(MainWindow.TABS) do
    for _, cell in pairs(row.cells[t[1]]) do cell:SetShown(t[1] == view.tab and not line.header) end
  end
  row.dayLabel:SetShown(line.header == true)
  row.dayTotal:SetShown(line.header == true)
  row.dot:SetShown(not line.header and line.dot ~= nil)
  if line.header then
    row.dayLabel:SetText(line.label)
    row.dayTotal:SetText(line.total)
    Paint(row.dayTotal, COLORS[line.netColor])
    return
  end
  local cells = row.cells[view.tab]
  for _, c in ipairs(MainWindow.COLUMNS[view.tab]) do cells[c[2]]:SetText(line[c[2]]) end
  Paint(cells.item, QUALITY[line.quality] or QUALITY[1])
  Paint(cells.net, COLORS[line.netColor])
  if cells.risk then Paint(cells.risk, line.riskAmber and COLORS.amber or COLORS.muted) end
  if line.dot then
    row.dot:ClearAllPoints()
    row.dot:SetPoint("RIGHT", cells.chance, "RIGHT", 0, 0)
    Paint(row.dot, DOTS[line.dot])
  end
end

--- The lines the current tab shows, its footer and its empty-state text.
---@param nowMs number
---@return table[], string, string
local function Lines(nowMs)
  local offset = Adapter.LocalOffsetMs(nowMs)
  local gold = VM.TotalGold((DB.Get() or {}).characters)
  local actions = Plan.AllActions()
  if view.tab == "today" then
    local today = VM.Today(actions, nowMs, offset, view.minChance, LookOf, gold)
    return today.rows, VM.TodayFooter(today, view.minChance), "Nothing to do in the next hour."
  end
  local week = VM.Week(actions, nowMs, offset, LookOf, gold)
  return VM.WeekLines(week), "Week total " .. week.total, "Nothing planned this week."
end

--- Redraw the window from the view model.
function MainWindow.Render()
  if not ui then return end
  local nowMs = Adapter.NowMs()
  local info = Adapter.PlayerInfo()
  ui.sub:SetText(info.realm .. " · " .. info.faction)
  local age = VM.Age(Market.Times(), nowMs)
  ui.age:SetText(age.text)
  Paint(ui.age, age.amber and COLORS.amber or COLORS.pos)
  ui.chance:SetText("Min chance " .. view.minChance .. "%")
  ui.chance:SetShown(view.tab == "today")
  for _, t in ipairs(MainWindow.TABS) do ui.headers[t[1]]:SetShown(t[1] == view.tab) end
  local problem = Market.Problem()
  local lines, footer, emptyText = {}, "", problem
  if not problem then lines, footer, emptyText = Lines(nowMs) end
  local page = VM.Page(lines, view.offset, MainWindow.ROWS)
  view.offset = page.offset
  for i, row in ipairs(ui.rows) do Fill(row, page.lines[i]) end
  ui.empty:SetShown(#lines == 0)
  ui.empty:SetText(emptyText)
  ui.footer:SetText(Plan.Problem() or footer)
  ui.track:SetShown(page.scrollable)
  ui.thumb:SetShown(page.scrollable)
  if page.scrollable then
    local track = MainWindow.ROWS * MainWindow.ROW_HEIGHT
    ui.thumb:ClearAllPoints()
    ui.thumb:SetHeight(track * page.thumbSize)
    ui.thumb:SetPoint("TOPRIGHT", ui.list, "TOPRIGHT", 0, -track * page.thumbTop)
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
