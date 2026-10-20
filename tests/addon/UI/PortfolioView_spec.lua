-- Specs for addon/AHProfit/UI/PortfolioView.lua: the Portfolio tab's view-model (SPEC §4.3; A77-A79, A84, UI19).
-- Pure data; the frames are checked by tests/visual/MainWindow_spec.lua (UI16, UI19).

local Stubs = require("wow_stubs")
local World = require("portfolio_world")

--- The tab's data for the logged-in character now.
---@param nowMs number|nil
---@param liveGold number|nil  Copper; default 21 g
---@return table  PortfolioView.Build's result
local function Tab(nowMs, liveGold)
  local Adapt = AHProfit.Adapt
  local info = AHProfit.Adapter.PlayerInfo()
  local charId = info.name .. "-" .. info.realm
  return AHProfit.PortfolioView.Build(Adapt.Current(), Adapt.Snapshot(), AHProfit.Plan.Positions(charId),
    { nowMs = nowMs or World.NOW, offsetMs = 0, nameOf = Adapt.NameOf, lookOf = AHProfit.WindowLines.LookOf,
      itemOf = AHProfit.Market.Item, totalGold = 314000, liveGold = liveGold or 210000, charId = charId,
      skills = World.Skills(), problem = Adapt.Problem() })
end

--- The tab's list lines.
---@param tab table
---@param minChance number|nil
---@return table  PortfolioView.Lines' result
local function Lines(tab, minChance)
  local VM = AHProfit.ViewModel
  local decisions = VM.Decisions(AHProfit.Plan.NotSelling(), AHProfit.Plan.Record, AHProfit.WindowLines.LookOf, {})
  return AHProfit.PortfolioView.Lines(tab, { minChance = minChance or 50, recordOf = AHProfit.Plan.Record,
    decisions = decisions, snapshot = AHProfit.Adapt.Snapshot(), charName = "Amaya" })
end

--- A section's rows as "id:status".
---@param section table
---@return string[]
local function Statuses(section)
  local out = {}
  for i, r in ipairs(section.rows) do out[i] = r.id .. ":" .. r.status end
  return out
end

--- Lines as short texts: "section Today", "row DE Scouting Gloves… +6s 25c Funded", "note …", "foot …".
---@param lines table[]
---@return string[]
local function Texts(lines)
  local out = {}
  for i, l in ipairs(lines) do
    if l.lineKind == "row" and l.set == "pfPositions" then
      out[i] = "row " .. l.item .. " " .. l.qty .. " " .. l.state .. " " .. l.net
    elseif l.lineKind == "row" and l.set == "pfWeek" then
      out[i] = "row " .. l.action .. " " .. l.item .. " " .. l.adjustedArrow .. " (" .. l.wait .. " · " .. l.premium .. ") " .. l.status
    elseif l.lineKind == "row" then
      out[i] = "row " .. l.action .. " " .. l.item .. " " .. l.net .. " " .. l.status
    else
      out[i] = l.lineKind .. " " .. (l.label or l.title or l.text or l.set)
    end
  end
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
      c[4].id .. ":Required profession unavailable: Enchanting 150", c[5].id .. ":Insufficient liquidity" },
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

  it("A79 Amaya's tab in the mockup world: positions, Today funded then unfunded, Next 7 days, totals and footer", function()
    World.Name(Stubs)
    World.Boot(Stubs, World.Mockup())
    local tab = Tab()
    local out = Lines(tab, 0)
    assert_eq(Texts(out.lines), {
      "section Positions", "colhead pfPositions",
      "row Mageweave Cloth ×10 of 25 Partially sold +6s", "row Wool Cloth ×20 Holding +4s 72c",
      "row Medium Leather ×20 Needs reconciliation —",
      "foot committed 70s · est. +10s 72c · +15% on committed capital",
      "section Today", "colhead pfToday",
      "row DE Scouting Gloves of the Monkey +6s 25c Funded", "note disenchant now · sell today 18:00",
      "row DE Embossed Leather Gloves +3s 80c Funded", "note disenchant now · sell today 18:00",
      "row BUY Linen Cloth +2s 3c Funded", "note buy now · sell today 18:00",
      "row DE Grunt's Bracers of Defense +6s Required profession unavailable: Enchanting 150",
      "row BUY Elixir of Fortitude +9s Insufficient liquidity: 3/day, 40 planned",
      "foot funded: cost 20s 60c · profit +12s 8c · 59% on cash",
      "section Next 7 days", "colhead pfWeek",
      "row BUY Thick Leather → +41s 75c (3.35 d · 31.75%) Funded",
      "row CRAFT Runed Copper Rod → +11s 12c (6.31 d · 46.55%) Funded",
      "row BUY Silk Cloth → +5s 15c (5.35 d · 41.75%) Funded",
      "row CRAFT Bolt of Silk Cloth → +1s 32c (6.35 d · 46.75%) Funded",
      "row BUY Greater Magic Essence → +8s 18c (6.35 d · 46.75%) Forecast too uncertain (1 week of data)",
      "foot funded: cost 2g 31s 40c · profit +80s 54c · 35% on cash" })
    assert_eq({ out.footer, tab.footer.capacity }, { "funded +92s 62c · cost 2g 52s · 37% on cash",
                                                     "capacity 6g 49s 40c · 24s 40c unallocated" })
    local bracers, elixir = out.lines[15], out.lines[16]
    assert_eq({ bracers.statusKind, elixir.statusKind, bracers.muted, bracers.tall, out.lines[9].tall },
              { "neg", "amber", true, true, false }, "a profession is red, liquidity amber; long reasons wrap")
    assert_eq({ bracers.detail, elixir.detail }, { "Amaya has Enchanting 142",
      "the market sells about 3 a day; 40 would take about 13 days" })
    assert_eq(out.lines[24].detail, "its Sunday peak has been seen once; a forecast needs 3 weeks")
    assert_eq({ out.lines[9].each, out.lines[9].total, out.lines[9].chance, out.lines[9].risk },
              { "8s", "8s", "90%", "0.3%" }, "800c of 314000c")
    World.Unname(Stubs)
  end)

  it("A84 record lines, Needs a decision after Today's totals, and Min chance as a view filter only", function()
    World.Name(Stubs)
    local opts = World.Mockup()
    opts.candidates[5].chance1000 = 350                               -- the elixir sells 35% of the time
    World.Boot(Stubs, opts)
    AHProfitPlan.records = { [4234] = "1;Your record (30 d): 0 of 3 sold · expired 3× while cheapest",
                             [World.LINEN] = "0;Your record (30 d): 2 of 2 sold" }
    local out = Lines(Tab(), 50)
    local texts = Texts(out.lines)
    assert_eq({ texts[13], texts[14], texts[15] }, { "row BUY Linen Cloth +2s 3c Funded", "note buy now · sell today 18:00",
                                                     "note Your record (30 d): 2 of 2 sold" })
    assert_eq({ texts[16], texts[17] }, { "row DE Grunt's Bracers of Defense +6s Required profession unavailable: Enchanting 150",
                                          "foot funded: cost 20s 60c · profit +12s 8c · 59% on cash · 1 hidden by Min chance" })
    assert_eq({ texts[18], texts[19], texts[20], texts[21] }, { "section Needs a decision",
      "row DECIDE Heavy Leather — NOT SELLING", "note Your record (30 d): 0 of 3 sold · expired 3× while cheapest",
      "note right-click: Blacklist or Keep" })
    assert_eq(out.footer, "funded +92s 62c · cost 2g 52s · 37% on cash · 1 hidden (chance < 50%)")
    opts.candidates[2].chance1000 = 400                               -- now a funded row is below 50% too
    World.Boot(Stubs, opts)
    local tab = Tab()
    out = Lines(tab, 50)
    assert_eq({ out.footer, tab.footer.capacity, #AHProfit.Adapt.Current().funded },
              { "funded +92s 62c · cost 2g 52s · 37% on cash · 2 hidden (chance < 50%)",
                "capacity 6g 49s 40c · 24s 40c unallocated", 7 }, "hidden, still funded and counted")
    World.Unname(Stubs)
  end)

  it("UI19 risk budget exhausted: the empty block replaces Today and Next 7 days; positions stay", function()
    World.Name(Stubs)
    local opts = World.Mockup()
    opts.snapshot = World.NOW .. ";300;150;50;8;20;20;7;1;355800;108800;0;0"
    opts.money = 143000
    World.Boot(Stubs, opts)
    local tab = Tab(nil, 143000)
    local out = Lines(tab)
    assert_eq(tab.top.text, "Amaya 14g 30s · portfolio 35g 58s · 30% risk · capacity 0c · snapshot 10:38")
    assert_eq({ tab.empty.title, tab.empty.text }, { "Nothing funded: risk budget exhausted",
      "10g 88s committed of a 10g 67s 40c limit. New rows come back as positions sell." })
    assert_eq({ tab.footer.funded, tab.footer.capacity }, { "funded 0c", "3 positions" })
    local texts = Texts(out.lines)
    assert_eq({ #texts, texts[1], texts[6], texts[7] },
              { 7, "section Positions", "foot committed 70s · est. +10s 72c · +15% on committed capital",
                "empty Nothing funded: risk budget exhausted" })
    local PV = AHProfit.PortfolioView
    assert_eq(PV.EmptyBlock({ funded = {}, excluded = {} }, { committed = 0, capitalBase = 1000, riskTolerance1000 = 300 },
                            { liveGold = 0 }).title, "Nothing funded: no gold in hand")
    assert_eq(PV.EmptyBlock({ funded = {}, excluded = {} }, { committed = 0, capitalBase = 1000, riskTolerance1000 = 300 },
                            { liveGold = 5, dataStale = true }).title, "Nothing funded: market data too old")
    assert_eq(PV.EmptyBlock({ funded = {}, excluded = {} }, { committed = 0, capitalBase = 1000, riskTolerance1000 = 300 },
                            { liveGold = 5 }).text, PV.EMPTY_TEXTS["nothing pays"])
    assert_eq(PV.EmptyBlock({ funded = {}, excluded = { {} } }, { committed = 0, capitalBase = 1000, riskTolerance1000 = 300 },
                            { liveGold = 5 }).title, "Nothing funded: every candidate is excluded")
    World.Unname(Stubs)
  end)

  it("UI19 on Brugan, an excluded row's tooltip: its reason and detail, the risk limit and what was funded before it", function()
    World.Name(Stubs)
    local c = World.Candidates()
    c[#c + 1] = { id = "craft:3385:" .. World.BRUGAN .. ":-/-/now:1", strategy = "craft", char = World.BRUGAN, key = 3385,
                  qty = 10, gold = 4500, net = 80, sale = World.TONIGHT }
    World.Boot(Stubs, { player = "Brugan", candidates = c, money = 104000, bags = { { World.ROD, 5 } },
                        reserved = { [World.AMAYA] = 32400, [World.BRUGAN] = 37300 } })
    local tab = Tab(nil, 104000)
    local potion = nil
    for _, r in ipairs(tab.today.rows) do if r.key == 3385 then potion = r end end
    assert_eq(potion.status, "Exceeds risk tolerance (needs 45s, 24s 40c left)")
    local tip = AHProfit.PortfolioView.ExcludedTooltip({ potion }, AHProfit.Adapt.Snapshot(), "Brugan", "today")
    assert_eq(tip.title, "Not funded today (Brugan)")
    assert_eq(tip.lines, {
      { text = "Lesser Mana Potion · CRAFT ×10", colour = "title" },
      { text = "Exceeds risk tolerance: needs 45s, 24s 40c left", colour = "amber" },
      { text = "its turn came after every funded row, so 24s 40c is also what stays unallocated", colour = "muted", wrap = true },
      { left = "Expected net", right = "+80c" },
      { left = "Risk limit", right = "10g 67s 40c · committed 4g 18s" },
      { left = "Funded before it", right = "6g 25s of 6g 49s 40c" } })
    World.Unname(Stubs)
  end)

  it("shows the week's rows with their wait and premium, positions with their totals, and % per section", function()
    World.Boot(Stubs)
    local tab = Tab()
    local thick = tab.week.rows[1]
    assert_eq({ thick.adjusted, thick.wait, thick.premium, thick.note }, { "+41s 75c", "3.35 d", "31.75%", "buy now · sell Thu 19:00" })
    assert_eq(tab.week.foot, "funded: cost 2g 31s 40c · profit +80s 54c · 35% on cash")
    assert_eq({ #tab.positions.rows, tab.positions.rows[1].state, tab.positions.rows[1].next, tab.positions.foot },
      { 1, "Holding", "post now · found, uses no capital", "committed 0c · est. +4s 72c" },
      "found Wool Cloth: no cost, so no % on committed capital")
    local PV = AHProfit.PortfolioView
    assert_eq({ PV.ProfitPct(6306, 5100), PV.ProfitPct(134, 0), PV.PoolPct({ { net = 134, cash = 0 } }) }, { 124, nil, nil })
    assert_eq({ PV.NextAction({ nextAction = "post", target = World.At(5, 19), costLeft = 10 }, World.NOW, 0),
                PV.NextAction({ nextAction = "de", costLeft = 10 }, World.NOW, 0), PV.NextAction({ costLeft = 10 }, World.NOW, 0) },
              { "post for the Sat 19:00 peak", "disenchant", "—" })
    assert_eq({ PV.PerDay(3), PV.PerDay(2.66), PV.When(World.NOW - 1, World.NOW, 0), PV.ReasonLine("Insufficient liquidity: 3/day") },
              { "3", "2.7", "now", "Insufficient liquidity: 3/day" })
    assert_eq(#Lines(PV.Build({ funded = {}, excluded = {}, picks = {}, net = 0, gold = 0, capacityLeft = 0 },
      AHProfit.Adapt.Snapshot(), {}, { nowMs = World.NOW, offsetMs = 0, nameOf = tostring, liveGold = 5 })).lines, 3,
      "no positions: the heading, 'no open positions' and the empty block")
  end)
end)
