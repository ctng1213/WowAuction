-- UI/RowMenu.lua: the menu a right-click on an item row opens (SPEC §4.3): keep the item for
-- yourself, keep at least some, or sell it normally again (A25); and the item rules: don't buy or
-- craft it, ignore it, or keep selling it with a fresh record (A53, SPEC §3.4.2). A small frame of
-- its own, so it needs neither of the client's dropdown APIs.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local DB = AHProfit.DB
local Reserves = AHProfit.Reserves

local RowMenu = {}
AHProfit.RowMenu = RowMenu

RowMenu.WIDTH = 190
local FLAT = "Interface\\Buttons\\WHITE8X8"

local ui = nil
local target = nil          -- { key, name } the menu is for
local listeners = {}        -- called after a reserve changes

--- A menu entry: a full-width text button.
---@param parent table
---@param name string
---@param y number    Pixels down from the top
---@param text string
---@param onClick function
---@return table
local function Entry(parent, name, y, text, onClick)
  local button = Adapter.CreateFrame("Button", name, parent)
  button:SetHeight(20)
  button:SetPoint("TOPLEFT", parent, "TOPLEFT", 6, -y)
  button:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -6, -y)
  button:SetNormalFontObject("GameFontHighlightSmall")
  button:SetText(text)
  button:SetScript("OnClick", onClick)
  return button
end

--- Build the menu the first time it opens.
local function Build()
  local frame = Adapter.CreateFrame("Frame", "AHProfitRowMenu", UIParent, "BackdropTemplate")
  frame:SetSize(RowMenu.WIDTH, 220)
  frame:SetFrameStrata("DIALOG")
  frame:SetBackdrop({ bgFile = FLAT, edgeFile = FLAT, edgeSize = 1 })
  frame:SetBackdropColor(0.03, 0.04, 0.09, 0.96)
  frame:SetBackdropBorderColor(0.36, 0.42, 0.54)
  frame:SetClampedToScreen(true)
  ui = { frame = frame }
  ui.title = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
  ui.title:SetPoint("TOPLEFT", frame, "TOPLEFT", 8, -8)
  ui.title:SetWidth(RowMenu.WIDTH - 16)
  ui.title:SetWordWrap(false)
  ui.title:SetJustifyH("LEFT")
  ui.keepAll = Entry(frame, "AHProfitRowMenuKeepAll", 28, "Keep all for myself", function() RowMenu.Keep(Reserves.ALL) end)
  ui.keepSome = Entry(frame, "AHProfitRowMenuKeepSome", 50, "Keep at least…", function() RowMenu.AskAmount() end)
  ui.amount = Adapter.CreateFrame("EditBox", "AHProfitRowMenuAmount", frame, "InputBoxTemplate")
  ui.amount:SetSize(60, 20)
  ui.amount:SetPoint("TOPLEFT", frame, "TOPLEFT", 14, -74)
  ui.amount:SetNumeric(true)
  ui.amount:SetAutoFocus(false)
  ui.amount:SetScript("OnEnterPressed", function(self) RowMenu.Keep(tonumber(self:GetText())) end)
  ui.ok = Entry(frame, "AHProfitRowMenuOK", 74, "OK", function() RowMenu.Keep(tonumber(ui.amount:GetText())) end)
  ui.ok:ClearAllPoints()
  ui.ok:SetSize(50, 20)
  ui.ok:SetPoint("TOPLEFT", frame, "TOPLEFT", 84, -74)
  ui.sell = Entry(frame, "AHProfitRowMenuSell", 100, "Sell normally", function() RowMenu.Keep(nil) end)
  ui.noBuy = Entry(frame, "AHProfitRowMenuNoBuy", 126, "Don't buy or craft it", function() RowMenu.SetRule("no_buy") end)
  ui.ignore = Entry(frame, "AHProfitRowMenuIgnore", 148, "Ignore it completely", function() RowMenu.SetRule("ignore") end)
  ui.keepSelling = Entry(frame, "AHProfitRowMenuKeepSelling", 170, "Keep selling it (start fresh)",
                         function() RowMenu.SetRule("keep") end)
  ui.cancel = Entry(frame, "AHProfitRowMenuCancel", 194, "Cancel", function() RowMenu.Close() end)
  frame:Hide()
  Adapter.CloseWithEscape("AHProfitRowMenu")
end

--- Open the menu for an item, next to the row that was right-clicked.
---@param anchor table  The row
---@param key number
---@param name string
function RowMenu.Open(anchor, key, name)
  if not ui then Build() end
  target = { key = key, name = name }
  ui.title:SetText(name)
  ui.amount:Hide()
  ui.ok:Hide()
  ui.amount:SetText("")
  ui.frame:ClearAllPoints()
  ui.frame:SetPoint("TOPLEFT", anchor, "BOTTOMLEFT", 40, 0)
  ui.frame:Show()
end

--- Show the amount box for "Keep at least…".
function RowMenu.AskAmount()
  ui.amount:Show()
  ui.ok:Show()
  ui.amount:SetFocus()
end

--- Save the reserve for the menu's item and close (nil sells normally again).
---@param qty number|nil  Units to keep; Reserves.ALL keeps all; nil or 0 clears
function RowMenu.Keep(qty)
  local db = DB.Get()
  if db and target then
    local info = Adapter.PlayerInfo()
    Reserves.Set(db, info.name .. "-" .. info.realm, target.key, (qty and qty ~= 0) and qty or nil)
    for _, fn in ipairs(listeners) do fn() end
  end
  RowMenu.Close()
end

--- Set an item rule for the menu's item, for AHSync to read (SPEC §3.4.2, A53), and close.
---@param rule string  "no_buy", "ignore" or "keep"
function RowMenu.SetRule(rule)
  local db = DB.Get()
  if db and target then
    db.rules = db.rules or {}
    db.rules[target.key] = { rule = rule, setAt = Adapter.NowMs() }
    for _, fn in ipairs(listeners) do fn() end
  end
  RowMenu.Close()
end

--- Close the menu.
function RowMenu.Close()
  if ui then ui.frame:Hide() end
  target = nil
end

--- Call a function after a reserve or a rule changes (the window and the panel redraw).
---@param fn function
function RowMenu.OnChange(fn)
  listeners[#listeners + 1] = fn
end

--- The menu's frames (tests).
---@return table|nil
function RowMenu.Frames()
  return ui
end
