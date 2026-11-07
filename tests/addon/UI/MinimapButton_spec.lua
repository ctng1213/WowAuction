-- Specs for addon/AHProfit/UI/MinimapButton.lua: the minimap button's frame, clicks, drag, hover and
-- /ahp minimap (SPEC §4.3, UI20-UI23).

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")

local NOW = 1760000000000

--- Round to a tenth, for comparing offsets.
---@param x number
---@return number
local function Tenth(x)
  return math.floor(x * 10 + 0.5) / 10
end

--- The button's anchor offset from the minimap's centre.
---@param button table
---@return table  { x, y } to a tenth
local function Offset(button)
  local p = button.points[#button.points]
  assert_eq({ p.point, p.rel == Minimap, p.relPoint }, { "CENTER", true, "CENTER" })
  return { Tenth(p.x), Tenth(p.y) }
end

--- Log in with a market file (1 h old unless given) and one post due now for Amaya.
---@param keepSaved boolean|nil   Keep AHProfitDB, as across a /reload
---@param generatedAt number|nil  The market file's time, ms
---@return table                  The button
---@return table                  Its dot
local function LogIn(keepSaved, generatedAt)
  Stubs.Reset({ keepSaved = keepSaved })
  UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, generatedAt or NOW - UIWorld.HOUR)
  UIWorld.Plan({ { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1046, chance = 92 } })
  Stubs.Login()
  return AHProfit.MinimapButton.Frame()
end

describe("MinimapButton", function()
  it("UI22 at login it sits at 225° on the minimap, and a left or right click opens or closes the window", function()
    local button, dot = LogIn()
    assert_eq({ button.parent == Minimap, button:IsShown(), button.clicks, Offset(button) },
              { true, true, { "LeftButtonUp", "RightButtonUp" }, { -56.6, -56.6 } })
    assert_eq({ button.strata, button.fixedStrata, button.fixedLevel, dot.layer, dot.sublevel },
              { { "MEDIUM" }, { true }, { true }, "OVERLAY", 7 }, "the dot draws above the ring (OVERLAY 0)")
    assert_eq(AHProfitDB.minimap, nil, "nothing is saved until the button is dragged or hidden")
    button:Click("LeftButton")
    assert_eq(AHProfit.MainWindow.Frames().frame:IsShown(), true)
    button:Click("RightButton")
    assert_eq(AHProfit.MainWindow.Frames().frame:IsShown(), false)
    assert_eq(AHProfit.Events.Errors(), {})
  end)

  it("UI21 a drag released 30 px right of and 52 px above the minimap's centre snaps to 60° and is kept", function()
    local button = LogIn()
    button.scripts.OnDragStart(button)
    assert_eq(button.moving, true)
    button.centerX, button.centerY = Minimap.centerX + 30, Minimap.centerY + 52
    button.scripts.OnDragStop(button)
    assert_eq({ button.moving, button.userPlaced, AHProfitDB.minimap.angle, Offset(button) },
              { false, { false }, 60, { 40, 69.3 } })
    button = LogIn(true)
    assert_eq(Offset(button), { 40, 69.3 }, "the angle survives a /reload")
  end)

  it("UI23 /ahp minimap hides the button across a /reload, and again shows it", function()
    local button = LogIn()
    Stubs.Slash("/ahp minimap")
    assert_eq({ button:IsShown(), AHProfitDB.minimap.hidden }, { false, true })
    button = LogIn(true)
    assert_eq(button:IsShown(), false)
    Stubs.Slash("/ahp minimap")
    assert_eq({ button:IsShown(), AHProfitDB.minimap.hidden }, { true, false })
  end)

  it("UI20 hovering draws the data age on the title line, Amaya's Today count and net, and the hint", function()
    local button, dot = LogIn()
    button.scripts.OnEnter(button)
    local title, now = Stubs.tooltip.doubles[1], Stubs.tooltip.doubles[2]
    assert_eq({ title[1], title[2], Stubs.tooltip.shown }, { "AHProfit", "data 1 h old", true })
    assert_eq(now[1], "Now on Amaya")
    assert_eq(now[2]:match("^1 action · %+") ~= nil, true, now[2])
    assert_eq(Stubs.tooltip.lines[#Stubs.tooltip.lines], "Click: open AHProfit · Drag: move")
    assert_eq(dot.shown, false, "fresh data, a usable plan: no dot")
    button.scripts.OnLeave(button)
    assert_eq(Stubs.tooltip.shown, false)
    Stubs.EnterCombat()
    button.scripts.OnEnter(button)
    assert_eq({ #Stubs.tooltip.doubles, Stubs.tooltip.lines[#Stubs.tooltip.lines] },
              { 1, "Click: open AHProfit · Drag: move" }, "in combat: the age and the hint only")
    Stubs.LeaveCombat()
    AHProfit.MainWindow.Today = function() error("boom") end
    button.scripts.OnEnter(button)
    assert_eq(Stubs.tooltip.doubles[2][2], "couldn't read the plan")
  end)

  it("UI20 data that turns 24 h old during the session gets its dot from the timer set at login", function()
    local _, dot = LogIn(false, NOW - 24 * UIWorld.HOUR + 30000)
    assert_eq(dot.shown, false)
    Stubs.Advance(31)
    assert_eq(dot.shown, true)
  end)
end)
