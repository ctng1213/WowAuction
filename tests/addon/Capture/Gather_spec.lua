-- Specs for addon/AHProfit/Capture/Gather.lua: gather events from loot windows (SPEC §2.2.1 gather, §3.4E.3).

local Stubs = require("wow_stubs")

local MINING, HERBS, SKINNING, FISHING = 2575, 2366, 8613, 7620
local DISENCHANT, PICK_POCKET, OPENING, FIREBALL = 13262, 921, 3365, 133
local COPPER, TIN, STONE, LEATHER, SCRAPS, PEACEBLOOM, SMALLFISH = 2770, 2771, 2835, 2318, 2934, 2447, 6291
local T0 = 1760000000000

--- Log Brugan in for the first time this session (PLAYER_ENTERING_WORLD with isInitialLogin).
local function LoginBrugan()
  Stubs.Reset()
  Stubs.world.player.name, Stubs.world.player.class = "Brugan", "WARRIOR"
  Stubs.Login()
  Stubs.FireEvent("PLAYER_ENTERING_WORLD", true, false)
end

--- The player's successful cast of a spell.
---@param spell number
local function Cast(spell)
  Stubs.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-" .. spell, spell)
end

--- A loot window opens with these slots, then closes.
---@param slots table[]
---@param source string|nil
local function Loot(slots, source)
  Stubs.OpenLoot(slots, source)
  Stubs.FireEvent("LOOT_OPENED")
  Stubs.FireEvent("LOOT_CLOSED")
end

--- The gather events as "source loot" strings.
---@return string[]
local function Gathered()
  local out = {}
  for _, e in ipairs(AHProfitDB.events) do
    if e.type == "gather" then out[#out + 1] = e.source .. " " .. e.loot end
  end
  return out
end

describe("Gather", function()
  it("A85 a Mining cast's loot window is one gather event with its source, login and loot", function()
    LoginBrugan()
    Stubs.Advance(60)
    Cast(MINING)
    Loot({ { id = COPPER, count = 3 }, { id = STONE, count = 1 } }, "object")
    assert_eq(AHProfitDB.events, { { id = "Brugan-Grommash:" .. (T0 + 60000) .. ":0", type = "gather",
      char = "Brugan-Grommash", at = T0 + 60000, source = "mining", login = T0, loot = "2770,3;2835,1" } })
    assert_eq(AHProfit.Events.Traces(), { "gather mining loot 2 -> gather" })
  end)

  it("A86 a corpse is monster loot without the coin, then its Skinning is skinning", function()
    LoginBrugan()
    Loot({ { id = LEATHER, count = 1 }, { id = SCRAPS, count = 2 }, { coin = 50 } }, "creature")
    Cast(SKINNING)
    Loot({ { id = LEATHER, count = 1 } }, "creature")
    assert_eq(Gathered(), { "monster 2318,1;2934,2", "skinning 2318,1" })
  end)

  it("A87 Herb Gathering is herbalism and a Fishing cast's bobber is fishing", function()
    LoginBrugan()
    Cast(HERBS)
    Loot({ { id = PEACEBLOOM, count = 2 } }, "object")
    Stubs.FireEvent("UNIT_SPELLCAST_CHANNEL_START", "player", "Cast-F", FISHING)
    Stubs.Advance(20)                                                      -- the bobber is clicked 20 s later
    Loot({ { id = SMALLFISH, count = 1 } }, "object")
    assert_eq(Gathered(), { "herbalism 2447,2", "fishing 6291,1" })
  end)

  it("A88 a Disenchant, a chest, a clam and a picked pocket record no gather event", function()
    LoginBrugan()
    Stubs.world.containers[0] = { size = 4, slots = { [3] = { id = 4570, count = 1, locked = true } } }
    Stubs.FireEvent("UNIT_SPELLCAST_START", "player", "Cast-D", DISENCHANT)
    Cast(DISENCHANT)
    Loot({ { id = 10940, count = 3 } })
    Cast(OPENING)
    Loot({ { id = 2589, count = 4 } }, "object")                           -- a chest
    Loot({ { id = 4306, count = 1 } }, "item")                             -- a clam opened from the bags
    Cast(PICK_POCKET)
    Loot({ { id = 5367, count = 1 } }, "creature")                         -- a live creature's pocket
    assert_eq(Gathered(), {})
    assert_eq(#AHProfitDB.events, 1)
    assert_eq(AHProfitDB.events[1].type, "disenchant")
    assert_eq(AHProfit.Events.Traces(), { "de start 4570", "de loot 1 -> disenchant", "loot after 'Opening' -> none",
                                          "loot after 'Pick Pocket' -> none" })
  end)

  it("A89 buying, mail, trades, the bank and crafting open no loot window and record no gather event", function()
    LoginBrugan()
    AHProfitDB.knownRecipes["Brugan-Grommash"] = "2963"
    Stubs.world.containers[0] = { size = 4, slots = { [1] = { id = PEACEBLOOM, count = 20 } } }
    Stubs.FireEvent("BAG_UPDATE", 0)
    Stubs.FireEvent("MAIL_INBOX_UPDATE")
    Stubs.FireEvent("TRADE_CLOSED")
    Stubs.FireEvent("BANKFRAME_OPENED")
    Cast(2963)                                                             -- Bolt of Linen Cloth
    Stubs.Advance(1)
    assert_eq(Gathered(), {})
  end)

  it("A90 one loot window is one event summed per item by key, however often LOOT_OPENED fires", function()
    LoginBrugan()
    Cast(MINING)
    Stubs.OpenLoot({ { id = COPPER, count = 2 }, { id = COPPER, count = 1 }, { id = TIN, count = 1 } }, "object")
    Stubs.FireEvent("LOOT_OPENED")
    Stubs.FireEvent("LOOT_OPENED")                                         -- the autoloot path fires it twice
    Stubs.FireEvent("LOOT_CLOSED")
    assert_eq(Gathered(), { "mining 2770,3;2771,1" })
  end)

  it("A91 a /reload keeps the initial login, and the pruning handshake drops gather events at or before T", function()
    LoginBrugan()
    Cast(MINING)
    Loot({ { id = COPPER, count = 2 } }, "object")
    Stubs.Advance(120)
    local saved = AHProfitDB
    Stubs.Reset({ keepSaved = true })                                      -- /reload: a new Lua state, same file
    Stubs.world.player.name, Stubs.world.player.class = "Brugan", "WARRIOR"
    Stubs.world.serverTime = Stubs.world.serverTime + 300
    Stubs.Login()
    Stubs.FireEvent("PLAYER_ENTERING_WORLD", false, true)
    Stubs.FireEvent("PLAYER_ENTERING_WORLD", false, false)                 -- a zone change changes nothing
    Cast(MINING)
    Loot({ { id = COPPER, count = 3 } }, "object")
    assert_eq(AHProfitDB == saved, true)
    local logins = {}
    for _, e in ipairs(AHProfitDB.events) do logins[#logins + 1] = e.login end
    assert_eq(logins, { T0, T0 })
    local first = AHProfitDB.events[1].at
    assert_eq(AHProfit.DB.Prune(AHProfitDB, { ["Brugan-Grommash"] = first }, first + 600000), 1)
    assert_eq(Gathered(), { "mining 2770,3" })
    Stubs.Reset({ keepSaved = true })                                      -- a relog: a new initial login
    Stubs.world.player.name, Stubs.world.player.class = "Brugan", "WARRIOR"
    Stubs.world.serverTime = Stubs.world.serverTime + 900
    Stubs.Login()
    Stubs.FireEvent("PLAYER_ENTERING_WORLD", true, false)
    assert_eq(AHProfitDB.characters["Brugan-Grommash"].login, T0 + 900000)
  end)

  it("FM42 an unreadable loot window records nothing and raises no error; the next one is recorded", function()
    LoginBrugan()
    Cast(MINING)
    Loot({}, "object")                                                     -- no slots
    Cast(MINING)
    Stubs.Define("GetLootSlotLink", function() error("loot read failed") end)
    Loot({ { id = COPPER, count = 2 } }, "object")
    assert_eq(Gathered(), {})
    assert_eq(#AHProfit.Events.Errors(), 1)
    assert_eq(AHProfit.Events.Errors()[1]:find("loot read failed", 1, true) ~= nil, true)
    Stubs.Define("GetLootSlotLink", function(i) return Stubs.ItemLink(Stubs.world.loot[i].id) end)
    Cast(MINING)
    Loot({ { id = COPPER, count = 2 } }, "object")
    assert_eq(Gathered(), { "mining 2770,2" })
    assert_eq(AHProfit.Events.Traces(), { "gather mining loot 0 -> none", "gather mining loot error -> none",
                                          "gather mining loot 1 -> gather" })
  end)

  it("a stale or contradicted gathering cast leaves a corpse monster loot and an object nothing", function()
    LoginBrugan()
    Cast(FIREBALL)                                                         -- a combat spell is no loot cast
    Loot({ { id = LEATHER, count = 1 } }, "creature")
    Cast(MINING)                                                           -- the node's loot never opened
    Loot({ { id = SCRAPS, count = 1 } }, "creature")
    Cast(SKINNING)
    Loot({ { id = 2589, count = 1 } }, "object")                           -- not what a skinning cast opens
    Cast(MINING)
    Stubs.Advance(11)                                                      -- older than the 10 s timeout
    Loot({ { id = COPPER, count = 1 } }, "object")
    Stubs.FireEvent("UNIT_SPELLCAST_SUCCEEDED", "target", "Cast-X", MINING)   -- another unit's cast
    Loot({ { id = COPPER, count = 1 } }, "object")
    assert_eq(Gathered(), { "monster 2318,1", "monster 2934,1" })
  end)

  it("without GetLootSourceInfo a gathering cast is still recorded but no corpse ever is", function()
    LoginBrugan()
    Stubs.Define("GetLootSourceInfo", nil)
    Loot({ { id = LEATHER, count = 1 } }, "creature")
    Cast(MINING)
    Loot({ { id = COPPER, count = 1 } }, "object")
    assert_eq(Gathered(), { "mining 2770,1" })
    assert_eq(AHProfit.Adapter.LootCast(nil), nil)
  end)

  it("a gather event before any PLAYER_ENTERING_WORLD carries now as the login, kept from then on", function()
    Stubs.Reset()
    Stubs.Login()
    Cast(MINING)
    Loot({ { id = COPPER, count = 1 } }, "object")
    Stubs.Advance(30)
    Cast(MINING)
    Loot({ { id = COPPER, count = 1 } }, "object")
    assert_eq(AHProfitDB.events[1].login, T0)
    assert_eq(AHProfitDB.events[2].login, T0)
    assert_eq(AHProfit.Capture.Gather.PerfLine(), "loot capture 0.000 ms last window, 0.000 ms slowest (2 windows)")
  end)
end)
