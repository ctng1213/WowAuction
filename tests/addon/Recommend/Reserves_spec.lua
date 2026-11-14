-- Specs for addon/AHProfit/Recommend/Reserves.lua: items you keep for yourself (A25).

local Stubs = require("wow_stubs")

--- Boot the addon and return the module.
---@return table
local function Boot()
  Stubs.Reset()
  Stubs.Login()
  return AHProfit.Reserves
end

describe("Reserves", function()
  it("A25 stores reserves per character and clears them", function()
    local Reserves = Boot()
    local db = { reserves = {} }
    Reserves.Set(db, "A", 159, 10)
    Reserves.Set(db, "A", 118, Reserves.ALL)
    Reserves.Set(db, "B", 159, 2)
    assert_eq({ db.reserves.A, db.reserves.B }, { "118,-1;159,10", "159,2" })
    assert_eq(Reserves.Get(db, "A"), { [118] = -1, [159] = 10 })
    Reserves.Set(db, "A", 159, nil)
    Reserves.Set(db, "A", 118, nil)
    assert_eq({ db.reserves.A, Reserves.Get(db, "A"), Reserves.Get({}, "A") }, { nil, {}, {} })
  end)

  it("A25 keeps reserved units out of what can be sold, and trims AHSync's sales to the rest", function()
    local Reserves = Boot()
    local reserves = { [159] = 10, [118] = Reserves.ALL }
    assert_eq(Reserves.Apply({ [159] = 13, [118] = 2, [2589] = 20, [4306] = 5 }, reserves), { [159] = 3, [2589] = 20, [4306] = 5 })
    local actions = { { kind = "post", key = 159, qty = 13, net = 130 }, { kind = "vendor", key = 118, qty = 2, net = 20 },
                      { kind = "buy", key = 118, qty = 5, net = 0 }, { kind = "post", key = 2589, qty = 20, net = 900 } }
    local trimmed = Reserves.Trim(actions, reserves, { [159] = 13, [118] = 2, [2589] = 20 })
    assert_eq({ #trimmed, trimmed[1].qty, trimmed[1].net, trimmed[2].kind, trimmed[3].qty, actions[1].qty },
              { 3, 3, 30, "buy", 20, 13 })
    assert_eq({ Reserves.Note(10), Reserves.Note(Reserves.ALL), Reserves.Note(nil) },
              { "Keeping 10 for yourself", "Kept for yourself", nil })
    assert_eq({ Reserves.Free(5, 10), Reserves.Free(5, nil) }, { 0, 5 })
  end)
  it("A60 at login the plan's newer reserves win per item; setting one in game records its time", function()
    local T, HOUR, A = 1760000000000, 3600000, "Amaya-Grommash"
    local LINEN, SILK, WOOL = 2589, 4306, 2592
    Stubs.Reset()
    Stubs.Login()                                                        -- creates the saved table
    AHProfitDB.reserves[A] = LINEN .. ",10;" .. SILK .. ",3"
    AHProfitDB.reserveTimes[A] = LINEN .. "," .. T .. ";" .. SILK .. "," .. (T + 2 * HOUR)
    Stubs.Reset({ keepSaved = true })
    _G.AHProfitPlan = { schemaVersion = 1, exportVersion = 7, generatedAt = T, checksum = "0:0", ingestedUntil = {}, actions = {}, rules = {},
      reserves = { [A] = LINEN .. ",5," .. (T + HOUR) .. ";" .. SILK .. ",0," .. (T + HOUR) .. ";" .. WOOL .. ",-1," .. (T + HOUR) } }
    Stubs.Login()
    local Reserves = AHProfit.Reserves
    assert_eq(Reserves.Get(AHProfitDB, A), { [LINEN] = 5, [SILK] = 3, [WOOL] = Reserves.ALL })
    assert_eq(Reserves.Times(AHProfitDB, A), { [LINEN] = T + HOUR, [SILK] = T + 2 * HOUR, [WOOL] = T + HOUR })
    Reserves.Set(AHProfitDB, A, SILK, nil)                               -- cleared in game: its time is recorded
    assert_eq({ Reserves.Get(AHProfitDB, A)[SILK], Reserves.Times(AHProfitDB, A)[SILK] }, { nil, AHProfit.Adapter.NowMs() })
    Reserves.Set(AHProfitDB, A, SILK, 2, T + 3 * HOUR)                 -- kept again in game, later than the plan
    assert_eq(Reserves.Merge(AHProfitDB, A, AHProfit.Plan.Reserves(A)), 0)   -- nothing in the plan is newer now
    _G.AHProfitPlan = nil
  end)
end)
