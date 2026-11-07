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
end)
