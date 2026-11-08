--- The mockups' portfolio world as the addon files carry it (SPEC §3.4D, §3.5; A76-A80, DG1-DG7), for the live
--- adaptation specs. Monday 10:38, Amaya 21 g and Brugan 10 g 40 s, capacity 6 g 49 s 40 c, Brugan's reserved 3 g 73 s:
--- Amaya's ten candidates of PF15 (seven funded) plus two of Brugan's, the copper chain (a rod is two bars, as DG1
--- and DG3 count it) and Bolt of Silk Cloth as the graph. Files are built as tables with their checksums, like
--- tests/support/mockup_export.py's real export, but each spec can change a row before installing them.

local World = {}

World.M = "forever.pvp.horde.us"
World.AMAYA, World.BRUGAN = "Amaya-Grommash", "Brugan-Grommash"
World.HOUR, World.DAY = 3600000, 86400000
World.MON = 1728259200000                      -- Mon 2024-10-07 00:00 UTC
World.NOW = World.MON + 10 * World.HOUR + 38 * 60000
World.TONIGHT = World.MON + 18 * World.HOUR
World.ORE, World.BAR, World.ROD, World.RUNED, World.ESSENCE, World.DUST = 2770, 2840, 6217, 6218, 10938, 10940
World.SILK, World.BOLT, World.THICK, World.LINEN, World.BRACERS, World.WOOL = 4306, 4305, 4304, 2589, 6187, 2592

--- A sale time in the mockup's week.
---@param day number   Days after Monday
---@param hour number  Hour of that day, UTC
---@return number      UTC ms
function World.At(day, hour)
  return World.MON + day * World.DAY + hour * World.HOUR
end

--- One K string (SPEC §3.5).
---@param c table  { id, strategy, char, key, qty, inputs (string), gold, net, chance1000 (default 900), sale, windowEnd,
---                needs, status }
---@return string
function World.K(c)
  return table.concat({ c.id, c.strategy, c.char, c.key, c.qty or 1, c.inputs or "", c.gold, c.cashBasis or 0, c.net,
                        c.chance1000 or 900, c.sale, c.windowEnd or "", 80, c.needs or "", c.status or "" }, ";")
end

--- The candidates, Amaya's first (PF15's rows), then two of Brugan's.
---@return table[]  World.K's argument per candidate
function World.Candidates()
  local A, B, T = World.AMAYA, World.BRUGAN, World.TONIGHT
  local thick, runed, silk, bolt = World.At(3, 19), World.At(6, 18), World.At(5, 19), World.At(6, 19)
  return {
    { id = "flip:7005:" .. A .. ":-/now/now:1", strategy = "flip", char = A, key = 7005, gold = 800, net = 625, sale = T },
    { id = "craft:4239:" .. A .. ":2318o+2320v/-/now:1", strategy = "craft", char = A, key = 4239,
      inputs = "2318:3:own/2320:2:vendor", gold = 500, net = 380, sale = T, needs = "Leatherworking:55" },
    { id = "flip:2589:" .. A .. ":-/now/now:1", strategy = "flip", char = A, key = World.LINEN, qty = 20, gold = 760,
      net = 203, sale = T },
    { id = "disenchant:6187:" .. A .. ":-/now/now:1", strategy = "disenchant", char = A, key = World.BRACERS,
      inputs = "6187:1:ah", gold = 1500, net = 600, sale = T, needs = "Enchanting:150", status = "profession" },
    { id = "buy_later:3825:" .. A .. ":-/now/now:1", strategy = "buy_later", char = A, key = 3825, qty = 40, gold = 12000,
      net = 900, sale = T, status = "liquidity" },
    { id = "buy_later:4304:" .. A .. ":-/now/h" .. thick / World.HOUR .. ":1", strategy = "buy_later", char = A,
      key = World.THICK, qty = 30, gold = 16080, net = 5500, sale = thick, windowEnd = thick + 3 * World.HOUR },
    { id = "multistage:6218:" .. A .. ":6217c+10938a+10940a/-/h" .. runed / World.HOUR .. ":1", strategy = "multistage",
      char = A, key = World.RUNED, inputs = "6217:1:craft/10938:1:ah/10940:1:ah", gold = 3700, net = 1630, sale = runed,
      windowEnd = runed + 3 * World.HOUR, needs = "Enchanting:1" },
    { id = "buy_later:4306:" .. A .. ":-/now/h" .. silk / World.HOUR .. ":1", strategy = "buy_later", char = A,
      key = World.SILK, qty = 20, gold = 2800, net = 730, sale = silk, windowEnd = silk + 3 * World.HOUR },
    { id = "craft:4305:" .. A .. ":4306o/-/h" .. bolt / World.HOUR .. ":1", strategy = "craft", char = A,
      key = World.BOLT, inputs = "4306:4:own", gold = 560, net = 194, sale = bolt, windowEnd = bolt + 3 * World.HOUR,
      needs = "Tailoring:125" },
    { id = "buy_later:11082:" .. A .. ":-/now/h" .. bolt / World.HOUR .. ":1", strategy = "buy_later", char = A,
      key = 11082, gold = 3600, net = 1200, sale = bolt, windowEnd = bolt + 3 * World.HOUR, status = "uncertain" },
    { id = "flip:7969:" .. B .. ":-/now/h" .. World.At(5, 18) / World.HOUR .. ":1", strategy = "flip", char = B,
      key = 7969, gold = 30000, net = 7342, sale = World.At(5, 18), windowEnd = World.At(5, 21) },
    { id = "sell_stock:6217:" .. B .. ":-/-/now:1", strategy = "sell_stock", char = B, key = World.ROD, qty = 5,
      inputs = "6217:5:own", gold = 100, cashBasis = 5000, net = 6306, sale = T },
  }
end

--- One item string with a price (SPEC §3.5 `I`).
---@param cur number|nil  Its current price, copper
---@param extra table|nil { postPrice, sellValue, chance1000, supply, refStack, closes30, med7, p90, sold10 (units sold in
---                       10 days, default 10), sell (its sell windows, "start:hours:best:how1000:weeks/...") }
---@return string
function World.Item(cur, extra)
  local e = extra or {}
  local fields = { cur or "", 100, 60, e.med7 or "", "", "", e.p90 or "", 0, e.sold10 or 10, 10, e.supply or "", 80, "",
                   e.postPrice or "", e.refStack or 1, e.sellValue or "", 0, e.chance1000 or "", "", 0, e.closes30 or 100 }
  return table.concat(fields, ",") .. ";" .. (e.sell or "") .. ";"
end

-- The mockup's item names (docs/mockups/addon.html), set by World.Name and cleared by World.Unname.
World.NAMES = { [7005] = "Scouting Gloves of the Monkey", [4239] = "Embossed Leather Gloves",
                [6187] = "Grunt's Bracers of Defense", [3825] = "Elixir of Fortitude", [4304] = "Thick Leather",
                [4305] = "Bolt of Silk Cloth", [11082] = "Greater Magic Essence", [4338] = "Mageweave Cloth",
                [2319] = "Medium Leather", [4234] = "Heavy Leather", [3385] = "Lesser Mana Potion", [7969] = "Mithril Spurs" }

--- Give the mockup's items their names in the client's cache.
---@param Stubs table
function World.Name(Stubs)
  for key, name in pairs(World.NAMES) do Stubs.ITEM_NAMES[key] = name end
end

--- Take the mockup's names out of the client's cache again.
---@param Stubs table
function World.Unname(Stubs)
  for key in pairs(World.NAMES) do Stubs.ITEM_NAMES[key] = nil end
end

--- The mockup's Portfolio tab for Amaya (A79): Scouting Gloves and Embossed Leather Gloves are disenchants, as the
--- mockup shows them; the market file knows the elixir sells 3 a day and the essence's Sunday peak was seen once.
---@return table  World.Install's options
function World.Mockup()
  local c = World.Candidates()
  c[1].id, c[1].strategy, c[1].inputs = "disenchant:7005:" .. World.AMAYA .. ":-/now/now:1", "disenchant", "7005:1:ah"
  c[2].id, c[2].strategy, c[2].inputs, c[2].needs = "disenchant:4239:" .. World.AMAYA .. ":-/now/now:1", "disenchant",
                                                     "4239:1:ah", nil
  local A = World.AMAYA
  return { candidates = c,
           items = { [3825] = World.Item(300, { sold10 = 30 }), [11082] = World.Item(900, { sell = "162:3:163:1100:1" }) },
           positions = { "pos:" .. A .. ":4338;4338;Partially sold;25;10;3000;600;;post",
                         "stock:" .. A .. ":2592;2592;Holding;20;20;0;472;;hold",
                         "pos:" .. A .. ":2319;2319;Needs reconciliation;20;20;4000;;;" } }
end

--- The graph: the copper chain (a rod is two bars) and Bolt of Silk Cloth, in the exported format.
---@return table  G
function World.G()
  return { R = { [2657] = "2840:1000:2770*1", [7818] = "6217:1000:2840*2", [7421] = "6218:1000:6217*1/10938*1/10940*1",
                 [3839] = "4305:1000:4306*4" },
           U = { [2770] = "2657", [2840] = "7818", [6217] = "7421", [10938] = "7421", [10940] = "7421", [4306] = "3839" },
           DE = "6187", order = "2770,2840,6217,4306,4305,6187,10938,10940,6218" }
end

--- The market file's checksum (Core/Market.lua's rule).
---@param markets table
---@return string
local function MarketChecksum(markets)
  local count, length = 0, 0
  for _, m in pairs(markets) do
    for _, t in pairs(m.I or {}) do count, length = count + 1, length + #t end
    for _, t in pairs(m.C or {}) do count, length = count + 1, length + #t end
    for _, t in pairs(m.D or {}) do count, length = count + 1, length + #t end
    for _, t in ipairs(m.K or {}) do count, length = count + 1, length + #t end
    for _, t in pairs(m.G.R) do count, length = count + 1, length + #t end
    for _, t in pairs(m.G.U) do count, length = count + 1, length + #t end
    for _, t in ipairs({ m.G.DE, m.G.order }) do if t ~= "" then count, length = count + 1, length + #t end end
  end
  return count .. ":" .. length
end

--- The plan file's checksum (Core/Plan.lua's rule).
---@param f table
---@return string
local function PlanChecksum(f)
  local count, length = 0, 0
  for _, list in pairs(f.actions) do for _, t in ipairs(list) do count, length = count + 1, length + #t end end
  for _, t in pairs(f.portfolio) do count, length = count + 1, length + #t end
  for _, t in pairs(f.gold) do count, length = count + 1, length + #t end
  for _, c in pairs(f.reserved) do count, length = count + 1, length + #tostring(c) end
  for _, list in pairs(f.positions) do for _, t in ipairs(list) do count, length = count + 1, length + #t end end
  return count .. ":" .. length
end

--- Install both addon files as globals.
---@param opts table|nil  { candidates (World.Candidates() changed), items = key -> I string, G, snapshot (string),
---                       positions (Amaya's position strings), reserved = charId -> copper, actions (Amaya's action
---                       strings) }
function World.Install(opts)
  local o = opts or {}
  local K = {}
  for i, c in ipairs(o.candidates or World.Candidates()) do K[i] = World.K(c) end
  local market = { observationsUntil = World.NOW - World.HOUR, curveHour = math.floor(World.NOW / World.HOUR),
                   I = o.items or {}, C = {}, K = K, G = o.G or World.G() }
  local markets = { [World.M] = market }
  _G.AHProfitMarket = { schemaVersion = 1, exportVersion = 5, generatedAt = World.NOW,
                        charMarkets = { [World.AMAYA] = World.M, [World.BRUGAN] = World.M }, markets = markets,
                        checksum = MarketChecksum(markets) }
  local plan = { schemaVersion = 1, exportVersion = 5, generatedAt = World.NOW, basedOnUntil = World.NOW,
                 ingestedUntil = { [World.AMAYA] = World.NOW }, actions = { [World.AMAYA] = o.actions }, rules = {},
                 portfolio = { [World.M] = o.snapshot or (World.NOW .. ";300;150;50;8;20;20;7;1;355800;41800;64940;62500") },
                 gold = { [World.AMAYA] = "210000," .. World.NOW, [World.BRUGAN] = "104000," .. World.NOW },
                 reserved = o.reserved or { [World.AMAYA] = 25200, [World.BRUGAN] = 37300 },
                 positions = { [World.AMAYA] = o.positions or { "stock:" .. World.AMAYA .. ":2592;2592;Holding;20;20;0;472;;post" } } }
  plan.checksum = PlanChecksum(plan)
  _G.AHProfitPlan = plan
end

--- Amaya's professions as the profession capture stores them.
---@return table
function World.Skills()
  return { Enchanting = { skill = 142 }, Leatherworking = { skill = 60 }, Tailoring = { skill = 130 } }
end

World.BAGS = { { 2318, 3 }, { 4306, 4 } }      -- the Light Leather and Silk Cloth her crafts use from stock

--- Log Amaya in to the world: the files installed, her gold, professions and bags (SPEC §2.2 capture).
---@param Stubs table     wow_stubs
---@param opts table|nil  World.Install's, plus { money (copper, default 21 g), nowMs, bags = { { id, count } }
---                       (default World.BAGS), player (default "Amaya"), keepSaved (a /reload: SavedVariables stay) }
function World.Boot(Stubs, opts)
  local o = opts or {}
  Stubs.Reset(o.keepSaved and { keepSaved = true } or nil)
  Stubs.world.player.name = o.player or "Amaya"
  Stubs.world.serverTime = math.floor((o.nowMs or World.NOW) / 1000)
  Stubs.world.money = o.money or 210000
  Stubs.world.skillLines = { { name = "Professions", header = true }, { name = "Enchanting", rank = 142, max = 150 },
                             { name = "Leatherworking", rank = 60, max = 150 }, { name = "Tailoring", rank = 130, max = 150 } }
  local slots = {}
  for i, b in ipairs(o.bags or World.BAGS) do slots[i] = { id = b[1], count = b[2] } end
  Stubs.world.containers = { [0] = { size = 16, slots = slots } }
  World.Install(o)
  Stubs.Login()
end

--- Make a complete scan's book the live one and tell its listeners, as a finished scan does (SPEC §4.2).
---@param Stubs table
---@param book table   key -> World.Book(...)
function World.Scan(Stubs, book)
  AHProfit.Scan.Replicate.LastBook = function() return { at = Stubs.world.serverTime * 1000, book = book } end
  AHProfit.Scan.Replicate.NotifyBook()
end

--- A live book entry for Live/Replicate: price -> { [stack * 5 + band] = count }.
---@param levels table[]  { price, units } each, listed as one stack per level
---@return table
function World.Book(levels)
  local out = {}
  for _, l in ipairs(levels) do out[l[1]] = { [l[2] * 5 + 4] = 1 } end
  return out
end

return World
