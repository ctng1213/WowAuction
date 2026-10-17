-- Specs for addon/AHProfit/Core/DB.lua: setup, migrations, event ids, pruning,
-- codecs, and the A1 SavedVariables round-trip golden file.

local Stubs = require("wow_stubs")
local A1 = require("a1_world")

--- Fresh stubs with the addon loaded and ADDON_LOADED fired.
---@param saved any  AHProfitDB before the addon loads
---@return table     AHProfit.DB
local function Boot(saved)
  Stubs.Reset()
  AHProfitDB = saved
  Stubs.LoadAddon()
  Stubs.FireEvent("ADDON_LOADED", "AHProfit")
  return AHProfit.DB
end

describe("DB", function()
  it("A1 capture writes a SavedVariables file that ingest parses back to the same records", function()
    local text, errors = A1.Run(Stubs)
    assert_eq(errors, {}, "no handler may fail during the sessions")
    if os.getenv("AHP_UPDATE_GOLDEN") == "1" then Stubs.WriteFile(A1.GOLDEN, text) end
    local golden = Stubs.ReadFile(A1.GOLDEN)
    assert_eq(text, golden, "tests/fixtures/savedvars/a1.lua is what test_savedvars.py parses; "
      .. "regenerate with AHP_UPDATE_GOLDEN=1 only on purpose and review the diff")
  end)

  it("first run creates an empty format 1 database", function()
    local DB = Boot(nil)
    assert_eq(DB.Get(), AHProfitDB)
    assert_eq(AHProfitDB, { formatVersion = 1, addonVersion = "0.1.0", characters = {}, professions = {},
      recipes = {}, knownRecipes = {}, inventory = {}, ownAuctions = {}, scans = {}, events = {}, rules = {} })
  end)

  it("ignores ADDON_LOADED for other addons", function()
    Stubs.Reset()
    Stubs.LoadAddon()
    Stubs.FireEvent("ADDON_LOADED", "Blizzard_AuctionUI")
    assert_eq(AHProfit.DB.Get(), nil)
    assert_eq(AHProfitDB, nil)
  end)

  it("keeps an existing format 1 database and fills in missing tables", function()
    local saved = { formatVersion = 1, addonVersion = "0.0.9", characters = { x = { level = 1 } } }
    local DB = Boot(saved)
    assert_eq(DB.Get() == saved, true)
    assert_eq(saved.characters.x.level, 1)
    assert_eq(saved.scans, {})
    assert_eq(saved.addonVersion, "0.1.0")
  end)

  it("refuses a database from a newer addon and leaves it untouched", function()
    local saved = { formatVersion = 2, characters = { x = 1 } }
    local DB = Boot(saved)
    assert_eq(DB.Get(), nil)
    assert_eq(AHProfitDB == saved, true)
    assert_eq(saved, { formatVersion = 2, characters = { x = 1 } })
    assert_eq(DB.SetupError(), "AHProfitDB is format 2, newer than this addon (1); update AHProfit")
  end)

  it("refuses a damaged database without a format version", function()
    local DB = Boot({ characters = {} })
    assert_eq(DB.Get(), nil)
    assert_eq(DB.SetupError(), "AHProfitDB is damaged (no formatVersion); left untouched")
  end)

  it("migrates step by step on a copy", function()
    local DB = Boot(nil)
    local migrations = {
      [1] = function(db) db.renamed = db.old; db.old = nil end,
      [2] = function(db) db.added = true end,
    }
    local saved = { formatVersion = 1, old = { 1, 2 } }
    local migrated = DB.Migrate(saved, migrations, 3)
    assert_eq(migrated, { formatVersion = 3, renamed = { 1, 2 }, added = true })
    assert_eq(saved, { formatVersion = 1, old = { 1, 2 } })
  end)

  it("a failed migration leaves the original untouched and says why", function()
    local DB = Boot(nil)
    local migrations = { [1] = function(db) db.half = true; error("boom", 0) end }
    local saved = { formatVersion = 1, keep = "me" }
    local migrated, err = DB.Migrate(saved, migrations, 2)
    assert_eq(migrated, nil)
    assert_eq(err, "migration 1 -> 2 failed: boom")
    assert_eq(saved, { formatVersion = 1, keep = "me" })
  end)

  it("a missing migration step fails", function()
    local DB = Boot(nil)
    local migrated, err = DB.Migrate({ formatVersion = 1 }, {}, 2)
    assert_eq(migrated, nil)
    assert_eq(err, "no migration from AHProfitDB version 1")
  end)

  it("event ids count up within a second and restart each second per character", function()
    local DB = Boot(nil)
    assert_eq(DB.NewEventId("Amaya-Grommash", 1760000000000), "Amaya-Grommash:1760000000000:0")
    assert_eq(DB.NewEventId("Amaya-Grommash", 1760000000000), "Amaya-Grommash:1760000000000:1")
    assert_eq(DB.NewEventId("Brugan-Grommash", 1760000000000), "Brugan-Grommash:1760000000000:0")
    assert_eq(DB.NewEventId("Amaya-Grommash", 1760000001000), "Amaya-Grommash:1760000001000:0")
  end)

  it("event ids continue after the newest saved event after a /reload in the same second", function()
    Stubs.Reset()
    AHProfitDB = { formatVersion = 1, scans = {}, events = {
      { id = "Amaya-Grommash:1760000000000:0", type = "craft", char = "Amaya-Grommash", at = 1760000000000 },
      { id = "Amaya-Grommash:1760000000000:1", type = "craft", char = "Amaya-Grommash", at = 1760000000000 },
    } }
    Stubs.LoadAddon()
    Stubs.FireEvent("ADDON_LOADED", "AHProfit")
    assert_eq(AHProfit.DB.NewEventId("Amaya-Grommash", 1760000000000), "Amaya-Grommash:1760000000000:2")
    assert_eq(AHProfit.DB.NewEventId("Amaya-Grommash", 1760000001000), "Amaya-Grommash:1760000001000:0")
  end)

  it("AppendEvent gives the event an id and appends it", function()
    local DB = Boot(nil)
    local db = DB.Get()
    DB.AppendEvent(db, { type = "craft", char = "Brugan-Grommash", at = 1760003600000, spell = 2663, count = 3 })
    assert_eq(db.events[1].id, "Brugan-Grommash:1760003600000:0")
    assert_eq(#db.events, 1)
  end)
end)

describe("DB.Prune", function()
  local T = 1760000000000
  local HOUR = 3600000

  it("removes scans and events at or before ingestedUntil and keeps later ones", function()
    local DB = Boot(nil)
    local db = DB.NewDB()
    db.scans = { { id = "s1", at = T - 1 }, { id = "s2", at = T }, { id = "s3", at = T + 1 } }
    db.events = { { id = "e1", type = "craft", at = T }, { id = "e2", type = "mail_sent", at = T + 1000 } }
    local removed = DB.Prune(db, T, T + 2 * HOUR)
    assert_eq(removed, 3)
    assert_eq(db.scans, { { id = "s3", at = T + 1 } })
    assert_eq(db.events, { { id = "e2", type = "mail_sent", at = T + 1000 } })
  end)

  it("keeps unconfirmed attempts younger than 1 h even when ingested", function()
    local DB = Boot(nil)
    local db = DB.NewDB()
    db.events = {
      { id = "young", type = "purchase", at = T, confirmed = false },
      { id = "old", type = "purchase", at = T - HOUR, confirmed = false },
      { id = "done", type = "post", at = T, confirmed = true },
    }
    DB.Prune(db, T, T + HOUR - 1)
    assert_eq(db.events, { { id = "young", type = "purchase", at = T, confirmed = false } })
  end)

  it("drops junk rows that are not tables", function()
    local DB = Boot(nil)
    local db = DB.NewDB()
    db.events = { "junk", { id = "e", type = "craft", at = T + 1 } }
    DB.Prune(db, T, T)
    assert_eq(db.events, { { id = "e", type = "craft", at = T + 1 } })
  end)

  it("drops an unconfirmed attempt exactly 1 h old", function()
    local DB = Boot(nil)
    local db = DB.NewDB()
    db.events = { { id = "a", type = "post", at = T, confirmed = false } }
    DB.Prune(db, T, T + HOUR)
    assert_eq(db.events, {})
  end)
end)

describe("DB codecs", function()
  it("formats integers without exponent and floats with 14 digits", function()
    local DB = Boot(nil)
    assert_eq(DB.FormatNumber(1760000000000), "1760000000000")
    assert_eq(DB.FormatNumber(2147483648 * 28592 + 2589), "61400852466205")
    assert_eq(DB.FormatNumber(-5), "-5")
    assert_eq(DB.FormatNumber(0.25), "0.25")
  end)

  it("encodes rows and id sets", function()
    local DB = Boot(nil)
    assert_eq(DB.EncodeRows({ { 2840, 20, 0 }, { 6948, 1, 1 } }), "2840,20,0;6948,1,1")
    assert_eq(DB.EncodeRows({}), "")
    assert_eq(DB.EncodeIdSet({ [2663] = true, [2662] = true }), "2662;2663")
    assert_eq(DB.EncodeIdSet({}), nil)
    assert_eq(DB.DecodeIdSet("2662;2663"), { [2662] = true, [2663] = true })
    assert_eq(DB.DecodeIdSet(nil), {})
  end)
end)
