-- Specs for addon/AHProfit/UI/FlipView.lua: the addon's own one-stack flips (SPEC §4.3, A59).

local Stubs = require("wow_stubs")
local UIWorld = require("ui_world")

local NOW = 1760000000000              -- Thu 9 Oct 08:53 UTC
local LINEN, SILK, WOOL = 2589, 4306, 2592

--- Log in with Linen, Silk and Wool in the market file, each with a flip offer; the plan flips Wool.
---@param linenNow number|nil  Linen's cheapest price now, copper (default 40)
---@return table  AHProfit.MainWindow
local function Boot(linenNow)
  Stubs.Reset()
  Stubs.world.money = 100000
  local offer = "53,61,138"                                              -- 53c a unit expected, 61c on Sat 18:00 UTC
  UIWorld.Market({ [LINEN] = UIWorld.Item(linenNow or 40, 86, 48, { refStack = 20, chance = 0.9 }),
                   [SILK] = UIWorld.Item(30, 80, 60, { refStack = 20 }), [WOOL] = UIWorld.Item(20, 80, 40) },
                 NOW - UIWorld.HOUR, { [LINEN] = offer, [SILK] = offer, [WOOL] = offer })
  UIWorld.Plan({ { id = "w1", kind = "buy", key = WOOL, qty = 5, price = 20, at = NOW, net = 40 },
                 { id = "w2", kind = "post", key = WOOL, qty = 5, price = 40, at = NOW + 86400000, net = 60, parent = "w1",
                   chance = 70 } })
  Stubs.Login()
  AHProfitDB.rules[SILK] = { rule = "no_buy", setAt = NOW }
  return AHProfit.MainWindow
end

describe("FlipView", function()
  it("A59 a market offer under its buy price is the addon's own one-stack flip; rules and planned flips win", function()
    local MainWindow = Boot()
    Stubs.Slash("/ahp")
    MainWindow.ShowTab("flip")
    local VM = AHProfit.ViewModel
    assert_eq(VM.FlipCeiling(53), 44)                                    -- floor(53 ÷ 1.2)
    local own = VM.OwnFlipRows({ [LINEN] = { expected = 53, price = 61, slot = 138 } }, {
      itemOf = function() return { cur = 40, refStack = 20, chance = 0.9 } end, levelsOf = function() return nil end,
      lookOf = function() return { name = "Linen Cloth", quality = 1, conf = 86 } end, skip = function() return false end,
      nowMs = NOW, offsetMs = 0, totalGold = 100000 })
    assert_eq({ own[1].qty, own[1].buy, own[1].sell, own[1].when, own[1].profit, own[1].chance, own[1].note },
              { "×20", "40c", "61c", "Sat 18:00", "+2s 60c", "90%", "addon's own idea · one stack · buy at or below 44c" })
    assert_eq(MainWindow.Frames().footer:GetText():find("1 own idea (one stack each)", 1, true) ~= nil, true)
    local gone = VM.OwnFlipRows({ [LINEN] = { expected = 53, price = 61, slot = 138 } }, {
      itemOf = function() return { cur = 40, refStack = 20 } end,
      levelsOf = function() return { { price = 46, units = 20 } } end,                     -- a scan: 46c > 44c
      lookOf = function() return { name = "Linen Cloth", quality = 1 } end, skip = function() return false end,
      nowMs = NOW, offsetMs = 0, totalGold = 100000 })
    assert_eq(#gone, 0)
  end)

  it("A69 a stack a live book shows dumped below its next level is a flip to relist now", function()
    Boot()
    local VM = AHProfit.ViewModel
    local rows = {}
    for r = 1, 24 do rows[r] = { 0.9, 0.8, 0.5 } end
    local curve = { rows = rows, aheadPoints = { 0, 5, 20 }, refreshMs = NOW - 3 * UIWorld.HOUR }
    local linen = { med7 = 48, p90 = 55, refStack = 20, conf = 86 }
    --- The Flip tab context over one candidate, with or without a curve.
    ---@param withCurve boolean
    ---@param second number  The next level's price, copper
    ---@return table
    local function Ctx(withCurve, second)
      return { itemOf = function() return linen end, levelsOf = function() return nil end,
               lookOf = function() return { name = "Linen Cloth", quality = 1, conf = 86 } end,
               skip = function() return false end, nowMs = NOW, offsetMs = 0, totalGold = 100000,
               curveOf = function() return withCurve and curve or nil end, vendorOf = function() return 10 end,
               nowCandidates = { { key = LINEN, cheapest = 10, second = second, units = 20 } } }
    end
    local own = VM.OwnFlipRows({}, Ctx(true, 50))
    assert_eq({ own[1].qty, own[1].buy, own[1].sell, own[1].when, own[1].profit, own[1].chance, own[1].note },
              { "×20", "10c", "49c", "now", "+6s 57c", "90%", "buy at 10c, relist now at 49c" })   -- 856.9 − 200
    own = VM.OwnFlipRows({}, Ctx(false, 50))
    assert_eq({ own[1].profit, own[1].chance }, { "+3s 61c", "50%" })            -- 560.5 − 200, volume unknown
    assert_eq(#VM.OwnFlipRows({}, Ctx(true, 11)), 0)                             -- relisting at 10c doesn't pay
    local both = VM.OwnFlipRows({ [LINEN] = { expected = 53, price = 61, slot = 138 } }, Ctx(true, 50))
    assert_eq(#both, 1)                                                          -- one row per item
    linen.cur = 10
    both = VM.OwnFlipRows({ [LINEN] = { expected = 53, price = 61, slot = 138 } }, Ctx(true, 50))
    assert_eq({ #both, both[1].when }, { 1, "Sat 18:00" })     -- the window offer pays more: (53 − 10) × 20 = 860
  end)

  it("R28 a relist now needs a listing left or enough closes to ground its price", function()
    Boot()
    local VM = AHProfit.ViewModel
    local item = { med7 = 55, p90 = 60, refStack = 20, conf = 86, closes30 = 4 }
    --- The Flip tab context over one Linen candidate whose next level is `second` (nil: the only listing).
    ---@param second number|nil
    ---@return table
    local function Ctx(second)
      return { itemOf = function() return item end, levelsOf = function() return nil end,
               lookOf = function() return { name = "Linen Cloth", quality = 1, conf = 86 } end,
               skip = function() return false end, nowMs = NOW, offsetMs = 0, totalGold = 100000,
               curveOf = function() return nil end, vendorOf = function() return 10 end,
               nowCandidates = { { key = LINEN, cheapest = 10, second = second, units = 20 } } }
    end
    assert_eq(#VM.OwnFlipRows({}, Ctx(nil)), 0)                  -- the only listing, 4 closes: nothing grounds 55c
    assert_eq(VM.OwnFlipRows({}, Ctx(50))[1].sell, "49c")        -- under the 50c level left, min(49c, med7 55c)
    item.closes30 = 30
    assert_eq(VM.OwnFlipRows({}, Ctx(nil))[1].sell, "55c")       -- 30 closes ground the level
  end)
end)
