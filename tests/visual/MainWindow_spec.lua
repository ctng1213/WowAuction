-- Layout snapshots of the /ahp window (SPEC §8.4 layer 2, UI1-UI4, UI9). Each scenario draws the
-- window with the stub frames, runs the automatic layout checks and compares the frame tree with
-- tests/visual/golden/<scenario>.txt. `python tools/visual.py --update` accepts a change, and
-- `--html` draws it for comparison with docs/mockups/addon.html.

local Stubs = require("wow_stubs")
local Layout = require("layout")
local UIWorld = require("ui_world")

local NOW = 1760000000000
local KINDS = { "buy", "post", "craft", "de", "vendor", "post" }

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
                   at = NOW, net = (i % 4 == 0) and -20 * i or 150 * i, chance = kind == "post" and 60 + i or nil,
                   char = i % 2 == 0 and "Brugan-Grommash" or nil }
  end
  UIWorld.Plan(actions)
  Stubs.Login()
  AHProfitDB.characters["Brugan-Grommash"] = { gold = 104000 }
  Stubs.Slash("/ahp")
  Stubs.ITEM_NAMES[6586] = nil
  return AHProfit.MainWindow.Frames()
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
    assert_eq(snapshot:find('"Scouting Glo[^"]*…"') ~= nil, true)
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
end)
