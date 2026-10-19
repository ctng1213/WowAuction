-- Recommend/Options.lua: the addon's own recommendations (SPEC §4.0), so it works without AHSync.
-- They choose between options AHSync precomputed in MarketIntelligence.lua, for the state the
-- addon sees right now; no history, no simulation:
-- - crafts from what you carry, what the AH lists and what vendors sell, for your known recipes (A9, A20, A30);
-- - the rest of what you carry: posting now (its sell value and chance) or a merchant (A15-A19);
-- each unless AHSync's plan already covers it. A mat a craft uses is not also offered for sale.

local AHProfit = AHProfit
local Fees = AHProfit.Engine.Fees
local Prices = AHProfit.Engine.Prices
local SellProb = AHProfit.Engine.SellProb
local Live = AHProfit.Live
local Craft = AHProfit.Engine.Craft

local Options = {}
Options.SELL_WINDOW_HOURS = 8      -- config sell_window_hours (SPEC §3.6): the posting-now window
Options.MAX_SUPPLY_SHARE = 0.5     -- config max_supply_share (SPEC §3.4 Liquidity)
AHProfit.Options = Options

--- Total units per item of the unbound stacks in a list of bag rows.
---@param bags table[]  { key, qty, bound } (Inventory.Decode)
---@return table        key -> units
function Options.Units(bags)
  local units = {}
  for _, row in ipairs(bags) do
    if not row.bound then units[row.key] = (units[row.key] or 0) + row.qty end
  end
  return units
end

--- An item's sell option re-priced from live listings (A19), as analysis prices it (market.analyze_item):
--- post at min(cheapest − 1, med7), P from the curve row for this hour at the units ahead, and
--- E_post per unit with the vendor price as the fallback and the deposit for the window.
---@param item table          Market.Item(key)
---@param levels table[]      Live.Levels for the item
---@param curve table|nil     Market.Curve(key)
---@param vendorEach number   Merchant price, copper
---@param nowMs number
---@return table              A copy of item with postPrice, sellValue and chance from the live book
function Options.Reprice(item, levels, curve, vendorEach, nowMs)
  local live = {}
  for k, v in pairs(item) do live[k] = v end
  local price = Prices.PostPrice(levels[1] and levels[1].price, item.med7)
  if not price then return live end
  local ref = item.refStack or 1
  local p = item.chance or 0
  if curve then p = SellProb.Lookup(curve.rows, curve.refreshMs, nowMs, Live.Ahead(levels, price), curve.aheadPoints) end
  local deposit = Fees.DepositCopper(vendorEach, ref, Fees.ShortestDurationCovering(Options.SELL_WINDOW_HOURS))
  live.postPrice, live.chance = price, p
  live.sellValue = SellProb.ExpectedPost(p, ref, price, vendorEach, deposit) / ref
  live.live = true
  return live
end

--- The best way to sell one item you carry, or nil when neither pays (A15, A17).
---@param key number           Item key
---@param qty number           Units carried
---@param item table|nil       Market.Item(key)
---@param vendorEach number|nil  What a merchant pays for one, copper
---@param nowMs number
---@param charId string
---@return table|nil           A plan-shaped action (kind post | vendor), own = true
function Options.SellOne(key, qty, item, vendorEach, nowMs, charId)
  if not item then return nil end
  local postEach, vendor = item.sellValue, vendorEach or 0
  local action = { id = "own:" .. key, key = key, qty = qty, at = nowMs, status = "pending", char = charId, own = true }
  if postEach and item.postPrice and postEach >= vendor and postEach > 0 then
    action.kind, action.price, action.net = "post", item.postPrice, postEach * qty
    action.duration = Fees.ShortestDurationCovering(Options.SELL_WINDOW_HOURS)        -- A56
    action.chance = item.chance and math.floor(item.chance * 100 + 0.5) or nil
    action.ownNote = item.live and "post now at the live price · AHSync has no plan for it"
                     or "post now · AHSync has no plan for it"
    return action
  end
  if vendor > 0 then
    action.kind, action.price, action.net = "vendor", vendor, vendor * qty
    action.ownNote = "a merchant pays more than the AH"
    return action
  end
  return nil
end

--- The addon's own sell actions for what a character carries (A15-A20).
---@param units table  key -> unbound units carried (Options.Units)
---@param ctx table    { planned = key -> true (AHSync covers it), reserved = key -> units crafts use,
---                    itemOf, vendorOf (key -> merchant price), vendorBuyOf (key -> vendor price; optional),
---                    nowMs, charId }
---@return table[]     Plan-shaped actions, by key
function Options.Sell(units, ctx)
  local keys, out, reserved = {}, {}, ctx.reserved or {}
  for key, qty in pairs(units) do
    if not ctx.planned[key] and qty - (reserved[key] or 0) > 0 then keys[#keys + 1] = key end
  end
  table.sort(keys)
  for _, key in ipairs(keys) do
    -- A vendor-sold item is never posted: only a merchant sale is offered (R23, A35).
    local vendorSold = ctx.vendorBuyOf and ctx.vendorBuyOf(key)
    local action = Options.SellOne(key, units[key] - (reserved[key] or 0), (not vendorSold) and ctx.itemOf(key) or {},
                                   ctx.vendorOf(key), ctx.nowMs, ctx.charId)
    if action then out[#out + 1] = action end
  end
  return out
end

--- The character's known recipes that make an item, from SavedVariables.
---@param db table      AHProfitDB
---@param charId string
---@return table[]      { spell, product, yieldMin, yieldMax, cooldown, reagents = { { key, count } } }, by spell
function Options.KnownRecipes(db, charId)
  local known, out = {}, {}
  for spell in ((db.knownRecipes or {})[charId] or ""):gmatch("%d+") do known[tonumber(spell)] = true end
  for _, definitions in pairs(db.recipes or {}) do
    for spell, r in pairs(definitions) do
      if known[spell] and r.product then
        local reagents = {}
        for key, count in (r.reagents or ""):gmatch("(%-?%d+),(%d+)") do reagents[#reagents + 1] = { tonumber(key), tonumber(count) } end
        out[#out + 1] = { spell = spell, product = r.product, yieldMin = r.yieldMin or 1, yieldMax = r.yieldMax or r.yieldMin or 1,
                          cooldown = r.cooldown, reagents = reagents, crafters = 1 }
      end
    end
  end
  table.sort(out, function(a, b) return a.spell < b.spell end)
  return out
end

--- n -> expected net of n crafts' output posted now (recommend.pages._product_net). The addon has no
--- queue simulation: it uses the product's posting-now chance for any quantity, so it equals the
--- desktop's when that chance doesn't depend on how much is posted (C1-C3).
---@param recipe table
---@param item table|nil   The product's market item
---@param vendorSell number  What a merchant pays for one product
---@return function, number|nil  The function, and the chance it uses (nil without a price)
function Options.ProductNet(recipe, item, vendorSell)
  local avg = (recipe.yieldMin + recipe.yieldMax) / 2
  local p = item and item.postPrice and (item.chance or 0) or nil
  local duration = Fees.ShortestDurationCovering(Options.SELL_WINDOW_HOURS)
  local cache = { [0] = 0.0 }
  return function(n)
    if cache[n] == nil then
      local units = math.max(1, math.floor(n * avg + 0.5))
      if p == nil then
        cache[n] = units * vendorSell
      else
        cache[n] = SellProb.ExpectedPost(p, units, item.postPrice, vendorSell, Fees.DepositCopper(vendorSell, units, duration))
      end
    end
    return cache[n]
  end, p
end

--- The most crafts worth making for the posting-now window: its output may fill at most
--- max_supply_share of what others list, and always one reference stack (capacity.liquidity_cap,
--- the posting-now window without a supply pattern; SPEC §3.4 Liquidity, A30).
--- Units of the product you already hold count against it (A36).
---@param recipe table      { yieldMin, yieldMax }
---@param item table|nil    The product's market item (supply, refStack)
---@param held number|nil   Units of the product you hold
---@param nowMs number|nil  Now, for the supply factor of this hour (A62); nil applies none
---@return number|nil       Crafts, 0 when what you hold fills the cap; nil (no cap) when neither the current nor
---                          the typical supply is known
function Options.CraftCap(recipe, item, held, nowMs)
  local supply = item and (item.supply or item.typicalSupply)          -- typical daily supply as a fallback (A57)
  if not supply then return nil end
  supply = supply * Options.SupplyFactor(item, nowMs)                  -- over the window, not just now (A62)
  local units = math.max(item.refStack or 1, math.floor(Options.MAX_SUPPLY_SHARE * supply)) - (held or 0)
  if units <= 0 then return 0 end
  return math.max(1, math.floor(units / ((recipe.yieldMin + recipe.yieldMax) / 2)))
end

Options.SUSPECT_LOW, Options.SUSPECT_HIGH = 0.1, 10   -- engine.prices: below 10% of p10 or above 10× p90 (FM5)

--- Whether a listing's price may be bought from (SPEC §5.2, FM5, A58): not far out of the item's usual
--- range, unless AHSync already confirmed such a price (its cur is out of range and not suspect).
---@param item table|nil  The mat's market item (p10, p90, cur, suspect)
---@param price number    Copper each
---@return boolean
function Options.Buyable(item, price)
  if not (item and item.p10 and item.p90) then return true end
  --- Whether a price is out of the item's usual range.
  ---@param p number  Copper
  ---@return boolean
  local function Outside(p) return p < Options.SUSPECT_LOW * item.p10 or p > Options.SUSPECT_HIGH * item.p90 end
  if not Outside(price) then return true end
  return item.cur ~= nil and Outside(item.cur) and item.suspect == 0
end

--- The supply factor over the posting-now window for this hour, from the item's S row (SPEC §3.4 Liquidity, A62).
---@param item table        A market item (supplyRow, curveHour)
---@param nowMs number|nil  Now, UTC ms
---@return number           The factor (5% steps); 1 without a row or a time
function Options.SupplyFactor(item, nowMs)
  if not (item.supplyRow and item.curveHour and nowMs) then return 1 end
  local code = item.supplyRow:byte(SellProb.CurveRow(item.curveHour * 3600000, nowMs) + 1)
  return code and (code - 35) / 20 or 1
end

--- Where a craft's mats can come from now: what you carry at its hold value, the AH and vendors. A
--- vendor-sold mat comes from the vendor only, and what you carry of it is worth no more than the
--- vendor's price (craft.vendor_first, R22, A33).
---@param recipes table[]  Recipes considered
---@param owned table      key -> units you carry, not yet used
---@param ctx table        { itemOf, levelsOf, vendorBuyOf (key -> vendor price or nil; optional) }
---@return table           A Craft.Option market
local function CraftMarket(recipes, owned, ctx)
  local market = { owned = owned, hold = {}, vendor = {}, books = {} }
  for _, r in ipairs(recipes) do
    for _, reagent in ipairs(r.reagents) do
      local key = reagent[1]
      local item, levels = ctx.itemOf(key), ctx.levelsOf(key)
      local vendor = ctx.vendorBuyOf and ctx.vendorBuyOf(key) or nil
      market.vendor[key] = vendor
      market.hold[key] = item and item.sellValue or 0.0
      if vendor then market.hold[key] = math.min(market.hold[key], vendor) end
      local book = {}
      if vendor then
        -- no AH source for a vendor-sold mat
      elseif levels then
        for _, level in ipairs(levels) do
          if Options.Buyable(item, level.price) then book[#book + 1] = { level.price, level.units } end
        end
      elseif item and item.cur and item.curQty and Options.Buyable(item, item.cur) then
        book[1] = { item.cur, item.curQty }          -- no live book: cur × curQty (SPEC §3.4 depth unknown)
      end
      market.books[key] = book
    end
  end
  return market
end

--- The addon's own craft actions (A9, A20): the best-paying craft first, each using what the
--- ones before it left, until none pays. SPEC GAP: vendor-sold mats have no buy price in game
--- data the addon keeps, so only owned and AH mats are used.
---@param recipes table[]  Options.KnownRecipes, minus those AHSync's plan crafts
---@param units table      key -> units carried
---@param ctx table        { itemOf, levelsOf, vendorOf, nowMs, charId }
---@return table[]         Plan-shaped craft actions
---@return table           key -> owned units the crafts use (to leave out of the sell rows)
function Options.Crafts(recipes, units, ctx)
  local owned, reserved, out, left = {}, {}, {}, {}
  for key, qty in pairs(units) do owned[key] = qty end
  for _, r in ipairs(recipes) do
    if not (ctx.vendorBuyOf and ctx.vendorBuyOf(r.product)) then left[#left + 1] = r end   -- R23: no vendor products
  end
  while #left > 0 do
    local best, bestAt
    for i, r in ipairs(left) do
      local net, p = Options.ProductNet(r, ctx.itemOf(r.product), ctx.vendorOf(r.product) or 0)
      local listed = (ctx.listed or {})[r.product]
      local cap = Options.CraftCap(r, ctx.itemOf(r.product), (units[r.product] or 0) + (listed and listed.units or 0),
                                   ctx.nowMs)
      local option = Craft.Option(r, CraftMarket({ r }, owned, ctx), net, nil, cap)
      option.p = p
      if option.crafts > 0 and (not best or option.profit > best.profit) then best, bestAt = option, i end
    end
    if not best then break end
    local r = table.remove(left, bestAt)
    for key, used in pairs(best.ownedUsed) do
      owned[key] = owned[key] - used
      reserved[key] = (reserved[key] or 0) + used
    end
    out[#out + 1] = { id = "own:craft:" .. r.spell, kind = "craft", key = r.product, qty = best.crafts, net = best.profit,
                      chance = best.p and math.floor(best.p * 100 + 0.5) or nil, at = ctx.nowMs, status = "pending",
                      char = ctx.charId, own = true, ownSource = Craft.Source(best), matGold = best.matGold,
                      uses = best.ownedUsed }
  end
  return out, reserved
end

--- Every known recipe that pays, each costed on its own against what you carry (A27): the Craft
--- tab's view. Today's crafts (Options.Crafts) share the mats out instead, best first.
---@param recipes table[]  Options.KnownRecipes
---@param units table      key -> units carried, less what you keep
---@param ctx table        { itemOf, levelsOf, vendorOf, vendorBuyOf, listed (key -> { units, endsAt }: your auctions) }
---@return table[]         { key, spell, crafts, matGold, profit, p, source, ownedUsed, bought, fromVendor,
---                        vendorPrice (key -> copper), ahGold (key -> copper) }, best profit first; a recipe whose
---                        product you already hold up to its cap is { key, spell, made = units held, inBags,
---                        listed, endsAt, profit = 0 } (A36, A42)
function Options.CraftOptions(recipes, units, ctx)
  local out = {}
  for _, r in ipairs(recipes) do
    local vendorSold = ctx.vendorBuyOf and ctx.vendorBuyOf(r.product)     -- R23: no vendor-sold products
    local net, p = Options.ProductNet(r, ctx.itemOf(r.product), ctx.vendorOf(r.product) or 0)
    local market = CraftMarket({ r }, units, ctx)
    local inBags, listed = units[r.product] or 0, (ctx.listed or {})[r.product]   -- your auctions count too (A42)
    local held = inBags + (listed and listed.units or 0)
    local cap = Options.CraftCap(r, ctx.itemOf(r.product), held, ctx.nowMs)
    local option = Craft.Option(r, market, net, nil, cap)
    if cap == 0 and held > 0 and not vendorSold then
      out[#out + 1] = { key = r.product, spell = r.spell, made = held, inBags = inBags,
                        listed = listed and listed.units or 0, endsAt = listed and listed.endsAt, profit = 0 }
    elseif option.crafts > 0 and not vendorSold then
      out[#out + 1] = { key = r.product, spell = r.spell, crafts = option.crafts, matGold = option.matGold,
                        profit = option.profit, p = p, source = Craft.Source(option), ownedUsed = option.ownedUsed,
                        bought = option.bought, fromVendor = option.fromVendor, vendorPrice = market.vendor,
                        ahGold = option.ahGold }
    end
  end
  table.sort(out, function(a, b)
    if a.profit ~= b.profit then return a.profit > b.profit end
    return a.spell < b.spell
  end)
  return out
end

--- Drop planned buys whose price is no longer on the AH (A2): a live scan shows no units at or
--- below the plan's price (the item is gone, or now costs more than the flip's profit line).
---@param actions table[]    Plan actions
---@param levelsOf function  key -> Live.Levels, or nil when there is no newer scan
---@return table[]           The actions still possible
---@return number            Buys dropped
function Options.WithoutGoneBuys(actions, levelsOf)
  local kept, gone = {}, 0
  for _, a in ipairs(actions) do
    local levels = a.kind == "buy" and a.price and levelsOf(a.key)
    if levels and Live.Ahead(levels, a.price) < a.qty then
      gone = gone + 1
    else
      kept[#kept + 1] = a
    end
  end
  return kept, gone
end

--- Disenchant targets on the AH (A23), for any enchanter: the buy price (the live scan's cheapest,
--- else the file's), the expected mats at their sell values, and the profit; unprofitable items
--- are left out. Each target says the skill it needs, so the tab is useful without an enchanter.
---@param candidates table  Market.Disenchantable()
---@param ctx table         { itemOf (key -> market item), levelsOf (key -> live levels or nil) }
---@return table[]          { key, ilvl, skill, buy, ev, profit, mats = { { key, units } }, mainMat, matChance },
---                         best profit first
function Options.Disenchant(candidates, ctx)
  local Disenchant = AHProfit.Engine.Disenchant
  local tbl, out = Disenchant.Table(), {}
  for key, c in pairs(candidates) do
    local item, levels = ctx.itemOf(key), ctx.levelsOf(key)
    local buy = levels and levels[1] and levels[1].price or (not levels and item and item.cur) or nil
    local _, outcomes = Disenchant.OutcomesFor(tbl, c.quality, c.ilvl, c.kind)
    if buy and outcomes then
      local values, mats = {}, {}
      for i, o in ipairs(outcomes) do
        local mat = ctx.itemOf(o[1])
        values[i] = mat and mat.sellValue or 0
        mats[i] = { key = o[1], units = o[2] * (o[3] + o[4]) / 2 }
      end
      local ev = Disenchant.ExpectedValue(outcomes, values)
      if ev - buy > 0 then
        local main = ctx.itemOf(outcomes[1][1])
        out[#out + 1] = { key = key, ilvl = c.ilvl, skill = c.skill, buy = buy, ev = ev, profit = ev - buy, mats = mats,
                          mainMat = outcomes[1][1], matChance = main and main.chance }
      end
    end
  end
  table.sort(out, function(a, b)
    if a.profit ~= b.profit then return a.profit > b.profit end
    return a.key < b.key
  end)
  return out
end

--- The best skill in a profession across every character AHProfit has seen on the account.
---@param db table          AHProfitDB
---@param profession string e.g. "Enchanting"
---@return number|nil       nil when nobody has it
function Options.BestSkill(db, profession)
  local best
  for _, skills in pairs(db.professions or {}) do
    local p = skills[profession]
    if p and (not best or p.skill > best) then best = p.skill end
  end
  return best
end
