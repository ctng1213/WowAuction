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

describe("Options", function()
  it("A15 what you carry gets the addon's own row when AHSync has no plan for it", function()
    local Options = Boot()
    local rows = Options.Sell({ { key = 2589, qty = 15, bound = false }, { key = 2589, qty = 5, bound = false } }, {},
                              ItemsOf({ [2589] = LINEN }), VendorOf({ [2589] = 3 }), NOW, "Amaya-Grommash")
    assert_eq(rows, { { id = "own:2589", kind = "post", key = 2589, qty = 20, price = 45, net = 840, chance = 90, at = NOW,
                        status = "pending", char = "Amaya-Grommash", own = true,
                        ownNote = "post now · AHSync has no plan for it" } })
  end)

  it("A16 items the plan already covers get no own row", function()
    local Options = Boot()
    local rows = Options.Sell({ { key = 2589, qty = 20, bound = false }, { key = 2840, qty = 6, bound = false } },
                              { [2589] = true }, ItemsOf({ [2589] = LINEN, [2840] = { postPrice = 500, sellValue = 470 } }),
                              VendorOf({}), NOW, "Amaya-Grommash")
    assert_eq({ #rows, rows[1].key, rows[1].net, rows[1].chance }, { 1, 2840, 2820, nil })
  end)

  it("A17 a merchant that pays more than posting wins", function()
    local Options = Boot()
    local rows = Options.Sell({ { key = 3300, qty = 7, bound = false } }, {}, ItemsOf({ [3300] = { postPrice = 10, sellValue = 9 } }),
                              VendorOf({ [3300] = 12 }), NOW, "Amaya-Grommash")
    assert_eq({ rows[1].kind, rows[1].price, rows[1].net, rows[1].chance }, { "vendor", 12, 84, nil })
  end)

  it("A18 bound items, items without market data and items nobody pays for get no row", function()
    local Options = Boot()
    local rows = Options.Sell({ { key = 6948, qty = 1, bound = true }, { key = 4306, qty = 10, bound = false },
                                { key = 1, qty = 1, bound = false } }, {},
                              ItemsOf({ [6948] = LINEN, [1] = { postPrice = 5, sellValue = 0 } }), VendorOf({}), NOW, "A")
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
end)
