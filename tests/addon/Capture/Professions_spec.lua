-- Specs for addon/AHProfit/Capture/Professions.lua.

local Stubs = require("wow_stubs")

--- A Blacksmithing trade skill window with the given recipes.
---@param recipes table[]  Recipe rows (wow_stubs world.tradeSkill format)
---@return table           world.tradeSkill
local function Blacksmithing(recipes)
  return { name = "Blacksmithing", rank = 115, max = 150, recipes = recipes }
end

local COPPER_ROD = { name = "Copper Rod", spell = 2663, product = 6217, min = 1, max = 1, reagents = { { 2840, 2 } } }
local PANTS = { name = "Copper Chain Pants", spell = 2662, product = 2852, min = 1, max = 1, reagents = { { 2840, 4 } } }

describe("Professions", function()
  it("login writes only profession skill lines", function()
    Stubs.Reset()
    Stubs.world.skillLines = {
      { name = "Professions", header = true },
      { name = "Blacksmithing", rank = 115, max = 150 },
      { name = "First Aid", rank = 30, max = 75 },
      { name = "Swords", rank = 200, max = 200 },
    }
    Stubs.Login()
    assert_eq(AHProfitDB.professions["Amaya-Grommash"], {
      Blacksmithing = { skill = 115, max = 150 }, ["First Aid"] = { skill = 30, max = 75 } })
  end)

  it("a skill change replaces the professions after the debounce", function()
    Stubs.Reset()
    Stubs.world.skillLines = { { name = "Mining", rank = 120, max = 150 } }
    Stubs.Login()
    Stubs.world.skillLines = { { name = "Mining", rank = 121, max = 150 } }
    Stubs.FireEvent("SKILL_LINES_CHANGED")
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.professions["Amaya-Grommash"], { Mining = { skill = 121, max = 150 } })
  end)

  it("opening the trade skill window writes recipe definitions and known recipes", function()
    Stubs.Reset()
    Stubs.world.tradeSkill = Blacksmithing({ { header = "Materials" }, COPPER_ROD })
    Stubs.Login()
    Stubs.FireEvent("TRADE_SKILL_SHOW")
    Stubs.Advance(0.5)
    -- skill and cooldown are unknown on Classic APIs, so they are left out (SPEC §2.2.1).
    assert_eq(AHProfitDB.recipes.Blacksmithing, { [2663] = { product = 6217, yieldMin = 1, yieldMax = 1,
      reagents = "2840,2" } })
    assert_eq(AHProfitDB.knownRecipes["Amaya-Grommash"], "2663")
  end)

  it("a collapsed Skills header keeps the old professions instead of writing a partial list", function()
    Stubs.Reset()
    Stubs.world.skillLines = { { name = "Professions", header = true }, { name = "Mining", rank = 120, max = 150 } }
    Stubs.Login()
    Stubs.world.skillLines = { { name = "Professions", header = true, collapsed = true } }
    Stubs.FireEvent("SKILL_LINES_CHANGED")
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.professions["Amaya-Grommash"], { Mining = { skill = 120, max = 150 } })
  end)

  it("known recipes only grow, so a filtered window doesn't forget recipes", function()
    Stubs.Reset()
    Stubs.world.tradeSkill = Blacksmithing({ COPPER_ROD, PANTS })
    Stubs.Login()
    Stubs.FireEvent("TRADE_SKILL_SHOW")
    Stubs.Advance(0.5)
    Stubs.world.tradeSkill = Blacksmithing({ PANTS })
    Stubs.FireEvent("TRADE_SKILL_UPDATE")
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.knownRecipes["Amaya-Grommash"], "2662;2663")
  end)

  it("listens to window updates only while the window is open", function()
    Stubs.Reset()
    Stubs.Login()
    assert_eq(Stubs.IsRegistered("TRADE_SKILL_UPDATE"), false)
    Stubs.FireEvent("TRADE_SKILL_SHOW")
    assert_eq(Stubs.IsRegistered("TRADE_SKILL_UPDATE"), true)
    Stubs.FireEvent("TRADE_SKILL_CLOSE")
    assert_eq(Stubs.IsRegistered("TRADE_SKILL_UPDATE"), false)
    Stubs.FireEvent("CRAFT_SHOW")
    assert_eq(Stubs.IsRegistered("CRAFT_UPDATE"), true)
    Stubs.FireEvent("CRAFT_CLOSE")
    assert_eq(Stubs.IsRegistered("CRAFT_UPDATE"), false)
  end)

  it("a capture that comes due after the window closed writes nothing", function()
    Stubs.Reset()
    Stubs.world.tradeSkill = Blacksmithing({ COPPER_ROD })
    Stubs.Login()
    Stubs.FireEvent("TRADE_SKILL_SHOW")
    Stubs.FireEvent("TRADE_SKILL_CLOSE")
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.recipes, {})
  end)

  it("craft window recipes sort their reagents by key", function()
    Stubs.Reset()
    Stubs.world.craft = { name = "Enchanting", rank = 142, max = 150, recipes = {
      { name = "Runed Copper Rod", spell = 7421, product = 6218,
        reagents = { { 10940, 1 }, { 6217, 1 }, { 10938, 1 } } },
    } }
    Stubs.Login()
    Stubs.FireEvent("CRAFT_SHOW")
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.recipes.Enchanting[7421].reagents, "6217,1;10938,1;10940,1")
  end)

  it("merchant visits add unlimited items to the vendor reagents, ascending", function()
    Stubs.Reset()
    Stubs.Login()
    Stubs.world.merchant = { { id = 2321, numAvailable = -1 }, { id = 6270, numAvailable = 1 } }
    Stubs.FireEvent("MERCHANT_SHOW")
    Stubs.Advance(0.5)
    Stubs.FireEvent("MERCHANT_CLOSED")
    assert_eq(Stubs.IsRegistered("MERCHANT_UPDATE"), false)
    Stubs.world.merchant = { { id = 2320, numAvailable = -1 } }
    Stubs.FireEvent("MERCHANT_SHOW")
    Stubs.FireEvent("MERCHANT_UPDATE")
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.vendorReagents, "2320;2321")
  end)

  -- Forever's client has the modern APIs, not the Classic ones (verified 2026-10-16, SPEC §6).

  it("login writes professions from the modern API when GetProfessions exists", function()
    Stubs.Reset()
    local info = { [3] = { "Herbalism", 15, 75 }, [5] = { "Fishing", 12, 75 }, [4] = { "Cooking", 1, 75 } }
    Stubs.Define("GetProfessions", function() return 3, nil, nil, 5, 4 end)
    Stubs.Define("GetProfessionInfo", function(i) local p = info[i]; return p[1], "icon", p[2], p[3] end)
    Stubs.Login()
    assert_eq(AHProfitDB.professions["Amaya-Grommash"], {
      Herbalism = { skill = 15, max = 75 }, Fishing = { skill = 12, max = 75 }, Cooking = { skill = 1, max = 75 },
    })
  end)

  it("merchant visits read stock from C_MerchantFrame.GetItemInfo when it exists", function()
    Stubs.Reset()
    Stubs.world.merchant = { { id = 2320 }, { id = 2321 }, { id = 6270 } }
    local stock = { { numAvailable = -1 }, { numAvailable = -1, hasExtendedCost = true }, { numAvailable = 3 } }
    Stubs.Define("GetMerchantItemInfo", nil)
    Stubs.Define("C_MerchantFrame", { GetItemInfo = function(i) return stock[i] end })
    Stubs.Login()
    Stubs.FireEvent("MERCHANT_SHOW")
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.vendorReagents, "2320")
    assert_eq(AHProfit.Events.Errors(), {})
  end)

  it("with C_TradeSkillUI the recipe window listens to TRADE_SKILL_LIST_UPDATE, not the unknown Classic event", function()
    Stubs.Reset()
    Stubs.world.unknownEvents = { TRADE_SKILL_UPDATE = true }
    Stubs.Define("C_TradeSkillUI", {})
    Stubs.Login()
    Stubs.FireEvent("TRADE_SKILL_SHOW")
    assert_eq(Stubs.IsRegistered("TRADE_SKILL_LIST_UPDATE"), true)
    Stubs.FireEvent("TRADE_SKILL_CLOSE")
    assert_eq(Stubs.IsRegistered("TRADE_SKILL_LIST_UPDATE"), false)
    assert_eq(AHProfit.Events.Errors(), {})
  end)
end)
