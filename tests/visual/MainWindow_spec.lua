-- Layout snapshots of the /ahp window (SPEC §8.4 layer 2, UI1-UI4, UI6, UI9, UI10, UI11-UI15). Each scenario draws the
-- window with the stub frames, runs the automatic layout checks and compares the frame tree with
-- tests/visual/golden/<scenario>.txt. `python tools/visual.py --update` accepts a change, and
-- `--html` draws it for comparison with docs/mockups/addon.html.

local Stubs = require("wow_stubs")
local Layout = require("layout")
local UIWorld = require("ui_world")

local NOW = 1760000000000
local KINDS = { "buy", "post", "craft", "de", "de", "post" }   -- no vendor: a vendor sale is no Today row (D64, A73)

--- Log in with n Today actions over a few items, then open the window.
---@param n number            Actions due now
---@param marketMissing boolean|nil  No MarketIntelligence.lua
---@return table              The window's frames
local function Open(n, marketMissing)
  Stubs.Reset()
  Stubs.ITEM_NAMES[6586] = "Scouting Gloves of the Monkey"
  _G.AHProfitMarket = nil
  if not marketMissing then
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48), [2840] = UIWorld.Item(500, 60, 40),
                     [6586] = UIWorld.Item(800, 30, 3) }, NOW - 2 * UIWorld.HOUR)
  end
  local actions, keys = {}, { 2589, 2840, 6586 }
  for i = 1, n do
    local kind = KINDS[(i - 1) % #KINDS + 1]
    actions[i] = { id = string.format("a%02d", i), kind = kind, key = keys[(i - 1) % 3 + 1], qty = i, price = 40 * i,
                   at = NOW, net = (i % 4 == 0) and -20 * i or 150 * i, chance = kind == "post" and 60 + i or nil }
  end
  UIWorld.Plan(actions)
  Stubs.Login()
  AHProfitDB.characters["Brugan-Grommash"] = { gold = 104000 }
  Stubs.Slash("/ahp")
  Stubs.ITEM_NAMES[6586] = nil
  return AHProfit.MainWindow.Frames()
end

--- The Craft tab with Copper Rod made from the 6 Copper Bars in the bags (UI11, UI14).
---@return table  The window's frames
local function CraftWindow()
  Stubs.Reset()
  _G.AHProfitPlan = nil
  Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2840, count = 6 } } } }
  UIWorld.Market({ [6217] = UIWorld.Item(4100, 86, 6, { postPrice = 4000, chance = 1, sellValue = 3800, refStack = 1 }),
                   [2840] = UIWorld.Item(5000, 80, 40, { postPrice = 1000, sellValue = 950 }) }, NOW)
  Stubs.Login()
  AHProfitDB.knownRecipes["Amaya-Grommash"] = "2663"
  AHProfitDB.recipes.Blacksmithing = { [2663] = { product = 6217, yieldMin = 1, yieldMax = 1, reagents = "2840,2" } }
  Stubs.Slash("/ahp")
  AHProfit.MainWindow.ShowTab("craft")
  return AHProfit.MainWindow.Frames()
end

--- The x of the first shown note line and of its tab's Item column.
---@param ui table   The window's frames
---@param tab string
---@return number|nil, number
local function NoteAndItem(ui, tab)
  local rects = Layout.Resolve(ui.frame)
  for _, r in ipairs(ui.rows) do
    if r:IsShown() and r.note:IsShown() then return rects[r.note].x, rects[ui.rows[1].cells[tab].item].x end
  end
  return nil, rects[ui.rows[1].cells[tab].item].x
end

describe("MainWindow layout", function()
  it("UI1 Today with 12 mixed actions", function()
    local ui = Open(12)
    Layout.Golden("UI1_today", ui.frame)
    assert_eq(ui.thumb:IsShown(), false)
  end)

  it("UI2 40 actions: 12 rows drawn and a scroll bar", function()
    local ui = Open(40)
    Layout.Golden("UI2_scroll", ui.frame)
    assert_eq({ ui.rows[12]:IsShown(), ui.thumb:IsShown() }, { true, true })
  end)

  it("UI3 a long item name in the narrow window is cut with an ellipsis", function()
    local ui = Open(3)
    ui.frame:SetSize(560, 360)
    local snapshot = Layout.Golden("UI3_truncate", ui.frame)
    assert_eq(snapshot:find("Scouting Gloves of the Monkey", 1, true), nil)
    assert_eq(snapshot:find('"Scou[^"]*…"') ~= nil, true)
  end)

  it("UI4 without MarketIntelligence.lua: no rows, and how to fix it", function()
    local ui = Open(5, true)
    Layout.Golden("UI4_no_market", ui.frame)
    assert_eq({ ui.empty:GetText(), ui.rows[1]:IsShown() }, { "No market data yet. Run AHSync, then /reload.", false })
  end)

  it("UI9 the window at its minimum size, 560 x 360", function()
    local ui = Open(12)
    ui.frame:SetSize(560, 360)
    Layout.Golden("UI9_minimum", ui.frame)
  end)

  it("UI11 the Craft tab at the minimum size: a recipe covered by owned mats shows Mats \"—\"", function()
    local ui = CraftWindow()
    ui.frame:SetSize(560, 360)
    Layout.Golden("UI11_craft", ui.frame)
    assert_eq({ ui.rows[1].cells.craft.mats:GetText(), ui.rows[2].note:GetText() }, { "—", "use 6 owned Copper Bar" })
  end)

  it("UI14 a note starts where the Item column starts: under the item on Craft, after the time and badge on Week", function()
    local note, item = NoteAndItem(CraftWindow(), "craft")
    assert_eq({ note, item }, { item, item }, "Craft: the first column")
    Stubs.Reset()
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW - UIWorld.HOUR)
    UIWorld.Plan({ { id = "f", kind = "buy", key = 2589, qty = 20, price = 38, at = NOW, net = 210, vendor = true } })
    Stubs.Login()
    Stubs.Slash("/ahp week")
    note, item = NoteAndItem(AHProfit.MainWindow.Frames(), "week")
    assert_eq({ note, item }, { item, item }, "Week: after the time and the badge")
  end)

  it("UI13 the Flip tab at the minimum size: seven tabs fit beside the Min chance box", function()
    Stubs.Reset()
    local SAT10, SAT18, SUN11 = 1760176800000, 1760205600000, 1760266800000   -- Sat 11 Oct 10:00 and 18:00, Sun 11:00 UTC
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48), [4306] = UIWorld.Item(70, 80, 60), [2840] = UIWorld.Item(500, 80, 40) },
                   NOW - UIWorld.HOUR)
    UIWorld.Plan({ { id = "f1", kind = "buy", key = 2589, qty = 20, price = 40, at = NOW, net = 210 },
                   { id = "f2", kind = "post", key = 2589, qty = 20, price = 55, at = SAT10, net = 900, parent = "f1",
                     chance = 88, peak = SAT18 },
                   { id = "s1", kind = "buy", key = 4306, qty = 5, price = 70, at = NOW, net = 40 },
                   { id = "s2", kind = "post", key = 4306, qty = 5, price = 100, at = SUN11, net = 120, parent = "s1", chance = 70 },
                   { id = "m1", kind = "buy", key = 2840, qty = 2, price = 500, at = NOW, net = 0 },
                   { id = "m2", kind = "craft", key = 6217, qty = 1, at = NOW, net = 300, parent = "m1" },
                   { id = "m3", kind = "post", key = 6217, qty = 1, price = 4000, at = SAT10, net = 300, parent = "m1" } })
    Stubs.Login()
    AHProfitDB.characters["Amaya-Grommash"].gold = 10000
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("flip")
    local ui = AHProfit.MainWindow.Frames()
    ui.frame:SetSize(560, 360)
    local snapshot = Layout.Golden("UI13_flip", ui.frame)
    -- The last tab ends at x 1090, before the Min chance box at 1100.
    assert_eq(snapshot:find("AHProfitWindowTabReservations 1012,396 78x22", 1, true) ~= nil, true)
    assert_eq(ui.rows[1].cells.flip.item:GetText(), "Linen Cloth")
  end)

  it("UI10 Today with a row below Min chance and an item not selling: Needs a decision at the bottom", function()
    Stubs.Reset()
    Stubs.ITEM_NAMES[2318] = "Heavy Leather"
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48), [2840] = UIWorld.Item(500, 60, 40), [2318] = UIWorld.Item(50, 70, 10) },
                   NOW - UIWorld.HOUR)
    UIWorld.Plan({ { id = "l", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1000, chance = 92 },
                   { id = "b", kind = "post", key = 2840, qty = 6, price = 500, at = NOW, net = 300, chance = 30 } })
    AHProfitPlan.records = { [2318] = "1;Your record (30 d): 0 of 3 sold · expired 3× while cheapest · price −14% · qty 1/8" }
    Stubs.Login()
    Stubs.Slash("/ahp")
    local ui = AHProfit.MainWindow.Frames()
    Layout.Golden("UI10_decisions", ui.frame)
    assert_eq(ui.footer:GetText():match("1 hidden") ~= nil, true)
    Stubs.ITEM_NAMES[2318] = nil
  end)

  it("UI6 a not-selling row right-clicked: the menu with the rules fits", function()
    Stubs.Reset()
    Stubs.ITEM_NAMES[2318] = "Heavy Leather"
    UIWorld.Market({ [2318] = UIWorld.Item(50, 70, 10) }, NOW - UIWorld.HOUR)
    UIWorld.Plan({})
    AHProfitPlan.records = { [2318] = "1;Your record (30 d): 0 of 3 sold" }
    Stubs.Login()
    Stubs.Slash("/ahp")
    local ui = AHProfit.MainWindow.Frames()
    local row = ui.rows[2]                                        -- the header, then the DECIDE row
    row:GetScript("OnMouseUp")(row, "RightButton")
    Layout.Golden("UI6_row_menu", AHProfit.RowMenu.Frames().frame)
    assert_eq(row.cells.today.action:GetText(), "DECIDE")
    Stubs.ITEM_NAMES[2318] = nil
  end)

  it("UI12 the Reservations tab at the minimum size: five tabs fit beside the Min chance box", function()
    Stubs.Reset()
    Stubs.ITEM_NAMES[159] = "Refreshing Spring Water"
    Stubs.world.containers = { [0] = { size = 4, slots = { [1] = { id = 159, count = 13 }, [2] = { id = 4306, count = 7 } } } }
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW - UIWorld.HOUR)
    UIWorld.Plan({})
    Stubs.Login()
    AHProfitDB.reserves["Amaya-Grommash"] = "159,10;2589,5;4306,-1"
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("reservations")
    local ui = AHProfit.MainWindow.Frames()
    ui.frame:SetSize(560, 360)
    Layout.Golden("UI12_reservations", ui.frame)
    assert_eq(ui.chance:IsShown(), false)
    Stubs.ITEM_NAMES[159] = nil
  end)

  it("Week with a flip: day headers, notes under rows and coin money (D37, mockup Week tab)", function()
    Stubs.Reset()
    _G.AHProfitMarket = nil
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW - UIWorld.HOUR)
    UIWorld.Plan({ { id = "f", kind = "buy", key = 2589, qty = 20, price = 38, at = NOW, net = 210 },
                   { id = "g", kind = "post", key = 2589, qty = 20, price = 55, at = NOW + 86400000, net = 95,
                     parent = "f", chance = 88, peak = NOW + 86400000 + 8 * UIWorld.HOUR } })
    Stubs.Login()
    Stubs.Slash("/ahp week")
    local ui = AHProfit.MainWindow.Frames()
    Layout.Golden("week_notes", ui.frame)
    assert_eq(ui.rows[3].note:GetText(), "then: post Fri 08:53, for the 16:53 peak")   -- the chain's last line (D62)
  end)

  it("UI15 Today with a craft chain: one CRAFT row, its buys and its sale as lines under it", function()
    Stubs.Reset()
    _G.AHProfitMarket = nil
    Stubs.ITEM_NAMES[3371], Stubs.ITEM_NAMES[6303], Stubs.ITEM_NAMES[6370] = "Empty Vial", "Raw Brilliant Smallfish", "Fish Bowl"
    UIWorld.Market({ [6370] = UIWorld.Item(2500, 80, 30) }, NOW - UIWorld.HOUR)
    UIWorld.Plan({ { id = "v", kind = "buy", key = 3371, qty = 76, price = 20, at = NOW - 120000, net = 0, vendor = true },
                   { id = "f", kind = "buy", key = 6303, qty = 76, price = 75, at = NOW - 60000, net = 0, parent = "v" },
                   { id = "k", kind = "craft", key = 6370, qty = 76, at = NOW, net = 120448, parent = "v" },
                   { id = "p", kind = "post", key = 6370, qty = 76, price = 2500, at = NOW + 3 * 86400000, net = 120448,
                     parent = "v", chance = 90, peak = NOW + 3 * 86400000 + 8 * UIWorld.HOUR } })
    Stubs.Login()
    AHProfitDB.characters["Brugan-Grommash"] = { gold = 314000 }
    Stubs.Slash("/ahp")
    local ui = AHProfit.MainWindow.Frames()
    Layout.Golden("UI15_chain", ui.frame)
    assert_eq({ ui.rows[1].cells.today.action:GetText(), ui.rows[2].note:GetText(), ui.rows[4].note:GetText(),
                ui.rows[5]:IsShown() },
              { "CRAFT", "buy 76 Empty Vial from a vendor · 15s 20c", "then: post Sun 08:53, for the 16:53 peak", false })
    Stubs.ITEM_NAMES[3371], Stubs.ITEM_NAMES[6303], Stubs.ITEM_NAMES[6370] = nil, nil, nil
  end)
end)
