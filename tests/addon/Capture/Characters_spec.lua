-- Specs for addon/AHProfit/Capture/Characters.lua.

local Stubs = require("wow_stubs")

describe("Characters", function()
  it("login writes the character row", function()
    Stubs.Reset()
    Stubs.world.money = 1234567
    Stubs.Login()
    assert_eq(AHProfitDB.characters, { ["Amaya-Grommash"] = { name = "Amaya", realm = "Grommash",
      faction = "Horde", class = "MAGE", level = 60, gold = 1234567, updated = 1760000000000 } })
    assert_eq(AHProfit.Capture.Characters.CurrentId(), "Amaya-Grommash")
  end)

  it("a burst of money changes updates gold once, after the debounce", function()
    Stubs.Reset()
    Stubs.world.money = 100
    Stubs.Login()
    Stubs.world.money = 150
    Stubs.FireEvent("PLAYER_MONEY")
    Stubs.world.money = 175
    Stubs.FireEvent("PLAYER_MONEY")
    assert_eq(AHProfitDB.characters["Amaya-Grommash"].gold, 100)
    Stubs.Advance(0.5)
    local row = AHProfitDB.characters["Amaya-Grommash"]
    assert_eq(row.gold, 175)
    assert_eq(row.updated, 1760000000000)
    Stubs.Advance(10)
    Stubs.world.money = 0
    Stubs.FireEvent("PLAYER_MONEY")
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.characters["Amaya-Grommash"].updated, 1760000011000)  -- 0.5 + 10 + 0.5 s after login
  end)

  it("level up is read after the debounce, when UnitLevel has caught up", function()
    Stubs.Reset()
    Stubs.world.player.level = 59
    Stubs.Login()
    Stubs.FireEvent("PLAYER_LEVEL_UP", 60)
    Stubs.world.player.level = 60
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.characters["Amaya-Grommash"].level, 60)
  end)

  it("captures nothing when the database could not be set up", function()
    Stubs.Reset()
    AHProfitDB = { formatVersion = 99 }
    Stubs.Login()
    Stubs.FireEvent("PLAYER_MONEY")
    Stubs.Advance(1)
    assert_eq(AHProfitDB, { formatVersion = 99 })
    assert_eq(AHProfit.Events.Errors(), {})
  end)
end)
