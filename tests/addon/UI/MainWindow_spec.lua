-- Specs for addon/AHProfit/UI/MainWindow.lua: what /ahp does (SPEC §4.3). Layout is checked by
-- the snapshot scenarios in tests/visual/MainWindow_spec.lua.

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")

local NOW = 1760000000000

--- Log in with one Linen post due now and one Copper Bar buy tomorrow.
---@return table  AHProfit.MainWindow
local function Boot()
  Stubs.Reset()
  Stubs.world.money = 123456                             -- 12g 34s 56c: the budget in the footer
  UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48), [2840] = UIWorld.Item(500, 60, 40) }, NOW - UIWorld.HOUR)
  UIWorld.Plan({ { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1046, chance = 92 },
                 { id = "b", kind = "buy", key = 2840, qty = 6, price = 500, at = NOW + 86400000, net = 0 } })
  Stubs.Login()
  return AHProfit.MainWindow
end

--- The texts of the shown cells of the first row, for the current tab.
---@param tab string
---@return table  field -> text
local function FirstRow(tab)
  local row = AHProfit.MainWindow.Frames().rows[1]
  local out = {}
  for field, cell in pairs(row.cells[tab]) do out[field] = cell:GetText() end
  return out
end

describe("MainWindow", function()
  it("/ahp opens and closes the window; it is built only when first opened", function()
    local MainWindow = Boot()
    assert_eq(MainWindow.Frames(), nil)
    Stubs.Slash("/ahp")
    local frame = MainWindow.Frames().frame
    assert_eq(frame:IsShown(), true)
    assert_eq(UISpecialFrames, { "AHProfitWindow" })      -- Escape closes it
    Stubs.Slash("/ahprofit")
    assert_eq(frame:IsShown(), false)
  end)

  it("Today shows the plan's actions due now with the market's volume and the age badge", function()
    local MainWindow = Boot()
    Stubs.Slash("/ahp")
    local row, coins = FirstRow("today"), AHProfit.ViewModel.WithCoins
    assert_eq({ row.action, row.item, row.qty, row.each, row.total, row.net, row.chance, row.volume, row.who },
              { "POST", "Linen Cloth", "×20", coins("55c"), coins("11s"), coins("+10s 46c"), row.chance, "48/day", nil })
    assert_eq(row.chance:match("^92%% |T.-:7:7:0:0:8:8:0:8:0:8:(%d+:%d+:%d+)|t$"), "61:219:79")   -- the high (green) dot
    local ui = MainWindow.Frames()
    assert_eq({ ui.age:GetText(), ui.sub:GetText(), ui.footer:GetText(), ui.budget:GetText() },
              { "data 1 h old", "Grommash · Horde", coins("1 action · expected +10s 46c"), "Budget " .. coins("12g 34s 56c") })
    assert_eq(ui.rows[2]:IsShown(), false)
  end)

  it("the Week tab lists each day with its total, and /ahp week opens it directly", function()
    local MainWindow = Boot()
    Stubs.Slash("/ahp week")
    local ui = MainWindow.Frames()
    local coins = AHProfit.ViewModel.WithCoins
    assert_eq({ ui.rows[1].dayLabel:GetText(), ui.rows[1].dayTotal:GetText() }, { "Thu 9 (today)", coins("+10s 46c") })
    assert_eq({ ui.rows[2].cells.week.item:GetText(), ui.rows[3].dayLabel:GetText() }, { "Linen Cloth", "Fri 10" })
    assert_eq({ ui.rows[1].cells.week.item:IsShown(), ui.chance:IsShown(), ui.footer:GetText() },
              { false, false, coins("Week total +10s 46c") })
    MainWindow.ShowTab("today")
    assert_eq({ ui.headers.today:IsShown(), ui.headers.week:IsShown() }, { true, false })
  end)

  it("the Min chance button steps 50 → 75 → 0 and hides rows below it", function()
    local MainWindow = Boot()
    Stubs.Slash("/ahp")
    local ui = MainWindow.Frames()
    assert_eq(ui.chance:GetText():match("^Min chance: (%d+)%% |T.*ScrollDown"), "50")
    ui.chance:Click()
    ui.chance:Click()
    assert_eq(ui.chance:GetText():match("^Min chance: (%d+)%%"), "0")
    MainWindow.NextMinChance()
    MainWindow.NextMinChance()
    MainWindow.NextMinChance()
    assert_eq({ ui.chance:GetText():match("^Min chance: (%d+)%%"), ui.footer:GetText() },
              { "75", AHProfit.ViewModel.WithCoins("1 action · expected +10s 46c") })
  end)

  it("hovering a column header shows what it means", function()
    local MainWindow = Boot()
    Stubs.Slash("/ahp")
    local header = MainWindow.Frames().headerCells.today.chance
    assert_eq(header:GetText(), "Chance")
    header.hit:GetScript("OnEnter")(header.hit)
    assert_eq({ Stubs.tooltip.shown, Stubs.tooltip.lines[1] }, { true, "Chance" })
    header.hit:GetScript("OnLeave")(header.hit)
    assert_eq(Stubs.tooltip.shown, false)
  end)

  it("only the logged-in character's actions show, and there is no Who column", function()
    Stubs.Reset()
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW)
    UIWorld.Plan({ { id = "a", kind = "post", key = 2589, qty = 1, price = 50, at = NOW, net = 40, chance = 90 },
                   { id = "b", kind = "post", key = 2589, qty = 2, price = 50, at = NOW, net = 90, chance = 90,
                     char = "Brugan-Grommash" } })
    Stubs.Login()
    Stubs.Slash("/ahp")
    local ui = AHProfit.MainWindow.Frames()
    assert_eq({ FirstRow("today").qty, ui.rows[2]:IsShown(), ui.headerCells.today.who }, { "×1", false, nil })
  end)

  it("scrolling moves through a long list and stops at its ends", function()
    Stubs.Reset()
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW)
    local actions = {}
    for i = 1, 20 do
      actions[i] = { id = string.format("a%02d", i), kind = "post", key = 2589, qty = i, price = 50, at = NOW, net = 1000 - i,
                     chance = 90 }
    end
    UIWorld.Plan(actions)
    Stubs.Login()
    Stubs.Slash("/ahp")
    local MainWindow = AHProfit.MainWindow
    MainWindow.Frames().list:GetScript("OnMouseWheel")(MainWindow.Frames().list, -1)
    assert_eq(FirstRow("today").qty, "×4")                -- one notch is 3 lines: the 4th best is first
    MainWindow.Scroll(100)
    assert_eq(FirstRow("today").qty, "×9")                -- stops when the last 12 show
    MainWindow.Scroll(-100)
    assert_eq(FirstRow("today").qty, "×1")
  end)

  it("names come from the saved link first, then the client cache, then the key", function()
    local MainWindow = Boot()
    AHProfitDB.itemNames = { [2589] = "2,Linen Cloth of the Whale" }
    Stubs.world.uncached[2840] = true
    Stubs.Slash("/ahp week")
    local ui = MainWindow.Frames()
    assert_eq(ui.rows[2].cells.week.item:GetText(), "Linen Cloth of the Whale")
    assert_eq(ui.rows[4].cells.week.item:GetText(), "Item 2840")
  end)

  it("an error while opening is printed to chat, not raised as a Lua error", function()
    local MainWindow = Boot()
    MainWindow.Toggle = function() error("boom") end
    Stubs.Slash("/ahp")
    assert_eq(Stubs.chat[#Stubs.chat]:find("the window failed: .*boom") ~= nil, true)
  end)
end)
