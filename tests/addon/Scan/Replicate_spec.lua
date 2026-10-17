-- Specs for addon/AHProfit/Scan/Replicate.lua: full scans from ReplicateItems answers (SPEC §4.2).

local Stubs = require("wow_stubs")

local LINEN, COPPER_BAR, SILK, WOOL = 2589, 2840, 4306, 2592

--- Fresh stubs with a fake auction house, logged in.
---@param house table  Stubs.ReplicateHouse argument
---@return table       The house (its calls are counted)
local function Boot(house)
  Stubs.Reset({ keepSaved = AHProfitDB ~= nil and house.keepSaved })
  Stubs.Define("C_AuctionHouse", Stubs.ReplicateHouse(house))
  Stubs.Login()
  return house
end

--- n auctions over four items, every unit price, stack and band mixed.
---@param n number  Auctions
---@return table[]  Stubs.Auction rows
local function ManyAuctions(n)
  local ids, rows = { LINEN, COPPER_BAR, SILK, WOOL }, {}
  for i = 1, n do rows[i] = Stubs.Auction(ids[i % 4 + 1], 10 + i % 50, 1 + i % 20, 1 + i % 4) end
  return rows
end

--- Total units in an encoded order book.
---@param rows string  "key,price,stack,band,count;..."
---@return number      Σ stack × count
local function Units(rows)
  local units = 0
  for stack, count in rows:gmatch("[^,;]+,[^,;]+,([^,;]+),[^,;]+,([^,;]+)") do units = units + stack * count end
  return units
end

describe("Replicate", function()
  it("A6 another addon's ReplicateItems answer is saved as one full scan", function()
    local house = Boot({ auctions = {
      Stubs.Auction(LINEN, 45, 20, 3), Stubs.Auction(LINEN, 45, 20, 3), Stubs.Auction(COPPER_BAR, 100, 5, 1) } })
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE")      -- AuctionHouseFYI asked; we only listen
    Stubs.Advance(60)
    local scan = AHProfitDB.scans[1]
    assert_eq({ #AHProfitDB.scans, scan.kind, scan.coverage, scan.rows },
      { 1, "full", 1, "2589,45,20,3,2;2840,100,5,1,1" }, "two identical Linen auctions group into count 2; time left 3 = band 3 (long)")
    assert_eq({ scan.id, scan.char, scan.at }, { "Amaya-Grommash:1760000000000:0", "Amaya-Grommash", 1760000000000 })
    assert_eq(house.calls, 0, "an answer arrived, so the 30 s fallback never calls")
  end)

  it("A7 with no answer within 30 s AHProfit calls ReplicateItems once, then not again for 20 min", function()
    local house = Boot({ auctions = { Stubs.Auction(LINEN, 45, 20, 3) } })
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.Advance(29)
    assert_eq(house.calls, 0)
    Stubs.Advance(1)
    assert_eq(house.calls, 1)
    Stubs.Advance(1)
    assert_eq(#AHProfitDB.scans, 1, "our own call's answer is read like any other")
    Stubs.FireEvent("AUCTION_HOUSE_CLOSED")
    Stubs.Advance(300)
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.Advance(30)
    assert_eq(house.calls, 1, "5 min later is inside the 20 min rule")
  end)

  it("A8 a 10,000-auction answer is read in 4 ms steps and waits out combat", function()
    Boot({ auctions = ManyAuctions(10000), costMs = 0.25 })
    local Replicate = AHProfit.Scan.Replicate
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE")
    assert_eq(Replicate.Stats().steps, 1, "the first step runs at once")
    Stubs.EnterCombat()
    Stubs.Advance(5)
    assert_eq({ Replicate.Stats().steps, #AHProfitDB.scans }, { 1, 0 }, "nothing runs in combat")
    Stubs.LeaveCombat()
    Stubs.Advance(5)
    local stats, scan = Replicate.Stats(), AHProfitDB.scans[1]
    assert_eq(stats.maxStepMs <= Replicate.BUDGET_MS, true, "no step over 4 ms (" .. stats.maxStepMs .. ")")
    assert_eq(stats.steps >= 10000 * 0.25 / 4, true, "15 auctions per 4 ms step (the 16th would cross at twice the average): at least 625 steps")
    local units = 0
    for _, a in ipairs(ManyAuctions(10000)) do units = units + a.count end
    assert_eq({ scan.kind, Units(scan.rows) }, { "full", units }, "every auction's units are in the book")
  end)

  it("A10 an unanswered call doubles the wait to 40 min", function()
    local house = Boot({ auctions = {}, answers = false })
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.Advance(30)
    assert_eq(house.calls, 1)
    Stubs.FireEvent("AUCTION_HOUSE_CLOSED")
    Stubs.Advance(1200 - 60)
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.Advance(30)
    assert_eq(house.calls, 1, "20 min after an unanswered call: wait 40")
    Stubs.FireEvent("AUCTION_HOUSE_CLOSED")
    Stubs.Advance(1200)
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.Advance(30)
    assert_eq(house.calls, 2, "40 min after it: call again")
  end)

  it("A11 pets, no-buyout and unknown-time-left auctions are left out and counted", function()
    Boot({ auctions = {
      { id = LINEN, count = 1, buyout = 100, left = 3, pet = true }, { id = LINEN, count = 5, buyout = 0, left = 3 },
      { id = LINEN, count = 5, buyout = 500, left = 0 }, { id = LINEN, count = 5, buyout = 500 },
      Stubs.Auction(LINEN, 45, 20, 3) } })
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE")
    Stubs.Advance(1)
    assert_eq(AHProfitDB.scans[1].rows, "2589,45,20,3,1")
    Stubs.FireEvent("PLAYER_LOGOUT")
    assert_eq(AHProfitDB.diagnostics.scan, "calls=0|n=5|skipped=4|pet=1|count=0|buyout=1|band=2|link=0|byId=0"
      .. "|left=0:1,3:1,nil:1|steps=4|maxStepMs=0.04|readMs=0.04|sortMs=0.00|encodeMs=0.00|saveMs=0.00|kind=full|rows=1|dropped=0",
      "one skip per reason; the histogram shows every raw time left; read, sort, encode and save each take a frame")
  end)

  it("A12 Forever's time left 4 is very long", function()
    Boot({ auctions = { Stubs.Auction(LINEN, 45, 20, 4) } })
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE")
    Stubs.Advance(1)
    assert_eq(AHProfitDB.scans[1].rows, "2589,45,20,4,1", "1 short .. 4 very long, as written")
  end)

  it("A13 an auction without a link is keyed by item id, or -itemId when it could carry a suffix", function()
    Boot({ auctions = {
      { id = LINEN, count = 20, buyout = 900, left = 3, noLink = true },
      { id = 6585, count = 1, buyout = 800, left = 4, noLink = true } } })
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE")
    Stubs.Advance(1)
    assert_eq(AHProfitDB.scans[1].rows, "-6585,800,1,4,1;2589,45,20,3,1",
      "trade goods can't roll a suffix; armor might, so it stays out of valuation (-itemId)")
    assert_eq(AHProfit.Scan.Replicate.Stats().byId, 2)
  end)

  it("FM19 the AH closed at 40% of a full scan is saved as partial", function()
    Boot({ auctions = ManyAuctions(75), costMs = 0.25 })
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE")    -- reads 15
    Stubs.RunDueTimers()                             -- reads 15 more: 32 of 80
    Stubs.FireEvent("AUCTION_HOUSE_CLOSED")
    Stubs.Advance(1)
    local scan = AHProfitDB.scans[1]
    assert_eq({ scan.kind, scan.coverage }, { "partial", 0.4 }, "30 of 75 auctions read")
  end)

  it("a /reload mid-read saves what was read as partial", function()
    Boot({ auctions = ManyAuctions(75), costMs = 0.25 })
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE")
    Stubs.FireEvent("PLAYER_LOGOUT")
    assert_eq({ AHProfitDB.scans[1].kind, AHProfitDB.scans[1].coverage }, { "partial", 0.2 })
  end)

  it("FM22 a fifth un-ingested full scan drops the oldest; events are kept", function()
    local old = {}
    for i = 1, 4 do
      old[i] = { id = "Amaya-Grommash:175999000" .. i .. "000:0", char = "Amaya-Grommash", at = 1759990000000 + i * 1000,
                 kind = "full", coverage = 1, rows = "2589,40,20,4,1" }
    end
    AHProfitDB = { formatVersion = 1, scans = old, events = {
      { id = "Amaya-Grommash:1759990000000:1", type = "post", char = "Amaya-Grommash", at = 1759990000000,
        key = 2589, stack = 20, price = 40, durationH = 24, deposit = 6, confirmed = true } } }
    Boot({ auctions = { Stubs.Auction(LINEN, 45, 20, 3) }, keepSaved = true })
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE")
    Stubs.Advance(1)
    local ids = {}
    for i, scan in ipairs(AHProfitDB.scans) do ids[i] = scan.at end
    assert_eq(ids, { 1759990002000, 1759990003000, 1759990004000, 1760000000000 }, "the oldest full scan went")
    assert_eq(#AHProfitDB.events, 1, "events are never dropped")
    assert_eq(AHProfit.Scan.Replicate.Warning(), "Dropped 1 old full scan(s): run AHSync to sync")
  end)

  it("partial scans don't count toward the cap, and a repeated update starts no second scan", function()
    Boot({ auctions = { Stubs.Auction(LINEN, 45, 20, 3) } })
    local db = AHProfitDB
    for i = 1, 5 do db.scans[i] = { kind = i == 5 and "full" or "partial", at = i } end
    assert_eq(AHProfit.Scan.Replicate.CapFullScans(db), 0)
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE")
    Stubs.Advance(1)
    Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE")
    Stubs.Advance(1)
    assert_eq(#db.scans, 6, "one new scan, not two")
  end)

  it("an answer while the AH is closed, or with no auctions yet, starts nothing", function()
    Boot({ auctions = {} })
    Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE")
    Stubs.FireEvent("AUCTION_HOUSE_SHOW")
    Stubs.FireEvent("REPLICATE_ITEM_LIST_UPDATE")
    Stubs.Advance(1)
    assert_eq(#AHProfitDB.scans, 0)
  end)
end)
