-- Engine/Graph.lua: the dependency graph the addon reprices along (SPEC §4.0 repricing, §3.5 `G`, DG1-DG7, FM39).
-- Route costs and candidate repricing are the twins of ahsync/engine/graph.py's route_cost, route_costs,
-- input_delta and repriced; keep them formula-for-formula identical (CLAUDE.md; shared scenarios:
-- tests/scenarios/graph.json, DG1). The rest is addon-only: loading the exported graph and refusing an `order` that
-- isn't topological (the addon never discovers the graph, FM39), the incremental pass that recomputes only what a
-- price change reaches (DG2, DG4, DG7), and adding a recipe learned in game (A83). Pure: data in, numbers out; the
-- work each pass does is counted in g.work so tests count it instead of timing it (SPEC §5.2).

local AHProfit = AHProfit
AHProfit.Engine = AHProfit.Engine or {}

local Graph = {}
AHProfit.Engine.Graph = Graph

Graph.INVALID = "dependency graph invalid: using AHSync's numbers"     -- the status line (FM39, DG6)

--- A copy of a recipe with its reagents by key and its yield in thousandths (as engine.graph.GraphRecipe).
---@param r table  { product, yield (units a craft, average) or yield1000, reagents = { { key, count } } }
---@return table   { product, yield1000, reagents = { { key, count } } by key }
local function NormalRecipe(r)
  local reagents = {}
  for i, m in ipairs(r.reagents) do reagents[i] = { key = m.key, count = m.count } end
  table.sort(reagents, function(a, b) return a.key < b.key end)
  return { product = r.product, yield1000 = r.yield1000 or math.floor(r.yield * 1000 + 0.5), reagents = reagents }
end

--- Sorted keys of a table whose keys are numbers.
---@param t table
---@return number[]
local function SortedKeys(t)
  local out = {}
  for k in pairs(t) do out[#out + 1] = k end
  table.sort(out)
  return out
end

--- Index the order (item -> place) and the recipes by product (each list by spell).
---@param g table  The graph; g.pos and g.byProduct are replaced
local function Index(g)
  g.pos = {}
  for i, key in ipairs(g.order) do g.pos[key] = i end
  g.byProduct = {}
  for _, spell in ipairs(SortedKeys(g.recipes)) do
    local product = g.recipes[spell].product
    g.byProduct[product] = g.byProduct[product] or {}
    table.insert(g.byProduct[product], spell)
  end
end

--- Whether an order is topological for the kept edges: every item once, each reagent before what it makes (FM39).
---@param g table  The graph, indexed
---@return boolean
local function Valid(g)
  local count = 0
  for _ in pairs(g.pos) do count = count + 1 end
  if count ~= #g.order then return false end                     -- an item listed twice
  for _, r in pairs(g.recipes) do
    if not g.pos[r.product] then return false end
  end
  for key, spells in pairs(g.uses) do
    for _, spell in ipairs(spells) do
      local r = g.recipes[spell]
      if not (g.pos[key] and r and g.pos[key] < g.pos[r.product]) then return false end
    end
  end
  return true
end

--- Load the exported graph into the in-memory one, checking its order (SPEC §4.0, FM39, DG6).
---@param decoded table  Market.DecodeGraph's shape: { recipes = { [spell] = { product, yield or yield1000, reagents } },
---                      uses = { [key] = { spell, ... } }, order = { key, ... }, disenchant = { key, ... } }
---@return table|nil     The graph { recipes, uses, order, pos, byProduct, work = { nodes, recipes, passes } }; nil
---                      when its order isn't topological
---@return string|nil    Graph.INVALID when refused
function Graph.Load(decoded)
  local g = { recipes = {}, uses = {}, order = {}, work = { nodes = 0, recipes = 0, passes = 0 } }
  for spell, r in pairs(decoded.recipes or {}) do g.recipes[spell] = NormalRecipe(r) end
  for key, spells in pairs(decoded.uses or {}) do
    local list = {}
    for i, spell in ipairs(spells) do list[i] = spell end
    table.sort(list)
    g.uses[key] = list
  end
  for i, key in ipairs(decoded.order or {}) do g.order[i] = key end
  Index(g)
  if not Valid(g) then return nil, Graph.INVALID end
  return g
end

--- The cheaper of buying an item and crafting it from reagents before it in the order (graph.route_cost, DG3).
---@param g table          The graph
---@param key number       The item
---@param routes table     key -> { cost, crafted } for the items already costed
---@param priceOf function key -> buy price (copper a unit) or nil
---@return table|nil       { cost (float copper a unit), crafted }; a tie buys; nil without a price or usable recipe
function Graph.RouteCost(g, key, routes, priceOf)
  local buy, craft = priceOf(key), nil
  local here = g.pos[key]
  for _, spell in ipairs(g.byProduct[key] or {}) do
    local r = g.recipes[spell]
    g.work.recipes = g.work.recipes + 1
    local total, usable = 0.0, true
    for _, m in ipairs(r.reagents) do
      local got = g.pos[m.key] and here and g.pos[m.key] < here and routes[m.key] or nil   -- a cut edge counts as none
      if not got then usable = false; break end
      total = total + m.count * got.cost
    end
    if usable then
      local each = total * 1000 / r.yield1000
      if craft == nil or each < craft then craft = each end
    end
  end
  if craft ~= nil and (buy == nil or craft < buy) then return { cost = craft, crafted = true } end
  if buy == nil then return nil end
  return { cost = buy + 0.0, crafted = false }
end

--- Every graph item's cheapest route, in the exported order (graph.route_costs): one full pass.
---@param g table
---@param priceOf function  key -> buy price or nil
---@return table            key -> { cost, crafted }
function Graph.RouteCosts(g, priceOf)
  local routes = {}
  for _, key in ipairs(g.order) do routes[key] = Graph.RouteCost(g, key, routes, priceOf) end
  return routes
end

--- What a set of changed items reaches through the reverse index, every level down, each item once (DG2, DG4).
---@param g table
---@param changed number[]  Item keys whose price changed
---@return table            key -> true for every item reached, the changed ones included
local function Reach(g, changed)
  local dirty, stack = {}, {}
  for _, key in ipairs(changed) do
    if not dirty[key] then dirty[key] = true; stack[#stack + 1] = key end
  end
  while #stack > 0 do
    local key = table.remove(stack)
    for _, spell in ipairs(g.uses[key] or {}) do
      local product = g.recipes[spell].product
      if not dirty[product] then dirty[product] = true; stack[#stack + 1] = product end
    end
  end
  return dirty
end

--- One repricing pass: recompute the route of every item a change reaches, once each, in the exported order, so
--- intermediates come before what they make (SPEC §4.0, DG2-DG4, DG7). Unreached items keep their cached routes.
---@param g table           The graph; g.work counts the items and recipes visited and the pass
---@param routes table      key -> { cost, crafted }; updated in place
---@param priceOf function  key -> live buy price or nil
---@param changed number[]  Item keys whose price changed
---@return number[]         Every item reached, graph items in the exported order, then the others by key
function Graph.Reprice(g, routes, priceOf, changed)
  g.work.passes = g.work.passes + 1
  local inGraph, outside = {}, {}
  for key in pairs(Reach(g, changed)) do
    if g.pos[key] then inGraph[#inGraph + 1] = key else outside[#outside + 1] = key end
  end
  table.sort(inGraph, function(a, b) return g.pos[a] < g.pos[b] end)
  table.sort(outside)
  for _, key in ipairs(inGraph) do
    g.work.nodes = g.work.nodes + 1
    routes[key] = Graph.RouteCost(g, key, routes, priceOf)
  end
  for _, key in ipairs(outside) do inGraph[#inGraph + 1] = key end
  return inGraph
end

--- Whether an input's source is priced: it names the AH or a craft (graph.input_delta).
---@param source string  "own", "ah", "vendor", "craft", or several joined by "+"
---@return boolean
local function Priced(source)
  for part in (source .. "+"):gmatch("([^+]*)%+") do
    if part == "ah" or part == "craft" then return true end
  end
  return false
end

--- How much a candidate's bought and crafted inputs cost more now than at the export (graph.input_delta, DG1).
---@param inputs table[]  { key, count, source } per input, as K lists them
---@param base table      key -> { cost } at the exported prices
---@param now table       key -> { cost } at the live prices
---@return number         Σ count × (now − then), float copper, over priced inputs known at both
function Graph.InputDelta(inputs, base, now)
  local delta = 0.0
  for _, input in ipairs(inputs) do
    local before, after = base[input.key], now[input.key]
    if Priced(input.source) and before and after then delta = delta + input.count * (after.cost - before.cost) end
  end
  return delta
end

--- A candidate's gold needed and expected net after its inputs moved (graph.repriced, DG1).
---@param gold number     Exported gold needed, copper
---@param net number      Exported expected net, copper
---@param inputs table[]  As InputDelta
---@param base table      Routes at the exported prices
---@param now table       Routes at the live prices
---@return number         gold + floor(delta + 0.5), integer copper
---@return number         net − delta, float copper
function Graph.Repriced(gold, net, inputs, base, now)
  local delta = Graph.InputDelta(inputs, base, now)
  return gold + math.floor(delta + 0.5), net - delta
end

--- A depth-first order of every graph item, inputs first, cutting each edge back onto the path (graph._topological).
---@param g table
---@return number[]  The order
---@return table[]   { key, spell } edges cut
local function Topological(g)
  local nodes, state, order, cut = {}, {}, {}, {}
  for _, key in ipairs(g.order) do nodes[key] = true end
  for _, r in pairs(g.recipes) do
    nodes[r.product] = true
    for _, m in ipairs(r.reagents) do nodes[m.key] = true end
  end
  --- The (spell, reagent) edges into an item, by spell then key.
  ---@param key number
  ---@return table[]
  local function Inputs(key)
    local out = {}
    for _, spell in ipairs(g.byProduct[key] or {}) do
      for _, m in ipairs(g.recipes[spell].reagents) do out[#out + 1] = { spell, m.key } end
    end
    return out
  end
  for _, root in ipairs(SortedKeys(nodes)) do
    if not state[root] then
      state[root] = 1
      local stack = { { root, Inputs(root), 0 } }
      while #stack > 0 do
        local top = stack[#stack]
        top[3] = top[3] + 1
        local edge = top[2][top[3]]
        if not edge then
          table.remove(stack); state[top[1]] = 2; order[#order + 1] = top[1]
        elseif state[edge[2]] == 1 then
          cut[#cut + 1] = { edge[2], edge[1] }
        elseif not state[edge[2]] then
          state[edge[2]] = 1; stack[#stack + 1] = { edge[2], Inputs(edge[2]), 0 }
        end
      end
    end
  end
  return order, cut
end

--- Add a recipe learned in game to the in-memory graph (A83): its edges, its items, and a valid order again.
--- A recipe that would close a loop has that edge cut, as the desktop cuts it (C13).
---@param g table        The graph; changed
---@param spell number
---@param recipe table   { product, yield or yield1000, reagents = { { key, count } } }
---@return boolean       false when the graph already has the spell (nothing changes)
function Graph.AddRecipe(g, spell, recipe)
  if g.recipes[spell] then return false end
  g.recipes[spell] = NormalRecipe(recipe)
  Index(g)
  local order, cut = Topological(g)
  for _, m in ipairs(g.recipes[spell].reagents) do
    g.uses[m.key] = g.uses[m.key] or {}
    table.insert(g.uses[m.key], spell)
    table.sort(g.uses[m.key])
  end
  for _, edge in ipairs(cut) do                    -- the walk's cut edges leave the reverse index (C13)
    local kept = {}
    for _, s in ipairs(g.uses[edge[1]] or {}) do if s ~= edge[2] then kept[#kept + 1] = s end end
    g.uses[edge[1]] = kept
  end
  g.order = order
  Index(g)
  return true
end
