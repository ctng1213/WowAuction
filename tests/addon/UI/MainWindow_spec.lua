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
    AHProfitDB.inventory["Amaya-Grommash"] = nil                    -- nothing carried: only the flip's buy
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
    Stubs.world.money = 10000000                          -- 1000g: the mats are well within her live cap (A64)
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
    assert_eq(ui.rows[2].note:GetText(), "post Sat 10:00, for the 18:00 peak · on the AH now from 38c")
    assert_eq(ui.footer:GetText(), VM.WithCoins("1 flip · capital needed 8s · 1 gone"))
  end)

  it("A66 a gone flip stays on the Flip tab, greyed below the others, and costs no capital", function()
    Stubs.Reset()
    Stubs.world.money = 10000
    UIWorld.Market({ [4306] = UIWorld.Item(50, 86, 48), [2589] = UIWorld.Item(10, 80, 60) }, NOW - UIWorld.HOUR)
    UIWorld.Plan({ { id = "s1", kind = "buy", key = 4306, qty = 5, price = 50, at = NOW, net = -250 },
                   { id = "s2", kind = "post", key = 4306, qty = 5, price = 90, at = NOW, net = 400, parent = "s1", chance = 80 },
                   { id = "l1", kind = "buy", key = 2589, qty = 4, price = 10, at = NOW, net = -40 },
                   { id = "l2", kind = "post", key = 2589, qty = 4, price = 20, at = NOW, net = 60, parent = "l1", chance = 80 } })
    Stubs.Login()
    AHProfit.Scan.Replicate.LastBook = function()
      return { at = NOW + 60000, book = { [4306] = { [60] = { [26] = 1 } }, [2589] = { [9] = { [21] = 1 } } } }
    end
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("flip")
    local ui, VM = AHProfit.MainWindow.Frames(), AHProfit.ViewModel
    assert_eq({ ui.rows[1].cells.flip.item:GetText(), ui.rows[3].cells.flip.item:GetText() }, { "Linen Cloth", "Silk Cloth" })
    assert_eq(ui.rows[4].note:GetText(), "gone: nothing on the AH at or below 50c now")
    assert_eq(ui.footer:GetText(), VM.WithCoins("1 flip · capital needed 40c · 1 gone"))
  end)

  it("A30 the Craft tab buys mats from a vendor and says so, up to the product's liquidity cap", function()
    Stubs.Reset()
    Stubs.world.money = 10000000                          -- 1000g: the mats are well within her live cap (A64)
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
    Stubs.world.money = 10000000                          -- 1000g: the mats are well within her live cap (A64)
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
    Stubs.world.money = 10000000                          -- 1000g: the mats are well within her live cap (A64)
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
    assert_eq(kinds, { "POST Linen Cloth" })                     -- the wood's vendor sale is on Inventory, not Today (A73)
    AHProfit.MainWindow.ShowTab("inventory")
    local notes = {}
    for _, row in ipairs(ui.rows) do
      if row:IsShown() and row.note:IsShown() then notes[#notes + 1] = row.note:GetText() end
    end
    local vendored = false
    for _, text in ipairs(notes) do vendored = vendored or text == "Vendor at 9c each" end
    assert_eq(vendored, true)
    AHProfit.MainWindow.ShowTab("craft")
    assert_eq(ui.empty:GetText(), "None of your recipes pays right now.")
    Stubs.ITEM_NAMES[4470], Stubs.ITEM_NAMES[4471], Stubs.ITEM_SELL[4470] = nil, nil, nil
  end)

  it("A36 once you hold what a craft is worth making, its row shows a check and \"made\"", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    Stubs.ITEM_NAMES[4471], Stubs.ITEM_NAMES[4470] = "Basic Campfire Kit", "Simple Wood"
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 4470, count = 1 } } } }
    UIWorld.Market({ [4471] = UIWorld.Item(99, 80, 10, { postPrice = 99, chance = 1, refStack = 1, supply = 2 }) }, NOW)
    AHProfitMarket.vendor = { [4470] = 38 }
    Stubs.Login()
    AHProfitDB.knownRecipes["Amaya-Grommash"] = "6413"
    AHProfitDB.recipes.Survival = { [6413] = { product = 4471, yieldMin = 1, yieldMax = 1, reagents = "4470,1" } }
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("craft")
    local ui, VM = AHProfit.MainWindow.Frames(), AHProfit.ViewModel
    assert_eq({ FirstRow("craft").crafts, ui.rows[2].note:GetText() }, { "×1", "use 1 owned Simple Wood" })
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 4471, count = 1 } } } }   -- crafted
    Stubs.FireEvent("BAG_UPDATE_DELAYED")
    Stubs.Advance(2)
    local row = FirstRow("craft")
    assert_eq({ row.source, row.crafts, row.mats, row.profit, ui.rows[2].note:GetText(), ui.footer:GetText() },
              { VM.CHECK .. " made", "×1", "—", "—", "×1 in your bags · post it at the AH", "0 of 1 known recipe pay now · 1 made" })
    Stubs.ITEM_NAMES[4471], Stubs.ITEM_NAMES[4470] = nil, nil
  end)

  it("A37 a made product's note says when to sell it: the plan's peak, a hold, or now", function()
    Boot()
    local VM = AHProfit.ViewModel
    local look = function() return { name = "Basic Campfire Kit", quality = 1 } end
    local made = { { key = 4471, spell = 1, made = 1, profit = 0 } }
    local SUN9, SUN17 = 1760259600000, 1760288400000                -- Sun 12 Oct 09:00 and 17:00 UTC
    local function Note(actions) return VM.CraftRows(made, look, 0, actions, NOW, 0)[1].notes[1] end
    assert_eq({ Note({ { id = "p", kind = "post", key = 4471, qty = 1, price = 99, at = SUN9, peak = SUN17 } }),
                Note({ { id = "h", kind = "hold", key = 4471, qty = 1, at = NOW } }),
                Note({ { id = "o", kind = "post", key = 4471, qty = 1, price = 80, at = NOW, own = true } }), Note({}) },
              { "×1 in your bags · post Sun 09:00, for the 17:00 peak", "×1 in your bags · hold for a later week",
                "×1 in your bags · post it now at 80c", "×1 in your bags · post it at the AH" })
  end)

  it("A42 a made product you have up on the AH stays made, with when it ends, until the auction ends", function()
    Stubs.Reset()
    Stubs.world.money = 10000000                          -- 1000g: the mats are well within her live cap (A64)
    _G.AHProfitPlan = nil
    Stubs.ITEM_NAMES[4471], Stubs.ITEM_NAMES[4470] = "Basic Campfire Kit", "Simple Wood"
    UIWorld.Market({ [4471] = UIWorld.Item(99, 80, 10, { postPrice = 99, chance = 1, refStack = 1, supply = 2 }) }, NOW)
    AHProfitMarket.vendor = { [4470] = 38 }
    Stubs.Login()
    AHProfitDB.knownRecipes["Amaya-Grommash"] = "6413"
    AHProfitDB.recipes.Survival = { [6413] = { product = 4471, yieldMin = 1, yieldMax = 1, reagents = "4470,1" } }
    AHProfitDB.ownAuctions["Amaya-Grommash"] = { at = NOW, rows = "4471,80,1,3,1", ends = "4471,1," .. (NOW + 11 * 3600000 + 60000) }
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("craft")
    local ui, VM = AHProfit.MainWindow.Frames(), AHProfit.ViewModel
    local row = FirstRow("craft")
    assert_eq({ row.source, row.crafts, ui.rows[2].note:GetText(), ui.rows[3]:IsShown() },
              { VM.CHECK .. " made", "×1", "×1 on the AH · ends in 11 h", false })
    Stubs.Advance(12 * 3600)                                      -- the auction has ended
    AHProfit.MainWindow.Refresh()
    assert_eq({ FirstRow("craft").source, ui.rows[2].note:GetText() }, { "Vendor", "buy 1 Simple Wood from a vendor · 38c" })
    Stubs.ITEM_NAMES[4471], Stubs.ITEM_NAMES[4470] = nil, nil
  end)

  it("A49 a plan post of an item you no longer hold leaves Today; bank units count; a chain's post stays", function()
    Stubs.Reset()
    Stubs.ITEM_NAMES[1529], Stubs.ITEM_NAMES[4471], Stubs.ITEM_NAMES[4470] = "Tiger's Eye", "Basic Campfire Kit", "Simple Wood"
    Stubs.world.containers = { [0] = { size = 4, slots = { [1] = { id = 1529, count = 1 }, [2] = { id = 2589, count = 15 } } } }
    UIWorld.Market({ [1529] = UIWorld.Item(900, 80, 5), [2589] = UIWorld.Item(45, 86, 48), [4471] = UIWorld.Item(99, 80, 10) }, NOW)
    UIWorld.Plan({ { id = "t", kind = "post", key = 1529, qty = 1, price = 900, at = NOW, net = 800, chance = 80 },
                   { id = "l", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1000, chance = 90 },
                   { id = "w", kind = "buy", key = 4470, qty = 7, price = 38, at = NOW, net = 0, vendor = true },
                   { id = "k", kind = "post", key = 4471, qty = 7, price = 99, at = NOW, net = 392, parent = "w", chance = 100 } })
    Stubs.Login()
    AHProfitDB.inventory["Amaya-Grommash"].bank = "2589,5,0"
    Stubs.Slash("/ahp")
    local ui = AHProfit.MainWindow.Frames()
    --- The Today rows as "item ×qty".
    local function Rows()
      local out = {}
      for _, r in ipairs(ui.rows) do
        local c = r.cells.today
        if r:IsShown() and c.item:IsShown() then out[#out + 1] = c.item:GetText() .. " " .. c.qty:GetText() end
      end
      table.sort(out)
      return out
    end
    assert_eq(Rows(), { "Basic Campfire Kit ×7", "Linen Cloth ×20", "Simple Wood ×7", "Tiger's Eye ×1" })
    Stubs.world.containers[0].slots[1] = nil                       -- the Tiger's Eye is on the AH now
    Stubs.FireEvent("BAG_UPDATE_DELAYED")
    Stubs.Advance(2)
    assert_eq(Rows(), { "Basic Campfire Kit ×7", "Linen Cloth ×20", "Simple Wood ×7" })
    Stubs.ITEM_NAMES[1529], Stubs.ITEM_NAMES[4471], Stubs.ITEM_NAMES[4470] = nil, nil, nil
  end)

  it("A50 the Inventory tab lists your auctions with what they bring and when they end", function()
    Stubs.Reset()
    Stubs.ITEM_NAMES[4471] = "Basic Campfire Kit"
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW)
    UIWorld.Plan({})
    Stubs.Login()
    AHProfitDB.ownAuctions["Amaya-Grommash"] = { at = NOW, rows = "4471,80,1,3,1",
      ends = "4471,1," .. (NOW + 11 * 3600000 + 60000) .. ",80;2447,2," .. (NOW - 60000) .. ",48" }   -- the Peacebloom has ended
    Stubs.Slash("/ahp")
    AHProfit.MainWindow.ShowTab("inventory")
    local ui, row = AHProfit.MainWindow.Frames(), FirstRow("inventory")
    assert_eq({ row.item, row.where, row.qty, row.worth }, { "Basic Campfire Kit", "AH", "×1", AHProfit.ViewModel.WithCoins("76c") })
    assert_eq({ ui.rows[2].note:GetText(), ui.rows[3]:IsShown() }, { "listed at 80c each · ends in 11 h", false })
    Stubs.ITEM_NAMES[4471] = nil
  end)

  it("A52 record lines under each row and a Needs a decision section for items not selling", function()
    Stubs.Reset()
    Stubs.ITEM_NAMES[2318] = "Heavy Leather"
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48), [2318] = UIWorld.Item(50, 70, 10) }, NOW)
    UIWorld.Plan({ { id = "l", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1000, chance = 92 },
                   { id = "h", kind = "post", key = 2318, qty = 5, price = 47, at = NOW, net = 100, chance = 60 } })
    AHProfitPlan.records = { [2318] = "1;Your record (30 d): 0 of 3 sold · expired 3× while cheapest",
                             [2589] = "0;Your record (30 d): 2 of 2 sold" }
    Stubs.Login()
    Stubs.Slash("/ahp")
    local ui, texts = AHProfit.MainWindow.Frames(), {}
    for _, r in ipairs(ui.rows) do
      if r:IsShown() then
        if r.note:IsShown() then texts[#texts + 1] = "note: " .. r.note:GetText()
        elseif r.dayLabel:IsShown() then texts[#texts + 1] = "header: " .. r.dayLabel:GetText()
        else texts[#texts + 1] = r.cells.today.action:GetText() .. " " .. r.cells.today.item:GetText() end
      end
    end
    assert_eq(texts, { "POST Linen Cloth", "note: Your record (30 d): 2 of 2 sold",
                       "POST Heavy Leather", "note: Your record (30 d): 0 of 3 sold · expired 3× while cheapest",
                       "header: Needs a decision", "DECIDE Heavy Leather",
                       "note: Your record (30 d): 0 of 3 sold · expired 3× while cheapest", "note: right-click: Blacklist or Keep" })
    AHProfit.MainWindow.ShowTab("week")
    local weekNotes = {}
    for _, r in ipairs(ui.rows) do
      if r:IsShown() and r.note:IsShown() and r.note:GetText():match("^Your record") then weekNotes[#weekNotes + 1] = r.note:GetText() end
    end
    assert_eq(#weekNotes, 2)
    Stubs.ITEM_NAMES[2318] = nil
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
    _G.AHProfitMarket = { schemaVersion = 1, exportVersion = 4, generatedAt = NOW, charMarkets = {},
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

  it("A5 an engine error during a re-rank keeps the last good list and shows a status line, no popup", function()
    local MainWindow = Boot()
    Stubs.Slash("/ahp")
    local before = FirstRow("today").item
    local VM, today = AHProfit.ViewModel, AHProfit.ViewModel.Today
    VM.Today = function() error("boom") end
    MainWindow.Refresh()                                       -- e.g. a finished scan re-ranks the window
    VM.Today = today
    assert_eq(FirstRow("today").item, before)
    assert_eq(MainWindow.Frames().footer:GetText(), "couldn't update: boom (showing the last good list)")
    MainWindow.Refresh()
    assert_eq(MainWindow.Frames().footer:GetText():find("couldn't", 1, true), nil)
  end)

  it("FM34 files from an older AHSync leave the tabs empty and the footer says to restart AHSync", function()
    --- Log in with a Linen market and plan, their export versions changed, and open the window.
    ---@param market number|nil  The market file's exportVersion
    ---@param plan number|nil    The plan file's exportVersion
    ---@return table             The window's frames
    local function Open(market, plan)
      Stubs.Reset()
      UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW - UIWorld.HOUR)
      UIWorld.Plan({ { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1046, chance = 92 } })
      AHProfitMarket.exportVersion, AHProfitPlan.exportVersion = market, plan
      Stubs.Login()
      Stubs.Slash("/ahp")
      return AHProfit.MainWindow.Frames()
    end
    local ui = Open(0, nil)                                     -- both stale: the market's problem shows
    assert_eq({ ui.rows[1]:IsShown(), ui.empty:GetText(), ui.footer:GetText() }, { false, "AHSync is older than this addon: restart AHSync, then /reload", "AHSync is older than this addon: restart AHSync, then /reload" })
    AHProfit.MainWindow.ShowTab("week")
    assert_eq({ ui.rows[1]:IsShown(), ui.footer:GetText() }, { false, "AHSync is older than this addon: restart AHSync, then /reload" })
    ui = Open(4, 0)                                             -- only the plan is stale: its rows are gone, and why
    assert_eq({ ui.footer:GetText(), #AHProfit.Plan.Actions("Amaya-Grommash") }, { "AHSync is older than this addon: restart AHSync, then /reload", 0 })
  end)

  it("FM35 files from a newer AHSync say to deploy the addon in the footer", function()
    Stubs.Reset()
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW - UIWorld.HOUR)
    UIWorld.Plan({})
    AHProfitMarket.exportVersion, AHProfitPlan.exportVersion = 5, 5
    Stubs.Login()
    Stubs.Slash("/ahp")
    assert_eq(AHProfit.MainWindow.Frames().footer:GetText(), "This addon is older than AHSync's files: run tools/deploy.py, then /reload")
  end)
end)
