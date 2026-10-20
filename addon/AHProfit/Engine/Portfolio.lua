-- Engine/Portfolio.lua: the portfolio optimizer's selection, stage B (SPEC §3.4D.4-D.6).
-- Port of ahsync/engine/selection.py; keep the two formula-for-formula identical (CLAUDE.md): the same fill order,
-- D.6 checks and texts, swap pass and validation, so equal inputs fund the same ids in the same order (PF15, PF27;
-- shared scenarios: tests/scenarios/portfolio.json). Python skips swaps that provably can't win or can't fit and
-- refills only from batches that could enter; this port tries every swap and walks every batch, which gives the same
-- result (a Python test checks it). Not in the .toc yet: the addon reselects from M12 on.
-- A pick: { cand_id, opp, batch, char_id, lst = "today"|"week", gold, adjusted, net, name, reason, claims = {{rid, units}} }.

local AHProfit = AHProfit
AHProfit.Engine = AHProfit.Engine or {}

local Portfolio = {}
AHProfit.Engine.Portfolio = Portfolio

local LIST_WORDS = { today = "today", week = "this week" }
local SOFT = { claimed = true, gold_char = true, limit = true, risk = true }   -- reasons a swap may clear
local STALE_MS = 24 * 3600000
local MIN_WINDOW_CLOSES = 24       -- selection.MIN_WINDOW_CLOSES (= prices.MIN_HOURLY_CLOSES, PF30)

--- Money as WoW shows it (views.money): "1g 18s 10c", zero parts left out, "0c" for zero.
---@param copper number  Copper; rounded half up
---@return string
function Portfolio.Money(copper)
  local amount = math.floor(copper + 0.5)
  local sign = ""
  if amount < 0 then sign, amount = "−", -amount end
  local parts = {}
  local gold, silver, cop = math.floor(amount / 10000), math.floor(amount / 100) % 100, amount % 100
  if gold > 0 then parts[#parts + 1] = gold .. "g" end
  if silver > 0 then parts[#parts + 1] = silver .. "s" end
  if cop > 0 then parts[#parts + 1] = cop .. "c" end
  if #parts == 0 then return sign .. "0c" end
  return sign .. table.concat(parts, " ")
end
local Money = Portfolio.Money

--- A character's name without its realm.
---@param charId string  E.g. "Amaya-Grommash"
---@return string        E.g. "Amaya"
local function ShortName(charId)
  return (string.match(charId, "^([^-]*)"))
end

--- Whether key a sorts before key b: (tier, score) pairs compared in order (selection.order_key).
---@param a table  {tier, score}
---@param b table  {tier, score}
---@return boolean
local function KeyLess(a, b)
  if a[1] ~= b[1] then return a[1] < b[1] end
  return a[2] < b[2]
end

--- A pick's place before tie-breaks: (0, −adjusted) with no gold, else (1, −adjusted ÷ gold) (selection.order_key).
---@param p table  The pick
---@return table   {tier, score}
local function OrderKey(p)
  if p.gold <= 0 then return { 0, -p.adjusted } end
  return { 1, -p.adjusted / p.gold }
end

--- Group picks by opportunity, each group in batch order.
---@param picks table  Picks
---@return table       opp -> picks; and the opportunities sorted
---@return table
local function ByOpportunity(picks)
  local groups, opps = {}, {}
  for _, p in ipairs(picks) do
    if not groups[p.opp] then groups[p.opp] = {}; opps[#opps + 1] = p.opp end
    table.insert(groups[p.opp], p)
  end
  table.sort(opps)
  for _, opp in ipairs(opps) do
    table.sort(groups[opp], function(a, b) return a.batch < b.batch end)
    for i, p in ipairs(groups[opp]) do
      if p.batch ~= i then error("opportunity " .. opp .. " must have batches 1..n", 2) end
    end
  end
  return groups, opps
end

--- The greedy fill's order (selection.fill_order): per copper of exposure, no-gold first, ties by id; within an
--- opportunity batch n + 1 never before batch n.
---@param picks table  Picks, any order
---@return table       Picks in fill order
function Portfolio.FillOrder(picks)
  local seen = {}
  for _, p in ipairs(picks) do
    if seen[p.cand_id] then error("candidate ids must be unique", 2) end
    seen[p.cand_id] = true
  end
  local groups, opps = ByOpportunity(picks)
  local keyed = {}
  for _, opp in ipairs(opps) do
    local worst = nil
    for _, p in ipairs(groups[opp]) do
      local key = OrderKey(p)
      if worst == nil or KeyLess(worst, key) then worst = key end
      keyed[#keyed + 1] = { key = worst, p = p }
    end
  end
  table.sort(keyed, function(a, b)
    if KeyLess(a.key, b.key) or KeyLess(b.key, a.key) then return KeyLess(a.key, b.key) end
    return a.p.cand_id < b.p.cand_id
  end)
  local out, next = {}, {}
  for i, k in ipairs(keyed) do
    local n = (next[k.p.opp] or 0) + 1
    next[k.p.opp] = n
    out[i] = groups[k.p.opp][n]
  end
  return out
end

--- A fresh allocation.
---@param budget table     {capacity, spendable = {{char, copper}}}
---@param resources table  {{rid, limit, name}}
---@return table           The state
local function NewState(budget, resources)
  local s = { capacityLeft = budget.capacity, spendLeft = {}, resLeft = {}, holders = {}, counts = {}, lastBatch = {},
              funded = {} }
  for _, c in ipairs(budget.spendable) do s.spendLeft[c[1]] = c[2] end
  for _, r in ipairs(resources) do s.resLeft[r.rid] = r.limit end
  return s
end

--- A shallow copy of a map.
---@param t table
---@return table
local function CopyMap(t)
  local out = {}
  for k, v in pairs(t) do out[k] = v end
  return out
end

--- An independent copy of an allocation.
---@param s table  The state
---@return table
local function CopyState(s)
  local funded = {}
  for i, p in ipairs(s.funded) do funded[i] = p end
  return { capacityLeft = s.capacityLeft, spendLeft = CopyMap(s.spendLeft), resLeft = CopyMap(s.resLeft),
           holders = CopyMap(s.holders), counts = CopyMap(s.counts), lastBatch = CopyMap(s.lastBatch), funded = funded }
end

--- Total adjusted profit, summed in funding order.
---@param s table  The state
---@return number
local function Total(s)
  local out = 0.0
  for _, p in ipairs(s.funded) do out = out + p.adjusted end
  return out
end

--- The first claimed resource that isn't free any more (selection._claim_reason).
---@param s table      The state
---@param p table      The pick
---@param byRid table  rid -> resource
---@return string|nil
local function ClaimReason(s, p, byRid)
  for _, c in ipairs(p.claims) do
    local rid, units = c[1], c[2]
    if units > s.resLeft[rid] then
      local kind, r = string.match(rid, "^([^:]*)"), byRid[rid]
      local what = units .. " " .. r.name .. " on the AH"
      if kind == "own" then what = units .. " " .. r.name elseif kind == "cap" then what = r.name .. " capacity" end
      local holder = s.holders[rid]
      return "Resources already claimed: " .. what .. (holder and (" (" .. holder .. ")") or "")
    end
  end
  return nil
end

--- Whether a pick can be funded now; else the first failing check in D.6's order (selection.check).
---@param s table      The state
---@param p table      The pick
---@param ctx table    {byRid, budget, totalSpendable}
---@param skipClaims boolean|nil  True past the place where the fill stopped (selection._stop_index)
---@return string|nil  Category: "static", "order", "claimed", "gold_all", "gold_char", "limit", "risk"
---@return string|nil  Status text
local function Check(s, p, ctx, skipClaims)
  if p.reason ~= nil then return "static", p.reason end
  if p.batch > 1 and (s.lastBatch[p.opp] or 0) ~= p.batch - 1 then return "order", "" end
  local claimed = (not skipClaims) and ClaimReason(s, p, ctx.byRid) or nil
  if claimed then return "claimed", claimed end
  if p.gold > ctx.totalSpendable then
    return "gold_all", "Insufficient gold (needs " .. Money(p.gold) .. ", all characters hold " .. Money(ctx.totalSpendable) .. ")"
  end
  local left = s.spendLeft[p.char_id] or 0
  if p.gold > left then
    return "gold_char", "Insufficient gold on " .. ShortName(p.char_id) .. " (needs " .. Money(p.gold) .. ", has "
      .. Money(math.max(0, left)) .. ")"
  end
  local limit = (p.lst == "today") and ctx.budget.today_limit or ctx.budget.week_limit
  if s.lastBatch[p.opp] == nil and (s.counts[p.lst] or 0) >= limit then
    return "limit", "Recommendation limit reached (" .. limit .. " of " .. limit .. " " .. LIST_WORDS[p.lst] .. ")"
  end
  if p.gold > s.capacityLeft then
    return "risk", "Exceeds risk tolerance (needs " .. Money(p.gold) .. ", " .. Money(s.capacityLeft) .. " left)"
  end
  return nil
end

--- Fund a pick (selection._fund).
---@param s table  The state; changed
---@param p table  The pick
local function Fund(s, p)
  s.capacityLeft = s.capacityLeft - p.gold
  s.spendLeft[p.char_id] = (s.spendLeft[p.char_id] or 0) - p.gold
  for _, c in ipairs(p.claims) do
    s.resLeft[c[1]] = s.resLeft[c[1]] - c[2]
    if s.holders[c[1]] == nil then s.holders[c[1]] = p.name end
  end
  if s.lastBatch[p.opp] == nil then s.counts[p.lst] = (s.counts[p.lst] or 0) + 1 end
  s.lastBatch[p.opp] = p.batch
  s.funded[#s.funded + 1] = p
end

--- Take funded picks out again: one batch and its opportunity's later batches (selection._unfund).
---@param s table     The state; changed
---@param gone table  The picks
local function Unfund(s, gone)
  local ids, first = {}, math.huge
  for _, p in ipairs(gone) do
    ids[p.cand_id] = true
    first = math.min(first, p.batch)
    s.capacityLeft = s.capacityLeft + p.gold
    s.spendLeft[p.char_id] = s.spendLeft[p.char_id] + p.gold
    for _, c in ipairs(p.claims) do s.resLeft[c[1]] = s.resLeft[c[1]] + c[2] end
  end
  local kept = {}
  for _, p in ipairs(s.funded) do if not ids[p.cand_id] then kept[#kept + 1] = p end end
  s.funded = kept
  if first == 1 then
    s.lastBatch[gone[1].opp] = nil
    s.counts[gone[1].lst] = s.counts[gone[1].lst] - 1
  else
    s.lastBatch[gone[1].opp] = first - 1
  end
  s.holders = {}
  for _, p in ipairs(s.funded) do
    for _, c in ipairs(p.claims) do if s.holders[c[1]] == nil then s.holders[c[1]] = p.name end end
  end
end

--- For each place in a fill order, the least gold any pick from there on needs (selection._lowest_gold).
---@param order table  Picks in fill order
---@return table       One value per place, copper
local function LowestGold(order)
  local out, low = {}, nil
  for i = #order, 1, -1 do
    low = (low == nil) and order[i].gold or math.min(low, order[i].gold)
    out[i] = low
  end
  return out
end

--- Whether both lists hold their limit of rows (selection._full).
---@param s table    The state
---@param ctx table  Check context
---@return boolean
local function Full(s, ctx)
  return (s.counts.today or 0) >= ctx.budget.today_limit and (s.counts.week or 0) >= ctx.budget.week_limit
end

--- The greedy fill: fund every pick, in order, that passes its checks; stop once nothing left can enter (selection._fill).
---@param s table      The state; changed
---@param order table  Picks in fill order
---@param ctx table    Check context
local function Fill(s, order, ctx)
  local funded, lowest = {}, LowestGold(order)
  for _, p in ipairs(s.funded) do funded[p.cand_id] = true end
  for i, p in ipairs(order) do
    if Full(s, ctx) and s.capacityLeft < lowest[i] then return end
    if not funded[p.cand_id] and Check(s, p, ctx) == nil then
      Fund(s, p)
      funded[p.cand_id] = true
    end
  end
end

--- Each unfunded pick's exclusion category against an allocation.
---@param s table      The state
---@param order table  Picks in fill order
---@param ctx table    Check context
---@return table       cand_id -> category
local function Categories(s, order, ctx)
  local funded, out = {}, {}
  for _, p in ipairs(s.funded) do funded[p.cand_id] = true end
  for _, p in ipairs(order) do
    if not funded[p.cand_id] then out[p.cand_id] = (Check(s, p, ctx)) end
  end
  return out
end

--- The excluded picks a swap pass tries: the best `most` by adjusted profit, ties by id (selection._tried).
---@param categories table  cand_id -> category
---@param order table       Picks in fill order
---@param most number       Config swap_candidates
---@return table            cand_id -> true
local function Tried(categories, order, most)
  local soft = {}
  for _, p in ipairs(order) do if SOFT[categories[p.cand_id] or ""] then soft[#soft + 1] = p end end
  table.sort(soft, function(a, b)
    if a.adjusted ~= b.adjusted then return a.adjusted > b.adjusted end
    return a.cand_id < b.cand_id
  end)
  local out = {}
  for i = 1, math.min(most, #soft) do out[soft[i].cand_id] = true end
  return out
end

--- The best swap for one funded pick, or nil (selection._swap_pass's inner loop, without pruning).
---@param s table      The state
---@param f table      The funded pick to swap out
---@param order table  Picks in fill order
---@param ctx table    Check context
---@return table|nil   The allocation after the best swap
---@return number      Its total
local function BestSwap(s, f, order, ctx)
  local gone = {}
  for _, p in ipairs(s.funded) do if p.opp == f.opp and p.batch >= f.batch then gone[#gone + 1] = p end end
  local tried = Tried(Categories(s, order, ctx), order, ctx.budget.swap_candidates or 50)
  local bestTotal, bestId, bestState = Total(s), nil, nil
  for _, x in ipairs(order) do
    if tried[x.cand_id] and x.opp ~= f.opp then
      local trial = CopyState(s)
      Unfund(trial, gone)
      if Check(trial, x, ctx) == nil then
        Fund(trial, x)
        Fill(trial, order, ctx)
        local total = Total(trial)
        if total > bestTotal or (bestId ~= nil and total == bestTotal and x.cand_id < bestId) then
          bestTotal, bestId, bestState = total, x.cand_id, trial
        end
      end
    end
  end
  return bestState, bestTotal
end

--- One swap pass with greedy refill (selection._swap_pass).
---@param s table      The greedy allocation
---@param order table  Picks in fill order
---@param ctx table    Check context
---@return table       The allocation after the pass
local function SwapPass(s, order, ctx)
  local snapshot = {}
  for i, p in ipairs(s.funded) do snapshot[i] = p end
  for _, f in ipairs(snapshot) do
    local still = false
    for _, p in ipairs(s.funded) do if p == f then still = true end end
    if still then
      local current = Total(s)
      local best, total = BestSwap(s, f, order, ctx)
      if best ~= nil and total > current then s = best end
    end
  end
  return s
end

--- Every unfunded pick's D.6 status against the final allocation (selection._reasons).
---@param s table      The final state
---@param order table  Picks in fill order
---@param ctx table    Check context
---@return table       cand_id -> status
local function Reasons(s, order, ctx)
  local funded, rest, byBatch, out, place = {}, {}, {}, {}, {}
  for _, p in ipairs(s.funded) do funded[p.cand_id] = true end
  for i, p in ipairs(order) do
    byBatch[p.opp .. "\0" .. p.batch] = p
    place[p.cand_id] = i
    if not funded[p.cand_id] then rest[#rest + 1] = p end
  end
  local stop = #order + 1
  if Full(s, ctx) then
    local lowest = LowestGold(order)
    for i = 1, #order do if s.capacityLeft < lowest[i] then stop = i; break end end
  end
  table.sort(rest, function(a, b) if a.opp ~= b.opp then return a.opp < b.opp end return a.batch < b.batch end)
  for _, p in ipairs(rest) do
    local category, text = Check(s, p, ctx, place[p.cand_id] >= stop)
    if category == nil then error(p.cand_id .. " fits but was left out", 2) end
    if category == "order" then text = out[byBatch[p.opp .. "\0" .. (p.batch - 1)].cand_id] end
    out[p.cand_id] = text
  end
  return out
end

--- Choose the funded picks: greedy fill, one swap pass, reasons (selection.select).
---@param picks table      Picks, any order
---@param resources table  {{rid, limit, name}}
---@param budget table     {capacity, spendable = {{char, copper}}, today_limit, week_limit}
---@return table           {funded = {cand_id...} in funding order, reasons = {cand_id -> status}, capacityLeft,
---                        adjusted, net, gold = the funded picks' totals}
function Portfolio.Select(picks, resources, budget)
  local byRid, total = {}, 0
  for _, r in ipairs(resources) do byRid[r.rid] = r end
  for _, p in ipairs(picks) do
    if p.lst ~= "today" and p.lst ~= "week" then error(p.cand_id .. ": list must be today or week", 2) end
    for _, c in ipairs(p.claims) do if not byRid[c[1]] then error(p.cand_id .. " claims an unknown resource", 2) end end
  end
  for _, c in ipairs(budget.spendable) do total = total + c[2] end
  local ctx = { byRid = byRid, budget = budget, totalSpendable = total }
  local order = Portfolio.FillOrder(picks)
  local state = NewState(budget, resources)
  Fill(state, order, ctx)
  state = SwapPass(state, order, ctx)
  local funded, net, gold = {}, 0.0, 0
  for i, p in ipairs(state.funded) do
    funded[i], net, gold = p.cand_id, net + p.net, gold + p.gold
  end
  return { funded = funded, reasons = Reasons(state, order, ctx), capacityLeft = state.capacityLeft,
           adjusted = Total(state), net = net, gold = gold }
end

--- D.5's totals and completeness: totals sum the funded picks only; every pick is funded or excluded, never both.
---@param picks table   Picks
---@param byId table    cand_id -> pick
---@param result table  From Select
local function ValidateTotals(picks, byId, result)
  local adjusted, net, gold, seen = 0.0, 0.0, 0, 0
  for _, id in ipairs(result.funded) do
    local p = byId[id]
    adjusted, net, gold, seen = adjusted + p.adjusted, net + p.net, gold + p.gold, seen + 1
    if result.reasons[id] ~= nil then error(id .. " is both funded and excluded", 3) end
  end
  for _ in pairs(result.reasons) do seen = seen + 1 end
  if seen ~= #picks then error("every pick must be funded or excluded", 3) end
  if adjusted ~= result.adjusted or net ~= result.net or gold ~= result.gold then
    error("a total is not the sum of the funded picks", 3)
  end
end

--- Check an allocation before it is used (selection.validate, SPEC §3.4D.5); errors on any violation.
---@param picks table      Picks
---@param resources table  Resources
---@param budget table     The budget
---@param result table     From Select
function Portfolio.Validate(picks, resources, budget, result)
  local byId, left, spend, gold, rows, last = {}, {}, {}, 0, { today = {}, week = {} }, {}
  for _, p in ipairs(picks) do byId[p.cand_id] = p end
  for _, r in ipairs(resources) do left[r.rid] = r.limit end
  for _, c in ipairs(budget.spendable) do spend[c[1]] = c[2] end
  local counts = { today = 0, week = 0 }
  for _, id in ipairs(result.funded) do
    local p = byId[id]
    if p.reason ~= nil then error(id .. " is funded but ineligible", 2) end
    if p.batch ~= (last[p.opp] or 0) + 1 then error(p.opp .. ": a later batch is funded without an earlier one", 2) end
    last[p.opp] = p.batch
    gold = gold + p.gold
    spend[p.char_id] = (spend[p.char_id] or 0) - p.gold
    if p.gold > 0 and spend[p.char_id] < 0 then error("a character's funded gold is over its spendable gold", 2) end
    for _, c in ipairs(p.claims) do
      left[c[1]] = left[c[1]] - c[2]
      if left[c[1]] < 0 then error(c[1] .. " is claimed beyond its limit", 2) end
    end
    if not rows[p.lst][p.opp] then rows[p.lst][p.opp] = true; counts[p.lst] = counts[p.lst] + 1 end
  end
  if gold > budget.capacity or result.capacityLeft ~= budget.capacity - gold then
    error("the funded gold is over the remaining capacity", 2)
  end
  if counts.today > budget.today_limit or counts.week > budget.week_limit then error("a list holds more rows than its limit", 2) end
  ValidateTotals(picks, byId, result)
end

--- A sales rate with one decimal, half up, without a trailing .0 (selection.per_day).
---@param rate number  Units a day
---@return string
local function PerDay(rate)
  local tenths = math.floor(rate * 10 + 0.5)
  if tenths % 10 == 0 then return tostring(math.floor(tenths / 10)) end
  return math.floor(tenths / 10) .. "." .. (tenths % 10)
end

--- A price's age: whole hours under 48 h, else whole days (selection.age_text).
---@param ageMs number
---@return string
local function AgeText(ageMs)
  local hours = math.floor(ageMs / 3600000)
  if hours < 48 then return hours .. " h" end
  return math.floor(hours / 24) .. " days"
end

--- The first static check a pick fails, in D.6's order (selection.static_reason, PF24, PF30).
---@param e table             {profession, skill, actorSkill, untrustedWeeks, priceAgeMs, soldPerDay, plannedUnits,
---                           missingUnits, missingName, windowCloses, volumeUnknown, noListing}; windowCloses is
---                           nil for a sale now; volumeUnknown is nil when the rate is known, "" for the item's own,
---                           else a mat; noListing: it relists into an empty book on thin data (R28)
---@param horizonDays number  Config horizon_days
---@return string|nil         The status; nil when every static check passes
function Portfolio.StaticReason(e, horizonDays)
  if e.profession ~= nil and (e.actorSkill == nil or e.actorSkill < e.skill) then
    return "Required profession unavailable: " .. e.profession .. " " .. e.skill
  end
  if e.windowCloses ~= nil and e.windowCloses < MIN_WINDOW_CLOSES then
    return "Forecast too uncertain (" .. e.windowCloses .. " close" .. (e.windowCloses ~= 1 and "s" or "")
      .. " in 30 d, needs " .. MIN_WINDOW_CLOSES .. ")"
  end
  if e.untrustedWeeks ~= nil then
    return "Forecast too uncertain (" .. e.untrustedWeeks .. " week" .. (e.untrustedWeeks ~= 1 and "s" or "") .. " of data)"
  end
  if e.priceAgeMs ~= nil and e.priceAgeMs > STALE_MS then return "Forecast too stale (prices " .. AgeText(e.priceAgeMs) .. " old)" end
  if e.volumeUnknown == "" then return "Sales volume unknown" end
  if e.volumeUnknown ~= nil then return "Sales volume unknown (" .. e.volumeUnknown .. ")" end
  if e.noListing then return "No listing left to price against" end
  if e.soldPerDay ~= nil and e.plannedUnits > horizonDays * e.soldPerDay then
    return "Insufficient liquidity: " .. PerDay(e.soldPerDay) .. "/day, " .. e.plannedUnits .. " planned"
  end
  if e.missingUnits ~= nil then return "Missing materials: " .. e.missingUnits .. " " .. e.missingName end
  return nil
end
