-- Specs for addon/AHProfit/Engine/Graph.lua: loading and checking the exported graph, route costs, the incremental
-- repricing pass and learned recipes (SPEC §4.0; shared scenarios tests/scenarios/graph.json: DG1; DG2-DG4, DG6).

local Stubs = require("wow_stubs")
local World = require("portfolio_world")

--- Boot the engine module.
---@return table  AHProfit.Engine.Graph
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "Engine/Graph.lua" })
  return AHProfit.Engine.Graph
end

--- The world's graph as Market.DecodeGraph gives it.
---@return table
local function Decoded()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "Core/Events.lua", "Core/DB.lua", "Capture/Characters.lua", "Engine/SellProb.lua",
                    "Core/Market.lua", "Engine/Graph.lua" })
  return AHProfit.Market.DecodeGraph(World.G())
end

--- A price function from a table.
---@param prices table  key -> copper
---@return function
local function PriceOf(prices)
  return function(key) return prices[key] end
end

describe("Graph shared scenarios", function()
  for _, s in ipairs(dofile("tests/addon/generated/graph.lua")) do
    it(s.id .. " " .. s.name, function()
      local Graph = Boot()
      local a = s.args
      local decoded = { recipes = {}, uses = {}, order = a.order }
      for _, r in ipairs(a.recipes) do
        local mats = {}
        for i, m in ipairs(r[4]) do mats[i] = { key = m[1], count = m[2] } end
        decoded.recipes[r[1]] = { product = r[2], yield1000 = r[3], reagents = mats }
      end
      for key, spells in pairs(a.uses) do decoded.uses[tonumber(key)] = spells end
      local g = assert(Graph.Load(decoded))
      local base, now = {}, {}
      for k, v in pairs(a.base_prices) do base[tonumber(k)] = v end
      for k, v in pairs(a.prices) do now[tonumber(k)] = v end
      local before, after = Graph.RouteCosts(g, PriceOf(base)), Graph.RouteCosts(g, PriceOf(now))
      local costs, cands = {}, {}
      for key, r in pairs(after) do costs[tostring(key)] = { r.cost, r.crafted } end
      for _, c in ipairs(a.candidates) do
        local inputs = {}
        for i, m in ipairs(c.inputs) do inputs[i] = { key = m[1], count = m[2], source = m[3] } end
        cands[c.id] = { Graph.Repriced(c.gold, c.net, inputs, before, after) }
      end
      assert_eq({ costs = costs, candidates = cands }, s.expect, s.why)
    end)
  end
end)

describe("Graph", function()
  it("DG2 a copper ore change recomputes bar, rod and runed rod in the exported order, each once", function()
    local decoded = Decoded()
    local Graph = AHProfit.Engine.Graph
    local g = assert(Graph.Load(decoded))
    local prices = { [World.ORE] = 1200, [World.BAR] = 1000, [World.ESSENCE] = 500, [World.DUST] = 300 }
    local routes = Graph.RouteCosts(g, PriceOf(prices))
    g.work.recipes, g.work.nodes = 0, 0
    prices[World.ORE] = 400                                   -- smelting (4s) now beats the AH's 10s bar
    local reached = Graph.Reprice(g, routes, PriceOf(prices), { World.ORE })
    assert_eq(reached, { World.ORE, World.BAR, World.ROD, World.RUNED }, "intermediates first, each once")
    assert_eq({ g.work.recipes, g.work.nodes, g.work.passes }, { 3, 4, 1 })
    assert_eq({ routes[World.BAR].cost, routes[World.BAR].crafted, routes[World.ROD].cost, routes[World.RUNED].cost },
              { 400, true, 800, 1600 })
  end)

  it("DG3 the rod's route switches from bought to crafted when bars get cheap, and the runed rod follows", function()
    local decoded = Decoded()
    local Graph = AHProfit.Engine.Graph
    local g = assert(Graph.Load(decoded))
    local prices = { [World.ORE] = 1200, [World.BAR] = 1000, [World.ROD] = 1500, [World.ESSENCE] = 500, [World.DUST] = 300 }
    local base = Graph.RouteCosts(g, PriceOf(prices))
    assert_eq({ base[World.ROD].cost, base[World.ROD].crafted }, { 1500, false }, "an AH rod 15s beats 2 bars at 10s")
    local routes = Graph.RouteCosts(g, PriceOf(prices))
    prices[World.BAR] = 600
    Graph.Reprice(g, routes, PriceOf(prices), { World.BAR })
    assert_eq({ routes[World.ROD].cost, routes[World.ROD].crafted }, { 1200, true }, "2 bars at 6s = 12s < 15s")
    local inputs = { { key = World.ROD, count = 1, source = "ah" }, { key = World.ESSENCE, count = 1, source = "ah" } }
    assert_eq({ Graph.Repriced(3700, 1630, inputs, base, routes) }, { 3400, 1930 }, "the runed rod's cost falls 3s")
  end)

  it("DG4 a silk change recomputes only silk and its bolt: no copper item is visited", function()
    local decoded = Decoded()
    local Graph = AHProfit.Engine.Graph
    local g = assert(Graph.Load(decoded))
    local prices = { [World.ORE] = 1200, [World.BAR] = 1000, [World.SILK] = 140, [World.ESSENCE] = 500, [World.DUST] = 300 }
    local routes = Graph.RouteCosts(g, PriceOf(prices))
    local copper = { [World.ORE] = routes[World.ORE], [World.BAR] = routes[World.BAR], [World.ROD] = routes[World.ROD],
                     [World.RUNED] = routes[World.RUNED] }
    g.work.recipes, g.work.nodes = 0, 0
    prices[World.SILK] = 120
    assert_eq(Graph.Reprice(g, routes, PriceOf(prices), { World.SILK }), { World.SILK, World.BOLT })
    assert_eq({ g.work.nodes, g.work.recipes, routes[World.BOLT].cost }, { 2, 1, 480 })
    for key, route in pairs(copper) do assert_eq(routes[key] == route, true, "the copper chain's cache is untouched") end
  end)

  it("DG6 an order that lists Copper Rod before Copper Bar is refused with the status line, without a Lua error", function()
    local decoded = Decoded()
    local Graph = AHProfit.Engine.Graph
    decoded.order = { World.ORE, World.ROD, World.BAR, World.SILK, World.BOLT, World.BRACERS, World.ESSENCE, World.DUST,
                      World.RUNED }
    assert_eq({ AHProfit.Engine.Graph.Load(decoded) }, { nil, "dependency graph invalid: using AHSync's numbers" })
    decoded.order = { World.ORE, World.BAR }                 -- an item missing from the order
    assert_eq(AHProfit.Engine.Graph.Load(decoded), nil)
    decoded.order = { World.ORE, World.ORE }                 -- an item twice
    assert_eq(AHProfit.Engine.Graph.Load(decoded), nil)
    assert_eq(Graph.INVALID, "dependency graph invalid: using AHSync's numbers")
  end)

  it("adds a learned recipe with its edges and a valid order, and cuts an edge that would close a loop", function()
    local decoded = Decoded()
    local Graph = AHProfit.Engine.Graph
    local g = assert(Graph.Load(decoded))
    assert_eq(Graph.AddRecipe(g, 2963, { product = 2997, yield = 1, reagents = { { key = World.WOOL, count = 3 } } }), true)
    assert_eq({ g.uses[World.WOOL], g.pos[World.WOOL] < g.pos[2997] }, { { 2963 }, true })
    assert_eq(Graph.AddRecipe(g, 2963, { product = 2997, yield = 1, reagents = {} }), false, "known already")
    Graph.AddRecipe(g, 9999, { product = World.ORE, yield = 1, reagents = { { key = World.RUNED, count = 1 } } })
    local routes = Graph.RouteCosts(g, PriceOf({ [World.RUNED] = 5 }))
    assert_eq(routes[World.ORE] ~= nil, true, "the loop is cut, so ore is costed from the runed rod")
    for key, spells in pairs(g.uses) do
      for _, spell in ipairs(spells) do assert_eq(g.pos[key] < g.pos[g.recipes[spell].product], true) end
    end
  end)
end)
