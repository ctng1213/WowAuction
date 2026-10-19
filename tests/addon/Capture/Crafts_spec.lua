-- Specs for addon/AHProfit/Capture/Crafts.lua: craft events (SPEC §2.2.1, A47).

local Stubs = require("wow_stubs")

describe("Crafts", function()
  it("A47 each successful cast of a known recipe is a craft; other spells and other units are not", function()
    Stubs.Reset()
    Stubs.Login()
    AHProfitDB.knownRecipes["Amaya-Grommash"] = "6413"
    Stubs.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-1", 6413)
    Stubs.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-2", 6413)
    Stubs.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-3", 133)       -- Fireball
    Stubs.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "target", "Cast-4", 6413)
    local crafts = {}
    for _, e in ipairs(AHProfitDB.events) do crafts[#crafts + 1] = e.type .. " " .. e.spell .. " " .. e.count end
    assert_eq(crafts, { "craft 6413 1", "craft 6413 1" })
  end)
end)
