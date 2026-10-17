-- Specs for addon/AHProfit/Core/Adapter.lua: item keys, player, recipe windows, containers, mail, merchant.

local Stubs = require("wow_stubs")

--- Fresh stubs with only the Adapter loaded.
---@return table  AHProfit.Adapter
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua" })
  return AHProfit.Adapter
end

describe("Adapter", function()
  it("the .toc lists the Core and Capture files in load order", function()
    Stubs.Reset()
    assert_eq(Stubs.TocFiles(), { "Core/Adapter.lua", "Core/Events.lua", "Core/DB.lua",
      "Capture/Characters.lua", "Capture/Professions.lua", "Capture/Inventory.lua" })
  end)

  it("item key of a plain item link is its item id", function()
    local Adapter = Boot()
    assert_eq(Adapter.ItemKey(Stubs.ItemLink(2840)), 2840)
    assert_eq(Adapter.ItemKey("item:2589:0:0:0:0:0:0:0:60"), 2589)
  end)

  it("a random-suffix item's key is minus its item id until §6 item 11 is verified", function()
    local Adapter = Boot()
    assert_eq(Adapter.ItemKey(Stubs.ItemLink(6585, 1017)), -6585)
    assert_eq(Adapter.ItemKey("item:6585:0:0:0:0:0:0:0:60"), 6585)
  end)

  it("item key is nil for non-item links and nil", function()
    local Adapter = Boot()
    assert_eq(Adapter.ItemKey(nil), nil)
    assert_eq(Adapter.ItemKey(Stubs.EnchantLink(7418, "Enchant Bracer - Minor Health")), nil)
    assert_eq(Adapter.ItemKey("|Hitem:abc|h[x]|h"), nil)
  end)

  it("time is server seconds times 1000", function()
    local Adapter = Boot()
    Stubs.world.serverTime = 1760000000.7
    assert_eq(Adapter.NowMs(), 1760000000000)
  end)

  it("reads the player's identity, level and gold", function()
    local Adapter = Boot()
    Stubs.world.money = 1234567
    assert_eq(Adapter.PlayerInfo(), { name = "Amaya", realm = "Grommash", faction = "Horde", class = "MAGE", level = 60 })
    assert_eq(Adapter.Money(), 1234567)
    assert_eq(Adapter.InCombat(), false)
  end)

  it("a collapsed Skills header makes the skill lines unreadable (nil)", function()
    local Adapter = Boot()
    Stubs.world.skillLines = { { name = "Professions", header = true, collapsed = true },
                               { name = "Weapon Skills", header = true }, { name = "Staves", rank = 1, max = 5 } }
    assert_eq(Adapter.ReadSkillLines(), nil)
  end)

  it("an occupied slot without an item link makes bags and bank unreadable (nil)", function()
    local Adapter = Boot()
    Stubs.world.containers = {
      [0] = { size = 2, slots = { [1] = { id = 2840, count = 6 }, [2] = { id = 2589, count = 1, noLink = true } } },
      [-1] = { size = 1, slots = { [1] = { id = 4306, count = 1, noLink = true } } },
    }
    assert_eq(Adapter.ReadBags(), nil)
    assert_eq(Adapter.ReadBank(), nil)
    C_Container = nil
    assert_eq(Adapter.ReadBags(), nil)
  end)

  it("an inbox with mail not loaded yet, or an attachment without a link, is unreadable (nil)", function()
    local Adapter = Boot()
    Stubs.world.inbox = { { { 2592, 20 } } }
    Stubs.world.inboxTotal = 2
    assert_eq(Adapter.ReadInbox(), nil)
    Stubs.world.inboxTotal = nil
    Stubs.world.inbox = { { { 2592, 20 }, { 10940, 1, noLink = true } } }
    assert_eq(Adapter.ReadInbox(), nil)
  end)

  it("reads skill lines without headers", function()
    local Adapter = Boot()
    Stubs.world.skillLines = { { name = "Professions", header = true }, { name = "Mining", rank = 120, max = 150 } }
    assert_eq(Adapter.ReadSkillLines(), { { name = "Mining", rank = 120, max = 150 } })
  end)

  it("reads a trade skill window, skipping headers", function()
    local Adapter = Boot()
    Stubs.world.tradeSkill = { name = "Blacksmithing", rank = 115, max = 150, recipes = {
      { header = "Materials" },
      { name = "Copper Rod", spell = 2663, product = 6217, min = 1, max = 2, reagents = { { 2840, 2 } } },
    } }
    assert_eq(Adapter.ReadRecipeWindow("trade"), { profession = "Blacksmithing", rank = 115, max = 150, recipes = {
      { spell = 2663, product = 6217, yieldMin = 1, yieldMax = 2, reagents = { { key = 2840, count = 2 } } },
    } })
  end)

  it("a closed trade skill window reads as nil", function()
    local Adapter = Boot()
    assert_eq(Adapter.ReadRecipeWindow("trade"), nil)
    assert_eq(Adapter.ReadRecipeWindow("craft"), nil)
  end)

  it("reads an enchant with no product from the craft window, with or without a recipe link", function()
    local Adapter = Boot()
    Stubs.world.craft = { name = "Enchanting", rank = 142, max = 150, recipes = {
      { name = "Enchant Bracer - Minor Health", spell = 7418, reagents = { { 10940, 1 } } },
    } }
    local expected = { profession = "Enchanting", rank = 142, max = 150, recipes = {
      { spell = 7418, yieldMin = 1, yieldMax = 1, reagents = { { key = 10940, count = 1 } } },
    } }
    assert_eq(Adapter.ReadRecipeWindow("craft"), expected)
    GetCraftRecipeLink = nil
    assert_eq(Adapter.ReadRecipeWindow("craft"), expected)
  end)

  it("skips a recipe whose reagent link is not loaded yet", function()
    local Adapter = Boot()
    Stubs.world.tradeSkill = { name = "Blacksmithing", rank = 1, max = 75, recipes = {
      { name = "Copper Rod", spell = 2663, product = 6217, reagents = { { 2840, 2 } } },
    } }
    GetTradeSkillReagentItemLink = function() return nil end
    assert_eq(Adapter.ReadRecipeWindow("trade").recipes, {})
  end)

  it("reads bags slot by slot with the bound flag", function()
    local Adapter = Boot()
    Stubs.world.containers = {
      [0] = { size = 4, slots = { [1] = { id = 6948, count = 1, bound = true }, [3] = { id = 2589, count = 15 } } },
      [2] = { size = 2, slots = { [2] = { id = 2589, count = 5 } } },
      [-1] = { size = 2, slots = { [1] = { id = 4306, count = 10 } } },
    }
    assert_eq(Adapter.ReadBags(), {
      { key = 6948, qty = 1, bound = true }, { key = 2589, qty = 15, bound = false }, { key = 2589, qty = 5, bound = false },
    })
    assert_eq(Adapter.ReadBank(), { { key = 4306, qty = 10, bound = false } })
  end)

  it("falls back to the legacy container API without C_Container", function()
    local Adapter = Boot()
    C_Container = nil
    Stubs.world.containers = { [0] = { size = 1, slots = { [1] = { id = 6948, count = 1, bound = true } } } }
    assert_eq(Adapter.ReadBags(), { { key = 6948, qty = 1, bound = false } })
  end)

  it("reads every mail attachment as not bound", function()
    local Adapter = Boot()
    Stubs.world.inbox = { { { 2592, 20 } }, {}, { { 10940, 2 }, { 10940, 1 } } }
    assert_eq(Adapter.ReadInbox(), {
      { key = 2592, qty = 20, bound = false }, { key = 10940, qty = 2, bound = false }, { key = 10940, qty = 1, bound = false },
    })
  end)

  it("reads only unlimited, gold-priced merchant items", function()
    local Adapter = Boot()
    Stubs.world.merchant = { { id = 2320, numAvailable = -1 }, { id = 6270, numAvailable = 1 },
                             { id = 2321, numAvailable = -1, extendedCost = true } }
    assert_eq(Adapter.ReadMerchantKeys(), { 2320 })
  end)

  it("stores and loads SavedVariables globals", function()
    local Adapter = Boot()
    Adapter.StoreSaved("AHProfitDB", { formatVersion = 1 })
    assert_eq(Adapter.LoadSaved("AHProfitDB"), { formatVersion = 1 })
  end)
end)
