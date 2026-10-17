--- Scenario A1's game sessions (SCENARIOS.md A1, SPEC §2.2.1).
--- Two characters on Grommash (Horde) play one session each into the same
--- AHProfitDB; the result is compared with tests/fixtures/savedvars/a1.lua,
--- which tests/ahsync/ingest/test_savedvars.py parses back.
---   Amaya-Grommash: mage 60, Enchanting 142/150 and Cooking 45/75; bags (with a
---     random-suffix item, key -6585), bank
---     and mailbox visited; craft window opened; a merchant visited.
---   Brugan-Grommash: warrior 40, Blacksmithing 115/150 and Mining 120/150;
---     bags, an empty bank; trade skill window opened; posts 5 Copper Rods (event).

local A1 = {}

A1.GOLDEN = "tests/fixtures/savedvars/a1.lua"
A1.AMAYA_LOGIN = 1760000000   -- server seconds
A1.BRUGAN_LOGIN = 1760003600

--- Shape the world for Amaya's session.
---@param world table  Stubs.world, changed in place
local function AmayaWorld(world)
  world.player = { name = "Amaya", realm = "Grommash", faction = "Horde",
                   className = "Mage", class = "MAGE", level = 60 }
  world.money = 1234567
  world.serverTime = A1.AMAYA_LOGIN
  world.skillLines = {
    { name = "Professions", header = true },
    { name = "Enchanting", rank = 142, max = 150 },
    { name = "Secondary Skills", header = true },
    { name = "Cooking", rank = 45, max = 75 },
    { name = "Weapon Skills", header = true },
    { name = "Staves", rank = 300, max = 300 },
  }
  world.containers = {
    [0] = { size = 16, slots = {
      [1] = { id = 6948, count = 1, bound = true },     -- Hearthstone
      [2] = { id = 2589, count = 15 },                 -- Linen Cloth, two stacks -> 20
      [3] = { id = 2589, count = 5 },
      [4] = { id = 10940, count = 3 },                 -- Strange Dust
    } },
    [1] = { size = 6, slots = {
      [2] = { id = 10938, count = 2 },                 -- Lesser Magic Essence
      [3] = { id = 6585, count = 1, suffix = 1017 },   -- Scouting Gloves "of the Monkey": key -6585
    } },
    [-1] = { size = 24, slots = { [1] = { id = 4306, count = 10 } } }, -- bank: Silk Cloth
    [5] = { size = 6, slots = { [1] = { id = 2589, count = 7 } } },    -- bank bag: Linen Cloth
  }
  world.inbox = {
    { { 2592, 20 } },                 -- Wool Cloth
    {},                               -- a letter with no items
    { { 10940, 2 }, { 10940, 1 } },   -- Strange Dust in two attachments -> 3
  }
  world.merchant = {
    { id = 2320, numAvailable = -1 },  -- Coarse Thread
    { id = 2321, numAvailable = -1 },  -- Fine Thread
    { id = 6270, numAvailable = 1 },   -- limited pattern: not a vendor reagent
  }
  world.craft = { name = "Enchanting", rank = 142, max = 150, recipes = {
    { header = "Enchant Bracer" },
    { name = "Enchant Bracer - Minor Health", spell = 7418, reagents = { { 10940, 1 } } },
    { header = "Rods" },
    { name = "Runed Copper Rod", spell = 7421, product = 6218, min = 1, max = 1,
      reagents = { { 10940, 1 }, { 6217, 1 }, { 10938, 1 } } },
  } }
end

--- Shape the world for Brugan's session.
---@param world table  Stubs.world, changed in place
local function BruganWorld(world)
  world.player = { name = "Brugan", realm = "Grommash", faction = "Horde",
                   className = "Warrior", class = "WARRIOR", level = 40 }
  world.money = 456789
  world.serverTime = A1.BRUGAN_LOGIN
  world.skillLines = {
    { name = "Professions", header = true },
    { name = "Blacksmithing", rank = 115, max = 150 },
    { name = "Mining", rank = 120, max = 150 },
  }
  world.containers = {
    [0] = { size = 16, slots = {
      [1] = { id = 6948, count = 1, bound = true },   -- Hearthstone
      [2] = { id = 2840, count = 6 },                -- Copper Bar
      [3] = { id = 6217, count = 5 },                -- Copper Rod
      [4] = { id = 2835, count = 12 },               -- Rough Stone
    } },
  }
  world.tradeSkill = { name = "Blacksmithing", rank = 115, max = 150, recipes = {
    { header = "Materials" },
    { name = "Rough Sharpening Stone", spell = 2660, product = 2862, min = 1, max = 1,
      reagents = { { 2835, 1 } } },
    { name = "Copper Rod", spell = 2663, product = 6217, min = 1, max = 1,
      reagents = { { 2840, 2 } } },
    { header = "Armor" },
    { name = "Copper Chain Pants", spell = 2662, product = 2852, min = 1, max = 1,
      reagents = { { 2840, 4 } } },
  } }
end

--- Play Amaya's session: login, bank, mailbox, craft window, merchant.
---@param Stubs table  The wow_stubs module
local function PlayAmaya(Stubs)
  Stubs.Reset()
  AmayaWorld(Stubs.world)
  Stubs.Login()
  Stubs.Advance(60)
  Stubs.FireEvent("BANKFRAME_OPENED")
  Stubs.FireEvent("BANKFRAME_CLOSED")
  Stubs.Advance(60)
  Stubs.FireEvent("MAIL_SHOW")
  Stubs.FireEvent("MAIL_INBOX_UPDATE")
  Stubs.FireEvent("MAIL_INBOX_UPDATE")
  Stubs.Advance(1)
  Stubs.FireEvent("MAIL_CLOSED")
  Stubs.Advance(60)
  Stubs.FireEvent("CRAFT_SHOW")
  Stubs.FireEvent("CRAFT_UPDATE")
  Stubs.Advance(1)
  Stubs.FireEvent("CRAFT_CLOSE")
  Stubs.FireEvent("MERCHANT_SHOW")
  Stubs.Advance(1)
  Stubs.FireEvent("MERCHANT_CLOSED")
end

--- Play Brugan's session in the same SavedVariables: login, trade skill window, one post.
---@param Stubs table  The wow_stubs module
local function PlayBrugan(Stubs)
  Stubs.Reset({ keepSaved = true })
  BruganWorld(Stubs.world)
  Stubs.Login()
  Stubs.Advance(10)
  Stubs.FireEvent("BANKFRAME_OPENED")   -- an empty bank: written as "" with its bankAt
  Stubs.FireEvent("BANKFRAME_CLOSED")
  Stubs.Advance(20)
  Stubs.FireEvent("TRADE_SKILL_SHOW")
  Stubs.FireEvent("TRADE_SKILL_UPDATE")
  Stubs.Advance(1)
  Stubs.FireEvent("TRADE_SKILL_CLOSE")
  -- Capture/Posts is a later milestone (M6b); the event goes in through its DB call.
  local DB = AHProfit.DB
  DB.AppendEvent(DB.Get(), { type = "post", char = "Brugan-Grommash", at = 1760003660000,
    key = 6217, stack = 5, price = 4100, durationH = 24, deposit = 60, confirmed = true })
end

--- Play both sessions and serialize the result as WoW would write it.
---@param Stubs table  The wow_stubs module
---@return string      The SavedVariables file contents
---@return string[]    Errors the addon caught during the sessions (should be empty)
function A1.Run(Stubs)
  PlayAmaya(Stubs)
  local errors = { unpack(AHProfit.Events.Errors()) }
  PlayBrugan(Stubs)
  for _, err in ipairs(AHProfit.Events.Errors()) do errors[#errors + 1] = err end
  return Stubs.Serialize("AHProfitDB", AHProfitDB), errors
end

return A1
