-- Specs for addon/AHProfit/Capture/Realmless.lua: folding "Name-" records into "Name-Realm" (SPEC §2.2.1).

local Stubs = require("wow_stubs")

local REAL = "Amaya-Grommash"

--- A file with Amaya under her real id and under "Amaya-" (sessions after the client stopped naming the realm),
--- and Brugan only under "Brugan-" beside his real row.
---@param realmUpdated number  When Amaya's real character row was last updated, ms
---@return table               AHProfitDB
local function TwoAmayas(realmUpdated)
  local function row(name, realm, class, updated, login)
    return { name = name, realm = realm, class = class, faction = "Horde", updated = updated, login = login }
  end
  return { formatVersion = 1,
    characters = { [REAL] = row("Amaya", "Grommash", "MAGE", realmUpdated, 500),
                   ["Amaya-"] = row("Amaya", "", "MAGE", 9000, 8000),
                   ["Brugan-Grommash"] = row("Brugan", "Grommash", "WARRIOR", 1000),
                   ["Brugan-"] = row("Brugan", "", "WARRIOR", 9000) },
    inventory = { [REAL] = { bank = "2840,1,0", bankAt = 1000, mail = "2589,4,0", mailAt = 900 },
                  ["Amaya-"] = { bank = "8150,3,0", bankAt = 9000 } },
    knownRecipes = { [REAL] = "17187", ["Amaya-"] = "17563" },
    reserves = { [REAL] = "2840,5", ["Amaya-"] = "2840,2;4306,-1" },
    reserveTimes = { [REAL] = "2840,500;2589,9500", ["Amaya-"] = "2840,9000;2589,9000;4306,9000" },
    cooldowns = { [REAL] = "17187,5000,c,1000", ["Amaya-"] = "17187,7000,g,9000;19566,9500,g,9000" },
    events = { { id = "Amaya-:9000:0", type = "craft", char = "Amaya-", at = 9000, spell = 17187, count = 1 },
               { id = "Brugan-Grommash:9000:0", type = "mail_sent", char = "Brugan-Grommash", to = "Amaya-",
                 at = 9000, key = 2840, qty = 1 } },
    scans = {} }
end

describe("Realmless", function()
  it("RL2 at login a realm-less copy folds into the real id: each place, item and spell the newer, rows re-assigned", function()
    Stubs.Reset()
    Stubs.world.player.realm = ""
    AHProfitDB = TwoAmayas(1000)
    Stubs.Login()
    local db = AHProfitDB
    for _, t in ipairs({ "characters", "inventory", "knownRecipes", "cooldowns", "reserves", "reserveTimes" }) do
      assert_eq(db[t]["Amaya-"], nil)
    end
    assert_eq(db.inventory[REAL].bank, "8150,3,0")                                -- the newer bank
    assert_eq(db.inventory[REAL].mail, "2589,4,0")                                -- only the real id had mail
    assert_eq(db.knownRecipes[REAL], "17187;17563")                               -- both windows' recipes
    assert_eq(db.reserves[REAL], "2840,2;4306,-1")                                -- 2840 set later; 4306 new
    assert_eq(db.reserveTimes[REAL], "2589,9500;2840,9000;4306,9000")             -- 2589: the real id's clear is newer
    local cds = AHProfit.Capture.Cooldowns.Decode(db.cooldowns[REAL])
    assert_eq({ cds[17187].readyAt, cds[17187].source, cds[19566].readyAt }, { 7000, "g", 9500 })
    assert_eq({ db.events[1].id, db.events[1].char }, { "Amaya-:9000:0", REAL })    -- the id is kept
    assert_eq(db.events[2].to, REAL)
    assert_eq({ db.characters[REAL].realm, db.characters[REAL].login }, { "Grommash", 8000 })
    assert_eq({ db.characters["Brugan-"], db.characters["Brugan-Grommash"].updated }, { nil, 9000 })   -- alts too
  end)

  it("RL2 pruning after a fold waits for AHSync to commit both ids, across a /reload, until AHSync folded too", function()
    Stubs.Reset()
    Stubs.world.player.realm = ""
    AHProfitDB = TwoAmayas(1000)
    Stubs.Login()
    local DB, foldAt = AHProfit.DB, AHProfitDB.realmFolds[REAL].at
    local before = foldAt - 1000                                                  -- a plan written before the fold
    local safe = DB.SafeIngestedUntil(AHProfitDB, { [REAL] = 9500, ["Amaya-"] = 8000, ["Mira-Grommash"] = 7000 }, before)
    assert_eq({ safe[REAL], safe["Mira-Grommash"] }, { 8000, 7000 })              -- the earlier of the two
    assert_eq(DB.SafeIngestedUntil(AHProfitDB, { [REAL] = 9500 }, before)[REAL], nil)   -- nothing committed as "Amaya-"
    local saved = AHProfitDB
    Stubs.Reset()                                                                 -- a /reload: the note is saved
    AHProfitDB = saved
    Stubs.Login()
    assert_eq(AHProfit.DB.SafeIngestedUntil(AHProfitDB, { [REAL] = 9500, ["Amaya-"] = 8000 }, before)[REAL], 8000)
    local after = AHProfit.DB.SafeIngestedUntil(AHProfitDB, { [REAL] = 9500 }, foldAt + 1000)   -- AHSync folded
    assert_eq({ after[REAL], AHProfitDB.realmFolds }, { 9500, nil })
  end)

  it("RL3 when the real id was updated later, its row stays; places and items still go by their own times", function()
    Stubs.Reset()
    AHProfitDB = TwoAmayas(9999)                                                  -- the client names the realm again
    Stubs.Login()
    local db = AHProfitDB
    assert_eq(db.inventory[REAL].bank, "8150,3,0")
    assert_eq(db.characters["Amaya-"], nil)
    assert_eq(db.characters[REAL].login, 500)
    assert_eq(db.knownRecipes[REAL], "17187;17563")
  end)

  it("RL3 a damaged cooldown string is dropped and the login still captures", function()
    Stubs.Reset()
    Stubs.world.player.realm = ""
    AHProfitDB = TwoAmayas(1000)
    AHProfitDB.cooldowns["Amaya-"] = 42
    Stubs.Login()
    assert_eq(AHProfitDB.cooldowns["Amaya-"], nil)
    assert_eq(AHProfitDB.characters[REAL].updated, 1760000000000)                 -- captured at login
    assert_eq(AHProfit.Events.Errors(), {})
  end)
end)
