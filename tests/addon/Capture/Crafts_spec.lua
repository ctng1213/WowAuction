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

  it("A55 a Disenchant's loot is one disenchant event of the locked item; an interrupted one or other loot adds none", function()
    Stubs.Reset()
    Stubs.world.containers[0] = { size = 4, slots = { [3] = { id = 4570, count = 1, locked = true } } }
    local loot = { { 10940, 3 }, { 10938, 1 }, { nil, 50 } }                  -- dust, essence, coin
    Stubs.Define("GetNumLootItems", function() return #loot end)
    Stubs.Define("GetLootSlotLink", function(i) return loot[i][1] and Stubs.ItemLink(loot[i][1]) or nil end)
    Stubs.Define("GetLootSlotInfo", function(i) return "icon", "name", loot[i][2] end)
    Stubs.Login()
    local DE = AHProfit.Capture.Crafts.DISENCHANT
    Stubs.FireEvent("UNIT_SPELLCAST_START", "player", "Cast-1", DE)
    Stubs.FireEvent("UNIT_SPELLCAST_INTERRUPTED", "player", "Cast-1", DE)
    Stubs.FireEvent("LOOT_OPENED")                                           -- interrupted: nothing
    Stubs.FireEvent("UNIT_SPELLCAST_START", "player", "Cast-2", DE)
    Stubs.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-2", DE)
    Stubs.FireEvent("LOOT_OPENED")
    Stubs.FireEvent("LOOT_OPENED")                                           -- a later loot window: nothing
    local events = {}
    for _, e in ipairs(AHProfitDB.events) do events[#events + 1] = e.type .. " " .. e.key .. " " .. e.results end
    assert_eq(events, { "disenchant 4570 10938,1;10940,3" })
  end)
end)
