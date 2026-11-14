-- Specs for addon/AHProfit/UI/RowMenu.lua: keeping items for yourself from a right-click (A25).

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")
local AHWorld = require("ah_world")

local NOW = 1760000000000
local WATER = 159

--- Log in carrying 13 Refreshing Spring Water that sells at 24c (90%), and open the AH.
---@return table  The AH panel's frames
local function Boot()
  Stubs.Reset()
  Stubs.ITEM_NAMES[WATER] = "Refreshing Spring Water"
  _G.AHProfitPlan = nil
  Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = WATER, count = 13 } } } }
  UIWorld.Market({ [WATER] = UIWorld.Item(25, 86, 40, { postPrice = 24, sellValue = 22, chance = 0.9 }) }, NOW)
  Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse({ auctions = {} }))
  Stubs.Login()
  AHWorld.Install()
  Stubs.FireEvent("AUCTION_HOUSE_SHOW")
  return AHProfit.AHPanel.Frames()
end

--- The quantity the panel suggests posting, or nil when it suggests nothing.
---@param panel table
---@return string|nil
local function Suggested(panel)
  local row = panel.post[1]
  return row:IsShown() and row.qty:GetText() or nil
end

describe("RowMenu", function()
  it("A25 keep at least 10, then all, then sell normally again", function()
    local panel = Boot()
    assert_eq(Suggested(panel), "×13")
    panel.post[1]:Click("RightButton")
    local menu = AHProfit.RowMenu.Frames()
    assert_eq({ menu.frame:IsShown(), menu.title:GetText(), menu.amount:IsShown() }, { true, "Refreshing Spring Water", false })
    menu.keepSome:Click()
    menu.amount:SetText("10")
    menu.ok:Click()
    assert_eq({ menu.frame:IsShown(), AHProfitDB.reserves["Amaya-Grommash"], Suggested(panel) }, { false, "159,10", "×3" })
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("inventory")
    assert_eq(AHProfit.MainWindow.Frames().rows[2].note:GetText(), AHProfit.ViewModel.WithCoins("Keeping 10 for yourself · Post now at 24c"))

    panel.post[1]:Click("RightButton")
    menu.keepAll:Click()
    assert_eq({ AHProfitDB.reserves["Amaya-Grommash"], Suggested(panel) }, { "159,-1", nil })
    AHProfit.MainWindow.Refresh()
    assert_eq(AHProfit.MainWindow.Frames().rows[2].note:GetText(), "Kept for yourself")

    AHProfit.MainWindow.Frames().rows[1]:GetScript("OnMouseUp")(AHProfit.MainWindow.Frames().rows[1], "RightButton")
    menu.sell:Click()
    assert_eq({ AHProfitDB.reserves["Amaya-Grommash"], Suggested(panel) }, { nil, "×13" })
    Stubs.ITEM_NAMES[WATER] = nil
  end)

  it("Enter in the amount box saves it; 0 or Cancel keeps nothing", function()
    local panel = Boot()
    panel.post[1]:Click("RightButton")
    local menu = AHProfit.RowMenu.Frames()
    menu.keepSome:Click()
    menu.amount:SetText("5")
    menu.amount:GetScript("OnEnterPressed")(menu.amount)
    assert_eq(AHProfitDB.reserves["Amaya-Grommash"], "159,5")
    panel.post[1]:Click("RightButton")
    menu.keepSome:Click()
    menu.amount:SetText("0")
    menu.ok:Click()
    assert_eq(AHProfitDB.reserves["Amaya-Grommash"], nil)
    panel.post[1]:Click("RightButton")
    menu.cancel:Click()
    assert_eq({ menu.frame:IsShown(), AHProfitDB.reserves["Amaya-Grommash"] }, { false, nil })
    Stubs.ITEM_NAMES[WATER] = nil
  end)

  it("A53 the menu sets an item rule for AHSync, and the decision row goes", function()
    local panel = Boot()
    UIWorld.Plan({})
    AHProfitPlan.records = { [WATER] = "1;Your record (30 d): 0 of 3 sold" }
    AHProfit.Plan.Reset()
    Stubs.Slash("/ahp")
    local ui = AHProfit.MainWindow.Frames()
    --- The shown DECIDE row, if any.
    local function DecideRow()
      for _, r in ipairs(ui.rows) do
        if r:IsShown() and r.cells.today.action:IsShown() and r.cells.today.action:GetText() == "DECIDE" then return r end
      end
    end
    local row = DecideRow()
    row:GetScript("OnMouseUp")(row, "RightButton")
    local menu = AHProfit.RowMenu.Frames()
    menu.noBuy:Click()
    assert_eq({ AHProfitDB.rules[WATER].rule, AHProfitDB.rules[WATER].setAt ~= nil, menu.frame:IsShown(), DecideRow() },
              { "no_buy", true, false, nil })
    panel.post[1]:Click("RightButton")
    menu.keepSelling:Click()
    assert_eq(AHProfitDB.rules[WATER].rule, "keep")
    menu.ignore:GetScript("OnClick")()                            -- the menu is closed: no item, nothing set
    assert_eq(AHProfitDB.rules[WATER].rule, "keep")
    Stubs.ITEM_NAMES[WATER] = nil
  end)
end)

