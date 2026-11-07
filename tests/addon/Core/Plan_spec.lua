-- Specs for addon/AHProfit/Core/Plan.lua: reading PortfolioPlan.lua and pruning at login (SPEC §3.5, §2.2).
-- tests/addon/generated/plan/ex3.lua is written by ahsync/export/plan_lua.py (tools/gen_lua_fixtures.py).

local Stubs = require("wow_stubs")

local EX3 = "tests/addon/generated/plan/ex3.lua"
local EX18 = "tests/addon/generated/plan/ex18.lua"     -- the mockup world's portfolio (tests/support/mockup_export.py)
local T = 1760000000000            -- the stubs' server time; EX3 has Amaya ingested until T + 2 h
local HOUR = 3600000
local A, B = "Amaya-Grommash", "Brugan-Grommash"

--- Log in once to create SavedVariables, add scans and events, then log in again with a plan.
---@param change function|nil  Edits AHProfitPlan before the second login
---@return table               AHProfit.Plan
local function LoginWithRows(change)
  Stubs.Reset()
  _G.AHProfitPlan = nil
  Stubs.Login()
  AHProfitDB.scans = { { id = "s1", char = A, at = T, rows = "" }, { id = "s2", char = A, at = T + 3 * HOUR, rows = "" },
                       { id = "s3", char = B, at = T, rows = "" } }
  AHProfitDB.events = { { id = "e1", char = A, type = "craft", at = T + HOUR, confirmed = true },
                        { id = "e2", char = B, type = "craft", at = T, confirmed = true } }
  Stubs.Reset({ keepSaved = true })
  _G.AHProfitPlan = nil
  dofile(EX3)
  if change then change(_G.AHProfitPlan) end
  Stubs.Login()
  return AHProfit.Plan
end

--- The ids left in a list of rows.
---@param rows table[]  Scans or events
---@return string[]
local function Ids(rows)
  local out = {}
  for i, row in ipairs(rows) do out[i] = row.id end
  return out
end

describe("Plan", function()
  it("FM18 your events after the plan mark its actions done locally, and the plan is provisional", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    Stubs.Login()
    AHProfitDB.recipes = { Blacksmithing = { [2663] = { product = 6217 } } }
    AHProfitDB.events = {
      { id = "old", char = A, type = "purchase", at = T, key = 2589, qty = 20, confirmed = true },      -- AHSync has it
      { id = "b", char = A, type = "purchase", at = T + HOUR, key = 2589, qty = 20, confirmed = true },
      { id = "try", char = A, type = "purchase", at = T + HOUR, key = 2840, qty = 6, confirmed = false }, -- an attempt
      { id = "p", char = A, type = "post", at = T + HOUR, key = 2840, stack = 5, confirmed = true },
      { id = "c", char = A, type = "craft", at = T + HOUR, spell = 2663, count = 1 },
      { id = "other", char = B, type = "purchase", at = T + HOUR, key = 2840, qty = 6, confirmed = true } }
    local actions = { { kind = "buy", key = 2589, qty = 20 }, { kind = "buy", key = 2840, qty = 6 },
                      { kind = "post", key = 2840, qty = 10 }, { kind = "craft", key = 6217, qty = 1 } }
    local left, applied = AHProfit.Plan.DoneLocally(A, actions, T)
    local out = {}
    for i, a in ipairs(left) do out[i] = a.kind .. " " .. a.key .. " " .. a.qty end
    assert_eq(out, { "buy 2840 6", "post 2840 5" })
    assert_eq(applied, 3)
    assert_eq(AHProfit.ViewModel.Provisional(applied), "provisional: 3 done in game since AHSync's plan · ")
    assert_eq(AHProfit.ViewModel.Provisional(0), "")
  end)

  it("A4 at login, rows AHSync committed are pruned per character", function()
    LoginWithRows()
    assert_eq(Ids(AHProfitDB.scans), { "s2", "s3" })      -- Amaya's scan at T goes; Brugan was never ingested
    assert_eq(Ids(AHProfitDB.events), { "e2" })
  end)

  it("EX8 reads your sell records and the items flagged not selling; a file without them has none", function()
    local Plan = LoginWithRows()
    AHProfitPlan.records = { [2318] = "1;Your record (30 d): 0 of 3 sold", [2589] = "0;Your record (30 d): 2 of 2 sold",
                             [9] = "junk" }
    assert_eq({ Plan.Record(2318), Plan.Record(2589).notSelling, Plan.Record(9), Plan.Record(7), Plan.NotSelling() },
              { { notSelling = true, line = "Your record (30 d): 0 of 3 sold" }, false, nil, nil, { 2318 } })
    AHProfitPlan.records = nil
    assert_eq(Plan.NotSelling(), {})
  end)

  it("EX7 a vendor buy decodes as vendor; an AH buy and an older 12-field string do not", function()
    local Plan = LoginWithRows()
    assert_eq({ Plan.DecodeAction("w;buy;4470;7;38;1;0;;;pending;;;v").vendor, Plan.DecodeAction("l;buy;2589;20;45;1;0;;;pending;;;").vendor,
                Plan.DecodeAction("l;buy;2589;20;45;1;0;;;pending;;").vendor }, { true, false, false })
  end)

  it("EX16 a mail decodes with its recipient; any other action, and an older 14-field string, has none", function()
    local Plan = LoginWithRows()
    assert_eq({ Plan.DecodeAction("m;mail;2840;6;;1;0;c;;pending;;;;;Amaya-Grommash").to,
                Plan.DecodeAction("p;post;2589;20;55;1;0;;;pending;93;;;24;").to,
                Plan.DecodeAction("p;post;2589;20;55;1;0;;;pending;93;;;24").to }, { "Amaya-Grommash", nil, nil })
  end)

  it("EX3 decodes the open actions and the rules, clears included", function()
    local Plan = LoginWithRows()
    assert_eq(Plan.Problem(), nil)
    local actions = Plan.Actions(A)
    assert_eq(actions[1], { id = "m:1:0", kind = "post", key = 2589, qty = 20, price = 55, at = T + 5 * HOUR, net = 1046,
                            windowEnd = T + 10 * HOUR, status = "pending", chance = 93, peak = T + 13 * HOUR, vendor = false })
    assert_eq({ actions[2].kind, actions[2].price, actions[2].parent, actions[2].status }, { "hold", nil, "m:1:0", "late" })
    assert_eq(Plan.Actions(B), {})
    assert_eq({ Plan.Rule(2589) }, { "no_buy", T })
    assert_eq({ Plan.Rule(4306) }, { "none", T + HOUR })
    assert_eq({ Plan.Rule(1) }, {})
  end)

  it("EX4 a plan of another version, or a damaged one, prunes nothing and shows no rows", function()
    local Plan = LoginWithRows(function(plan) plan.schemaVersion = 2 end)
    assert_eq(#AHProfitDB.scans, 3)
    assert_eq(Plan.Actions(A), {})
    assert_eq(Plan.Problem(), "AHSync's plan and the addon versions differ; plan rows are hidden. Update AHProfit or AHSync.")
    Plan = LoginWithRows(function(plan) plan.actions[A][1] = "tampered" end)
    assert_eq({ #AHProfitDB.scans, #AHProfitDB.events }, { 3, 2 })
    assert_eq(Plan.Problem(), "AHSync's plan file is damaged; plan rows are hidden until AHSync writes it again.")
    assert_eq({ Plan.Rule(2589) }, {})
  end)

  it("FM34 a plan from an older AHSync (export version 0 or none) prunes nothing, shows no rows and says restart AHSync", function()
    for _, version in ipairs({ 0, false, 3 }) do
      local Plan = LoginWithRows(function(plan) plan.exportVersion = version or nil end)
      assert_eq({ #AHProfitDB.scans, #AHProfitDB.events }, { 3, 2 })
      assert_eq(Plan.Actions(A), {})
      assert_eq({ Plan.Rule(2589) }, {})
      assert_eq(Plan.Problem(), "AHSync is older than this addon: restart AHSync, then /reload")
    end
    local Plan = LoginWithRows(function(plan) plan.exportVersion, plan.schemaVersion = 0, 2 end)   -- schema first
    assert_eq(Plan.Problem(), "AHSync's plan and the addon versions differ; plan rows are hidden. Update AHProfit or AHSync.")
    Plan = LoginWithRows(function(plan) plan.exportVersion, plan.actions[A][1] = 0, "tampered" end)  -- checksum last
    assert_eq(Plan.Problem(), "AHSync is older than this addon: restart AHSync, then /reload")
  end)

  it("FM35 a plan from a newer AHSync says to deploy the addon", function()
    local Plan = LoginWithRows(function(plan) plan.exportVersion = 6 end)
    assert_eq({ Plan.Actions(A), #AHProfitDB.scans }, { {}, 3 })
    assert_eq(Plan.Problem(), "This addon is older than AHSync's files: run tools/deploy.py, then /reload")
  end)

  it("PO8 a sale or post after the plan file shows from the addon's own events, never the older file's Holding", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    Stubs.Login()
    local MON10 = 1728259200000 + 10 * HOUR + 38 * 60000            -- the EX18 file's basedOnUntil for Brugan
    AHProfitDB.events = {
      { id = "x1", char = B, type = "auction_mail", outcome = "sold", key = 2840, qty = 6, at = MON10 + 1 },
      { id = "x2", char = B, type = "post", key = 2840, stack = 6, at = MON10 + 2, confirmed = true },
      { id = "x3", char = B, type = "post", key = 2457, stack = 2, at = MON10 + 3, confirmed = false },
      { id = "x4", char = B, type = "post", key = 2457, stack = 2, at = MON10 - 1, confirmed = true } }
    Stubs.Reset({ keepSaved = true })
    _G.AHProfitPlan = nil
    dofile(EX18)
    Stubs.Login()
    local coif, bar, elixir = unpack(AHProfit.Plan.LivePositions(B))
    assert_eq({ bar.state, bar.provisional, elixir.state, elixir.provisional, coif.state },
              { "Partially sold", true, "Ready to sell", nil, "Listed" }, "an attempt or an older post changes nothing")
    assert_eq(AHProfit.Plan.LocalPositions(B), {}, "no plan action for these: nothing local")
  end)

  it("EX18 decodes the portfolio snapshot, each character's gold, reservation and positions", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    dofile(EX18)
    Stubs.Login()
    local Plan = AHProfit.Plan
    assert_eq(Plan.Problem(), nil)
    local p = Plan.Portfolio("forever.pvp.horde.us")
    assert_eq({ p.riskTolerance1000, p.riskTolerance, p.premiumBase, p.premiumPerDay, p.immediateHours, p.todayLimit,
                p.weekLimit, p.horizonDays }, { 300, 0.3, 0.15, 0.05, 8, 20, 20, 7 })
    assert_eq({ p.capitalBase, p.committed, p.capacity, p.allocated, p.configVersion > 0 },
              { 355800, 41800, 64940, 62500, true })
    assert_eq(Plan.Portfolio("forever.pve.horde.us"), nil)
    local MON10 = 1728259200000 + 10 * HOUR
    assert_eq({ Plan.Gold(A) }, { 210000, MON10 })
    assert_eq({ Plan.Gold(B) }, { 104000, MON10 })
    assert_eq({ Plan.Gold("Zed-Grommash") }, {})
    assert_eq({ Plan.Reserved(A), Plan.Reserved(B), Plan.Reserved("Zed-Grommash") }, { 25200, 37300, 0 })
    local brugan = Plan.Positions(B)
    assert_eq(#brugan, 4)
    assert_eq(brugan[1], { id = "Brugan-Grommash:1728189480000:1", key = 7920, state = "Listed", units = 1,
                           unitsLeft = 1, costLeft = 30000, netNow = 4500, nextAction = "undercut: relist at 4g 49s 99c" })
    assert_eq({ brugan[4].key, brugan[4].state, brugan[4].target, brugan[4].nextAction },
              { 2772, "Processing", 1728586800000, "smelt today · post Thu 11:00 for the 19:00 peak" })
    local leather = Plan.Positions(A)[2]
    assert_eq({ leather.key, leather.state, leather.costLeft, leather.netNow, leather.target, leather.nextAction },
              { 2319, "Needs reconciliation", 4000, nil, nil, "check bags and mail: 14 of 20 found" })
    assert_eq(Plan.Positions("Zed-Grommash"), {})
    Stubs.Reset()
    _G.AHProfitPlan = nil
    dofile(EX18)
    AHProfitPlan.reserved[A] = 252000                                 -- the checksum counts the reservation's digits
    Stubs.Login()
    assert_eq({ AHProfit.Plan.Portfolio("forever.pvp.horde.us"), AHProfit.Plan.Reserved(A), AHProfit.Plan.Positions(B),
                AHProfit.Plan.Gold(A) }, { nil, 0, {}, nil })
  end)

  it("without a plan the addon carries on: nothing pruned, no problem shown", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    Stubs.Login()
    assert_eq({ AHProfit.Plan.Problem(), #AHProfit.Plan.Actions(A) }, { nil, 0 })
  end)
end)
