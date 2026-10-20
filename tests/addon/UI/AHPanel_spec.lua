-- Specs for addon/AHProfit/UI/AHPanel.lua: the panel at the auction house (SPEC §4.3, A24, FM24).

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")
local AHWorld = require("ah_world")

local NOW = 1760000000000

--- Log in with three planned buys (Linen ≤ 45c, Copper Bar ≤ 5s, Silk ≤ 70c) and a post of the 20 Linen in the bags,
--- then open the AH.
---@return table, table  The panel's frames, the AH window
local function OpenAH()
  Stubs.Reset()
  Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2589, count = 20 } } } }   -- the Linen to post
  UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48), [2840] = UIWorld.Item(500, 80, 40), [4306] = UIWorld.Item(70, 80, 60) },
                 NOW - 3600000)
  UIWorld.Plan({ { id = "a", kind = "buy", key = 2589, qty = 20, price = 45, at = NOW, net = 200 },
                 { id = "b", kind = "buy", key = 2840, qty = 6, price = 500, at = NOW, net = 300 },
                 { id = "c", kind = "buy", key = 4306, qty = 5, price = 70, at = NOW, net = 50 },
                 { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1000, chance = 92 } })
  Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse({ auctions = {} }))
  Stubs.Login()
  local ah = AHWorld.Install()
  Stubs.FireEvent("AUCTION_HOUSE_SHOW")
  return AHProfit.AHPanel.Frames(), ah
end

--- Log in with 3 Copper Rods in bag 0 slot 2 and 4 in bag 1, and a plan to post 5 at 41s now; open the AH.
---@return table, table  The panel's frames, the AH window
local function RodsAtAH()
  Stubs.Reset()
  Stubs.world.containers = { [0] = { size = 2, slots = { [2] = { id = 6217, count = 3 } } },
                             [1] = { size = 1, slots = { [1] = { id = 6217, count = 4 } } } }
  UIWorld.Market({ [6217] = UIWorld.Item(4200, 80, 6) }, NOW - 3600000)
  UIWorld.Plan({ { id = "p", kind = "post", key = 6217, qty = 5, price = 4100, at = NOW, net = 6306, chance = 58 } })
  Stubs.Login()
  local ah = AHWorld.Install()
  Stubs.FireEvent("AUCTION_HOUSE_SHOW")
  return AHProfit.AHPanel.Frames(), ah
end

describe("AHPanel", function()
  it("A24 opens docked to the AH with every target still checking, and closes with it", function()
    local ui = OpenAH()
    assert_eq({ ui.frame:IsShown(), ui.checked:GetText(), ui.shop[1].item:GetText(), ui.shop[1].colA:GetText() },
              { true, "0 of 3", "Linen Cloth", "checking" })
    assert_eq(ui.shop[1].colB:GetText(), AHProfit.ViewModel.WithCoins("45c"))
    assert_eq({ ui.post[1].item:GetText(), ui.post[1].qty:GetText(), ui.postCount:GetText() }, { "Linen Cloth", "×20", "1" })
    Stubs.FireEvent("AUCTION_HOUSE_CLOSED")
    assert_eq(ui.frame:IsShown(), false)
  end)

  it("A24 a search result turns its row found; clicking a row searches it and says how far this visit has got", function()
    local ui, ah = OpenAH()
    ui.shop[2]:Click()
    assert_eq({ ah.searches, ui.status:GetText(), ui.next }, { { "Copper Bar" }, "searched Copper Bar · 1 of 3", nil })
    C_AuctionHouse.GetBrowseResults = function() return { { itemKey = { itemID = 2589 }, minPrice = 38, totalQuantity = 20 } } end
    Stubs.FireEvent("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED")
    assert_eq({ ui.shop[1].colA:GetText(), ui.shop[1].colB:GetText(), ui.checked:GetText() },
              { AHProfit.ViewModel.WithCoins("38c"), AHProfit.ViewModel.WithCoins("45c"), "2 of 3" })
    ui.shop[1]:Click()
    assert_eq(ui.status:GetText(), "searched Linen Cloth · 2 of 3")
  end)

  it("A38 a search that doesn't list the item marks it gone; a random-suffix key matches its item id", function()
    local ui = OpenAH()
    C_AuctionHouse.GetBrowseResults = function() return {} end
    ui.shop[3]:Click()                                            -- Silk: the search lists nothing
    Stubs.FireEvent("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED")
    assert_eq(ui.shop[3].colA:GetText(), "gone")
    local suffixed = 2589 + 2147483648 * 4
    AHProfit.Live.NoteSearch(2589, 30, 5, NOW + 60000)
    assert_eq(AHProfit.Live.Item(suffixed, NOW - 3600000), { { price = 30, units = 5 } })
  end)

  it("FM24 closing the AH forgets this visit's searches; unpriced rows stay checking", function()
    local ui, ah = OpenAH()
    ui.shop[1]:Click()
    Stubs.FireEvent("AUCTION_HOUSE_CLOSED")
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    ui.shop[1]:Click()
    assert_eq({ ah.searches, ui.status:GetText(), ui.shop[2].colA:GetText() },
              { { "Linen Cloth", "Linen Cloth" }, "searched Linen Cloth · 1 of 3", "checking" })
  end)

  it("A39 a buyout lowers the row's quantity and a fully bought row drops off; a bid or a failed purchase does not", function()
    local ui = OpenAH()
    local house = C_AuctionHouse
    house.ConfirmCommoditiesPurchase = function() end
    house.PlaceBid = function() end
    house.GetAuctionInfoByID = function(id) return { itemKey = { itemID = 4306 }, quantity = 2, buyoutAmount = 140 } end
    Stubs.FireEvent("AUCTION_HOUSE_CLOSED")
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")                         -- hooks go in when the AH opens
    house.ConfirmCommoditiesPurchase(2589, 4)
    assert_eq(ui.shop[1].qty:GetText(), "×20")                     -- not until it succeeds
    Stubs.FireEvent("COMMODITY_PURCHASE_SUCCEEDED")
    assert_eq(ui.shop[1].qty:GetText(), "×16")
    house.ConfirmCommoditiesPurchase(2589, 3)
    Stubs.FireEvent("COMMODITY_PURCHASE_FAILED")
    assert_eq(ui.shop[1].qty:GetText(), "×16")
    house.PlaceBid(7, 100)                                        -- a bid: nothing changes
    assert_eq(ui.shop[3].qty:GetText(), "×5")
    house.PlaceBid(7, 140)                                        -- the buyout: 2 Silk, once it completes
    assert_eq(ui.shop[3].qty:GetText(), "×5")
    Stubs.FireEvent("AUCTION_HOUSE_PURCHASE_COMPLETED", 7)
    assert_eq(ui.shop[3].qty:GetText(), "×3")
    house.ConfirmCommoditiesPurchase(2840, 6)
    Stubs.FireEvent("COMMODITY_PURCHASE_SUCCEEDED")
    assert_eq({ ui.shop[2].item:GetText(), ui.checked:GetText():match("of (%d+)") }, { "Silk Cloth", "2" })   -- bars all bought
  end)

  it("A65 Shopping has one row per item, totalling every planned buy of it at the lower cap", function()
    Stubs.Reset()
    UIWorld.Market({ [2771] = UIWorld.Item(275, 80, 60), [3576] = UIWorld.Item(400, 80, 40), [2841] = UIWorld.Item(800, 80, 40) },
                   NOW - 3600000)
    UIWorld.Plan({ { id = "t1", kind = "buy", key = 2771, qty = 40, price = 279, at = NOW, net = -11160 },
                   { id = "t2", kind = "craft", key = 3576, qty = 40, at = NOW, net = 0, parent = "t1" },
                   { id = "b1", kind = "buy", key = 2771, qty = 22, price = 275, at = NOW, net = -6050 },
                   { id = "b2", kind = "craft", key = 2841, qty = 44, at = NOW, net = 0, parent = "b1" } })
    Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse({ auctions = {} }))
    Stubs.Login()
    AHWorld.Install()
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    local ui = AHProfit.AHPanel.Frames()
    assert_eq({ ui.shop[1].qty:GetText(), ui.shop[1].colB:GetText(), ui.shop[2]:IsShown(), ui.checked:GetText() },
              { "×62", AHProfit.ViewModel.WithCoins("2s 75c"), false, "0 of 1" })
  end)

  it("A24 Full scan asks for a ReplicateItems scan at most every 20 minutes", function()
    local ui = OpenAH()
    ui.scan:Click()
    assert_eq({ ui.status:GetText(), ui.bar:IsShown(), ui.fill:IsShown() }, { "full scan: waiting for the server…", true, false })
    ui.scan:Click()
    assert_eq(ui.status:GetText():match("^next scan in %d+ min$") ~= nil, true)
  end)

  it("without the AH search bar a search says so instead of failing", function()
    local ui, ah = OpenAH()
    ah.SearchBar = nil
    ui.shop[1]:Click()
    assert_eq(ui.status:GetText(), "search unavailable")
  end)

  it("an empty shopping list says why; the post list keeps Today's Min chance filter", function()
    Stubs.Reset()
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2589, count = 20 } } } }   -- the Linen to post
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW - 3600000)
    UIWorld.Plan({ { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1000, chance = 92 },
                   { id = "q", kind = "post", key = 2589, qty = 2, price = 55, at = NOW, net = 1, chance = 10 } })
    Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse({ auctions = {} }))
    Stubs.Login()
    AHWorld.Install()
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    local ui = AHProfit.AHPanel.Frames()
    assert_eq({ ui.empty:IsShown(), ui.empty:GetText(), ui.postCount:GetText(), ui.post[1].qty:GetText() },
              { true, "Nothing to buy right now.", "1", "×20" })   -- the 10% post is filtered
  end)

  it("UI7 each section has labelled columns, and every header explains itself on hover", function()
    local ui = OpenAH()
    local shop, post = {}, {}
    for i, h in ipairs(ui.shopHeaders) do shop[i] = h:GetText() end
    for i, h in ipairs(ui.postHeaders) do post[i] = h:GetText() end
    assert_eq({ shop, post }, { { "Item", "For", "Price", "Max", "Qty" }, { "Item", "List price", "Qty", "Est. profit", "Chance" } })
    for _, headers in ipairs({ ui.shopHeaders, ui.postHeaders }) do
      for _, h in ipairs(headers) do
        h.hit:GetScript("OnEnter")(h.hit)
        assert_eq(Stubs.tooltip.lines[2] ~= nil, true, h:GetText() .. " has no tooltip text")
      end
    end
    -- 20 Linen listed at 55c each; the post's own expected profit is 1000c.
    local row = ui.post[1]
    assert_eq({ row.item:GetText(), row.price:GetText(), row.qty:GetText(), row.profit:GetText(), row.chance:GetText():match("^%d+%%") },
              { "Linen Cloth", AHProfit.ViewModel.WithCoins("55c"), "×20", AHProfit.ViewModel.WithCoins("+10s"), "92%" })
  end)

  it("A29 clicking a Post row fills the AH's Sell tab; you press Post", function()
    local ui, ah = RodsAtAH()
    local sell = AHWorld.InstallSell(ah, 7)
    ui.post[1]:Click()
    assert_eq({ ah.posted.bag, ah.posted.slot, sell.quantity, sell.price, #ah.searches }, { 0, 2, 5, 4100, 0 })
    assert_eq(ui.status:GetText(), AHProfit.ViewModel.WithCoins("Sell tab: ×5 at 41s each · press Post"))
    sell.price = 4199                                     -- the AH puts its own price in after its search
    Stubs.FireEvent("COMMODITY_SEARCH_RESULTS_UPDATED")
    assert_eq(sell.price, 4100)
    sell.price = 4199
    Stubs.FireEvent("COMMODITY_SEARCH_RESULTS_UPDATED")   -- only once
    assert_eq(sell.price, 4199)
    ui.post[1]:Click()
    Stubs.Advance(11)                                     -- too late: the price you set by hand stays
    sell.price = 4199
    Stubs.FireEvent("ITEM_SEARCH_RESULTS_UPDATED")
    assert_eq(sell.price, 4199)
    Stubs.world.containers = {}
    ui.post[1]:Click()
    assert_eq(ui.status:GetText(), "no Copper Rod in your bags")
  end)

  it("A56 a Post click sets the plan's auction duration, or says which to pick without the control", function()
    Stubs.Reset()
    Stubs.world.containers = { [0] = { size = 2, slots = { [2] = { id = 6217, count = 5 } } } }
    UIWorld.Market({ [6217] = UIWorld.Item(4200, 80, 6) }, NOW - 3600000)
    UIWorld.Plan({ { id = "p", kind = "post", key = 6217, qty = 5, price = 4100, at = NOW, net = 6306, chance = 58,
                     duration = 24 } })
    Stubs.Login()
    local ah = AHWorld.Install()
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    local ui = AHProfit.AHPanel.Frames()
    local sell = AHWorld.InstallSell(ah, 7)
    ui.post[1]:Click()
    assert_eq(ui.status:GetText(), AHProfit.ViewModel.WithCoins("Sell tab: ×5 at 41s each · set the duration to 24 h · press Post"))
    sell.DurationDropDown = { SetDuration = function(self, index) self.index = index end }
    ui.post[1]:Click()
    assert_eq(sell.DurationDropDown.index, 3)                          -- the third duration: 24 h
    assert_eq(ui.status:GetText(), AHProfit.ViewModel.WithCoins("Sell tab: ×5 at 41s each · 24 h · press Post"))
    assert_eq(AHProfit.Plan.DecodeAction("a;post;1;1;1;1;1;;;pending;50;;;8").duration, 8)
    assert_eq(AHProfit.Plan.DecodeAction("a;post;1;1;1;1;1;;;pending;50;;").duration, nil)     -- an older file
  end)

  it("FM30 without the Sell tab's API, or for an item the AH won't take, a click says so", function()
    local ui, ah = RodsAtAH()
    ui.post[1]:Click()
    assert_eq(ui.status:GetText(), "Sell tab unavailable")
    local sell = AHWorld.InstallSell(ah, 7)
    C_AuctionHouse.IsSellItemValid = function() return false end
    ui.post[1]:Click()
    assert_eq({ ui.status:GetText(), sell.price }, { "Copper Rod can't be sold on the AH", nil })
  end)

  it("A30 the Post list shows only what is in your bags; a chain's later post waits", function()
    Stubs.Reset()
    Stubs.ITEM_NAMES[4471] = "Basic Campfire Kit"
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2589, count = 20 } } } }
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48), [4471] = UIWorld.Item(99, 80, 10) }, NOW - 3600000)
    UIWorld.Plan({ { id = "w", kind = "buy", key = 4470, qty = 7, price = 38, at = NOW, net = 0 },
                   { id = "c", kind = "craft", key = 4471, qty = 7, at = NOW, net = 392, parent = "w" },
                   { id = "k", kind = "post", key = 4471, qty = 7, price = 99, at = NOW, net = 392, parent = "w", chance = 100 },
                   { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1000, chance = 92 } })
    Stubs.Login()
    AHWorld.Install()
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    local ui = AHProfit.AHPanel.Frames()
    assert_eq({ ui.postCount:GetText(), ui.post[1].item:GetText(), ui.post[2]:IsShown() }, { "1", "Linen Cloth", false })
    Stubs.ITEM_NAMES[4471] = nil
  end)

  it("A31 posting from your bags updates the Post list: a posted item goes, a part-posted one shrinks", function()
    Stubs.Reset()
    Stubs.ITEM_NAMES[4471] = "Basic Campfire Kit"
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2589, count = 20 }, [2] = { id = 4471, count = 1 } } } }
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48), [4471] = UIWorld.Item(99, 80, 10) }, NOW - 3600000)
    UIWorld.Plan({ { id = "k", kind = "post", key = 4471, qty = 1, price = 99, at = NOW, net = 94, chance = 100 },
                   { id = "p", kind = "post", key = 2589, qty = 20, price = 55, at = NOW, net = 1000, chance = 92 } })
    Stubs.Login()
    AHWorld.Install()
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    local ui = AHProfit.AHPanel.Frames()
    assert_eq(ui.postCount:GetText(), "2")
    Stubs.world.containers[0].slots = { [1] = { id = 2589, count = 5 } }    -- the kit and 15 Linen are on the AH now
    Stubs.FireEvent("BAG_UPDATE_DELAYED")
    Stubs.Advance(2)                                                       -- the bag read is debounced
    assert_eq({ ui.postCount:GetText(), ui.post[1].item:GetText(), ui.post[1].qty:GetText(), ui.post[1].profit:GetText() },
              { "1", "Linen Cloth", "×5", AHProfit.ViewModel.WithCoins("+2s 50c") })
    Stubs.ITEM_NAMES[4471] = nil
  end)

  it("A32 a vendor buy stays off Shopping, which lists only what to buy on the AH", function()
    Stubs.Reset()
    Stubs.ITEM_NAMES[4470] = "Simple Wood"
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW - 3600000)
    UIWorld.Plan({ { id = "w", kind = "buy", key = 4470, qty = 7, price = 38, at = NOW, net = 0, vendor = true },
                   { id = "l", kind = "buy", key = 2589, qty = 20, price = 45, at = NOW, net = 200 } })
    Stubs.Login()
    local ah = AHWorld.Install()
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    local ui = AHProfit.AHPanel.Frames()
    assert_eq({ ui.shop[1].item:GetText(), ui.shop[2]:IsShown(), ui.checked:GetText() }, { "Linen Cloth", false, "0 of 1" })
    Stubs.Slash("/ahp")
    local notes = {}
    for _, row in ipairs(AHProfit.MainWindow.Frames().rows) do
      if row:IsShown() and row.note:IsShown() then notes[#notes + 1] = row.note:GetText() end
    end
    assert_eq(notes, { "from a vendor" })                         -- Today's wood row
    Stubs.ITEM_NAMES[4470] = nil
  end)

  it("A40 Shopping's For column says why each item is bought: CRAFT, FLIP or DE", function()
    Stubs.Reset()
    UIWorld.Market({ [2840] = UIWorld.Item(500, 80, 40), [4306] = UIWorld.Item(70, 80, 60) }, NOW - 3600000)
    UIWorld.Plan({ { id = "b", kind = "buy", key = 2840, qty = 6, price = 500, at = NOW, net = 0 },
                   { id = "c", kind = "craft", key = 6217, qty = 3, at = NOW, net = 900, parent = "b" },
                   { id = "s", kind = "buy", key = 4306, qty = 5, price = 70, at = NOW, net = 50 },
                   { id = "t", kind = "post", key = 4306, qty = 5, price = 105, at = NOW, net = 50, parent = "s" } })
    Stubs.Login()
    AHProfitDB.professions["Amaya-Grommash"] = { Enchanting = { skill = 300, max = 300 } }
    AHProfit.Options.Disenchant = function() return { { key = 6585, skill = 1, ev = 900 } } end
    AHWorld.Install()
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    local ui, why = AHProfit.AHPanel.Frames(), {}
    for i = 1, 3 do why[i] = ui.shop[i].item:GetText() .. " " .. ui.shop[i].why:GetText() end
    assert_eq(why, { "Copper Bar CRAFT", "Silk Cloth FLIP", Stubs.ITEM_NAMES[6585] .. " DE" })
  end)

  it("A82 funded rows first, then the unfunded ones muted and tagged, with their reason on hover, still clickable", function()
    local ui, ah = AHWorld.Unfunded()
    assert_eq({ ui.checked:GetText(), ui.postCount:GetText() }, { "3 of 5 + 1 unfunded", "1 + 1 unfunded" })
    local elixir, post = ui.shop[6], ui.post[2]
    assert_eq({ elixir.item:GetText(), elixir.tag:IsShown(), elixir.tag:GetText(), elixir.qty:GetText(), ui.shop[5].tag:IsShown() },
              { "Elixir of Fortitude", true, "unfunded", "×40", false })
    assert_eq({ post.item:GetText(), post.qty:GetText(), post.price:GetText(), post.profit:GetText(), post.chance:GetText():match("^%d+%%") },
              { "Elixir of Fortitude", "×40", AHProfit.ViewModel.WithCoins("3s 50c"), AHProfit.ViewModel.WithCoins("+9s"), "35%" })
    assert_eq({ ui.post[1].item:GetText(), ui.post[1].tag:IsShown() }, { "Copper Rod", false })
    elixir:GetScript("OnEnter")(elixir)
    assert_eq(Stubs.tooltip.lines, { "Elixir of Fortitude", "Unfunded: Insufficient liquidity: 3/day, 40 planned. Click to search anyway" })
    post:GetScript("OnEnter")(post)
    assert_eq(Stubs.tooltip.lines[2], "Unfunded: Insufficient liquidity: 3/day, 40 planned. Click to fill the Sell tab anyway")
    elixir:Click()
    assert_eq(ah.searches[#ah.searches], "Elixir of Fortitude")
    local sell = AHWorld.InstallSell(ah, 40)
    post:Click()
    assert_eq({ sell.quantity, sell.price }, { 40, 350 })
    assert_eq(elixir.item.textColor, { 0.56, 0.54, 0.49 })
    AHWorld.Unname()
  end)

  it("A43 Full scan shows its progress: waiting, reading n of total, saving, done; or no answer", function()
    Stubs.Reset()
    local auctions = {}
    for i = 1, 400 do auctions[i] = Stubs.Auction(2589, 40 + i % 5, 20, 3) end
    local house = { auctions = auctions, costMs = 0.5, answers = false }    -- slow rows: a couple per step
    Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse(house))
    UIWorld.Market({ [2589] = UIWorld.Item(45, 86, 48) }, NOW - 3600000)
    Stubs.Login()
    AHWorld.Install()
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    local ui = AHProfit.AHPanel.Frames()
    ui.scan:Click()                                               -- the server doesn't answer
    assert_eq(ui.status:GetText(), "full scan: waiting for the server…")
    Stubs.Advance(61)
    assert_eq({ ui.status:GetText(), ui.bar:IsShown() }, { "no answer: another addon may have scanned recently", false })
    Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE")                 -- another addon's scan arrives
    local done, total = ui.status:GetText():match("^full scan: reading (%d+) of (%d+)$")
    assert_eq({ total, ui.bar:IsShown() }, { "400", true })
    for _ = 1, 10 do Stubs.RunDueTimers() end                      -- ten more steps: one progress update
    local later = ui.status:GetText():match("^full scan: reading (%d+) of 400$")
    assert_eq(tonumber(later) > tonumber(done) and ui.fill:IsShown(), true)
    Stubs.Advance(5)
    assert_eq({ ui.status:GetText(), ui.bar:IsShown() }, { "full scan done · 400 auctions", false })
  end)
end)

