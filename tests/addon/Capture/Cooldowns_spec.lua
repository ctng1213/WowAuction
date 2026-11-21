-- Specs for addon/AHProfit/Capture/Cooldowns.lua: ready times per character and catalog spell (SPEC §3.4F.3).

local Stubs = require("wow_stubs")

local AMAYA = "Amaya-Grommash"
local NOW = 1760000000000            -- the stubs' server time at login, ms

--- An Alchemy window with Arcanite (learned, given seconds left), Undeath to Water (learned) and Earth to Water.
---@param arcaniteLeft number|nil  Arcanite's seconds left; nil = none reported
---@return table                   The fake C_TradeSkillUI
local function Alchemy(arcaniteLeft)
  return Stubs.ModernTradeSkill{ name = "Alchemy", rank = 280, max = 300, recipes = {
    { id = 17187, out = 12360, learned = true, cooldown = arcaniteLeft, reagents = {} },
    { id = 17563, out = 7080, learned = true, reagents = {} },
    { id = 17561, out = 7080, learned = false, reagents = {} } } }
end

--- Open the trade skill window and let the debounce run.
local function OpenWindow()
  Stubs.FireEvent("TRADE_SKILL_SHOW")
  Stubs.Advance(0.5)
end

describe("Cooldowns", function()
  it("CD30 a successful cast of a catalog spell records its ready time at once; other casts and units don't", function()
    Stubs.Reset()
    Stubs.Login()
    Stubs.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-1", 133)       -- Fireball
    Stubs.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "target", "Cast-2", 17187)
    assert_eq(AHProfitDB.cooldowns[AMAYA], nil)
    Stubs.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-3", 17187)     -- Transmute: Arcanite, 2 d
    assert_eq(AHProfitDB.cooldowns[AMAYA], "17187," .. (NOW + 172800000) .. ",c," .. NOW)
  end)

  it("CD12 the newest observation wins: a later game report corrects a cast, an older one never replaces it", function()
    Stubs.Reset()
    Stubs.Define("C_TradeSkillUI", Alchemy(158400))                               -- 1 d 20 h left
    Stubs.Login()
    Stubs.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-1", 17187)
    Stubs.Advance(3600)
    OpenWindow()
    local later = NOW + 3600500                                                    -- after the debounce
    local byspell = AHProfit.Capture.Cooldowns.Decode(AHProfitDB.cooldowns[AMAYA])
    assert_eq(byspell[17187], { readyAt = later - 500 + 158400000, source = "g", at = later - 500 })
    AHProfit.Capture.Cooldowns.Record(AHProfitDB, AMAYA, 17187, 1, "c", NOW)       -- older: ignored
    assert_eq(AHProfit.Capture.Cooldowns.Decode(AHProfitDB.cooldowns[AMAYA])[17187].source, "g")
  end)

  it("CD32 no time left counts as ready only once this client has given a real time left (D15)", function()
    Stubs.Reset()
    Stubs.Define("C_TradeSkillUI", Alchemy(nil))
    Stubs.Login()
    OpenWindow()
    assert_eq(AHProfitDB.cooldowns[AMAYA], nil)                                    -- not trusted yet: nothing stored
    assert_eq(AHProfitDB.cooldownReadsTrusted, nil)
    Stubs.Define("C_TradeSkillUI", Alchemy(7200))
    OpenWindow()
    local byspell = AHProfit.Capture.Cooldowns.Decode(AHProfitDB.cooldowns[AMAYA])
    assert_eq(AHProfitDB.cooldownReadsTrusted, true)
    assert_eq(byspell[17187].source, "g")
    assert_eq(byspell[17563].readyAt, byspell[17563].at)                           -- trusted now: none left = ready
    assert_eq(byspell[17561], nil)                                                 -- not learned: never stored
  end)

  it("a tool in the bags is read on BAG_UPDATE_COOLDOWN; one only in the bank, or none, is not", function()
    Stubs.Reset()
    Stubs.world.itemCooldowns[15846] = { 900, 259200, true }                       -- Salt Shaker, 259100 s left
    Stubs.Login()
    AHProfitDB.inventory[AMAYA] = { bags = "2840,5,0", bank = "15846,1,1" }
    Stubs.FireEvent("BAG_UPDATE_COOLDOWN")
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.cooldowns[AMAYA], nil)
    AHProfitDB.inventory[AMAYA] = { bags = "2840,5,0;15846,1,1" }                  -- soulbound, in the bags
    Stubs.FireEvent("BAG_UPDATE_COOLDOWN")
    Stubs.Advance(0.5)
    local salt = AHProfit.Capture.Cooldowns.Decode(AHProfitDB.cooldowns[AMAYA])[19566]
    assert_eq(salt.source, "g")
    assert_eq(salt.readyAt - salt.at, 259099000)        -- 900 + 259200 - GetTime 1001 (two debounces of 0.5 s)
  end)

  it("CD33 a tool moved into the bags is read after the bag change, and at login, with no cooldown event", function()
    Stubs.Reset()
    Stubs.world.itemCooldowns[15846] = { 900, 259200, true }                       -- Salt Shaker, on cooldown
    Stubs.world.containers = { [0] = { size = 2, slots = { [1] = { id = 2840, count = 5 } } } }
    Stubs.Login()
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.cooldowns[AMAYA], nil)                                    -- not in the bags yet
    Stubs.world.containers[0].slots[2] = { id = 15846, count = 1, bound = true }   -- moved from the bank
    Stubs.FireEvent("BAG_UPDATE_DELAYED")
    Stubs.Advance(0.5)
    local salt = AHProfit.Capture.Cooldowns.Decode(AHProfitDB.cooldowns[AMAYA])[19566]
    assert_eq(salt and salt.source, "g")
    AHProfitDB.cooldowns = {}
    Stubs.Login()                                                                  -- a /reload with it held
    Stubs.Advance(0.5)
    assert_eq(AHProfit.Capture.Cooldowns.Decode(AHProfitDB.cooldowns[AMAYA])[19566].source, "g")
  end)

  it("FM89 with no cooldown APIs nothing breaks: casts are still recorded and the probe lists them missing", function()
    Stubs.Reset()
    local trade = Alchemy(3600)
    trade.GetRecipeCooldown = nil
    Stubs.Define("C_TradeSkillUI", trade)
    Stubs.Login()
    C_Container.GetItemCooldown = nil
    AHProfitDB.inventory[AMAYA] = { bags = "15846,1,1" }
    OpenWindow()
    Stubs.FireEvent("BAG_UPDATE_COOLDOWN")
    Stubs.Advance(0.5)
    assert_eq(AHProfitDB.cooldowns[AMAYA], nil)
    Stubs.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-1", 19566)         -- Salt Shaker used
    assert_eq(AHProfit.Capture.Cooldowns.Decode(AHProfitDB.cooldowns[AMAYA])[19566].source, "c")
    local probe = AHProfit.Adapter.ProbeApis({ "C_TradeSkillUI.GetRecipeCooldown", "C_Container.GetItemCooldown" })
    assert_eq(probe, "C_TradeSkillUI.GetRecipeCooldown=nil;C_Container.GetItemCooldown=nil")
  end)

  it("the catalog decodes once from Data/Cooldowns.lua; encode and decode round-trip by spell id", function()
    Stubs.Reset()
    Stubs.Login()
    local C = AHProfit.Capture.Cooldowns
    local catalog = C.Catalog()
    assert_eq(catalog[17187], { cooldownS = 172800, tool = 9149, toolUse = false })
    assert_eq(catalog[19566], { cooldownS = 259200, tool = 15846, toolUse = true })
    assert_eq(C.Catalog() == catalog, true)
    local text = "17187,1760172800000,c,1760000000000;17559,1760086400000,g,1760001000000"
    assert_eq(C.Encode(C.Decode(text)), text)
    assert_eq(C.Decode(nil), {})
  end)
end)
