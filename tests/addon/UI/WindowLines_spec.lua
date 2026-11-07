-- Specs for addon/AHProfit/UI/WindowLines.lua: what each tab of the window gathers (SPEC §4.3). The Portfolio tab's
-- numbers are PortfolioView's (A79, A84) and the plan's Today rows PlanView's; these check which one a tab gets.

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")
local World = require("portfolio_world")

local NOW = 1760000000000

describe("WindowLines", function()
  it("the Portfolio tab shows the live reselection when the plan has a snapshot, its top line and capacity", function()
    World.Name(Stubs)
    World.Boot(Stubs, World.Mockup())
    local out = AHProfit.WindowLines.Lines("portfolio", World.NOW, 314000, 50)
    assert_eq({ out.own, out.top.text:sub(1, 17), out.right, out.lines[1].label, out.footer },
              { nil, "portfolio 35g 58s", "capacity 6g 49s 40c · 24s 40c unallocated", "Positions",
                "funded +92s 62c · cost 2g 52s · 37% on cash" })
    World.Unname(Stubs)
  end)

  it("without a snapshot it shows the plan's actions due now and the addon's own options, as Today did", function()
    Stubs.Reset()
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW - UIWorld.HOUR)
    UIWorld.Plan({ { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1046, chance = 92 },
                   { id = "q", kind = "post", key = 2589, qty = 1, price = 55, at = NOW, net = 20, chance = 30 } })
    Stubs.Login()
    local out = AHProfit.WindowLines.Lines("portfolio", NOW, 314000, 50)
    assert_eq({ out.own, out.top, out.lines[1].action, out.lines[1].item, out.footer },
              { true, nil, "POST", "Linen Cloth", "1 action · expected +10s 46c · 1 hidden (chance < 50%)" })
    assert_eq({ #AHProfit.WindowLines.TodayRows("Amaya-Grommash", 0), #AHProfit.WindowLines.TodayRows("Amaya-Grommash", 50) },
              { 2, 1 }, "the Post list's rows keep the window's Min chance")
    local flip = AHProfit.WindowLines.Lines("flip", NOW, 314000, 50)
    assert_eq(flip.empty, "No flips right now: none in AHSync's plan, and no market offer under its buy price.")
  end)
  it("A94 every tab builds on the live Forever client, which has no global GetItemInfo", function()
    Stubs.Reset()
    Stubs.LiveForeverClient()
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW - UIWorld.HOUR)
    UIWorld.Plan({ { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1046, chance = 92 } })
    Stubs.Login()
    for _, tab in ipairs({ "portfolio", "disenchant", "craft", "flip", "inventory", "reservations" }) do
      local ok, out = pcall(AHProfit.WindowLines.Lines, tab, NOW, 314000, 50)
      assert_eq({ tab, ok, type(out) }, { tab, true, "table" }, tostring(out))
    end
    assert_eq(AHProfit.WindowLines.Lines("portfolio", NOW, 314000, 50).lines[1].item, "Linen Cloth")
    assert_eq(AHProfit.Events.Errors(), {}, "login and capture raised nothing on the live client")
  end)
  it("A95 the Craft tab says when none pays only at the default chance, because sales rates are unknown", function()
    Stubs.Reset()
    Stubs.Login()
    local WL = AHProfit.WindowLines
    local recipes = { { product = 2318 }, { product = 4239 } }
    local unknown = { [2318] = { volumeUnknown = true, chance = 0.5 }, [4239] = { volumeUnknown = true, chance = 0.5 } }
    assert_eq(WL.CraftEmptyText(recipes, function(k) return unknown[k] end),
              "None of your recipes pays at a 50% chance to sell: sales rates aren't known yet for this market.")
    local known = { [2318] = { volumeUnknown = false, chance = 0.9 }, [4239] = { volumeUnknown = false, chance = 0.8 } }
    assert_eq(WL.CraftEmptyText(recipes, function(k) return known[k] end), "None of your recipes pays right now.")
    assert_eq(WL.CraftEmptyText({}, function() return nil end),
              "No recipes seen on this character yet. Open each profession window once.")
  end)
end)
