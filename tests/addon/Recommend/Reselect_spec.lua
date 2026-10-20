-- Specs for addon/AHProfit/Recommend/Reselect.lua: the logged-in character's live reselection (SPEC §4.0; A76, A80,
-- DG5). The world is the mockups' (tests/support/lua/portfolio_world.lua).

local Stubs = require("wow_stubs")
local World = require("portfolio_world")

--- The Thick Leather row whose buy waits for its Tue 20:00-23:00 window (A76).
---@return table[]  World.Candidates with that row changed
local function WaitingThickLeather()
  local cands = World.Candidates()
  local tue20 = World.At(1, 20) / World.HOUR
  cands[6].id = "buy_later:4304:" .. World.AMAYA .. ":-/h" .. tue20 .. "/h" .. World.At(3, 19) / World.HOUR .. ":1"
  return cands
end

--- Ids of a result's funded rows, in funding order.
---@param result table
---@return string[]
local function FundedIds(result)
  local out = {}
  for i, rc in ipairs(result.funded) do out[i] = rc.c.id end
  return out
end

--- A candidate id from the world by its row (World.Candidates order).
---@param i number
---@return string
local function Id(i)
  return World.Candidates()[i].id
end

describe("Reselect", function()
  it("A80 Amaya's live gold of 1g caps her selection, and Brugan's gold and rows are never used", function()
    World.Boot(Stubs, { money = 10000 })
    local result = AHProfit.Adapt.Reselect()
    assert_eq(result.cap, 10000, "min(2g 76s 40c, 1g)")
    assert_eq(FundedIds(result), { Id(1), Id(2), Id(7), Id(3), Id(9), Id(8) },
      "Scouting Gloves, Embossed Leather Gloves, Runed Copper Rod, Linen, then Bolt of Silk Cloth and Silk Cloth")
    local reasons = {}
    for _, e in ipairs(result.excluded) do reasons[e.rc.c.id] = e.reason end
    -- At its turn 42s 40c were left; its status is read against the final allocation (D.6), after Bolt and Silk.
    assert_eq(reasons[Id(6)], "Insufficient gold on Amaya (needs 1g 60s 80c, has 8s 80c)")
    assert_eq({ result.gold, 10000 - result.gold }, { 9120, 880 }, "8s 80c of her gold left")
    for _, rc in ipairs(AHProfit.Adapt.State().rcs) do assert_eq(rc.c.char, World.AMAYA, "Brugan's rows stay cached") end
  end)

  it("A76 a live price under the window's expected price moves the buy to now and keeps its sale", function()
    World.Boot(Stubs, { candidates = WaitingThickLeather(), nowMs = World.MON + 11 * World.HOUR,
                        items = { [World.THICK] = World.Item(536) } })
    AHProfit.Adapt.Current()
    World.Scan(Stubs, { [World.THICK] = World.Book({ { 510, 30 } }) })
    Stubs.Advance(1)
    local rc = AHProfit.Adapt.State().rcs[6]
    assert_eq({ rc.buyNow, rc.gold, rc.net }, { true, 15300, 6280 }, "30 x 5s 10c = 1g 53s; net up 30 x 26c to +62s 80c")
    local PV, VM = AHProfit.PortfolioView, AHProfit.ViewModel
    assert_eq({ VM.Money(rc.gold), VM.SignedMoney(rc.net) }, { "1g 53s", "+62s 80c" })
    assert_eq(PV.BuyTime(rc, 3, 0), "now · cheaper than Tue 20–23 (5s 36c)")
    local result = AHProfit.Adapt.Current()
    local row = AHProfit.PortfolioView.Build(result, AHProfit.Adapt.Snapshot(), {}, { nowMs = World.MON + 11 * World.HOUR,
      offsetMs = 0, nameOf = AHProfit.Adapt.NameOf, windowHoursOf = function() return 3 end }).week.rows[1]
    assert_eq({ row.id, row.note, row.buyTime }, { rc.c.id, "buy now · sell Thu 19:00", "now · cheaper than Tue 20–23 (5s 36c)" })
  end)

  it("A76 a live price above the window's expected price keeps the buy in its window", function()
    World.Boot(Stubs, { candidates = WaitingThickLeather(), items = { [World.THICK] = World.Item(536) } })
    AHProfit.Adapt.Current()
    World.Scan(Stubs, { [World.THICK] = World.Book({ { 600, 30 } }) })
    Stubs.Advance(1)
    local rc = AHProfit.Adapt.State().rcs[6]
    assert_eq({ rc.buyNow, rc.gold, rc.net }, { nil, 16080, 5500 })
    assert_eq(AHProfit.PortfolioView.BuyTime(rc, 3, 0), nil)
  end)

  it("DG5 a funded row's price change reselects; a price change on a row excluded by profession doesn't", function()
    World.Boot(Stubs, { items = { [World.BRACERS] = World.Item(1500) } })
    AHProfit.Adapt.Current()
    local Adapt = AHProfit.Adapt
    local runs = Adapt.Perf().reselections
    World.Scan(Stubs, { [World.THICK] = World.Book({ { 650, 30 } }), [World.BRACERS] = World.Book({ { 1500, 1 } }) })
    Stubs.Advance(1)
    assert_eq({ Adapt.State().rcs[6].gold, Adapt.Perf().reselections }, { 19500, runs + 1 }, "1g 95s: a funded row moved")
    World.Scan(Stubs, { [World.THICK] = World.Book({ { 650, 30 } }), [World.BRACERS] = World.Book({ { 1200, 1 } }) })
    Stubs.Advance(1)
    assert_eq({ Adapt.State().rcs[4].gold, Adapt.Perf().reselections, Adapt.Perf().passes }, { 1200, runs + 1, 2 },
      "the bracers' gold moved, but Enchanting 150 can't clear by price: no reselection")
  end)

  it("parses candidate ids, caps spending and keeps the exported status where live state can't change it", function()
    World.Boot(Stubs)
    local R = AHProfit.Reselect
    assert_eq({ R.Parse("buy_later:4304:Amaya-Grommash:-/h480140/h480163:2") }, { "buy_later:4304:Amaya-Grommash:-/h480140/h480163", 2, 480140 })
    assert_eq({ R.Parse("flip:7005:Amaya-Grommash:-/now/now:1") }, { "flip:7005:Amaya-Grommash:-/now/now", 1, nil })
    assert_eq({ R.Cap(210000, 64940, 37300) }, { 27640, 27640 })
    assert_eq({ R.Cap(10000, 30000, 37300) }, { 0, 0 })
    local levels = function() return { { price = 10, units = 5 } } end
    local none = function() return nil end
    assert_eq({ R.Status({ status = "stale" }, { levelsOf = levels }) }, { nil, nil }, "a scan newer than the build is fresh")
    assert_eq({ R.Status({ status = "stale" }, { levelsOf = none }) }, { "Forecast too stale", "stale" })
    assert_eq({ R.Status({ status = "no_listing" }, { levelsOf = levels }) }, { nil, nil })
    assert_eq({ R.Status({ status = "volume" }, { levelsOf = levels }) }, { "Sales volume unknown", "volume" })
    assert_eq({ R.Status({ status = "risk" }, { levelsOf = levels }) }, { nil, nil }, "selection decides")
    local bracers = { status = "profession", needs = { profession = "Enchanting", skill = 150 } }
    assert_eq({ R.Status(bracers, { levelsOf = none }) }, { "Required profession unavailable: Enchanting 150", "profession" })
    assert_eq({ R.Status(bracers, { levelsOf = none, skills = { Enchanting = { skill = 150 } } }) }, { nil, nil },
      "a skill-up clears it live (PF10)")
  end)

  it("a sale now is repriced against the live book; a flip relists against the level after the units it buys", function()
    World.Boot(Stubs)
    local R, SellProb, Fees = AHProfit.Reselect, AHProfit.Engine.SellProb, AHProfit.Engine.Fees
    local item = { postPrice = 1000, chance = 0.5, med7 = 1000, p90 = 1200, closes30 = 100 }
    --- E_post as Reselect values a post now.
    ---@param p number
    ---@param qty number
    ---@param price number
    ---@return number
    local function E(p, qty, price)
      return SellProb.ExpectedPost(p, qty, price, 10, Fees.DepositCopper(10, qty, Fees.ShortestDurationCovering(8)))
    end
    local live = { graph = true, base = {}, routes = {}, saleOf = function() return { item = item, vendor = 10, nowMs = World.NOW } end }
    local post = { id = "sell_stock:1:A-R:-/-/now:1", strategy = "sell_stock", char = "A-R", key = 1, qty = 5, inputs = {},
                   gold = 0, net = 3000 }
    live.levelsOf = function() return { { price = 900, units = 5 } } end
    assert_eq(R.Reprice(post, live).net, 3000 + E(0.5, 5, 899) - E(0.5, 5, 1000), "undercut to 8s 99c")
    local flip = { id = "flip:1:A-R:-/now/now:1", strategy = "flip", char = "A-R", key = 1, qty = 20, inputs = {}, gold = 200,
                   net = 800 }
    live.levelsOf = function() return { { price = 10, units = 20 }, { price = 950, units = 5 } } end
    assert_eq(R.Reprice(flip, live).net, 800 + E(0.5, 20, 949) - E(0.5, 20, 1000), "bought at 10c, relisted at 9s 49c")
    assert_eq(R.Reprice(flip, { graph = false }).net, 800, "no graph: the exported values")
  end)

  it("a learned recipe's craft option becomes a candidate valued at posting now; nothing that loses money", function()
    World.Boot(Stubs)
    local R = AHProfit.Reselect
    local c = R.LearnedCandidate(2964, 2997, { crafts = 6, matGold = 0, profit = 1530, p = 0.9, ownedUsed = { [2592] = 18 },
                                               bought = {} }, 1, World.AMAYA)
    assert_eq({ c.id, c.qty, c.gold, c.net, c.inputs }, { "craft:2997:Amaya-Grommash:2592o/-/now:1", 6, 0, 1530,
      { { key = 2592, count = 18, source = "own" } } })
    local mixed = R.LearnedCandidate(1, 5, { crafts = 2, matGold = 80.4, profit = 10, ownedUsed = { [7] = 1 },
                                             bought = { [7] = 1, [8] = 2 } }, 1.5, World.AMAYA)
    assert_eq({ mixed.id, mixed.qty, mixed.gold }, { "craft:5:Amaya-Grommash:7oa+8a/-/now:1", 3, 80 })
    assert_eq({ R.LearnedCandidate(1, 5, { crafts = 0, profit = 0 }, 1, World.AMAYA), R.LearnedCandidate(1, 5, nil, 1, "x") },
      { nil, nil })
  end)
end)
