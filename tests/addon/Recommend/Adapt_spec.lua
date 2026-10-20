-- Specs for addon/AHProfit/Recommend/Adapt.lua: the live state, batched repricing, learned recipes and the refused
-- graph (SPEC §4.0, §5.1; DG7, FM39, A83). Work is counted, not timed (SPEC §5.2 addon performance tests).

local Stubs = require("wow_stubs")
local World = require("portfolio_world")

local FILLER = 397                 -- recipes beyond the copper chain, so the graph holds 400

--- DG7's world: a 400-recipe graph and 150 candidates (the world's ten of Amaya, 140 filler crafts).
---@return table  World.Install's options
local function BigWorld()
  local g = { R = { [2657] = "2840:1000:2770*1", [7818] = "6217:1000:2840*2", [7421] = "6218:1000:6217*1/10938*1/10940*1" },
              U = { [2770] = "2657", [2840] = "7818", [6217] = "7421", [10938] = "7421", [10940] = "7421" }, DE = "" }
  local order = { "2770", "2840", "6217", "10938", "10940", "6218" }
  for i = 1, FILLER do
    local mat, product = 600000 + i, 500000 + i
    g.R[100000 + i] = product .. ":1000:" .. mat .. "*2"
    g.U[mat] = tostring(100000 + i)
    order[#order + 1] = mat .. "," .. product
  end
  g.order = table.concat(order, ",")
  local cands = {}
  for i, c in ipairs(World.Candidates()) do if i <= 10 then cands[#cands + 1] = c end end
  for i = 1, 140 do
    cands[#cands + 1] = { id = "craft:" .. (500000 + i) .. ":" .. World.AMAYA .. ":" .. (600000 + i) .. "a/-/now:1",
                          strategy = "craft", char = World.AMAYA, key = 500000 + i, inputs = (600000 + i) .. ":2:ah",
                          gold = 200, net = 50, sale = World.TONIGHT }
  end
  local items = { [World.ORE] = World.Item(1200), [World.BAR] = World.Item(1000), [World.ESSENCE] = World.Item(500),
                  [World.DUST] = World.Item(300) }
  return { G = g, candidates = cands, items = items }
end

--- The book of DG7's priced items at their file prices, with ore at a new price.
---@param ore number  Copper
---@return table
local function BaseBook(ore)
  return { [World.ORE] = World.Book({ { ore, 20 } }), [World.BAR] = World.Book({ { 1000, 20 } }),
           [World.ESSENCE] = World.Book({ { 500, 20 } }), [World.DUST] = World.Book({ { 300, 20 } }) }
end

describe("Adapt", function()
  it("DG7 an ore change visits 3 recipes; a 9,000-item full scan is one batched pass after it is saved", function()
    World.Boot(Stubs, BigWorld())
    local Adapt = AHProfit.Adapt
    Adapt.Current()
    assert_eq(#Adapt.State().rcs, 150)
    World.Scan(Stubs, BaseBook(1100))
    assert_eq(Adapt.Perf().passes, 0, "debounced: nothing until the timer, and nothing in the scan's own frame")
    Stubs.Advance(1)
    local perf = Adapt.Perf()
    assert_eq({ perf.passes, perf.recipes, perf.nodes, perf.candidates }, { 1, 3, 4, 1 },
      "ore, bar, rod and runed rod: 3 recipes; only the Runed Copper Rod candidate uses them")
    assert_eq(perf.repriceMs <= 2, true, "within the 2 ms state-change budget (the stub's clock counts the AH reads only)")
    local book = BaseBook(1050)
    for key = 1, 9000 do book[key] = World.Book({ { 100 + key % 7, 1 } }) end
    World.Scan(Stubs, book)
    World.Scan(Stubs, book)
    AHProfit.Live.NoteSearch(2770, 1050, 20, World.NOW + 1000)
    Stubs.Advance(1)
    assert_eq({ Adapt.Perf().passes, Adapt.Perf().recipes }, { 2, 3 }, "two scans and a search: one pass, never one per item")
  end)

  it("DG7 a repricing pass waits out combat and runs once after it", function()
    World.Boot(Stubs, BigWorld())
    local Adapt = AHProfit.Adapt
    Adapt.Current()
    Stubs.EnterCombat()
    World.Scan(Stubs, BaseBook(1100))
    Stubs.Advance(1)
    assert_eq(Adapt.Perf().passes, 0, "nothing in combat (SPEC §5.1)")
    Stubs.LeaveCombat()
    assert_eq(Adapt.Perf().passes, 1)
  end)

  it("FM39 a graph whose order lists Copper Rod before Copper Bar is refused; the exported values still reselect", function()
    local g = World.G()
    g.order = "2770,6217,2840,4306,4305,6187,10938,10940,6218"
    World.Boot(Stubs, { G = g })
    local Adapt = AHProfit.Adapt
    assert_eq(Adapt.Problem(), "dependency graph invalid: using AHSync's numbers")
    local result = Adapt.Current()
    assert_eq(#result.funded, 7, "PF15's seven rows, on the exported numbers")
    World.Scan(Stubs, { [World.THICK] = World.Book({ { 650, 30 } }) })
    Stubs.Advance(1)
    assert_eq(Adapt.State().rcs[6].gold, 16080, "no repricing without a graph: the exported value stays")
    assert_eq(AHProfit.Events.Errors(), {}, "no Lua error")
  end)

  it("A83 a recipe learned in game joins the graph, gets a craft candidate at posting now, and is funded", function()
    local items = { [2997] = World.Item(nil, { postPrice = 400, sellValue = 360, chance1000 = 900, supply = 100 }),
                    [World.WOOL] = World.Item(nil, { sellValue = 30 }) }
    World.Boot(Stubs, { items = items, bags = { { 2318, 3 }, { 4306, 4 }, { World.WOOL, 20 } } })
    local Adapt = AHProfit.Adapt
    Stubs.world.tradeSkill = { name = "Tailoring", rank = 130, max = 150, recipes = {
      { name = "Bolt of Woolen Cloth", spell = 2964, product = 2997, min = 1, max = 1, reagents = { { World.WOOL, 3 } } } } }
    Stubs.FireEvent("TRADE_SKILL_SHOW")
    Stubs.Advance(0.5)
    local s = Adapt.State()
    assert_eq({ s.graph.recipes[2964].product, s.graph.uses[World.WOOL] }, { 2997, { 2964 } })
    local id = "craft:2997:" .. World.AMAYA .. ":2592o/-/now:1"
    local funded = {}
    for _, rc in ipairs(s.result.funded) do funded[rc.c.id] = rc end
    assert_eq({ funded[id] ~= nil, funded[id].gold, funded[id].c.qty }, { true, 0, 6 },
      "six crafts from her 20 found Wool Cloth, no gold needed: funded first")
    assert_eq({ AHProfitDB.recipes.Tailoring[2964].product, AHProfitDB.knownRecipes[World.AMAYA] }, { 2997, "2964" },
      "captured, so AHSync's next export has it in G")
    Stubs.FireEvent("TRADE_SKILL_SHOW")
    Stubs.Advance(0.5)
    local count = 0
    for _, rc in ipairs(Adapt.State().rcs) do if rc.c.id == id then count = count + 1 end end
    assert_eq(count, 1, "known now: a second capture adds nothing")
  end)

  it("reports the reselection and repricing timings for /ahp perf", function()
    World.Boot(Stubs)
    AHProfit.Adapt.Current()
    Stubs.Slash("/ahp perf")
    assert_eq(#Stubs.chat >= 3, true)
    local text = table.concat(Stubs.chat, "\n")
    assert_eq({ text:find("reprice 0.00 ms", 1, true) ~= nil, text:find("reselect 0.00 ms (1 runs)", 1, true) ~= nil },
              { true, true })
  end)

  it("A64 an own craft's mats stay within Amaya's live cap, not a share of her gold", function()
    local items = { [7000] = World.Item(nil, { postPrice = 20000, sellValue = 19000, chance1000 = 1000, supply = 1000 }),
                    [7001] = World.Item(1000, { sellValue = 900 }) }
    World.Boot(Stubs, { money = 45500, items = items })
    assert_eq(AHProfit.Adapt.Cap(), 27640, "min(4g 55s, 6g 49s 40c - 3g 73s) = 2g 76s 40c")
    local recipe = { spell = 1, product = 7000, yieldMin = 1, yieldMax = 1, reagents = { { 7001, 10 } }, crafters = 1 }
    local option = AHProfit.Adapt.CraftOptionOf(recipe)
    assert_eq({ option.crafts, option.matGold }, { 2, 20000 }, "a craft buys 1g of mats: 10 would buy 10g, 2 fit 2g 76s 40c")
    Stubs.world.money = 20000
    assert_eq(AHProfit.Adapt.Cap(), 20000)
    _G.AHProfitPlan = nil
    AHProfit.Plan.Reset()
    assert_eq(AHProfit.Adapt.Cap(), 20000, "without a snapshot: her live gold")
  end)
end)
