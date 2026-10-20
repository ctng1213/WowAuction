-- Recommend/Adapt.lua: live adaptation of the logged-in character's portfolio (SPEC §4.0, §3.4D.10; M12).
-- It holds the in-memory graph and this character's repriced candidates, built lazily the first time anything asks
-- (never at login, SPEC §5.1), and keeps them current:
-- - a complete scan or a search result reprices through the graph in ONE batched pass, debounced and never in
--   combat (Events.Debounce, which runs through Events.RunWhenSafe; DG7);
-- - a reselection runs only when that pass could change the selection (Reselect.NeedsReselect, DG5), or when the
--   gold, the bags or the recipes changed;
-- - a recipe learned in game joins the graph with a craft candidate valued at posting now (A83);
-- - a graph whose order isn't topological is refused with a status line; the candidates keep their exported values
--   and reselection still runs on them (FM39, DG6).
-- Other characters' rows are never repriced or selected (A78). Timings and work counts feed /ahp perf.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB
local Market = AHProfit.Market
local Plan = AHProfit.Plan
local Live = AHProfit.Live
local Options = AHProfit.Options
local Graph = AHProfit.Engine.Graph
local Reselect = AHProfit.Reselect
local Inventory = AHProfit.Capture.Inventory

local Adapt = {}
AHProfit.Adapt = Adapt

Adapt.PLACES = { "bags", "bank", "mail" }      -- where held units count (SPEC §3.4D.3: one lot per character)

local st = nil                 -- the live state, built on first use (Build)
local perf = { repriceMs = 0, reselectMs = 0, recipes = 0, nodes = 0, candidates = 0, passes = 0, reselections = 0 }

--- This character's id.
---@return string  "Name-Realm"
local function CharId()
  local info = Adapter.PlayerInfo()
  return info.name .. "-" .. info.realm
end

--- An item's live listings from a scan or search newer than the market file, or nil (SPEC §4.0).
---@param key number
---@return table[]|nil
local function LevelsOf(key)
  local _, newest = Market.Times()
  return Live.Item(key, newest)
end

--- The price an item is bought at now: the live book's cheapest, else the market file's.
---@param key number
---@return number|nil  Copper a unit; nil when nothing is listed
local function LivePrice(key)
  local levels = LevelsOf(key)
  if levels then return levels[1] and levels[1].price end
  local item = Market.Item(key)
  return item and item.cur
end

--- The price the export was valued at: the market file's.
---@param key number
---@return number|nil
local function BasePrice(key)
  local item = Market.Item(key)
  return item and item.cur
end

--- What a merchant pays for one of an item, from the item cache.
---@param key number
---@return number|nil  Copper
local function VendorOf(key)
  return select(3, Adapter.ItemInfo(key % 2147483648))
end

--- What a sale now needs for its live repricing (Reselect.RepriceSale).
---@param c table  The candidate
---@return table|nil
local function SaleOf(c)
  local item = Market.Item(c.key)
  if not item then return nil end
  return { item = item, curve = Market.Curve(c.key), vendor = VendorOf(c.key) or 0,
           nowMs = Adapter.NowMs(), defaultP = Options.DEFAULT_SELL_PROB, defaultCheapest = Options.DEFAULT_SELL_PROB_CHEAPEST }
end

--- The live pricing context for Reselect.Reprice.
---@return table
local function LiveContext()
  return { graph = st.graph ~= nil, base = st.base, routes = st.routes, levelsOf = LevelsOf, saleOf = SaleOf }
end

--- Index one candidate under its output and input keys.
---@param byKey table  key -> { index, ... }
---@param i number     The candidate's index
---@param c table
local function IndexCandidate(byKey, i, c)
  local seen = {}
  local keys = { c.key }
  for _, input in ipairs(c.inputs) do keys[#keys + 1] = input.key end
  for _, key in ipairs(keys) do
    if not seen[key] then
      seen[key] = true
      byKey[key] = byKey[key] or {}
      table.insert(byKey[key], i)
    end
  end
end

--- Every item whose price matters: the graph's and the candidates', ascending.
---@return number[]
local function RelevantKeys()
  local set, out = {}, {}
  for _, key in ipairs(st.graph and st.graph.order or {}) do set[key] = true end
  for key in pairs(st.byKey) do set[key] = true end
  for key in pairs(set) do out[#out + 1] = key end
  table.sort(out)
  return out
end

--- Build the live state: the graph, its route costs at the exported and the live prices, this character's
--- candidates repriced (SPEC §4.0). The other characters' candidates are left out (A78).
local function Build()
  st = { charId = CharId(), rcs = {}, byKey = {}, lastPrice = {}, problem = nil }
  local decoded = Market.Graph()
  if decoded then st.graph, st.problem = Graph.Load(decoded) end
  if st.graph then
    st.base, st.routes = Graph.RouteCosts(st.graph, BasePrice), Graph.RouteCosts(st.graph, LivePrice)
  else
    st.base, st.routes = {}, {}
  end
  for i = 1, Market.CandidateCount() do
    local c = Market.Candidate(i)
    if c and c.char == st.charId then
      st.rcs[#st.rcs + 1] = Reselect.Reprice(c, LiveContext())
      IndexCandidate(st.byKey, #st.rcs, c)
    end
  end
  st.relevant = RelevantKeys()
  for _, key in ipairs(st.relevant) do st.lastPrice[key] = LivePrice(key) end
end

--- The live state, built on first use.
---@return table
local function State()
  if not st then Build() end
  return st
end

--- What the character holds now, by item, over bags, bank and mail; nil before any inventory snapshot.
---@param db table
---@return table|nil
local function Held(db)
  local inventory = (db.inventory or {})[st.charId]
  if not inventory then return nil end
  local units = {}
  for _, where in ipairs(Adapt.PLACES) do
    for key, qty in pairs(Options.Units(Inventory.Decode(inventory[where]))) do units[key] = (units[key] or 0) + qty end
  end
  return units
end

--- The other characters' reservations and cached spendable gold, from the snapshot only (A78, A80).
---@return number   Σ their reserved, copper
---@return table[]  { charId, gold − reserved } by charId (for the "all characters hold" check)
local function Others()
  local chars, sum, out = {}, 0, {}
  for _, c in ipairs(Plan.Characters()) do
    if c ~= st.charId and Market.SameMarket(c) then chars[#chars + 1] = c end
  end
  for _, c in ipairs(chars) do
    local reserved = Plan.Reserved(c)
    sum = sum + reserved
    out[#out + 1] = { c, math.max(0, (Plan.Gold(c) or 0) - reserved) }
  end
  return sum, out
end

--- The snapshot of this character's market, with your local positions' gold committed (A81): committed grows and
--- the remaining capacity shrinks by what they cost until AHSync's next export includes them.
---@return table|nil  As Plan.Portfolio, plus localCommitted (copper); nil without one
function Adapt.Snapshot()
  local market = Market.MarketId()
  local snapshot = market and Plan.Portfolio(market) or nil
  if not snapshot then return nil end
  local spent = Plan.LocalCommitted(CharId())
  snapshot.localCommitted = spent
  snapshot.committed = (snapshot.committed or 0) + spent
  snapshot.capacity = math.max(0, (snapshot.capacity or 0) - spent)
  return snapshot
end

-- A strategy -> the event kind that executes it (a local position takes its candidates out of the reselection).
Adapt.EXECUTED_BY = { flip = "buy", buy_later = "buy", disenchant = "buy", craft = "craft", multistage = "craft" }

--- This character's candidates less those a local position already executed (A81: the bought row leaves).
---@param rcs table[]  Repriced candidates
---@return table[]
function Adapt.Unexecuted(rcs)
  local done, out = {}, {}
  for _, p in ipairs(Plan.LocalPositions(CharId())) do done[p.kind .. ":" .. p.key] = true end
  for _, rc in ipairs(rcs) do
    local kind = Adapt.EXECUTED_BY[rc.c.strategy]
    if not (kind and done[kind .. ":" .. rc.c.key]) then out[#out + 1] = rc end
  end
  return out
end

--- The most the character may spend now (SPEC §4.0, A80, A64): min(live gold, the snapshot's remaining capacity −
--- the other characters' reservations); its live gold alone without a snapshot.
---@return number  Copper
function Adapt.Cap()
  local snapshot, gold = Adapt.Snapshot(), Adapter.Money()
  if not snapshot then return gold end
  State()
  local othersReserved = Others()
  return (Reselect.Cap(gold, snapshot.capacity or 0, othersReserved))
end

--- The addon's own craft option for one recipe, within the character's live cap (Options.CraftOptions, A64, A83).
---@param recipe table  Options.KnownRecipes' shape
---@return table|nil    Its CraftOptions row; nil when no craft pays
function Adapt.CraftOptionOf(recipe)
  local db, s = DB.Get() or {}, State()
  local units = Options.Units(Inventory.Decode((((db.inventory or {})[s.charId]) or {}).bags))
  local ctx = { itemOf = Adapt.LiveItem, levelsOf = LevelsOf, nowMs = Adapter.NowMs(), budget = Adapt.Cap(),
                vendorOf = VendorOf, vendorBuyOf = Market.VendorPrice }
  return Options.CraftOptions({ recipe }, units, ctx)[1]
end

--- An item's market data, repriced from live listings when a newer scan has them (A19), as the window prices it.
---@param key number
---@return table|nil
function Adapt.LiveItem(key)
  local item, levels = Market.Item(key), LevelsOf(key)
  if not item or not levels then return item end
  return Options.Reprice(item, levels, Market.Curve(key), VendorOf(key) or 0,
                         Adapter.NowMs())
end

--- Reselect now (SPEC §4.0, §3.4D.4): this character's candidates (less those a local position executed, A81),
--- live gold and skills, the cap of A80 (the local positions' gold committed).
---@return table|nil  Reselect.Run's result; nil without a snapshot
function Adapt.Reselect()
  local s, snapshot = State(), Adapt.Snapshot()
  if not snapshot then s.result = nil; return nil end
  local start, db = Adapter.ProfileMs(), DB.Get() or {}
  local othersReserved, others = Others()
  s.result = Reselect.Run(Adapt.Unexecuted(s.rcs), { snapshot = snapshot, charId = s.charId, liveGold = Adapter.Money(),
    othersReserved = othersReserved, others = others, held = Held(db), skills = (db.professions or {})[s.charId],
    levelsOf = LevelsOf, nameOf = Adapt.NameOf, nowMs = Adapter.NowMs() })
  perf.reselectMs, perf.reselections = Adapter.ProfileMs() - start, perf.reselections + 1
  return s.result
end

--- An item's name for reasons and rows.
---@param key number
---@return string
function Adapt.NameOf(key)
  return (Adapter.ItemInfo(key % 2147483648)) or tostring(key)
end

--- Reprice what changed since the last pass, in one pass, then reselect if that could change the selection
--- (SPEC §4.0, DG2-DG5, DG7).
function Adapt.Reprice()
  local s, start = State(), Adapter.ProfileMs()
  local changed = {}
  for _, key in ipairs(s.relevant) do
    local price = LivePrice(key)
    if price ~= s.lastPrice[key] then changed[#changed + 1] = key; s.lastPrice[key] = price end
  end
  if #changed == 0 then return end
  local before = s.graph and { nodes = s.graph.work.nodes, recipes = s.graph.work.recipes } or { nodes = 0, recipes = 0 }
  local dirty = s.graph and Graph.Reprice(s.graph, s.routes, LivePrice, changed) or changed
  local touched, seen, live = {}, {}, LiveContext()
  for _, key in ipairs(dirty) do
    for _, i in ipairs(s.byKey[key] or {}) do
      if not seen[i] then
        seen[i] = true
        local old = s.rcs[i]
        s.rcs[i] = Reselect.Reprice(old.c, live)
        s.rcs[i].code = old.code
        touched[#touched + 1] = { rc = s.rcs[i], before = old }
      end
    end
  end
  local work = s.graph and s.graph.work or before
  perf.repriceMs, perf.candidates, perf.passes = Adapter.ProfileMs() - start, #touched, perf.passes + 1
  perf.nodes, perf.recipes = work.nodes - before.nodes, work.recipes - before.recipes
  if Reselect.NeedsReselect(touched, s.result) then Adapt.Reselect() end
end

--- A complete scan's book or a search result arrived: one debounced repricing pass (DG7).
function Adapt.PricesChanged()
  if st then Events.Debounce("adapt.prices", Adapt.Reprice) end
end

--- Gold or bags changed: one debounced reselection (DG5).
function Adapt.StateChanged()
  if st then Events.Debounce("adapt.reselect", Adapt.Reselect) end
end

--- A recipe learned in game that the graph doesn't have (A83): add it from its captured definition, value its crafts
--- at posting now with the addon's own craft option, and reselect. AHSync's next export includes it.
---@param charId string     The character who learned it
---@param learned table[]   { spell, product, yieldMin, yieldMax, reagents = { { key, count } }, cooldown }
---@param optionOf function recipe -> Options.CraftOptions' row for it, or nil
---@return number           Candidates added
function Adapt.Learned(charId, learned, optionOf)
  local s, added = State(), 0
  if charId ~= s.charId then return 0 end
  for _, r in ipairs(learned) do
    local yield = (r.yieldMin + r.yieldMax) / 2
    local mats = {}
    for i, m in ipairs(r.reagents) do mats[i] = { key = m[1], count = m[2] } end
    local new = not (s.graph and s.graph.recipes[r.spell]) and not Adapt.Crafts(r.product)
    if new and s.graph and Graph.AddRecipe(s.graph, r.spell, { product = r.product, yield = yield, reagents = mats }) then
      Graph.Reprice(s.graph, s.routes, LivePrice, { r.product })
      s.base[r.product] = s.routes[r.product]
    end
    local c = new and Reselect.LearnedCandidate(r.spell, r.product, optionOf(r), yield, charId) or nil
    if c then
      s.rcs[#s.rcs + 1] = { c = c, gold = c.gold, net = c.net }
      IndexCandidate(s.byKey, #s.rcs, c)
      added = added + 1
    end
  end
  s.relevant = RelevantKeys()
  for _, key in ipairs(s.relevant) do s.lastPrice[key] = s.lastPrice[key] or LivePrice(key) end
  if added > 0 then Adapt.Reselect() end
  return added
end

--- A profession window showed recipes the character didn't know: add the ones the graph lacks, outside combat
--- (A83, SPEC §5.1). Nothing is built for a character without a snapshot of this market's candidates.
---@param charId string
---@param learned table[]  Professions.NotifyLearned's recipes
function Adapt.OnLearned(charId, learned)
  if Market.CandidateCount() == 0 then return end
  Events.RunWhenSafe("adapt.learned", function() Adapt.Learned(charId, learned, Adapt.CraftOptionOf) end)
end

--- Whether one of this character's candidates already crafts an item.
---@param product number
---@return boolean
function Adapt.Crafts(product)
  for _, rc in ipairs(State().rcs) do
    if rc.c.key == product and (rc.c.strategy == "craft" or rc.c.strategy == "multistage") then return true end
  end
  return false
end

--- The current reselection for the view-model, running one the first time.
---@return table|nil  Reselect.Run's result; nil without a snapshot
function Adapt.Current()
  local s = State()
  if s.result == nil then return Adapt.Reselect() end
  return s.result
end

--- Why the graph isn't used, for the status line (FM39).
---@return string|nil  Graph.INVALID when refused; nil otherwise
function Adapt.Problem()
  return State().problem
end

--- The live state, for tests and the view-model: { charId, graph, rcs, routes, base, result, problem }.
---@return table
function Adapt.State()
  return State()
end

--- The last pass's and reselection's numbers, for /ahp perf (SPEC §5.1 "one state change ≤ 2 ms").
---@return table  { repriceMs, reselectMs, recipes, nodes, candidates, passes, reselections }
function Adapt.Perf()
  return perf
end

--- /ahp perf's lines for the live adaptation.
---@return string[]
function Adapt.PerfLines()
  return { string.format("reprice %.2f ms (%d recipes, %d items, %d candidates; %d passes)", perf.repriceMs,
                         perf.recipes, perf.nodes, perf.candidates, perf.passes),
           string.format("reselect %.2f ms (%d runs)", perf.reselectMs, perf.reselections) }
end

--- Forget the live state (tests, and a /reload's new files).
function Adapt.Reset()
  st = nil
  perf = { repriceMs = 0, reselectMs = 0, recipes = 0, nodes = 0, candidates = 0, passes = 0, reselections = 0 }
end

AHProfit.Scan.Replicate.OnBook(function() Adapt.PricesChanged() end)
Live.OnSearch(function() Adapt.PricesChanged() end)
Inventory.OnChange(function() Adapt.StateChanged() end)
Events.Register("PLAYER_MONEY", function() Adapt.StateChanged() end)
AHProfit.Capture.Professions.OnLearned(function(charId, learned) Adapt.OnLearned(charId, learned) end)
