-- Specs for addon/AHProfit/UI/PortfolioView.lua: the Portfolio tab's view-model (SPEC §4.3; A77, A78). Pure data;
-- the frames come with M13.

local Stubs = require("wow_stubs")
local World = require("portfolio_world")

--- The tab's data for Amaya now.
---@param nowMs number|nil
---@return table  PortfolioView.Build's result
local function Tab(nowMs)
  local Adapt = AHProfit.Adapt
  return AHProfit.PortfolioView.Build(Adapt.Current(), Adapt.Snapshot(), AHProfit.Plan.Positions(World.AMAYA),
    { nowMs = nowMs or World.NOW, offsetMs = 0, nameOf = Adapt.NameOf, problem = Adapt.Problem() })
end

--- A section's rows as "id:status".
---@param section table
---@return string[]
local function Statuses(section)
  local out = {}
  for i, r in ipairs(section.rows) do out[i] = r.id .. ":" .. r.status end
  return out
end

describe("PortfolioView", function()
  it("A77 Linen at 50c no longer pays: its row leaves Today and the freed gold stays unallocated", function()
    World.Boot(Stubs)
    local before = Tab()
    assert_eq({ before.footer.funded, before.today.foot }, { "funded +92s 62c · cost 2g 52s · 37% on cash",
      "funded: cost 20s 60c · profit +12s 8c · 59% on cash" }, "A79's numbers before the scan")
    World.Scan(Stubs, { [World.LINEN] = World.Book({ { 50, 20 } }) })
    Stubs.Advance(1)
    local tab = Tab()
    local c = World.Candidates()
    assert_eq(Statuses(tab.today), { c[1].id .. ":Funded", c[2].id .. ":Funded",
      c[5].id .. ":Insufficient liquidity", c[4].id .. ":Required profession unavailable: Enchanting 150" },
      "Linen is gone; the bracers need Enchanting 150 and the elixir has no liquidity, so neither takes its 7s 60c")
    assert_eq(tab.footer.funded:sub(1, #"funded +90s 59c · cost 2g 44s 40c"), "funded +90s 59c · cost 2g 44s 40c")
    assert_eq(tab.footer.capacity, "capacity 6g 49s 40c · 32s unallocated", "24s 40c + Linen's 7s 60c")
    assert_eq({ tab.today.rows[3].muted, tab.today.rows[1].strategy, tab.today.rows[1].pct }, { true, "Flip", "78%" })
  end)

  it("A78 the top line is the snapshot's, with its time; Brugan's rows are neither shown nor recomputed", function()
    World.Boot(Stubs)
    local tab = Tab()
    assert_eq(tab.top.text, "portfolio 35g 58s · 30% risk · capacity 6g 49s 40c · 24s 40c unallocated · snapshot 10:38")
    assert_eq({ tab.top.time, tab.top.amber, tab.top.hover }, { "10:38", false, AHProfit.PortfolioView.HOVER })
    for _, section in ipairs({ tab.today, tab.week }) do
      for _, r in ipairs(section.rows) do assert_eq(r.id:find(World.BRUGAN, 1, true), nil) end
    end
    assert_eq(Tab(World.NOW + 30 * World.HOUR).top.amber, true, "30 h old: amber")
    assert_eq(Tab(World.NOW + 24 * World.HOUR).top.amber, false, "24 h is not yet stale")
  end)

  it("shows the week's rows with their wait and premium, positions with their totals, and % per section", function()
    World.Boot(Stubs)
    local tab = Tab()
    local thick = tab.week.rows[1]
    assert_eq({ thick.adjusted, thick.wait, thick.premium, thick.note }, { "+41s 75c", "3.35 d", "31.75%", "buy now · sell Thu 19:00" })
    assert_eq(tab.week.foot, "funded: cost 2g 31s 40c · profit +80s 54c · 35% on cash")
    assert_eq({ #tab.positions.rows, tab.positions.rows[1].state, tab.positions.foot },
      { 1, "Holding", "committed 0c · est. +4s 72c" }, "found Wool Cloth: no cost, so no % on committed capital")
    local PV = AHProfit.PortfolioView
    assert_eq({ PV.ProfitPct(6306, 5100), PV.ProfitPct(134, 0), PV.PoolPct({ { net = 134, cash = 0 } }) }, { 124, nil, nil })
  end)
end)
