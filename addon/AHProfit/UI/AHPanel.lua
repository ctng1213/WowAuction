-- UI/AHPanel.lua: the AHProfit panel docked to the right of the auction house (SPEC §4.3, UI7, A24).
-- It lists what to buy (AHSync's planned buys and the disenchant targets your enchanter can do)
-- with each one's state from the live data (the last scan, or a search), and what to post now.
-- Clicking a row searches for it; Next search steps through the rows still checking; Full scan
-- asks for a ReplicateItems scan. Frame code only: ViewModel.AHPanel makes the text.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local DB = AHProfit.DB
local Events = AHProfit.Events
local Market = AHProfit.Market
local Plan = AHProfit.Plan
local Live = AHProfit.Live
local Options = AHProfit.Options
local Replicate = AHProfit.Scan.Replicate
local VM = AHProfit.ViewModel

local AHPanel = {}
AHProfit.AHPanel = AHPanel

AHPanel.WIDTH, AHPanel.ROW_HEIGHT, AHPanel.SHOP_ROWS, AHPanel.POST_ROWS = 300, 20, 10, 5
-- Columns: { header, x (from the row's left), width, justify, row field }; the same for rows and headers.
-- Shopping rows start after the state icon; post rows have none.
AHPanel.COLUMNS = {
  shopping = { { "Item", 18, 128, "LEFT" }, { "Price", 150, 48, "RIGHT" }, { "Max", 202, 44, "RIGHT" },
               { "Qty", 254, 30, "RIGHT" } },
  post = { { "Item", 0, 82, "LEFT", "item" }, { "List price", 84, 58, "RIGHT", "each" }, { "Qty", 144, 26, "RIGHT", "qty" },
           { "Est. profit", 172, 64, "RIGHT", "postNet" }, { "Chance", 238, 46, "RIGHT", "chance" } },
}
local MONEY = { each = true, postNet = true }
AHPanel.ICONS = { found = "Interface\\RaidFrame\\ReadyCheck-Ready", gone = "Interface\\RaidFrame\\ReadyCheck-NotReady",
                  checking = "Interface\\RaidFrame\\ReadyCheck-Waiting" }
local FLAT = "Interface\\Buttons\\WHITE8X8"
local GOLD, MUTED, BORDER = { 1, 0.82, 0 }, { 0.56, 0.54, 0.49 }, { 0.42, 0.35, 0.2 }

local ui = nil
local searchedThisVisit = {}      -- key -> true: searched since the AH opened (FM24: dropped on close)
local current = nil               -- the panel's last view, for Next search
local pendingSell = nil           -- { price, untilMs }: put the price back once after the AH's own (A29)
AHPanel.REFILL_MS = 10000

--- An item's look (name, quality, market activity), as the window builds it.
---@param key number
---@return table
local function LookOf(key)
  local saved = (DB.Get() or {}).itemNames
  local name, quality = Adapter.ItemInfo(VM.BaseItemId(key))
  return VM.Look(key, Market.Item(key), saved and saved[key], name, quality)
end

--- Live levels for an item, or nil when nothing newer than the market file has been seen.
---@param key number
---@return table[]|nil
local function LevelsOf(key)
  local _, newest = Market.Times()
  return Live.Item(key, newest)
end

--- What to shop for: this character's planned buys, then disenchant targets your enchanter can do.
---@param charId string
---@return table[]  { key, qty, price }
local function Targets(charId)
  local out, seen = {}, {}
  for _, a in ipairs(Plan.CharacterActions(charId)) do
    if a.kind == "buy" and a.price and not a.vendor and not seen[a.key] then   -- vendor buys aren't on the AH (A32)
      out[#out + 1], seen[a.key] = { key = a.key, qty = a.qty, price = a.price }, true
    end
  end
  local best = Options.BestSkill(DB.Get() or {}, "Enchanting")
  if best then
    for _, t in ipairs(Options.Disenchant(Market.Disenchantable(), { itemOf = Market.Item, levelsOf = LevelsOf })) do
      if t.skill <= best and not seen[t.key] and #out < AHPanel.SHOP_ROWS then
        out[#out + 1], seen[t.key] = { key = t.key, qty = 1, price = math.floor(t.ev) }, true
      end
    end
  end
  return out
end

--- What to post now: this character's Today posts for items in its bags (A30).
---@param charId string
---@return table[]  Today rows
local function Posts(charId)
  local bags = (((DB.Get() or {}).inventory or {})[charId] or {}).bags
  local held = Options.Units(AHProfit.Capture.Inventory.Decode(bags))
  return VM.PostableRows(AHProfit.MainWindow.TodayRows(charId), held, AHPanel.POST_ROWS)
end

--- Explain a button or row on hover.
---@param button table
---@param title string
---@param text string
local function Explain(button, title, text)
  button:SetScript("OnEnter", function(self) Adapter.ShowTooltip(self, title, text) end)
  button:SetScript("OnLeave", function() Adapter.HideTooltip() end)
end

--- One pooled panel row: a state icon, the item and its cells; a click searches it or, on Post, fills the Sell tab.
---@param parent table
---@param i number
---@param y number  Pixels down from the panel's top
---@param columns table  AHPanel.COLUMNS.shopping or .post
---@return table
local function BuildRow(parent, i, y, columns)
  local row = Adapter.CreateFrame("Button", "AHProfitPanelRow" .. i, parent)
  row:SetHeight(AHPanel.ROW_HEIGHT)
  row:SetPoint("TOPLEFT", parent, "TOPLEFT", 8, -y)
  row:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -8, -y)
  row.icon = row:CreateTexture(nil, "ARTWORK")
  row.icon:SetSize(14, 14)
  row.icon:SetPoint("LEFT", row, "LEFT", 0, 0)
  row.cells = {}
  for i, c in ipairs(columns) do
    local cell = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    cell:SetPoint("LEFT", row, "LEFT", c[2], 0)
    cell:SetWidth(c[3])
    cell:SetWordWrap(false)
    cell:SetJustifyH(c[4])
    row.cells[i] = cell
  end
  if columns == AHPanel.COLUMNS.post then
    row.item, row.price, row.qty, row.profit, row.chance = unpack(row.cells)
  else
    row.item, row.colA, row.colB, row.qty = unpack(row.cells)
  end
  row:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  row.isPost = columns == AHPanel.COLUMNS.post
  row:SetScript("OnClick", function(self, button) AHPanel.OnRowClick(self, button) end)
  if row.isPost then
    Explain(row, "Post", "Click: put it on the AH's Sell tab with this price and quantity, then press the AH's Post "
                         .. "button. Right-click: keep it for yourself.")
  end
  return row
end

--- A row click: right opens the keep menu; left fills the Sell tab (Post) or searches (Shopping).
---@param row table
---@param button string  "LeftButton" or "RightButton"
function AHPanel.OnRowClick(row, button)
  if not row.itemName then return end
  if button == "RightButton" then return AHProfit.RowMenu.Open(row, row.key, row.itemName) end
  if row.isPost then return AHPanel.Sell(row.key, row.itemName, row.sellUnits, row.sellPrice) end
  AHPanel.Search(row.key, row.itemName)
end

--- A section heading with a count on the right.
---@param parent table
---@param y number
---@param text string
---@return table, table  The label and the count
local function Heading(parent, y, text)
  local label = parent:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  label:SetPoint("TOPLEFT", parent, "TOPLEFT", 10, -y)
  label:SetText(text)
  local count = parent:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  count:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -10, -y)
  return label, count
end

--- A section's column headers, each with a tooltip on hover (the same texts as the window's).
---@param parent table
---@param y number         Pixels down from the panel's top
---@param columns table    AHPanel.COLUMNS.shopping or .post
---@return table           The header labels
local function ColumnHeaders(parent, y, columns)
  local labels = {}
  for i, c in ipairs(columns) do
    local label = parent:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    label:SetPoint("TOPLEFT", parent, "TOPLEFT", 8 + c[2], -y)
    label:SetSize(c[3], AHPanel.ROW_HEIGHT)
    label:SetJustifyH(c[4])
    label:SetText(c[1])
    label.hit = Adapter.CreateFrame("Button", nil, parent)
    label.hit:SetAllPoints(label)
    label.hit:SetScript("OnEnter", function(self) Adapter.ShowTooltip(self, c[1], VM.COLUMN_HELP[c[1]]) end)
    label.hit:SetScript("OnLeave", function() Adapter.HideTooltip() end)
    labels[i] = label
  end
  return labels
end

--- A red panel button.
---@param parent table
---@param name string
---@param text string
---@param onClick function
---@return table
local function Button(parent, name, text, onClick)
  local button = Adapter.CreateFrame("Button", name, parent, "BackdropTemplate")
  button:SetSize(110, 22)
  button:SetNormalFontObject("GameFontNormalSmall")
  button:SetText(text)
  button:SetBackdrop({ bgFile = FLAT, edgeFile = FLAT, edgeSize = 1 })
  button:SetBackdropColor(0.48, 0.08, 0.08, 1)
  button:SetBackdropBorderColor(0, 0, 0)
  button:SetScript("OnClick", onClick)
  return button
end

--- Build the panel the first time the AH opens.
---@param anchor table  The AH window
local function Build(anchor)
  local frame = Adapter.CreateFrame("Frame", "AHProfitPanel", anchor, "BackdropTemplate")
  -- Each section: its heading (20 px), its column headers (20 px), then its rows.
  local shopTop = 34
  local postTop = shopTop + 40 + AHPanel.SHOP_ROWS * AHPanel.ROW_HEIGHT + 6
  local height = postTop + 40 + AHPanel.POST_ROWS * AHPanel.ROW_HEIGHT + 80   -- buttons, status line, footer
  frame:SetSize(AHPanel.WIDTH, height)
  frame:SetPoint("TOPLEFT", anchor, "TOPRIGHT", 2, 0)
  frame:SetBackdrop({ bgFile = FLAT, edgeFile = FLAT, edgeSize = 2 })
  frame:SetBackdropColor(0.05, 0.05, 0.06, 0.96)
  frame:SetBackdropBorderColor(BORDER[1], BORDER[2], BORDER[3])
  ui = { frame = frame, shop = {}, post = {} }
  ui.title = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  ui.title:SetPoint("TOPLEFT", frame, "TOPLEFT", 10, -10)
  ui.title:SetText("AHProfit at the AH")
  ui.title:SetTextColor(GOLD[1], GOLD[2], GOLD[3])
  ui.shopLabel, ui.checked = Heading(frame, shopTop, "Shopping")
  ui.shopHeaders = ColumnHeaders(frame, shopTop + 18, AHPanel.COLUMNS.shopping)
  for i = 1, AHPanel.SHOP_ROWS do ui.shop[i] = BuildRow(frame, i, shopTop + 40 + (i - 1) * AHPanel.ROW_HEIGHT, AHPanel.COLUMNS.shopping) end
  ui.postLabel, ui.postCount = Heading(frame, postTop, "Post")
  ui.postHeaders = ColumnHeaders(frame, postTop + 18, AHPanel.COLUMNS.post)
  for i = 1, AHPanel.POST_ROWS do
    ui.post[i] = BuildRow(frame, AHPanel.SHOP_ROWS + i, postTop + 40 + (i - 1) * AHPanel.ROW_HEIGHT, AHPanel.COLUMNS.post)
  end
  ui.next = Button(frame, "AHProfitPanelNext", "Next search", function() AHPanel.NextSearch() end)
  ui.next:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 10, 34)
  Explain(ui.next, "Next search", "Searches the next item on the Shopping list, one per click, top to bottom. "
                                  .. "Each item is searched once per AH visit.")
  ui.scan = Button(frame, "AHProfitPanelScan", "Full scan", function() AHPanel.FullScan() end)
  ui.scan:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -10, 34)
  Explain(ui.scan, "Full scan", "Reads the whole auction house at once, which updates every price here. "
                                .. "Allowed every 20 minutes.")
  ui.footer = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  ui.footer:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 10, 12)
  -- The status line has a row of its own, above the buttons, so it never runs into the footer.
  ui.status = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  ui.status:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 10, 62)
  ui.empty = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
  ui.empty:SetPoint("TOPLEFT", frame, "TOPLEFT", 12, -(shopTop + 44))
  ui.empty:SetSize(AHPanel.WIDTH - 24, 26)          -- wraps onto a second line inside the panel
  ui.empty:SetJustifyH("LEFT")
end

--- Fill a shopping row.
---@param row table
---@param line table|nil  A ViewModel.AHPanel shopping row
local function FillShop(row, line)
  row:SetShown(line ~= nil)
  if not line then return end
  row.key, row.itemName = line.key, line.item
  row.icon:SetTexture(AHPanel.ICONS[line.state])
  row.item:SetText(line.item)
  row.colA:SetText(VM.WithCoins(line.price))
  row.colA:SetTextColor(line.state == "found" and 0.24 or MUTED[1], line.state == "found" and 0.86 or MUTED[2],
                        line.state == "found" and 0.31 or MUTED[3])
  row.colB:SetText(VM.WithCoins(line.max))
  row.qty:SetText(line.qty)
end

--- Fill a post row.
---@param row table
---@param line table|nil  A Today post row
local function FillPost(row, line)
  row:SetShown(line ~= nil)
  if not line then return end
  row.key, row.itemName, row.sellUnits, row.sellPrice = line.key, line.item, line.units, line.price
  row.icon:SetTexture(nil)
  for i, c in ipairs(AHPanel.COLUMNS.post) do
    local text = line[c[5]]
    row.cells[i]:SetText(MONEY[c[5]] and VM.WithCoins(text) or text)
  end
  row.profit:SetTextColor(line.postNetColor == "neg" and 1 or 0.24, line.postNetColor == "neg" and 0.29 or 0.86,
                            line.postNetColor == "neg" and 0.24 or 0.31)
end

--- Redraw the panel from the live data.
function AHPanel.Render()
  if not ui then return end
  local info = Adapter.PlayerInfo()
  local charId = info.name .. "-" .. info.realm
  local gold = VM.TotalGold((DB.Get() or {}).characters)
  current = VM.AHPanel(Targets(charId), Posts(charId), LevelsOf, LookOf, gold)
  ui.checked:SetText(current.checked)
  ui.postCount:SetText(tostring(#current.posts))
  for i, row in ipairs(ui.shop) do FillShop(row, current.shopping[i]) end
  for i, row in ipairs(ui.post) do FillPost(row, current.posts[i]) end
  ui.footer:SetText(VM.WithCoins(current.footer))
  ui.empty:SetShown(#current.shopping == 0)
  ui.empty:SetText("Nothing to buy right now.")
end

--- Search the AH for one row's item (a click, so a hardware event).
---@param key number
---@param name string
function AHPanel.Search(key, name)
  searchedThisVisit[key] = true
  local started = Adapter.SearchAH(name)
  if not ui then return end
  if not started then ui.status:SetText("search unavailable") return end
  local done, total = 0, #(current and current.shopping or {})
  for _, row in ipairs(current and current.shopping or {}) do
    if searchedThisVisit[row.key] then done = done + 1 end
  end
  ui.status:SetText("searched " .. name .. " · " .. done .. " of " .. total)
end

--- Put a Post row's item on the AH's Sell tab, for you to post with the AH's own button (A29, FM30).
---@param key number
---@param name string
---@param units number|nil  Units to post
---@param price number|nil  Unit price, copper
function AHPanel.Sell(key, name, units, price)
  if not (ui and units and price) then return end
  local bag, slot = Adapter.FindInBags(key)
  if not bag then return ui.status:SetText("no " .. name .. " in your bags") end
  local ok, placed, why = pcall(Adapter.FillSell, bag, slot, units, price)
  if not ok or not placed then
    return ui.status:SetText(why == "invalid" and (name .. " can't be sold on the AH") or "Sell tab unavailable")
  end
  pendingSell = { price = price, untilMs = Adapter.NowMs() + AHPanel.REFILL_MS }
  ui.status:SetText(VM.WithCoins("Sell tab: ×" .. placed .. " at " .. VM.Money(price) .. " each · press Post"))
end

--- The AH's search results for the item on the Sell tab: it may have put its own price in, so put
--- the suggested one back, once, within REFILL_MS of the fill (A29).
--- UNVERIFIED: COMMODITY_SEARCH_RESULTS_UPDATED / ITEM_SEARCH_RESULTS_UPDATED fire after the Sell tab's own search.
function AHPanel.OnSellSearch()
  if pendingSell and Adapter.NowMs() <= pendingSell.untilMs then pcall(Adapter.RefillSellPrice, pendingSell.price) end
  pendingSell = nil
end

--- Next search: the next Shopping row, top to bottom, not yet searched this AH visit.
function AHPanel.NextSearch()
  for _, row in ipairs(current and current.shopping or {}) do
    if not searchedThisVisit[row.key] then return AHPanel.Search(row.key, row.item) end
  end
  if ui then ui.status:SetText("all searched this visit") end
end

--- Full scan: ask for a whole-AH scan, at most every 20 minutes.
function AHPanel.FullScan()
  local asked, waitMinutes = Replicate.RequestScan()
  if not ui then return end
  if asked then ui.status:SetText("scanning…")
  elseif waitMinutes then ui.status:SetText("next scan in " .. waitMinutes .. " min")
  else ui.status:SetText("scan already running") end
end

--- AUCTION_HOUSE_SHOW: dock and show the panel.
function AHPanel.OnShow()
  local anchor = Adapter.AuctionFrame()
  if not anchor then return end
  if not ui then Build(anchor) end
  ui.status:SetText("")
  ui.frame:Show()
  AHPanel.Render()
end

--- AUCTION_HOUSE_CLOSED: hide the panel; the search queue is dropped (FM24).
function AHPanel.OnClosed()
  searchedThisVisit, pendingSell = {}, nil
  if ui then ui.frame:Hide() end
end

--- AUCTION_HOUSE_BROWSE_RESULTS_UPDATED: note each result's cheapest price and redraw (A24).
--- UNVERIFIED: the event's name on Forever, as on retail.
function AHPanel.OnBrowseResults()
  local now = Adapter.NowMs()
  for _, r in ipairs(Adapter.ReadBrowseResults()) do Live.NoteSearch(r.itemId, r.minPrice, r.units, now) end
  if ui and ui.frame:IsShown() then AHPanel.Render() end
end

--- The panel's frames (tests).
---@return table|nil
function AHPanel.Frames()
  return ui
end

Events.Register("AUCTION_HOUSE_SHOW", AHPanel.OnShow)
Events.Register("AUCTION_HOUSE_CLOSED", AHPanel.OnClosed)
Events.Register("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED", AHPanel.OnBrowseResults)
Events.Register("COMMODITY_SEARCH_RESULTS_UPDATED", AHPanel.OnSellSearch)
Events.Register("ITEM_SEARCH_RESULTS_UPDATED", AHPanel.OnSellSearch)
Replicate.OnBook(function() if ui and ui.frame:IsShown() then AHPanel.Render() end end)
-- A post (or a buy) changes the bags; redraw once they have been read again (A31).
AHProfit.Capture.Inventory.OnChange(function() if ui and ui.frame:IsShown() then AHPanel.Render() end end)
AHProfit.RowMenu.OnChange(function() if ui and ui.frame:IsShown() then AHPanel.Render() end end)
