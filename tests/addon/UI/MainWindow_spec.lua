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

  it("every column header on every tab has a tooltip text", function()
    Boot()
    for tab, columns in pairs(AHProfit.MainWindow.COLUMNS) do
      for _, c in ipairs(columns) do
        if c[1] ~= "" then assert_eq(AHProfit.ViewModel.COLUMN_HELP[c[1]] ~= nil, true, tab .. ": " .. c[1]) end
      end
    end
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

  it("A15 with no plan at all, Today shows the addon's own row for what you carry", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2589, count = 20 } } } }
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48, { postPrice = 45, sellValue = 42, chance = 0.9 }) }, NOW)
    Stubs.Login()
    Stubs.Slash("/ahp")
    local row, ui = FirstRow("today"), AHProfit.MainWindow.Frames()
    assert_eq({ row.action, row.qty, row.net, row.chance:match("^%d+%%") }, { "POST", "×20", AHProfit.ViewModel.WithCoins("+8s 40c"), "90%" })
    assert_eq(ui.rows[2].note:GetText(), "post now · AHSync has no plan for it")
  end)

  it("A2 a finished AH scan drops gone flips from the open window and says so", function()
    local MainWindow = Boot()
    Stubs.Slash("/ahp")
    AHProfit.Scan.Replicate.LastBook = function() return { at = NOW + 60000, book = { [2840] = {} } } end
    UIWorld.Plan({ { id = "b", kind = "buy", key = 2840, qty = 6, price = 500, at = NOW, net = 300 } })
    AHProfit.Plan.Reset()
    MainWindow.Refresh()
    assert_eq(MainWindow.Frames().footer:GetText(), AHProfit.ViewModel.WithCoins("0 actions · expected 0c · 1 gone"))
  end)

  it("A9 with no plan, a known recipe and its mats in the bags, Today offers the craft and not the mats", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2840, count = 6 } } } }
    UIWorld.Market({ [6217] = UIWorld.Item(4100, 86, 6, { postPrice = 4000, chance = 1, sellValue = 3800, refStack = 1 }),
                     [2840] = UIWorld.Item(5000, 80, 40, { postPrice = 1000, sellValue = 950 }) }, NOW)   -- AH bars too dear
    Stubs.Login()
    AHProfitDB.knownRecipes["Amaya-Grommash"] = "2663"
    AHProfitDB.recipes.Blacksmithing = { [2663] = { product = 6217, yieldMin = 1, yieldMax = 1, reagents = "2840,2" } }
    Stubs.Slash("/ahp")
    local ui = AHProfit.MainWindow.Frames()
    assert_eq({ FirstRow("today").action, FirstRow("today").item, FirstRow("today").qty }, { "CRAFT", "Copper Rod", "×3" })
    assert_eq({ ui.rows[2].note:GetText(), ui.rows[3]:IsShown() }, { "from your own mats · AHSync has no plan for it", false })
  end)

  it("A27 the Craft tab lists each known recipe that pays, with its mats", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2840, count = 4 } } } }
    UIWorld.Market({ [6217] = UIWorld.Item(4100, 86, 6, { postPrice = 4000, chance = 1, sellValue = 3800, refStack = 1 }),
                     [2840] = UIWorld.Item(1000, 80, 40, { curQty = 2, postPrice = 1000, sellValue = 950 }),
                     [4240] = UIWorld.Item(100, 80, 4, { postPrice = 100, chance = 1, sellValue = 90 }) }, NOW)
    Stubs.Login()
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("craft")
    local ui = AHProfit.MainWindow.Frames()
    assert_eq(ui.empty:GetText(), "No recipes seen on this character yet. Open each profession window once.")
    AHProfitDB.knownRecipes["Amaya-Grommash"] = "2663;3755"
    AHProfitDB.recipes.Blacksmithing = { [2663] = { product = 6217, yieldMin = 1, yieldMax = 1, reagents = "2840,2" } }
    AHProfitDB.recipes.Tailoring = { [3755] = { product = 4240, yieldMin = 1, yieldMax = 1, reagents = "2589,6" } }
    AHProfit.MainWindow.Refresh()
    local row = FirstRow("craft")
    assert_eq({ row.item, row.source, row.crafts, row.mats, row.profit, row.chance:match("^%d+%%") },
              { "Copper Rod", "Mixed", "×3", AHProfit.ViewModel.WithCoins("20s"), AHProfit.ViewModel.WithCoins("+56s"), "100%" })
    assert_eq({ ui.rows[2].note:GetText(), ui.rows[3].note:GetText(), ui.rows[4]:IsShown(), ui.footer:GetText() },
              { "use 4 owned Copper Bar", "buy 2 Copper Bar on the AH · 20s", false, "1 of 2 known recipes pay now" })
  end)

  it("A28 the Flip tab lists the plan's flips, re-checked against the live AH", function()
    Stubs.Reset()
    Stubs.world.money = 10000                                           -- 1g: Linen's 8s buy is 8.0%
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
    AHProfit.Scan.Replicate.LastBook = function()
      return { at = NOW + 60000, book = { [2589] = { [38] = { [101] = 1 } }, [4306] = { [80] = { [26] = 1 } } } }
    end
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("flip")
    local ui, VM = AHProfit.MainWindow.Frames(), AHProfit.ViewModel
    local row = FirstRow("flip")
    assert_eq({ row.item, row.qty, row.buy, row.sell, row.when, row.profit, row.chance:match("^%d+%%"), row.risk },
              { "Linen Cloth", "×20", VM.WithCoins("40c"), VM.WithCoins("55c"), "Sat 18:00", VM.WithCoins("+2s 10c"), "88%", "8.0%" })
    assert_eq({ ui.rows[2].note:GetText(), ui.rows[3]:IsShown() },
              { "post Sat 10:00, for the 18:00 peak · on the AH now from 38c", false })
    assert_eq(ui.footer:GetText(), VM.WithCoins("1 flip · capital needed 8s · 1 gone"))
  end)

  it("A30 the Craft tab buys mats from a vendor and says so, up to the product's liquidity cap", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    Stubs.ITEM_NAMES[4471] = "Basic Campfire Kit"
    Stubs.ITEM_NAMES[4470] = "Simple Wood"
    UIWorld.Market({ [4471] = UIWorld.Item(99, 80, 10, { postPrice = 99, chance = 1, refStack = 1, supply = 14 }) }, NOW)
    AHProfitMarket.vendor = { [4470] = 38 }
    Stubs.Login()
    AHProfitDB.knownRecipes["Amaya-Grommash"] = "6413"
    AHProfitDB.recipes.Survival = { [6413] = { product = 4471, yieldMin = 1, yieldMax = 1, reagents = "4470,1" } }
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("craft")
    local ui, VM = AHProfit.MainWindow.Frames(), AHProfit.ViewModel
    local row = FirstRow("craft")
    -- 7 crafts (14 listed × 0.5) of a 99c kit (94.05c after the cut) from 38c vendor wood.
    assert_eq({ row.item, row.source, row.crafts, row.mats, row.profit },
              { "Basic Campfire Kit", "Vendor", "×7", VM.WithCoins("2s 66c"), VM.WithCoins("+3s 92c") })
    assert_eq(ui.rows[2].note:GetText(), "buy 7 Simple Wood from a vendor · 2s 66c")
    Stubs.ITEM_NAMES[4471], Stubs.ITEM_NAMES[4470] = nil, nil
  end)

  it("A33 buying vendor wood redraws the Craft tab, which then uses it; the cheaper AH wood is no source", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    Stubs.ITEM_NAMES[4471], Stubs.ITEM_NAMES[4470] = "Basic Campfire Kit", "Simple Wood"
    UIWorld.Market({ [4471] = UIWorld.Item(99, 80, 10, { postPrice = 99, chance = 1, refStack = 1, supply = 18 }),
                     [4470] = UIWorld.Item(30, 80, 40, { curQty = 20, sellValue = 60 }) }, NOW)
    AHProfitMarket.vendor = { [4470] = 38 }
    Stubs.Login()
    AHProfitDB.knownRecipes["Amaya-Grommash"] = "6413"
    AHProfitDB.recipes.Survival = { [6413] = { product = 4471, yieldMin = 1, yieldMax = 1, reagents = "4470,1" } }
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("craft")
    local ui, VM = AHProfit.MainWindow.Frames(), AHProfit.ViewModel
    assert_eq({ FirstRow("craft").source, ui.rows[2].note:GetText() }, { "Vendor", "buy 9 Simple Wood from a vendor · 3s 42c" })
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 4470, count = 9 } } } }   -- bought from the vendor
    Stubs.FireEvent("BAG_UPDATE_DELAYED")
    Stubs.Advance(2)
    local row = FirstRow("craft")
    -- Owned wood counts at 38c (not its 60c sell value), so it goes first: 9 × (94.05 − 38).
    assert_eq({ row.source, row.mats, row.profit, ui.rows[2].note:GetText() },
              { "Owned", "—", VM.WithCoins("+5s 4c"), "use 9 owned Simple Wood" })
    Stubs.ITEM_NAMES[4471], Stubs.ITEM_NAMES[4470] = nil, nil
  end)

  it("A34 a craft's mats are listed one per line, by source", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    Stubs.ITEM_NAMES[4240] = "Linen Belt"
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2589, count = 10 } } } }
    UIWorld.Market({ [4240] = UIWorld.Item(2000, 80, 4, { postPrice = 2000, chance = 1, refStack = 1, supply = 2 }),
                     [2589] = UIWorld.Item(45, 86, 48, { sellValue = 42 }),
                     [2592] = UIWorld.Item(33, 80, 40, { curQty = 5, sellValue = 30 }) }, NOW)
    AHProfitMarket.vendor = { [2320] = 10 }
    Stubs.Login()
    AHProfitDB.knownRecipes["Amaya-Grommash"] = "8776"
    AHProfitDB.recipes.Tailoring = { [8776] = { product = 4240, yieldMin = 1, yieldMax = 1, reagents = "2589,10;2320,2;2592,1" } }
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("craft")
    local ui, notes = AHProfit.MainWindow.Frames(), {}
    for i = 2, 4 do notes[#notes + 1] = ui.rows[i].note:GetText() end
    assert_eq(notes, { "use 10 owned Linen Cloth", "buy 2 Coarse Thread from a vendor · 20c", "buy 1 Wool Cloth on the AH · 33c" })
    Stubs.ITEM_NAMES[4240] = nil
  end)

  it("A35 a vendor-sold item is never posted, and a vendor-sold product never crafted", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    Stubs.ITEM_NAMES[4470], Stubs.ITEM_NAMES[4471] = "Simple Wood", "Basic Campfire Kit"
    Stubs.ITEM_SELL[4470] = 9
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 4470, count = 20 }, [2] = { id = 2589, count = 20 } } } }
    UIWorld.Market({ [4470] = UIWorld.Item(60, 80, 40, { postPrice = 55, sellValue = 50, chance = 0.9 }),
                     [2589] = UIWorld.Item(45, 86, 48, { postPrice = 45, sellValue = 42, chance = 0.9 }),
                     [4471] = UIWorld.Item(99, 80, 10, { postPrice = 99, chance = 1, refStack = 1, supply = 18 }) }, NOW)
    AHProfitMarket.vendor = { [4470] = 38, [4471] = 50 }
    Stubs.Login()
    AHProfitDB.knownRecipes["Amaya-Grommash"] = "6413"
    AHProfitDB.recipes.Survival = { [6413] = { product = 4471, yieldMin = 1, yieldMax = 1, reagents = "4470,1" } }
    Stubs.Slash("/ahp")
    local ui, kinds = AHProfit.MainWindow.Frames(), {}
    for i = 1, 4 do
      local c = ui.rows[i].cells.today
      if ui.rows[i]:IsShown() and c.item:IsShown() then kinds[#kinds + 1] = c.action:GetText() .. " " .. c.item:GetText() end
    end
    assert_eq(kinds, { "POST Linen Cloth", "VENDOR Simple Wood" })
    AHProfit.MainWindow.ShowTab("craft")
    assert_eq(ui.empty:GetText(), "None of your recipes pays right now.")
    Stubs.ITEM_NAMES[4470], Stubs.ITEM_NAMES[4471], Stubs.ITEM_SELL[4470] = nil, nil, nil
  end)

  it("A22 the Inventory tab lists what this character holds with its worth and plan", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    Stubs.world.containers = { [0] = { size = 3, slots = { [1] = { id = 2589, count = 20 }, [2] = { id = 6948, count = 1, bound = true } } } }
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48, { postPrice = 45, sellValue = 42, chance = 0.9 }) }, NOW)
    Stubs.Login()
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("inventory")
    local ui = AHProfit.MainWindow.Frames()
    local row = FirstRow("inventory")
    assert_eq({ row.item, row.where, row.qty, row.worth, row.chance:match("^%d+%%") },
              { "Linen Cloth", "bags", "×20", AHProfit.ViewModel.WithCoins("8s 40c"), "90%" })
    assert_eq({ ui.rows[2].note:GetText(), ui.rows[3].cells.inventory.item:GetText(), ui.rows[4].note:GetText() },
              { "Post now at 45c", "Hearthstone", "Bound · not planned" })
    assert_eq({ ui.footer:GetText(), ui.chance:IsShown(), ui.headers.inventory:IsShown() },
              { AHProfit.ViewModel.WithCoins("2 items · worth 8s 40c"), false, true })
  end)

  it("A23 the Disenchant tab is always there, even with no enchanter on the account", function()
    Stubs.Reset()
    local gloves, dust = "800,1,30,1100,1100,,,0,,,1,50,,,1,,0,500;;", "600,20,30,600,600,,,0,2100,,90,80,,,20,600,0,800;;"
    _G.AHProfitMarket = { schemaVersion = 1, generatedAt = NOW, charMarkets = {},
      markets = { [UIWorld.MARKET] = { observationsUntil = NOW, curveHour = 0, I = { [6585] = gloves, [10940] = dust }, C = {},
                                       D = { [6585] = "1,2,18,a" } } } }
    AHProfitMarket.checksum = "3:" .. (#gloves + #dust + #"1,2,18,a")
    Stubs.Login()
    Stubs.Slash("/ahp")
    local ui = AHProfit.MainWindow.Frames()
    assert_eq(ui.tabs.disenchant:GetText(), "Disenchant")
    AHProfit.MainWindow.ShowTab("disenchant")
    local row = FirstRow("disenchant")
    assert_eq({ row.item, row.skill, row.buy, row.chance:match("^%d+%%") },
              { "Scouting Gloves", "1", AHProfit.ViewModel.WithCoins("8s"), "80%" })
    assert_eq(ui.footer:GetText(), "1 target · no enchanter yet: what one could disenchant")
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
    local computes, sell = 0, AHProfit.Options.Sell
    AHProfit.Options.Sell = function(...) computes = computes + 1 return sell(...) end
    MainWindow.Scroll(3)
    MainWindow.Scroll(3)
    assert_eq(computes, 0)                                 -- scrolling only re-pages what was computed
    MainWindow.NextMinChance()
    assert_eq(computes, 1)
    AHProfit.Options.Sell = sell
  end)

  it("a change in the bags recomputes the open window's own rows", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48, { postPrice = 45, sellValue = 42, chance = 0.9 }) }, NOW)
    Stubs.Login()
    Stubs.Slash("/ahp")
    assert_eq(AHProfit.MainWindow.Frames().rows[1]:IsShown(), false)
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2589, count = 20 } } } }
    Stubs.FireEvent("BAG_UPDATE_DELAYED")
    Stubs.Advance(1)
    assert_eq(FirstRow("today").qty, "×20")
  end)

  it("names come from the saved link first, then the client cache, then the key", function()
    local MainWindow = Boot()
    AHProfitDB.itemNames = { [2589] = "2,Linen Cloth of the Whale" }
    Stubs.world.uncached[2840] = true
    Stubs.Slash("/ahp week")
    local ui = MainWindow.Frames()
    assert_eq(ui.rows[2].cells.week.item:GetText(), "Linen Cloth of the Whale")
    assert_eq(ui.rows[4].cells.week.item:GetText(), "Item 2840")
    Stubs.world.uncached[2840] = nil                       -- the client fetched it
    Stubs.FireEvent("GET_ITEM_INFO_RECEIVED", 2840, true)
    Stubs.Advance(1)
    assert_eq(ui.rows[4].cells.week.item:GetText(), "Copper Bar")
    assert_eq(Stubs.IsRegistered("GET_ITEM_INFO_RECEIVED"), false)   -- every name is known: stop listening
  end)

  it("an error while opening is printed to chat, not raised as a Lua error", function()
    local MainWindow = Boot()
    MainWindow.Toggle = function() error("boom") end
    Stubs.Slash("/ahp")
    assert_eq(Stubs.chat[#Stubs.chat]:find("the window failed: .*boom") ~= nil, true)
  end)

  it("A26 the Reservations tab lists what you keep; a click changes it", function()
    Stubs.Reset()
    Stubs.ITEM_NAMES[159] = "Refreshing Spring Water"
    Stubs.world.containers = { [0] = { size = 4, slots = { [1] = { id = 159, count = 13 }, [2] = { id = 4306, count = 7 } } } }
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW - UIWorld.HOUR)
    UIWorld.Plan({})
    Stubs.Login()
    Stubs.Slash("/ahp")
    local MainWindow = AHProfit.MainWindow
    MainWindow.ShowTab("reservations")
    local ui = MainWindow.Frames()
    assert_eq({ ui.empty:IsShown(), ui.empty:GetText() },
              { true, "Nothing kept for yourself. Right-click an item on any tab and pick Keep." })
    AHProfitDB.reserves["Amaya-Grommash"] = "159,10;2589,5;4306,-1"
    MainWindow.Refresh()
    local seen = {}
    for i = 1, 3 do
      local c = ui.rows[i].cells.reservations
      seen[i] = table.concat({ c.item:GetText(), c.keeping:GetText(), c.held:GetText(), c.free:GetText() }, " ")
    end
    assert_eq(seen, { "Linen Cloth 5 ×0 —", "Refreshing Spring Water 10 ×13 ×3", "Silk Cloth All ×7 —" })
    assert_eq(ui.footer:GetText(), "3 items kept · click a row to change it")
    ui.rows[2]:GetScript("OnMouseUp")(ui.rows[2], "LeftButton")
    local menu = AHProfit.RowMenu.Frames()
    assert_eq({ menu.frame:IsShown(), menu.title:GetText() }, { true, "Refreshing Spring Water" })
    menu.sell:Click()
    assert_eq({ AHProfitDB.reserves["Amaya-Grommash"], ui.footer:GetText() }, { "2589,5;4306,-1", "2 items kept · click a row to change it" })
    Stubs.ITEM_NAMES[159] = nil
  end)
end)
