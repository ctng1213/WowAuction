-- Specs for addon/AHProfit/Recommend/Options.lua: the addon's own sell options (SPEC §4.0, A15-A18).

local Stubs = require("wow_stubs")

local NOW = 1760000000000
local LINEN = { postPrice = 45, sellValue = 42, chance = 0.9 }

--- Boot the addon and return its options module.
---@return table
local function Boot()
  Stubs.Reset()
  Stubs.Login()
  return AHProfit.Options
end

--- Market data by key for these specs.
---@param items table  key -> item
---@return function
local function ItemsOf(items) return function(key) return items[key] end end

--- A merchant price lookup.
---@param prices table  key -> copper
---@return function
local function VendorOf(prices) return function(key) return prices[key] end end

--- A Sell / Crafts context over fixed market data, no live book.
---@param planned table  key -> true
---@param items table    key -> market item
---@param prices table   key -> merchant price
---@return table
local function Ctx(planned, items, prices)
  return { planned = planned, itemOf = ItemsOf(items), vendorOf = VendorOf(prices), levelsOf = function() return nil end,
           nowMs = NOW, charId = "Amaya-Grommash" }
end

describe("Options", function()
  it("A15 what you carry gets the addon's own row when AHSync has no plan for it", function()
    local Options = Boot()
    local units = Options.Units({ { key = 2589, qty = 15, bound = false }, { key = 2589, qty = 5, bound = false } })
    local rows = Options.Sell(units, Ctx({}, { [2589] = LINEN }, { [2589] = 3 }))
    assert_eq(rows, { { id = "own:2589", kind = "post", key = 2589, qty = 20, price = 45, net = 840, chance = 90, at = NOW,
                        status = "pending", char = "Amaya-Grommash", own = true, duration = 8,   -- A56
                        ownNote = "post now · AHSync has no plan for it" } })
  end)

  it("A16 items the plan already covers get no own row", function()
    local Options = Boot()
    local rows = Options.Sell({ [2589] = 20, [2840] = 6 },
                              Ctx({ [2589] = true }, { [2589] = LINEN, [2840] = { postPrice = 500, sellValue = 470 } }, {}))
    assert_eq({ #rows, rows[1].key, rows[1].net, rows[1].chance }, { 1, 2840, 2820, nil })
  end)

  it("A17 a merchant that pays more than posting wins", function()
    local Options = Boot()
    local rows = Options.Sell({ [3300] = 7 }, Ctx({}, { [3300] = { postPrice = 10, sellValue = 9 } }, { [3300] = 12 }))
    assert_eq({ rows[1].kind, rows[1].price, rows[1].net, rows[1].chance }, { "vendor", 12, 84, nil })
  end)

  it("A18 bound items, items without market data and items nobody pays for get no row", function()
    local Options = Boot()
    local units = Options.Units({ { key = 6948, qty = 1, bound = true }, { key = 4306, qty = 10, bound = false },
                                  { key = 1, qty = 1, bound = false } })
    local rows = Options.Sell(units, Ctx({}, { [6948] = LINEN, [1] = { postPrice = 5, sellValue = 0 } }, {}))
    assert_eq(rows, {})
  end)

  it("A19 a live scan re-prices what you carry from its cheapest listing and the curve", function()
    local Options = Boot()
    local item = { postPrice = 45, sellValue = 42, chance = 0.9, med7 = 49, refStack = 20 }
    local rows = {}
    for r = 1, 24 do rows[r] = { 0.8, 0.8, 0.8 } end
    local curve = { rows = rows, aheadPoints = { 0, 5, 20 }, refreshMs = NOW - 3 * 3600000 }
    local live = Options.Reprice(item, { { price = 40, units = 20 }, { price = 45, units = 40 } }, curve, 3, NOW)
    assert_eq({ live.postPrice, live.chance, live.live, item.postPrice }, { 39, 0.8, true, 45 })
    local row = Options.SellOne(2589, 20, live, 3, NOW, "Amaya-Grommash")
    assert_eq({ math.floor(row.net * 10 + 0.5), row.chance, row.ownNote },
              { 6042, 80, "post now at the live price · AHSync has no plan for it" })   -- 604.2c
    assert_eq(Options.Reprice({ chance = 0.5 }, {}, nil, 0, NOW).postPrice, nil)        -- nothing to price by
    assert_eq(Options.Reprice({ med7 = 49, refStack = 1, chance = 0.5 }, {}, nil, 0, NOW).postPrice, 49)
  end)

  it("A2 planned buys a live scan shows gone, or above their price, are dropped", function()
    local Options = Boot()
    local levels = { [2589] = { { price = 45, units = 20 } }, [4306] = {} }
    local actions = { { kind = "buy", key = 2589, qty = 20, price = 38 }, { kind = "buy", key = 4306, qty = 5, price = 70 },
                      { kind = "buy", key = 2840, qty = 1, price = 500 }, { kind = "post", key = 2589, qty = 20, price = 55 } }
    local kept, gone = Options.WithoutGoneBuys(actions, function(key) return levels[key] end)
    assert_eq({ #kept, gone, kept[1].key, kept[2].kind }, { 2, 2, 2840, "post" })      -- 2840: no newer scan
  end)

  it("A9 the addon offers crafts from your own mats, or from the AH, with the desktop's profit", function()
    local Options = Boot()
    local rod = { spell = 2663, product = 6217, yieldMin = 1, yieldMax = 1, reagents = { { 2840, 2 } }, crafters = 1 }
    local items = { [6217] = { postPrice = 4000, chance = 1 }, [2840] = { sellValue = 950 } }
    local rows, reserved = Options.Crafts({ rod }, { [2840] = 6 }, Ctx({}, items, {}))
    assert_eq({ rows[1].kind, rows[1].key, rows[1].qty, rows[1].net, rows[1].chance, rows[1].ownSource, reserved[2840] },
              { "craft", 6217, 3, 5700, 100, "Owned", 6 })                         -- C1: 3 x (38s - 19s)
    assert_eq(AHProfit.ViewModel.OwnCraftNote(rows[1]), "from your own mats · AHSync has no plan for it")
    items[2840] = { sellValue = 950, cur = 1000, curQty = 6 }
    rows = Options.Crafts({ rod }, {}, Ctx({}, items, {}))
    assert_eq({ rows[1].net, rows[1].ownSource, AHProfit.ViewModel.OwnCraftNote(rows[1]) },
              { 5400, "AH", "mats from the AH: 60s · AHSync has no plan for it" })   -- C2
    items[2840].curQty = 2                                                         -- C3: 4 owned, 2 on the AH
    rows = Options.Crafts({ rod }, { [2840] = 4 }, Ctx({}, items, {}))
    assert_eq({ rows[1].net, AHProfit.ViewModel.OwnCraftNote(rows[1]) },
              { 5600, "your mats topped up from the AH: 20s · AHSync has no plan for it" })
  end)

  it("A27 each known recipe that pays is costed on its own against what you carry", function()
    local Options = Boot()
    local rod = { spell = 2663, product = 6217, yieldMin = 1, yieldMax = 1, reagents = { { 2840, 2 } }, crafters = 1 }
    local belt = { spell = 3755, product = 4240, yieldMin = 1, yieldMax = 1, reagents = { { 2589, 6 } }, crafters = 1 }
    local items = { [6217] = { postPrice = 4000, chance = 1 }, [2840] = { sellValue = 950, cur = 1000, curQty = 2 },
                    [4240] = { postPrice = 100, chance = 1 }, [2589] = { sellValue = 42 } }
    local units = { [2840] = 4, [2589] = 20 }
    local rows = Options.CraftOptions({ belt, rod }, units, Ctx({}, items, {}))
    assert_eq(#rows, 1)                                                -- the belt loses money: left out
    assert_eq({ rows[1].key, rows[1].crafts, rows[1].matGold, rows[1].profit, rows[1].source, rows[1].ownedUsed[2840],
                rows[1].bought[2840] }, { 6217, 3, 2000, 5600, "Mixed", 4, 2 })   -- C3
    assert_eq(units[2840], 4)                                          -- what you carry is not used up
    local twin = { spell = 2664, product = 6217, yieldMin = 1, yieldMax = 1, reagents = { { 2840, 2 } }, crafters = 1 }
    rows = Options.CraftOptions({ twin, rod }, units, Ctx({}, items, {}))
    assert_eq({ rows[1].spell, rows[2].spell }, { 2663, 2664 })       -- equal profit: by spell
  end)

  it("A30 crafts stop at the product's liquidity cap; without supply data there is none", function()
    local Options = Boot()
    local two = { yieldMin = 1, yieldMax = 3 }
    assert_eq({ Options.CraftCap({ yieldMin = 1, yieldMax = 1 }, { supply = 14, refStack = 1 }),
                Options.CraftCap(two, { supply = 14, refStack = 1 }),         -- 7 units ÷ 2 a craft
                Options.CraftCap({ yieldMin = 1, yieldMax = 1 }, { supply = 1, refStack = 20 }),   -- one stack
                Options.CraftCap(two, { supply = 0 }), Options.CraftCap(two, {}), Options.CraftCap(two, nil) },
              { 7, 3, 20, 1, nil, nil })
    -- A36: what you hold counts against it.
    assert_eq({ Options.CraftCap({ yieldMin = 1, yieldMax = 1 }, { supply = 6, refStack = 1 }, 1),
                Options.CraftCap({ yieldMin = 1, yieldMax = 1 }, { supply = 6, refStack = 1 }, 3) }, { 2, 0 })
  end)

  it("A20 a mat a craft uses is not also offered for sale", function()
    local Options = Boot()
    local rod = { spell = 2663, product = 6217, yieldMin = 1, yieldMax = 1, reagents = { { 2840, 2 } }, crafters = 1 }
    local ctx = Ctx({}, { [6217] = { postPrice = 4000, chance = 1 }, [2840] = { postPrice = 1000, sellValue = 950 } }, {})
    local _, reserved = Options.Crafts({ rod }, { [2840] = 7 }, ctx)
    ctx.reserved = reserved
    local rows = Options.Sell({ [2840] = 7 }, ctx)
    assert_eq({ #rows, rows[1].key, rows[1].qty }, { 1, 2840, 1 })                  -- 6 crafted, 1 left to sell
  end)

  it("reads the character's known recipes that make items, and values a product without a price at the merchant", function()
    local Options = Boot()
    local db = { knownRecipes = { ["Amaya-Grommash"] = "2663;7418" },
                 recipes = { Blacksmithing = { [2663] = { product = 6217, yieldMin = 1, yieldMax = 1, reagents = "2840,2" },
                                               [9999] = { product = 1, reagents = "" } },
                             Enchanting = { [7418] = { reagents = "10940,1" } } } }
    assert_eq(Options.KnownRecipes(db, "Amaya-Grommash"), { { spell = 2663, product = 6217, yieldMin = 1, yieldMax = 1,
      reagents = { { 2840, 2 } }, crafters = 1 } })
    assert_eq(Options.KnownRecipes({}, "Amaya-Grommash"), {})
    local net, p = Options.ProductNet({ yieldMin = 1, yieldMax = 3 }, nil, 25)
    assert_eq({ net(0), net(1), net(2), p }, { 0, 50, 100, nil })                  -- 2 units a craft at 25c
  end)

  it("A23 disenchant targets for any enchanter: buy, expected mats, profit, the skill needed", function()
    local Options = Boot()
    local items = { [6585] = { cur = 800 }, [10940] = { sellValue = 600, chance = 0.8 }, [10939] = { sellValue = 1000 },
                    [9000] = { cur = 5000 } }
    local candidates = { [6585] = { skill = 1, quality = 2, ilvl = 18, kind = "armor" },
                         [9000] = { skill = 1, quality = 2, ilvl = 18, kind = "weapon" },
                         [9001] = { skill = 1, quality = 2, ilvl = 18, kind = "armor" } }       -- no price: skipped
    local rows = Options.Disenchant(candidates, Ctx({}, items, {}))
    assert_eq({ #rows, rows[1].key, rows[1].buy, rows[1].ev, rows[1].profit, rows[1].matChance }, { 1, 6585, 800, 1425, 625, 0.8 })
    local live = Options.Disenchant(candidates, { itemOf = ItemsOf(items),
                                                  levelsOf = function(key) return key == 6585 and { { price = 700, units = 1 } } or nil end })
    assert_eq(live[1].profit, 725)                                                  -- the live scan's 7s
    assert_eq(Options.BestSkill({ professions = { A = { Enchanting = { skill = 150 } }, B = { Enchanting = { skill = 90 } },
                                                  C = { Mining = { skill = 300 } } } }, "Enchanting"), 150)
    assert_eq(Options.BestSkill({}, "Enchanting"), nil)
  end)
end)
