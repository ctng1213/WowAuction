-- UI/PortfolioView.lua: the addon's Portfolio tab as view-model data (SPEC §4.3 Portfolio tab, §4.0; A76-A78, A80).
-- Pure: the live reselection (Recommend/Reselect.lua's result), the snapshot and the positions come in; text and
-- flags come out, so every number shown is made and tested here (SPEC §8.5). The frames come with M13. Profit % is
-- the twin of engine.portfolio.profit_pct and pool_profit_pct (D.8). Cross-character totals are the snapshot's,
-- shown with its time; this character's rows are live (A78).

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local ViewModel = AHProfit.ViewModel
local Timing = AHProfit.Engine.Timing
local Reselect = AHProfit.Reselect

local PortfolioView = {}
AHProfit.PortfolioView = PortfolioView

PortfolioView.STALE_MS = 24 * 3600000      -- the snapshot time turns amber after 24 h (A78)
PortfolioView.STRATEGIES = { flip = "Flip", buy_later = "Buy for later", craft = "Craft", multistage = "Multistage craft",
                             disenchant = "Disenchant", sell_stock = "Sell stock" }   -- engine.portfolio.STRATEGIES
PortfolioView.HOVER = "Cross-character totals are a cached copy of AHSync's last snapshot; only your own rows are live."

--- Profit % = expected net ÷ cash, whole percent half up (portfolio.profit_pct, D.8).
---@param net number   Expected net, copper
---@param cash number  Gold needed + cost basis of bought stock used, copper
---@return number|nil  nil when the cash is 0
function PortfolioView.ProfitPct(net, cash)
  if cash <= 0 then return nil end
  return math.floor(net * 100 / cash + 0.5)
end

--- Profit % of several rows, leaving out rows with no cash (portfolio.pool_profit_pct, D.8).
---@param rows table[]  { net, cash } each
---@return number|nil
function PortfolioView.PoolPct(rows)
  local net, cash = 0.0, 0
  for _, r in ipairs(rows) do
    if r.cash > 0 then net, cash = net + r.net, cash + r.cash end
  end
  return PortfolioView.ProfitPct(net, cash)
end

--- A local clock time, "10:38".
---@param ms number
---@param offsetMs number  Local time minus UTC
---@return string
local function HourMinute(ms, offsetMs)
  return Adapter.UtcDate("%H:%M", math.floor((ms + offsetMs) / 1000))
end

--- The top line: the snapshot's cross-character totals and its time (A78).
---@param snapshot table   Plan.Portfolio
---@param nowMs number
---@param offsetMs number
---@return table           { text, time, amber (older than 24 h), hover }
function PortfolioView.TopLine(snapshot, nowMs, offsetMs)
  local time = HourMinute(snapshot.snapshotMs, offsetMs)
  local text = "portfolio " .. ViewModel.Money(snapshot.capitalBase) .. " · " .. math.floor(snapshot.riskTolerance1000 / 10)
    .. "% risk · capacity " .. ViewModel.Money(snapshot.capacity) .. " · "
    .. ViewModel.Money(math.max(0, snapshot.capacity - snapshot.allocated)) .. " unallocated · snapshot " .. time
  return { text = text, time = time, amber = nowMs - snapshot.snapshotMs > PortfolioView.STALE_MS,
           hover = PortfolioView.HOVER }
end

--- Best time to buy for a buy moved to now (A76, the tooltip's line): "now · cheaper than Tue 20–23 (5s 36c)".
---@param rc table          The repriced candidate (buyNow, windowHour, expectedEach)
---@param windowHours number|nil  The buy window's length; nil leaves the end out
---@param offsetMs number
---@return string|nil       nil unless the buy moved to now
function PortfolioView.BuyTime(rc, windowHours, offsetMs)
  if not rc.buyNow then return nil end
  local start = rc.windowHour * 3600000
  local day, clock = ViewModel.Clock(start, offsetMs)
  local hour = tonumber(clock:sub(1, 2))
  local span = windowHours and (hour .. "–" .. ((hour + windowHours) % 24)) or tostring(hour)
  return "now · cheaper than " .. day .. " " .. span .. " (" .. ViewModel.Money(ViewModel.RoundHalfUp(rc.expectedEach)) .. ")"
end

--- A row's note: when it buys and sells (A76: "buy now · sell Thu 19:00").
---@param rc table
---@param nowMs number
---@param offsetMs number
---@return string|nil
local function NoteOf(rc, nowMs, offsetMs)
  local c = rc.c
  local sale = "sell now"
  if c.windowEnd ~= nil and c.sale and c.sale > nowMs then
    local day, clock = ViewModel.Clock(c.sale, offsetMs)
    sale = "sell " .. day .. " " .. clock
  end
  local _, _, hour = Reselect.Parse(c.id)
  if c.strategy == "flip" or c.strategy == "buy_later" then
    if hour and not rc.buyNow then
      local day, clock = ViewModel.Clock(hour * 3600000, offsetMs)
      return "buy " .. day .. " " .. clock .. " · " .. sale
    end
    return "buy now · " .. sale
  end
  return sale
end

--- One row of a section.
---@param rc table        The repriced candidate
---@param pick table      Its pick (adjusted, lst)
---@param status string|nil  nil when funded, else the reason
---@param ctx table       { snapshot, nowMs, offsetMs, nameOf, windowHoursOf (rc -> hours or nil) }
---@return table
local function Row(rc, pick, status, ctx)
  local c, s = rc.c, ctx.snapshot
  local wait = Timing.WaitHundredths(ctx.nowMs, c.sale)
  local premium = Timing.PremiumMicro(wait, s.premiumBase, s.premiumPerDay, s.immediateHours)
  local pct = PortfolioView.ProfitPct(rc.net, rc.gold + (c.cashBasis or 0))
  return { id = c.id, key = c.key, item = ctx.nameOf(c.key), strategy = PortfolioView.STRATEGIES[c.strategy] or c.strategy,
           qty = "×" .. c.qty, gold = ViewModel.Money(rc.gold), net = ViewModel.SignedMoney(rc.net),
           adjusted = ViewModel.SignedMoney(pick.adjusted), wait = string.format("%.2f d", wait / 100),
           premium = string.format("%.2f%%", premium / 10000), pct = pct and (pct .. "%") or "—",
           status = status or "Funded", funded = status == nil, muted = status ~= nil, note = NoteOf(rc, ctx.nowMs, ctx.offsetMs),
           buyTime = PortfolioView.BuyTime(rc, ctx.windowHoursOf and ctx.windowHoursOf(rc), ctx.offsetMs),
           sortAdjusted = pick.adjusted, netCopper = rc.net, goldCopper = rc.gold, cash = rc.gold + (c.cashBasis or 0) }
end

--- Rows best adjusted profit first, ties by id.
---@param rows table[]
local function SortRows(rows)
  table.sort(rows, function(a, b)
    if a.sortAdjusted ~= b.sortAdjusted then return a.sortAdjusted > b.sortAdjusted end
    return a.id < b.id
  end)
end

--- A section's totals line, funded rows only (D.5 totals, D.8): "funded: cost 20s 60c · profit +12s 8c · 59% on cash".
---@param funded table[]  The section's funded rows
---@return string
function PortfolioView.SectionFoot(funded)
  local gold, net, cash = 0, 0.0, {}
  for i, r in ipairs(funded) do
    gold, net = gold + r.goldCopper, net + r.netCopper
    cash[i] = { net = r.netCopper, cash = r.cash }
  end
  local pct = PortfolioView.PoolPct(cash)
  return "funded: cost " .. ViewModel.Money(gold) .. " · profit " .. ViewModel.SignedMoney(net)
    .. (pct and (" · " .. pct .. "% on cash") or "")
end

--- The Positions section from the snapshot (EX18; durable positions come with M14).
---@param positions table[]  Plan.Positions(charId)
---@param nameOf function
---@return table             { rows, foot }
local function Positions(positions, nameOf)
  local rows, committed, est, withNet = {}, 0, 0, false
  for _, p in ipairs(positions) do
    committed = committed + (p.costLeft or 0)
    if p.netNow then est, withNet = est + p.netNow, true end
    rows[#rows + 1] = { key = p.key, item = nameOf(p.key), qty = "×" .. (p.unitsLeft or 0), state = p.state,
                        cost = ViewModel.Money(p.costLeft or 0), net = p.netNow and ViewModel.SignedMoney(p.netNow) or "—" }
  end
  local pct = withNet and PortfolioView.ProfitPct(est, committed) or nil
  return { rows = rows, foot = "committed " .. ViewModel.Money(committed) .. " · est. " .. ViewModel.SignedMoney(est)
             .. (pct and (" · " .. (pct >= 0 and "+" or "") .. pct .. "% on committed capital") or "") }
end

--- The Portfolio tab's data (SPEC §4.3; A77, A78, A80): the top line, Positions, Today and Next 7 days with their
--- funded rows first, then the excluded ones muted with their reasons, each section's totals, and the footer.
---@param result table     Reselect.Run's result (this character's live selection)
---@param snapshot table   Plan.Portfolio for the market
---@param positions table[] Plan.Positions(charId)
---@param ctx table        { nowMs, offsetMs, nameOf, windowHoursOf, problem (status line or nil) }
---@return table           { top, problem, positions = { rows, foot }, today = { rows, foot }, week = { rows, foot },
---                        footer = { funded, capacity } }
function PortfolioView.Build(result, snapshot, positions, ctx)
  local rowCtx = { snapshot = snapshot, nowMs = ctx.nowMs, offsetMs = ctx.offsetMs, nameOf = ctx.nameOf,
                   windowHoursOf = ctx.windowHoursOf }
  local sections = { today = { funded = {}, excluded = {} }, week = { funded = {}, excluded = {} } }
  for _, rc in ipairs(result.funded) do
    local pick = result.picks[rc.c.id]
    table.insert(sections[pick.lst].funded, Row(rc, pick, nil, rowCtx))
  end
  for _, e in ipairs(result.excluded) do
    local pick = result.picks[e.rc.c.id]
    table.insert(sections[pick.lst].excluded, Row(e.rc, pick, e.reason, rowCtx))
  end
  local out = { top = PortfolioView.TopLine(snapshot, ctx.nowMs, ctx.offsetMs), problem = ctx.problem,
                positions = Positions(positions, ctx.nameOf) }
  local all = {}
  for _, name in ipairs({ "today", "week" }) do
    local sec = sections[name]
    SortRows(sec.funded)
    SortRows(sec.excluded)
    local rows = {}
    for _, r in ipairs(sec.funded) do rows[#rows + 1] = r; all[#all + 1] = { net = r.netCopper, cash = r.cash } end
    for _, r in ipairs(sec.excluded) do rows[#rows + 1] = r end
    out[name] = { rows = rows, foot = PortfolioView.SectionFoot(sec.funded) }
  end
  local pct = PortfolioView.PoolPct(all)
  out.footer = { funded = "funded " .. ViewModel.SignedMoney(result.net) .. " · cost " .. ViewModel.Money(result.gold)
                   .. (pct and (" · " .. pct .. "% on cash") or ""),
                 capacity = "capacity " .. ViewModel.Money(snapshot.capacity) .. " · "
                   .. ViewModel.Money(result.capacityLeft) .. " unallocated" }
  return out
end
