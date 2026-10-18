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
end)
