-- Recommend/Reselect.lua: the logged-in character's part of the portfolio, reselected live (SPEC §4.0, §3.4D.4;
-- A76, A80, A83, DG5). Pure: the exported candidates (K) and the snapshot come in, with the live state the caller
-- read (gold, skills, held units, the book, the graph's route costs); the funded set, the exclusions with their
-- reasons and the totals come out. The selection itself is Engine/Portfolio.lua (the desktop's, PF15), the premium
-- Engine/Timing.lua. Recommend/Adapt.lua owns the state and the events; UI/PortfolioView.lua shows the result.

local AHProfit = AHProfit
local Graph = AHProfit.Engine.Graph
local Portfolio = AHProfit.Engine.Portfolio
local Timing = AHProfit.Engine.Timing
local Prices = AHProfit.Engine.Prices
local SellProb = AHProfit.Engine.SellProb
local Fees = AHProfit.Engine.Fees

local Reselect = {}
AHProfit.Reselect = Reselect

Reselect.SWAP_CANDIDATES = 50          -- config swap_candidates (not exported; its default, SPEC §3.6)
Reselect.SELL_WINDOW_HOURS = 8         -- config sell_window_hours: a post now covers this window
-- K's static reason codes and the words the addon shows for them (SPEC §3.4D.6; the detail lines come with M13).
Reselect.CODE_TEXT = { uncertain = "Forecast too uncertain", stale = "Forecast too stale",
                       volume = "Sales volume unknown", no_listing = "No listing left to price against",
                       liquidity = "Insufficient liquidity", missing = "Missing materials" }
-- Exclusions a price change can clear (DG5): what selection decides, a stale price and an empty book. A missing
-- profession, a thin forecast, an unknown sales rate, liquidity and missing mats can't clear by price.
Reselect.PRICE_CLEARS = { claimed = true, gold_all = true, gold_char = true, limit = true, risk = true,
                          stale = true, no_listing = true }
local BUYS_ITSELF = { flip = true, buy_later = true }     -- the output is what it buys (K lists no inputs)

--- A candidate id's parts (SPEC §3.4D.3): `<strategy>:<key>:<charId>:<route>:<batch>`, route `<reagents>/<buy>/<sale>`.
---@param id string
---@return string        The opportunity (the id without its batch)
---@return number        The batch, from 1
---@return number|nil    The buy's hour (floor(ms ÷ 3,600,000)) when it waits for a window; nil when it buys now
function Reselect.Parse(id)
  local opp, batch = id:match("^(.*):(%d+)$")
  local buy = (opp or ""):match("/h(%d+)/[^/]*$")
  return opp or id, tonumber(batch) or 1, tonumber(buy)
end

--- The character's spendable gold in this run (SPEC §4.0, A80, A64): min(live gold, the exported remaining capacity −
--- what the funded plan reserves for the other characters).
---@param liveGold number         The character's gold now, copper
---@param capacity number         The snapshot's remaining capacity, copper
---@param othersReserved number   Σ the other characters' `reserved`, copper
---@return number                 The cap, copper, at least 0
---@return number                 The capacity left for this character, capacity − others' reserved, at least 0
function Reselect.Cap(liveGold, capacity, othersReserved)
  local mine = math.max(0, capacity - othersReserved)
  return math.max(0, math.min(liveGold, mine)), mine
end

--- What buying units costs from the cheapest level up.
---@param levels table[]  { price, units } ascending (Live.Levels)
---@param qty number
---@return number|nil     Copper; nil when the book holds fewer units
---@return number         The price of the level the last unit came from (0 when none)
local function BookCost(levels, qty)
  local total, need = 0, qty
  for _, level in ipairs(levels) do
    local take = math.min(need, level.units)
    total, need = total + take * level.price, need - take
    if need <= 0 then return total, level.price end
  end
  return nil, 0
end

--- A flip's or buy-for-later's buy, repriced from the live book (A76, A77, DG5). A buy due now costs what the book
--- asks, up or down; a buy waiting for its window moves to now when the book is at or below its expected price.
--- The exported gold is taken as the buy (its expected price a unit is gold ÷ qty).
---@param rc table        The repriced candidate; gold, net, buyNow, gone, expectedEach and windowHour change
---@param levels table[]  Live levels of its item
local function RepriceBuy(rc, levels)
  local c = rc.c
  local _, _, hour = Reselect.Parse(c.id)
  local cost = BookCost(levels, c.qty)
  if hour == nil then
    if cost == nil then rc.gone = true; return end
    rc.net, rc.gold = rc.net - (cost - rc.gold), cost
  elseif cost ~= nil and cost <= c.gold then
    rc.net, rc.gold = rc.net + (rc.gold - cost), cost
    rc.buyNow, rc.expectedEach, rc.windowHour = true, c.gold / c.qty, hour
  end
end

--- E_post of a candidate's output posted now (SPEC §3.3.2), for the sale-side delta.
---@param chance number   P
---@param qty number      Units
---@param price number    Unit price, copper
---@param vendor number   Value per unsold unit, copper
---@return number         Float copper
local function PostValue(chance, qty, price, vendor)
  local deposit = Fees.DepositCopper(vendor, qty, Fees.ShortestDurationCovering(Reselect.SELL_WINDOW_HOURS))
  return SellProb.ExpectedPost(chance, qty, price, vendor, deposit)
end

--- A sale now repriced from the live book (SPEC §4.0, A19): post at Prices.PostPrice against the cheapest other
--- listing (for a flip, the level after the units it buys, Prices.RelistPrice), P from the curve row for this hour at
--- the units ahead, or SellProb.DefaultChance without a curve and volume unknown; the delta to the exported value.
---@param rc table      The repriced candidate; net changes
---@param levels table[]
---@param sale table    { item (Market.Item), curve (Market.Curve or nil), vendor (copper), nowMs, defaultP,
---                     defaultCheapest }
local function RepriceSale(rc, levels, sale)
  local c, item = rc.c, sale.item
  if not (item and item.postPrice and item.chance) then return end
  local others = levels
  if BUYS_ITSELF[c.strategy] then
    local _, last = BookCost(levels, c.qty)
    others = {}
    for _, level in ipairs(levels) do if level.price > last then others[#others + 1] = level end end
  end
  local cheapest = others[1] and others[1].price
  local price = Prices.RelistPrice(cheapest, item.med7, item.p90, item.closes30)
  if not price then return end
  local p = item.chance
  if sale.curve then
    local ahead = 0
    for _, level in ipairs(others) do if level.price <= price then ahead = ahead + level.units end end
    p = SellProb.Lookup(sale.curve.rows, sale.curve.refreshMs, sale.nowMs, ahead, sale.curve.aheadPoints)
  elseif item.volumeUnknown then
    p = SellProb.DefaultChance(price, cheapest, sale.defaultP, sale.defaultCheapest)
  end
  rc.net = rc.net + PostValue(p, c.qty, price, sale.vendor) - PostValue(item.chance, c.qty, item.postPrice, sale.vendor)
end

--- One candidate repriced by the graph and the live book (SPEC §4.0): its inputs through the route costs (DG1, DG3),
--- its buy (A76) and, when it sells now, its sale (A19). With no graph (refused, FM39) it keeps the exported values.
---@param c table      Market.DecodeCandidate's record
---@param live table   { graph (bool), base, routes (route costs), levelsOf (key -> live levels or nil), saleOf
---                    (c -> sale table for RepriceSale, or nil) }
---@return table       { c, gold, net, buyNow, gone, expectedEach, windowHour }
function Reselect.Reprice(c, live)
  local rc = { c = c, gold = c.gold, net = c.net }
  if not live.graph then return rc end
  if not BUYS_ITSELF[c.strategy] then
    rc.gold, rc.net = Graph.Repriced(c.gold, c.net, c.inputs, live.base, live.routes)
  end
  local levels = live.levelsOf(c.key)
  if not levels then return rc end
  if BUYS_ITSELF[c.strategy] then RepriceBuy(rc, levels) end
  local sale = c.windowEnd == nil and live.saleOf and live.saleOf(c) or nil
  if sale and not rc.gone then RepriceSale(rc, levels, sale) end
  return rc
end

--- Why a candidate is ineligible now, re-checked live where live state can change it (SPEC §3.4D.6, PF10, A83).
---@param c table       The candidate
---@param state table   { skills = profession -> { skill } or nil (no live snapshot: trust the export),
---                     levelsOf, horizonDays }
---@return string|nil   The status; nil when eligible (selection decides the rest)
---@return string|nil   The static reason code behind it
function Reselect.Status(c, state)
  local code = c.status
  if c.needs then
    if state.skills then
      local have = state.skills[c.needs.profession]
      local text = Portfolio.StaticReason({ profession = c.needs.profession, skill = c.needs.skill,
                                            actorSkill = have and have.skill, plannedUnits = 0 }, state.horizonDays)
      if text then return text, "profession" end
      if code == "profession" then code = nil end
    elseif code == "profession" then
      return "Required profession unavailable: " .. c.needs.profession .. " " .. c.needs.skill, "profession"
    end
  end
  local levels = state.levelsOf(c.key)
  if (code == "stale" and levels) or (code == "no_listing" and levels and levels[1]) then code = nil end
  if code and Reselect.CODE_TEXT[code] then return Reselect.CODE_TEXT[code], code end
  return nil, nil
end

--- The owned units a candidate claims (inputs from your own stock) and the resources they come from.
---@param c table          The candidate
---@param held table|nil   key -> units the character holds now; nil when unknown (no claims are checked)
---@param resources table  rid -> { rid, limit, name }; added to
---@param nameOf function  key -> item name
---@return table[]         { rid, units }
local function Claims(c, held, resources, nameOf)
  local out = {}
  if not held then return out end
  for _, input in ipairs(c.inputs) do
    if ("+" .. input.source .. "+"):find("+own+", 1, true) then
      local rid = "own:" .. c.char .. ":" .. input.key
      resources[rid] = resources[rid] or { rid = rid, limit = held[input.key] or 0, name = nameOf(input.key) }
      out[#out + 1] = { rid, input.count }
    end
  end
  return out
end

--- Keep each opportunity's batches 1..n without a gap: a batch whose predecessor is gone goes too.
---@param rcs table[]  Repriced candidates
---@return table[]     Those kept, in the same order
local function Contiguous(rcs)
  local have, out = {}, {}
  for _, rc in ipairs(rcs) do
    local opp, batch = Reselect.Parse(rc.c.id)
    have[opp .. "\0" .. batch] = true
  end
  for _, rc in ipairs(rcs) do
    local opp, batch = Reselect.Parse(rc.c.id)
    local whole = true
    for b = 1, batch - 1 do if not have[opp .. "\0" .. b] then whole = false end end
    if whole then out[#out + 1] = rc end
  end
  return out
end

--- One pick for the selection (selection.Pick): list and adjusted profit at now (SPEC §3.4D.2, D.4).
---@param rc table       The repriced candidate
---@param ctx table      As Run's
---@param resources table
---@return table         The pick
local function PickOf(rc, ctx, resources)
  local c, s = rc.c, ctx.snapshot
  local opp, batch = Reselect.Parse(c.id)
  local wait = Timing.WaitHundredths(ctx.nowMs, c.sale)
  local premium = Timing.PremiumMicro(wait, s.premiumBase, s.premiumPerDay, s.immediateHours)
  return { cand_id = c.id, opp = opp, batch = batch, char_id = c.char, lst = Timing.Immediate(wait, s.immediateHours)
           and "today" or "week", gold = rc.gold, adjusted = Timing.Adjusted(rc.net, premium), net = rc.net,
           name = ctx.nameOf(c.key), reason = rc.reason, claims = Claims(c, ctx.held, resources, ctx.nameOf) }
end

--- Reselect the character's part of the portfolio (SPEC §4.0, §3.4D.4; A77, A80, A83).
---@param rcs table[]  Repriced candidates of this character only (Reselect.Reprice); each gets reason and code
---                    (Reselect.Status, re-checked every run)
---@param ctx table    { snapshot (Plan.Portfolio), charId, liveGold, othersReserved, others = { { charId, copper } }
---                    (the other characters' cached spendable gold: only the "all characters hold" check reads it,
---                    none of their rows is selected), held, skills, levelsOf, nameOf, nowMs }
---@return table       { funded = { rc }, excluded = { { rc, reason } }, dropped = { rc } (gone, or no longer pays),
---                    picks = id -> pick, cap, capacity, capacityLeft, gold, net }
function Reselect.Run(rcs, ctx)
  local s, kept, dropped = ctx.snapshot, {}, {}
  for _, rc in ipairs(rcs) do
    if rc.gone or rc.net <= 0 then dropped[#dropped + 1] = rc else kept[#kept + 1] = rc end
  end
  kept = Contiguous(kept)
  local cap, mine = Reselect.Cap(ctx.liveGold, s.capacity, ctx.othersReserved)
  local picks, resources, byId, rids, list = {}, {}, {}, {}, {}
  local state = { skills = ctx.skills, levelsOf = ctx.levelsOf, horizonDays = s.horizonDays }
  for i, rc in ipairs(kept) do
    rc.reason, rc.code = Reselect.Status(rc.c, state)
    picks[i] = PickOf(rc, ctx, resources)
    byId[rc.c.id] = rc
  end
  for rid in pairs(resources) do rids[#rids + 1] = rid end
  table.sort(rids)
  for i, rid in ipairs(rids) do list[i] = resources[rid] end
  local spendable = { { ctx.charId, math.max(0, ctx.liveGold) } }   -- its live gold; the capacity is the other bound
  for _, other in ipairs(ctx.others or {}) do spendable[#spendable + 1] = other end
  local budget = { capacity = mine, spendable = spendable, today_limit = s.todayLimit, week_limit = s.weekLimit,
                   swap_candidates = Reselect.SWAP_CANDIDATES }
  local result = Portfolio.Select(picks, list, budget)
  Portfolio.Validate(picks, list, budget, result)
  local out = { funded = {}, excluded = {}, dropped = dropped, picks = {}, cap = cap, capacity = mine,
                capacityLeft = result.capacityLeft, gold = result.gold, net = result.net }
  for _, p in ipairs(picks) do out.picks[p.cand_id] = p end
  for _, id in ipairs(result.funded) do out.funded[#out.funded + 1] = byId[id] end
  for _, rc in ipairs(kept) do
    local why = result.reasons[rc.c.id]
    if why then out.excluded[#out.excluded + 1] = { rc = rc, reason = why } end
  end
  return out
end

--- Whether a repricing pass could change the selection (DG5): a funded row's gold or net moved, or an excluded row
--- whose reason can clear by price (or that the book made eligible again) was touched.
---@param touched table[]  { rc, before = { gold, net, gone } } for each candidate the pass recomputed
---@param last table|nil   The last Run's result; nil runs one
---@return boolean
function Reselect.NeedsReselect(touched, last)
  if not last then return true end
  local funded = {}
  for _, rc in ipairs(last.funded) do funded[rc.c.id] = true end
  for _, t in ipairs(touched) do
    local rc, before = t.rc, t.before
    local moved = rc.gold ~= before.gold or rc.net ~= before.net or (rc.gone or false) ~= (before.gone or false)
    if moved then
      if funded[rc.c.id] then return true end
      if rc.code == nil or Reselect.PRICE_CLEARS[rc.code] then return true end
    end
  end
  return false
end

--- A craft candidate for a recipe learned in game, valued at posting now from the addon's own craft option (A83).
---@param spell number
---@param product number   The item it makes
---@param option table     Options.CraftOptions' row: { crafts, matGold, profit, p, ownedUsed, bought }
---@param yield number     Average units a craft makes
---@param charId string
---@return table           A candidate in Market.DecodeCandidate's shape, learned = true; nil when nothing pays
function Reselect.LearnedCandidate(spell, product, option, yield, charId)
  if not option or (option.crafts or 0) <= 0 or option.profit <= 0 then return nil end
  local keys, inputs, route = {}, {}, {}
  for key in pairs(option.ownedUsed or {}) do keys[key] = true end
  for key in pairs(option.bought or {}) do keys[key] = true end
  local sorted = {}
  for key in pairs(keys) do sorted[#sorted + 1] = key end
  table.sort(sorted)
  for _, key in ipairs(sorted) do
    local own, ah = (option.ownedUsed or {})[key] or 0, (option.bought or {})[key] or 0
    local source = (own > 0 and ah > 0) and "own+ah" or (own > 0 and "own" or "ah")
    inputs[#inputs + 1] = { key = key, count = math.floor(own + ah + 0.5), source = source }
    route[#route + 1] = key .. ((own > 0) and "o" or "") .. ((ah > 0) and "a" or "")
  end
  local id = "craft:" .. product .. ":" .. charId .. ":" .. (#route > 0 and table.concat(route, "+") or "-") .. "/-/now:1"
  return { id = id, strategy = "craft", char = charId, key = product, qty = math.max(1, math.floor(option.crafts * yield + 0.5)),
           inputs = inputs, gold = math.floor(option.matGold + 0.5), cashBasis = 0, net = option.profit, chance = option.p,
           sale = nil, windowEnd = nil, conf = nil, needs = nil, status = nil, funded = false, learned = true, spell = spell }
end
