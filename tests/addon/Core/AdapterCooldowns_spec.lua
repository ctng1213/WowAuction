-- Specs for addon/AHProfit/Core/AdapterCooldowns.lua: the cooldown reads (SPEC §3.4F.3, §6 item 7).

local Stubs = require("wow_stubs")

--- Load the addon and return its Adapter.
---@return table
local function Boot()
  Stubs.Login()
  return AHProfit.Adapter
end

describe("AdapterCooldowns", function()
  it("seconds left count on the GetTime clock, and a start from before a reboot is unwrapped", function()
    Stubs.Reset()
    local Adapter = Boot()                                   -- GetTime() = 1000
    assert_eq(Adapter.SecondsLeft(900, 172800), 172700)
    assert_eq(Adapter.SecondsLeft(900, 50), 0)               -- already over
    local left = Adapter.SecondsLeft(4294967, 172800)        -- started just before the 2^32 ms wrap
    assert_eq(left > 171799 and left < 171800, true)
  end)

  it("an item cooldown: none, the global cooldown, pending, running, and no API", function()
    Stubs.Reset()
    local Adapter = Boot()
    assert_eq(Adapter.ItemCooldownLeft(15846), 0)                          -- 0, 0: none
    Stubs.world.itemCooldowns[15846] = { 999.5, 1.5, true }
    assert_eq(Adapter.ItemCooldownLeft(15846), 0)                          -- only the global cooldown
    Stubs.world.itemCooldowns[15846] = { 0, 259200, 0 }
    assert_eq(Adapter.ItemCooldownLeft(15846), nil)                        -- enable 0: not started yet
    Stubs.world.itemCooldowns[15846] = { 900, 259200, 1 }
    assert_eq(Adapter.ItemCooldownLeft(15846), 259100)
    C_Container.GetItemCooldown = nil
    assert_eq(Adapter.ItemCooldownLeft(15846), nil)
  end)

  it("a recipe's cooldown in the open window: learned with time left, off cooldown, and not listed", function()
    Stubs.Reset()
    Stubs.Define("C_TradeSkillUI", Stubs.ModernTradeSkill{ name = "Alchemy", rank = 280, max = 300, recipes = {
      { id = 17187, out = 12360, learned = true, cooldown = 3600, reagents = {} },
      { id = 17563, out = 7080, learned = true, reagents = {} },
      { id = 17561, out = 7080, learned = false, reagents = {} } } })
    local Adapter = Boot()
    assert_eq({ Adapter.RecipeCooldown(17187) }, { true, 3600 })
    assert_eq({ Adapter.RecipeCooldown(17563) }, { true })
    assert_eq({ Adapter.RecipeCooldown(17561) }, { false })
    assert_eq({ Adapter.RecipeCooldown(99999) }, {})
    assert_eq(Adapter.OwnTradeWindowReady(), true)
  end)
end)
