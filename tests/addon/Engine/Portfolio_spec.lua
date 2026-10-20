-- Specs for addon/AHProfit/Engine/Portfolio.lua: the shared selection scenarios
-- (tests/scenarios/portfolio.json: PF5, PF11, PF15, PF24, PF27).

local Stubs = require("wow_stubs")

--- A JSON null from the generated fixtures as nil.
---@param v any
---@return any
local function Value(v)
  if v == JSON_NULL then return nil end
  return v
end

--- Boot the engine module the scenarios use.
---@return table  AHProfit.Engine.Portfolio
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "Engine/Portfolio.lua" })
  return AHProfit.Engine.Portfolio
end

--- A scenario's picks with JSON nulls as nil.
---@param picks table  From the fixture
---@return table
local function Picks(picks)
  local out = {}
  for i, p in ipairs(picks) do
    out[i] = { cand_id = p.cand_id, opp = p.opp, batch = p.batch, char_id = p.char_id, lst = p.lst, gold = p.gold,
               adjusted = p.adjusted, net = p.net, name = p.name, reason = Value(p.reason), claims = p.claims }
  end
  return out
end

--- Run one static_reason scenario.
---@param Portfolio table
---@param a table  The scenario's args
---@return string|nil
local function Static(Portfolio, a)
  return Portfolio.StaticReason({ profession = Value(a.profession), skill = a.skill, actorSkill = Value(a.actor_skill),
    untrustedWeeks = Value(a.untrusted_weeks), priceAgeMs = Value(a.price_age_ms), soldPerDay = Value(a.sold_per_day),
    plannedUnits = a.planned_units, missingUnits = Value(a.missing_units), missingName = Value(a.missing_name),
    windowCloses = Value(a.window_closes), volumeUnknown = Value(a.volume_unknown), noListing = Value(a.no_listing) },
    a.horizon_days)
end

describe("Portfolio shared scenarios", function()
  for _, s in ipairs(dofile("tests/addon/generated/portfolio.lua")) do
    it(s.id .. " " .. s.name, function()
      local Portfolio = Boot()
      if s.call == "static_reason" then
        assert_eq(Static(Portfolio, s.args), Value(s.expect), s.why)
        return
      end
      local picks = Picks(s.args.picks)
      local result = Portfolio.Select(picks, s.args.resources, s.args.budget)
      Portfolio.Validate(picks, s.args.resources, s.args.budget, result)
      assert_eq(result.funded, s.expect.funded, s.why)
      assert_eq(result.reasons, s.expect.reasons, s.why)
      assert_eq(result.capacityLeft, s.expect.capacity_left, s.why)
    end)
  end
end)

describe("Portfolio validation", function()
  it("refuses a later batch funded without its first, and an allocation over the capacity", function()
    local Portfolio = Boot()
    local picks = { { cand_id = "a:1", opp = "a", batch = 1, char_id = "A-R", lst = "today", gold = 10, adjusted = 1, net = 1, name = "A", claims = {} },
                    { cand_id = "a:2", opp = "a", batch = 2, char_id = "A-R", lst = "today", gold = 10, adjusted = 1, net = 1, name = "A", claims = {} } }
    local budget = { capacity = 15, spendable = { { "A-R", 100 } }, today_limit = 20, week_limit = 20 }
    local result = Portfolio.Select(picks, {}, budget)
    assert_eq(result.funded, { "a:1" })
    assert_eq(pcall(Portfolio.Validate, picks, {}, budget, { funded = { "a:2" }, reasons = { ["a:1"] = "x" },
      capacityLeft = 5, adjusted = 1, net = 1, gold = 10 }), false)
    assert_eq(pcall(Portfolio.Validate, picks, {}, budget, { funded = { "a:1", "a:2" }, reasons = {},
      capacityLeft = -5, adjusted = 2, net = 2, gold = 20 }), false)
    assert_eq(pcall(Portfolio.Validate, picks, {}, budget, { funded = { "a:1" }, reasons = { ["a:2"] = "x" },
      capacityLeft = 5, adjusted = 9, net = 1, gold = 10 }), false)
  end)

  it("formats money as the dashboard does", function()
    local Portfolio = Boot()
    assert_eq({ Portfolio.Money(16080), Portfolio.Money(0), Portfolio.Money(4500), Portfolio.Money(-120) },
      { "1g 60s 80c", "0c", "45s", "−1s 20c" })
  end)
end)
