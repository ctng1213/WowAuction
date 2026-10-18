-- UI7: the AH panel's layout, docked to the right of the auction house window (SPEC §4.3, §8.4).

local Stubs = require("wow_stubs")
local Layout = require("layout")
local UIWorld = require("ui_world")
local AHWorld = require("ah_world")

local NOW = 1760000000000

describe("AHPanel layout", function()
  it("UI7 five targets: two found, one gone, two checking; docked to the AH frame's right edge", function()
    Stubs.Reset()
    Stubs.ITEM_NAMES[6586] = "Scouting Gloves of the Monkey"
    local items = { [2589] = UIWorld.Item(45, 86, 48), [6586] = UIWorld.Item(800, 50, 3), [2840] = UIWorld.Item(500, 80, 40),
                    [4306] = UIWorld.Item(70, 80, 60), [2592] = UIWorld.Item(30, 80, 60) }
    UIWorld.Market(items, NOW - 3600000)
    UIWorld.Plan({ { id = "a", kind = "buy", key = 2589, qty = 20, price = 45, at = NOW, net = 200 },
                   { id = "b", kind = "buy", key = 6586, qty = 1, price = 900, at = NOW, net = 300 },
                   { id = "c", kind = "buy", key = 2840, qty = 6, price = 500, at = NOW, net = 50 },
                   { id = "d", kind = "buy", key = 4306, qty = 5, price = 70, at = NOW, net = 50 },
                   { id = "e", kind = "buy", key = 2592, qty = 5, price = 30, at = NOW, net = 50 },
                   { id = "p", kind = "post", key = 6217, qty = 5, price = 4100, at = NOW, net = 6306, chance = 58 } })
    Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse({ auctions = {} }))
    Stubs.world.money = 314000
    Stubs.Login()
    local ah = AHWorld.Install()
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    -- Searches found Linen at 38c and the gloves at 8s (both found), Copper Bar only at 9s (above 5s: gone);
    -- Silk and Wool aren't searched yet (checking). No full scan this visit.
    C_AuctionHouse.GetBrowseResults = function()
      return { { itemKey = { itemID = 2589 }, minPrice = 38, totalQuantity = 20 }, { itemKey = { itemID = 6586 }, minPrice = 800, totalQuantity = 1 },
               { itemKey = { itemID = 2840 }, minPrice = 900, totalQuantity = 6 } }
    end
    Stubs.FireEvent("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED")
    local ui = AHProfit.AHPanel.Frames()
    local snapshot = Layout.Golden("UI7_ah_panel", ui.frame)
    assert_eq(snapshot:find("AHProfitPanel 902,100 260x", 1, true) ~= nil, true)        -- right of the 800 px AH at x 100
    assert_eq(ui.checked:GetText(), "3 of 5")
    Stubs.ITEM_NAMES[6586] = nil
  end)
end)
