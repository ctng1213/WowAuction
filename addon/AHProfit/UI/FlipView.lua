-- UI/FlipView.lua: the Flip tab's own one-stack flips, from the market file's flip offers (SPEC §4.3, A59) and
-- from stacks a live book shows dumped below its next price level (A69), split from UI/ViewModel.lua for size.
-- It adds to the same AHProfit.ViewModel table; frame code only draws.

local AHProfit = AHProfit
local ViewModel = AHProfit.ViewModel
local Prices = AHProfit.Engine.Prices
local SellProb = AHProfit.Engine.SellProb
local Fees = AHProfit.Engine.Fees
local Options = AHProfit.Options

ViewModel.MIN_FLIP_MARGIN = 0.2        -- config min_flip_margin (SPEC §3.6)
local DAY_MS = 86400000

--- A planned flip whose buy is gone from the AH, kept on the Flip tab greyed out (A66).
---@param row table  A ViewModel.FlipRows row
---@return table     The same row, marked gone
function ViewModel.GoneFlip(row)
  row.gone, row.netColor, row.riskAmber = true, "muted", false
  row.note = "gone: nothing on the AH at or below " .. row.buy .. " now"
  return row
end

--- The most to pay each for one of the market file's flip offers: expected ÷ (1 + margin) (flips.pays, A59).
---@param expected number  Expected value per unit, copper
---@return number          Copper, rounded down
function ViewModel.FlipCeiling(expected)
  return math.floor(expected / (1 + ViewModel.MIN_FLIP_MARGIN))
end

--- One own flip row, or nil when the cheapest price now is over the offer's ceiling.
---@param key number
---@param o table      { expected, price, slot } from Market.FlipOffers
---@param ctx table    As ViewModel.OwnFlipRows
---@return table|nil
local function OwnFlipRow(key, o, ctx)
  local item, levels = ctx.itemOf(key), ctx.levelsOf(key)
  local now = levels and levels[1] and levels[1].price or (item and item.cur)
  local ceiling = ViewModel.FlipCeiling(o.expected)
  if not (item and now and now <= ceiling) then return nil end
  local ref, look = item.refStack or 1, ctx.lookOf(key)
  local net, spend = (o.expected - now) * ref, now * ref
  local risk, amber = ViewModel.Risk(spend, ctx.totalGold)
  local day, hour = ViewModel.Clock(ctx.nowMs + Prices.DaysUntilSlot(o.slot, ctx.nowMs) * DAY_MS, ctx.offsetMs)
  local chance = item.chance and ViewModel.RoundHalfUp(item.chance * 100)
  return {
    id = "own:" .. key, key = key, item = look.name, quality = look.quality, kind = "buy", qty = "×" .. ref,
    buy = ViewModel.Money(now), sell = ViewModel.Money(o.price), when = day .. " " .. hour,
    profit = ViewModel.SignedMoney(net), netColor = net > 0 and "pos" or "neg",
    chance = chance and (chance .. "%") or "—", dot = chance and ViewModel.Level(look.conf) or nil,
    risk = risk, riskAmber = amber, spend = spend, netCopper = net, own = true,
    note = "addon's own idea · one stack · buy at or below " .. ViewModel.Money(ceiling),
  }
end

--- One flip-now row from a live book (A69, FN1): buy the cheapest level (one reference stack at most) and relist
--- now at Prices.RelistPrice(second level, med7, p90, closes), with P from the curve at 0 ahead for this hour, or the
--- default chance for the cheapest position without a curve (volume unknown); nil when it doesn't pay the margin
--- or nothing grounds the relist price (no second level on thin data, R28).
---@param c table    { key, cheapest, second, units } from Live.FlipCandidates
---@param ctx table  As ViewModel.OwnFlipRows
---@return table|nil
local function NowFlipRow(c, ctx)
  local item = ctx.itemOf(c.key)
  if not item then return nil end
  local units, curve = math.min(c.units, item.refStack or 1), ctx.curveOf(c.key)
  local relist = Prices.RelistPrice(c.second, item.med7, item.p90, item.closes30)
  if not relist then return nil end
  local p = curve and SellProb.Lookup(curve.rows, curve.refreshMs, ctx.nowMs, 0, curve.aheadPoints)
            or SellProb.DefaultChance(relist, c.second, Options.DEFAULT_SELL_PROB, Options.DEFAULT_SELL_PROB_CHEAPEST)
  local vendor = ctx.vendorOf(c.key) or 0
  local deposit = Fees.DepositCopper(vendor, units, Fees.ShortestDurationCovering(Options.SELL_WINDOW_HOURS))
  local expected = Prices.FlipNow(p, units, c.cheapest, relist, vendor, deposit, ViewModel.MIN_FLIP_MARGIN)
  if not expected then return nil end
  local look, spend = ctx.lookOf(c.key), c.cheapest * units
  local net, chance = expected - spend, ViewModel.RoundHalfUp(p * 100)
  local risk, amber = ViewModel.Risk(spend, ctx.totalGold)
  return {
    id = "own:now:" .. c.key, key = c.key, item = look.name, quality = look.quality, kind = "buy", qty = "×" .. units,
    buy = ViewModel.Money(c.cheapest), sell = ViewModel.Money(relist), when = "now",
    profit = ViewModel.SignedMoney(net), netColor = net > 0 and "pos" or "neg", chance = chance .. "%",
    dot = ViewModel.Level(look.conf), risk = risk, riskAmber = amber, spend = spend, netCopper = net, own = true,
    note = "buy at " .. ViewModel.Money(c.cheapest) .. ", relist now at " .. ViewModel.Money(relist),
  }
end

--- The Flip tab's own rows: market-wide offers whose cheapest price now is at or below their ceiling (A59), and
--- stacks a live book shows dumped below its next level (A69); one row per item, the better paying.
---@param offers table  key -> { expected, price, slot } (Market.FlipOffers)
---@param ctx table     { itemOf, levelsOf, lookOf, skip (key -> true: planned, or a rule blocks it), nowMs, offsetMs,
---                     totalGold, nowCandidates (Live.FlipCandidates; optional), curveOf, vendorOf }
---@return table[]      Rows shaped as ViewModel.FlipRows', best profit first (ties: key)
function ViewModel.OwnFlipRows(offers, ctx)
  local byKey, rows = {}, {}
  for key, offer in pairs(offers) do
    byKey[key] = not ctx.skip(key) and OwnFlipRow(key, offer, ctx) or nil
  end
  for _, c in ipairs(ctx.nowCandidates or {}) do
    local row = not ctx.skip(c.key) and NowFlipRow(c, ctx) or nil
    if row and (not byKey[c.key] or row.netCopper > byKey[c.key].netCopper) then byKey[c.key] = row end
  end
  for _, row in pairs(byKey) do rows[#rows + 1] = row end
  table.sort(rows, function(x, y)
    if x.netCopper ~= y.netCopper then return x.netCopper > y.netCopper end
    return x.key < y.key
  end)
  return rows
end
