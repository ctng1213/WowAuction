-- Specs for addon/AHProfit/Core/Plan.lua: reading PortfolioPlan.lua and pruning at login (SPEC §3.5, §2.2).
-- tests/addon/generated/plan/ex3.lua is written by ahsync/export/plan_lua.py (tools/gen_lua_fixtures.py).

local Stubs = require("wow_stubs")

local EX3 = "tests/addon/generated/plan/ex3.lua"
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
  it("A4 at login, rows AHSync committed are pruned per character", function()
    LoginWithRows()
    assert_eq(Ids(AHProfitDB.scans), { "s2", "s3" })      -- Amaya's scan at T goes; Brugan was never ingested
    assert_eq(Ids(AHProfitDB.events), { "e2" })
  end)

  it("EX3 decodes the open actions and the rules, clears included", function()
    local Plan = LoginWithRows()
    assert_eq(Plan.Problem(), nil)
    local actions = Plan.Actions(A)
    assert_eq(actions[1], { id = "m:1:0", kind = "post", key = 2589, qty = 20, price = 55, at = T + 5 * HOUR, net = 1046,
                            windowEnd = T + 10 * HOUR, status = "pending", chance = 93, peak = T + 13 * HOUR })
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

  it("without a plan the addon carries on: nothing pruned, no problem shown", function()
    Stubs.Reset()
    _G.AHProfitPlan = nil
    Stubs.Login()
    assert_eq({ AHProfit.Plan.Problem(), #AHProfit.Plan.Actions(A) }, { nil, 0 })
  end)
end)
