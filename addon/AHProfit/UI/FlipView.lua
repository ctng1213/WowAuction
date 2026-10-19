-- UI/FlipView.lua: the Flip tab's own one-stack flips, from the market file's flip offers (SPEC §4.3, A59),
-- split from UI/ViewModel.lua for size. It adds to the same AHProfit.ViewModel table; frame code only draws.

local AHProfit = AHProfit
local ViewModel = AHProfit.ViewModel
local Prices = AHProfit.Engine.Prices

ViewModel.MIN_FLIP_MARGIN = 0.2        -- config min_flip_margin (SPEC §3.6)
local DAY_MS = 86400000

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

--- The Flip tab's own rows: market-wide offers whose cheapest price now is at or below their ceiling (A59).
---@param offers table  key -> { expected, price, slot } (Market.FlipOffers)
---@param ctx table     { itemOf, levelsOf, lookOf, skip (key -> true: planned, or a rule blocks it), nowMs, offsetMs,
---                     totalGold }
---@return table[]      Rows shaped as ViewModel.FlipRows', best profit first (ties: key)
function ViewModel.OwnFlipRows(offers, ctx)
  local keys, rows = {}, {}
  for key in pairs(offers) do keys[#keys + 1] = key end
  table.sort(keys)
  for _, key in ipairs(keys) do
    local row = not ctx.skip(key) and OwnFlipRow(key, offers[key], ctx) or nil
    if row then rows[#rows + 1] = row end
  end
  table.sort(rows, function(x, y)
    if x.netCopper ~= y.netCopper then return x.netCopper > y.netCopper end
    return x.key < y.key
  end)
  return rows
end
