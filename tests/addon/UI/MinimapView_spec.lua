-- Specs for addon/AHProfit/UI/MinimapView.lua: the minimap button's tooltip, dot and place (SPEC §4.3, UI20, UI21).

local Stubs = require("wow_stubs")

--- Fresh stubs with the view model and the minimap view loaded.
---@return table  AHProfit.MinimapView
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "UI/ViewModel.lua", "UI/MinimapView.lua" })
  return AHProfit.MinimapView
end

local FRESH = { text = "data 9 min old", amber = false }
local OLD = { text = "data 30 h old", amber = true }

--- Round to a tenth, for comparing offsets.
---@param x number
---@return number
local function Tenth(x)
  return math.floor(x * 10 + 0.5) / 10
end

describe("MinimapView", function()
  it("UI20 the tooltip says the data age and this character's Today count and net; a problem replaces it with a dot", function()
    local View = Boot()
    local tip = View.Tip({ age = FRESH, name = "Willie", today = { count = 3, total = "+2g 10s" } })
    assert_eq({ tip.title, tip.age, tip.ageAmber, tip.lines, tip.problem, tip.dot, tip.hint },
              { "AHProfit", "data 9 min old", false, { { left = "Now on Willie", right = "3 actions · +2g 10s" } }, nil,
                false, "Click: open AHProfit · Drag: move" })
    assert_eq(View.Tip({ age = FRESH, name = "Willie", today = { count = 1, total = "+45c" } }).lines[1].right,
              "1 action · +45c")
    assert_eq(View.Tip({ age = FRESH, name = "Willie", today = { count = 0, total = "+0c" } }).lines[1].right,
              "nothing due now")
    assert_eq(View.Tip({ age = FRESH, name = "Willie" }).lines[1].right, "no plan yet: run AHSync")
    assert_eq(View.Tip({ age = FRESH, name = "Willie", failed = true }).lines[1].right, "couldn't read the plan")
    local old = View.Tip({ age = OLD, name = "Willie", today = { count = 3, total = "+2g 10s" } })
    assert_eq({ old.age, old.ageAmber, old.dot }, { "data 30 h old", true, true })
    local missing = View.Tip({ age = { text = "no data", amber = true }, name = "Willie",
                               marketProblem = "No market data yet. Run AHSync, then /reload." })
    assert_eq({ missing.problem, missing.lines, missing.dot },
              { "No market data yet. Run AHSync, then /reload.", {}, true })
    assert_eq(View.Tip({ age = FRESH, name = "Willie", planStale = true }).dot, true, "a plan from another version")
    local fight = View.Tip({ age = FRESH, name = "Willie", inCombat = true })
    assert_eq({ fight.lines, fight.age, fight.hint }, { {}, "data 9 min old", "Click: open AHProfit · Drag: move" })
    local day = 24 * 3600000
    assert_eq({ View.SecondsUntilStale(1000000 - day + 30000, 1000000, day), View.SecondsUntilStale(1000000 - day - 1, 1000000, day),
                View.SecondsUntilStale(nil, 1000000, day) }, { 31, nil, nil })
  end)

  it("UI21 the button's offset on a 140 px minimap's rim, and the whole-degree angle a drop snaps to", function()
    local View = Boot()
    local radius = View.Radius(140)
    assert_eq(radius, 80)
    local function At(angle)
      local x, y = View.Offset(angle, radius)
      return { Tenth(x), Tenth(y) }
    end
    assert_eq({ At(225), At(0), At(90) }, { { -56.6, -56.6 }, { 80, 0 }, { 0, 80 } })
    assert_eq(View.AngleOf(30, 52), 60)
    assert_eq(At(60), { 40, 69.3 })
    assert_eq({ View.AngleOf(-10, 0), View.AngleOf(0, -10), View.AngleOf(0, 0) }, { 180, 270, 225 })
    assert_eq({ View.SavedAngle(nil), View.SavedAngle({}), View.SavedAngle({ angle = "x" }), View.SavedAngle({ angle = 400 }) },
              { 225, 225, 225, 40 })
    assert_eq({ View.SavedAngle({ angle = math.huge }), View.SavedAngle({ angle = -math.huge }), View.SavedAngle({ angle = -120 }) },
              { 225, 225, 240 })
  end)
end)
