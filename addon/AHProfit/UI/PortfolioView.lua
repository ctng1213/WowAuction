-- UI/PortfolioView.lua: the addon's Portfolio tab as view-model data (SPEC §4.3 Portfolio tab, §4.0, §3.4D.6-D.8;
-- A76-A80, A84, UI16, UI19). Pure: the live reselection (Recommend/Reselect.lua's result), the snapshot and the
-- positions come in; text and flags come out, so every number shown is made and tested here (SPEC §8.5). Profit % is
-- the twin of engine.portfolio.profit_pct and pool_profit_pct (D.8); the reason details follow
-- portfolio_view.reason_detail and the empty block selection.empty_state (D.6, D.7). Cross-character totals are the
-- snapshot's, shown with its time; this character's rows are live (A78). UI/MainWindow.lua draws PortfolioView.Lines.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local ViewModel = AHProfit.ViewModel
local Timing = AHProfit.Engine.Timing
local Reselect = AHProfit.Reselect

local PortfolioView = {}
AHProfit.PortfolioView = PortfolioView

local DAY_MS = 86400000
PortfolioView.STALE_MS = 24 * 3600000      -- the snapshot time turns amber after 24 h (A78)
PortfolioView.STRATEGIES = { flip = "Flip", buy_later = "Buy for later", craft = "Craft", multistage = "Multistage craft",
                             disenchant = "Disenchant", sell_stock = "Sell stock" }   -- engine.portfolio.STRATEGIES
PortfolioView.HOVER = "Cross-character totals are a cached copy of AHSync's last snapshot; only your own rows are live."
-- A strategy -> the action badge (portfolio_view.ACTION_OF).
PortfolioView.ACTION_OF = { flip = "buy", buy_later = "buy", craft = "craft", multistage = "craft", disenchant = "de",
                            sell_stock = "post" }
PortfolioView.NEG_CODES = { profession = true, missing = true }      -- shown red; every other reason amber
-- A position's state -> its badge colour (portfolio_view.STAGE_CLASS).
PortfolioView.STAGE_KIND = { Acquiring = "acq", Processing = "proc", Holding = "hold", ["Ready to sell"] = "ready",
                             Listed = "listed", ["Partially sold"] = "partial", Completed = "done",
                             ["Needs reconciliation"] = "recon" }
PortfolioView.STATUS_WIDTH, PortfolioView.STATUS_FONT = 150, 10    -- the Status column wraps within 150 px (UI16)
PortfolioView.ROW_HEIGHT, PortfolioView.TALL_HEIGHT, PortfolioView.EMPTY_HEIGHT = 20, 30, 50
PortfolioView.MIN_WINDOW_CLOSES = 24       -- selection.MIN_WINDOW_CLOSES
PortfolioView.PATTERN_WEEKS = 3            -- a window's forecast is trusted from 3 weeks (D.2)
-- A status text's start -> its code, for the selection's own reasons (selection.REASON_CODES).
PortfolioView.REASON_CODES = { { "Required profession unavailable", "profession" }, { "Forecast too uncertain", "uncertain" },
  { "Forecast too stale", "stale" }, { "Sales volume unknown", "volume" }, { "No listing left", "no_listing" },
  { "Insufficient liquidity", "liquidity" }, { "Missing materials", "missing" }, { "Resources already claimed", "claimed" },
  { "Insufficient gold on", "gold_char" }, { "Insufficient gold", "gold_all" },
  { "Recommendation limit reached", "limit" }, { "Exceeds risk tolerance", "risk" } }
-- The detail line under a reason that needs no numbers (portfolio_view.DETAILS).
PortfolioView.DETAILS = {
  stale = "prices older than 24 h are not planned on; run AHSync to fetch newer ones",
  volume = "neither the chance to sell nor the liquidity can be measured without a sales rate",
  no_listing = "nothing else is listed, and too few closes ground its price",
  missing = "no stock, no AH listing and no vendor sells it",
  claimed = "a better funded row uses it first",
  gold_all = "all your characters together hold less",
  gold_char = "your account could pay, but gold stays on its character",
  limit = "the list is full of better rows",
}

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

--- When something happens, as the rows say it (portfolio_view.when_text): "now", "today 18:00" or "Thu 19:00".
---@param ms number|nil
---@param nowMs number
---@param offsetMs number
---@return string
function PortfolioView.When(ms, nowMs, offsetMs)
  if ms == nil or ms <= nowMs then return "now" end
  local day, clock = ViewModel.Clock(ms, offsetMs)
  if math.floor((ms + offsetMs) / DAY_MS) == math.floor((nowMs + offsetMs) / DAY_MS) then return "today " .. clock end
  return day .. " " .. clock
end

--- The exposure limit: capital base × risk tolerance in whole thousandths, rounded down (portfolio.risk_budget).
---@param snapshot table  Plan.Portfolio
---@return number         Copper
function PortfolioView.RiskLimit(snapshot)
  return math.floor(snapshot.capitalBase * snapshot.riskTolerance1000 / 1000)
end

--- The top line: the snapshot's cross-character totals and its time (A78). With nothing funded it leads with the
--- character's live gold and leaves out the unallocated part (UI19). Local positions add "committed 4g 18s + 7s 60c
--- local" (A81).
---@param snapshot table   Plan.Portfolio
---@param nowMs number
---@param offsetMs number
---@param lead string|nil  "Amaya 14g 30s" in the empty state, else nil
---@return table           { text, time, amber (older than 24 h), hover }
function PortfolioView.TopLine(snapshot, nowMs, offsetMs, lead)
  local time = HourMinute(snapshot.snapshotMs, offsetMs)
  local spent = snapshot.localCommitted or 0
  local committed = spent > 0 and ("committed " .. ViewModel.Money(snapshot.committed - spent) .. " + "
    .. ViewModel.Money(spent) .. " local · ") or ""
  local text = "portfolio " .. ViewModel.Money(snapshot.capitalBase) .. " · " .. math.floor(snapshot.riskTolerance1000 / 10)
    .. "% risk · " .. committed .. "capacity " .. ViewModel.Money(snapshot.capacity) .. " · "
  if lead then
    text = lead .. " · " .. text .. "snapshot " .. time
  else
    text = text .. ViewModel.Money(math.max(0, snapshot.capacity - snapshot.allocated)) .. " unallocated · snapshot " .. time
  end
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

--- A row's plan: when it buys or makes and when it sells (A76: "buy now · sell Thu 19:00"; "craft now · sell today
--- 18:00").
---@param rc table
---@param nowMs number
---@param offsetMs number
---@return string
function PortfolioView.PlanText(rc, nowMs, offsetMs)
  local c = rc.c
  local when = PortfolioView.When(c.sale, nowMs, offsetMs)
  local sale = when == "now" and "sell now" or ("sell " .. when)
  local _, _, hour = Reselect.Parse(c.id)
  if c.strategy == "flip" or c.strategy == "buy_later" then
    if hour and not rc.buyNow then return "buy " .. PortfolioView.When(hour * 3600000, nowMs, offsetMs) .. " · " .. sale end
    return "buy now · " .. sale
  end
  local verb = ({ craft = "craft", multistage = "craft", disenchant = "disenchant", sell_stock = "post" })[c.strategy]
  return (verb or c.strategy) .. " now · " .. sale
end

--- A status text's reason code (selection.reason_code).
---@param reason string
---@return string|nil
function PortfolioView.CodeOf(reason)
  for _, pair in ipairs(PortfolioView.REASON_CODES) do
    if reason:sub(1, #pair[1]) == pair[1] then return pair[2] end
  end
  return nil
end

--- A sales rate as the liquidity reason writes it: one decimal, half up, no trailing .0 (selection.per_day).
---@param rate number  Units a day
---@return string
function PortfolioView.PerDay(rate)
  local tenths = math.floor(rate * 10 + 0.5)
  if tenths % 10 == 0 then return tostring(math.floor(tenths / 10)) end
  return math.floor(tenths / 10) .. "." .. tenths % 10
end

--- The full status text of an excluded row (SPEC §3.4D.6): K carries only a code, so the numbers the desktop writes
--- are added from the market file: "Insufficient liquidity: 3/day, 40 planned", "Forecast too uncertain (1 week of
--- data)" or "(4 closes in 30 d, needs 24)". Other reasons are already whole.
---@param rc table          The repriced candidate (its code from Reselect.Status)
---@param reason string     The reason the reselection gave
---@param itemOf function   key -> Market.Item or nil
---@return string
function PortfolioView.Reason(rc, reason, itemOf)
  local c, item = rc.c, itemOf(rc.c.key)
  local code = rc.code or PortfolioView.CodeOf(reason)
  if reason ~= Reselect.CODE_TEXT[code] or not item then return reason end
  if code == "liquidity" and item.soldPerDay then
    return reason .. ": " .. PortfolioView.PerDay(item.soldPerDay) .. "/day, " .. c.qty .. " planned"
  end
  if code == "uncertain" then
    if c.windowEnd and item.closes30 and item.closes30 < PortfolioView.MIN_WINDOW_CLOSES then
      return reason .. " (" .. item.closes30 .. " closes in 30 d, needs " .. PortfolioView.MIN_WINDOW_CLOSES .. ")"
    end
    local w = item.sellWindows and item.sellWindows[1]
    if w then return reason .. " (" .. w.weeks .. (w.weeks == 1 and " week" or " weeks") .. " of data)" end
  end
  return reason
end

--- The detail line under an excluded row's status (portfolio_view.reason_detail, SPEC §3.4D.6).
---@param rc table
---@param reason string     The full status text
---@param ctx table         { itemOf, skills (profession -> { skill }) or nil, charId, capacity (this character's),
---                         left (capacity left after the funded rows), offsetMs }
---@return string           "" when there is nothing to add
function PortfolioView.Detail(rc, reason, ctx)
  local c, code = rc.c, PortfolioView.CodeOf(reason)
  local item = ctx.itemOf(c.key)
  if code == "profession" and c.needs then
    local have = ctx.skills and ctx.skills[c.needs.profession]
    if not have then return "no character has " .. c.needs.profession end
    return ViewModel.ShortName(ctx.charId) .. " has " .. c.needs.profession .. " " .. have.skill
  elseif code == "uncertain" then
    if c.windowEnd and item and item.closes30 and item.closes30 < PortfolioView.MIN_WINDOW_CLOSES then
      return "a forecast from " .. item.closes30 .. " closes in 30 days is noise; a window sale needs "
        .. PortfolioView.MIN_WINDOW_CLOSES
    end
    local w = item and item.sellWindows and item.sellWindows[1]
    local weeks = w and w.weeks or 0
    local seen = ({ [1] = "once", [2] = "twice" })[weeks] or (weeks .. " times")
    local peak = c.sale and ("its " .. Adapter.UtcDate("%A", math.floor((c.sale + ctx.offsetMs) / 1000)) .. " peak") or "its peak"
    return peak .. " has been seen " .. seen .. "; a forecast needs " .. PortfolioView.PATTERN_WEEKS .. " weeks"
  elseif code == "liquidity" then
    if item and item.soldPerDay and item.soldPerDay > 0 then
      return "the market sells about " .. PortfolioView.PerDay(item.soldPerDay) .. " a day; " .. c.qty
        .. " would take about " .. ViewModel.RoundHalfUp(c.qty / item.soldPerDay) .. " days"
    end
    return "its windows' liquidity caps have no room left"
  elseif code == "risk" then
    if rc.gold > ctx.capacity then
      return "it alone needs more than the whole remaining capacity (" .. ViewModel.Money(ctx.capacity) .. ")"
    end
    return "its turn came after every funded row, so " .. ViewModel.Money(ctx.left) .. " is also what stays unallocated"
  end
  return PortfolioView.DETAILS[code] or ""
end

--- A row's Each: a buy's price each, a craft's mat gold a unit, a sale's post price (portfolio_view.each_copper).
---@param rc table
---@param item table|nil
---@return number|nil  Copper; nil when nothing is paid
local function EachOf(rc, item)
  if rc.c.strategy == "sell_stock" then return item and item.postPrice end
  if rc.gold <= 0 or rc.c.qty <= 0 then return nil end
  return ViewModel.RoundHalfUp(rc.gold / rc.c.qty)
end

--- One row of Today or Next 7 days.
---@param rc table        The repriced candidate
---@param pick table      Its pick (adjusted, lst)
---@param status string|nil  nil when funded, else the reason
---@param ctx table       Build's row context
---@return table
local function Row(rc, pick, status, ctx)
  local c, s = rc.c, ctx.snapshot
  local wait = Timing.WaitHundredths(ctx.nowMs, c.sale)
  local premium = Timing.PremiumMicro(wait, s.premiumBase, s.premiumPerDay, s.immediateHours)
  local pct = PortfolioView.ProfitPct(rc.net, rc.gold + (c.cashBasis or 0))
  local item, look = ctx.itemOf(c.key), ctx.lookOf(c.key)
  local each = EachOf(rc, item)
  local risk, amber = ViewModel.Risk(rc.gold, ctx.totalGold or 0)
  local chance = c.chance and ViewModel.RoundHalfUp(c.chance * 100) or nil
  local full = status and PortfolioView.Reason(rc, status, ctx.itemOf) or nil
  local code = full and PortfolioView.CodeOf(full)
  local kind = PortfolioView.ACTION_OF[c.strategy] or "buy"
  local tall = full ~= nil and ViewModel.WrapCount(full, PortfolioView.STATUS_WIDTH, PortfolioView.STATUS_FONT) > 1
  return { id = c.id, key = c.key, item = look.name, quality = look.quality,
           strategy = PortfolioView.STRATEGIES[c.strategy] or c.strategy, kind = kind,
           action = ViewModel.ACTION_LABELS[kind], qty = "×" .. c.qty, gold = ViewModel.Money(rc.gold),
           each = ViewModel.Money(each), total = each and ViewModel.Money(each * c.qty) or "—",
           net = ViewModel.SignedMoney(rc.net), netColor = rc.net > 0 and "pos" or (rc.net < 0 and "neg" or "muted"),
           chance = chance and (chance .. "%") or "—", chanceValue = chance, dot = chance and ViewModel.Level(c.conf) or nil,
           volume = (item and item.soldPerDay) and (ViewModel.RoundHalfUp(item.soldPerDay) .. "/day") or "—",
           risk = risk, riskAmber = amber, adjusted = ViewModel.SignedMoney(pick.adjusted),
           adjustedArrow = "→ " .. ViewModel.SignedMoney(pick.adjusted),
           wait = string.format("%.2f d", wait / 100), premium = string.format("%.2f%%", premium / 10000),
           pct = pct and (pct .. "%") or "—", status = full or "Funded", statusKind = full and
           (PortfolioView.NEG_CODES[code] and "neg" or "amber") or "funded",
           funded = status == nil, muted = status ~= nil, note = PortfolioView.PlanText(rc, ctx.nowMs, ctx.offsetMs),
           plan = PortfolioView.PlanText(rc, ctx.nowMs, ctx.offsetMs), tall = tall, rc = rc,
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

PortfolioView.SOFT_CODES = { claimed = true, gold_all = true, gold_char = true, limit = true, risk = true }

--- Excluded rows in the order the dashboard lists them (portfolio_view.excluded_order): rows left out only for gold,
--- capacity or a list limit first (they would be funded with more room), then by D.6's order, then the best adjusted
--- profit, the name and the id.
---@param rows table[]
local function SortExcluded(rows)
  local index = {}
  for i, pair in ipairs(PortfolioView.REASON_CODES) do index[pair[2]] = i end
  --- A row's place: 0 for a gold, capacity or list reason, else 1; then its reason's place in D.6's order.
  ---@param r table
  ---@return number, number
  local function Key(r)
    local code = PortfolioView.CodeOf(r.status)
    return PortfolioView.SOFT_CODES[code] and 0 or 1, index[code] or #PortfolioView.REASON_CODES + 1
  end
  table.sort(rows, function(a, b)
    local sa, ca = Key(a)
    local sb, cb = Key(b)
    if sa ~= sb then return sa < sb end
    if ca ~= cb then return ca < cb end
    if a.sortAdjusted ~= b.sortAdjusted then return a.sortAdjusted > b.sortAdjusted end
    if a.item ~= b.item then return a.item < b.item end
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

--- A position's next action: AHSync writes it whole since M14 ("collect mail: 15 sold, 25s 65c waiting",
--- "undercut: relist at 4g 49s 99c", position_views); an older file's kind reads "post now", "post for the Sat 19:00
--- peak", "disenchant", "sell to a vendor" or "hold"; none is "—". Found stock adds "found, uses no capital".
---@param p table          Plan.DecodePosition's, or Plan.LocalPositions'
---@param nowMs number
---@param offsetMs number
---@return string
function PortfolioView.NextAction(p, nowMs, offsetMs)
  local text = ({ de = "disenchant", vendor = "sell to a vendor", hold = "hold" })[p.nextAction or ""] or p.nextAction or "—"
  if p.nextAction == "post" then
    if p.target == nil or p.target <= nowMs then
      text = "post now"
    else
      local day, clock = ViewModel.Clock(p.target, offsetMs)
      text = "post for the " .. day .. " " .. clock .. " peak"
    end
  end
  if (p.costLeft or 0) == 0 and not p.isLocal then text = text .. " · found, uses no capital" end
  return text
end

--- The Positions section: AHSync's positions as your newer events left them, then your local ones (EX18, A81).
---@param positions table[]  Plan.LivePositions(charId)
---@param ctx table          { lookOf, nowMs, offsetMs }
---@return table             { rows, foot }: a local row has isLocal; the foot says how much of committed is local;
---                          the % counts the est. net of rows that cost something (found stock, cost 0 and not local,
---                          is left out and named: "% leaves out Wool Cloth (found stock only)"; portfolio_view)
local function Positions(positions, ctx)
  local rows, committed, est, costed, withNet, spent, found = {}, 0, 0, 0, false, 0, {}
  for _, p in ipairs(positions) do
    committed = committed + (p.costLeft or 0)
    spent = spent + (p.isLocal and p.costLeft or 0)
    local isFound = (p.costLeft or 0) == 0 and not p.isLocal
    if p.netNow then est = est + p.netNow end
    if p.netNow and not isFound then costed, withNet = costed + p.netNow, true end
    if p.netNow and isFound then found[#found + 1] = ctx.lookOf(p.key).name end
    local left, units = p.unitsLeft or 0, p.units or p.unitsLeft or 0
    local look = ctx.lookOf(p.key)
    rows[#rows + 1] = { key = p.key, item = look.name, quality = look.quality,
                        qty = "×" .. left .. (left ~= units and (" of " .. units) or ""), state = p.state,
                        stateKind = PortfolioView.STAGE_KIND[p.state] or "hold", cost = ViewModel.Money(p.costLeft or 0),
                        net = p.netNow and ViewModel.SignedMoney(p.netNow) or "—",
                        netColor = p.netNow and (p.netNow > 0 and "pos" or "neg") or "muted",
                        next = PortfolioView.NextAction(p, ctx.nowMs, ctx.offsetMs), isLocal = p.isLocal }
  end
  local pct = withNet and PortfolioView.ProfitPct(costed, committed) or nil
  local leftOut = (pct and #found > 0) and (" · % leaves out " .. table.concat(found, ", ") .. " (found stock only)") or ""
  return { rows = rows, foot = "committed " .. ViewModel.Money(committed)
             .. (spent > 0 and (" (" .. ViewModel.Money(spent) .. " local)") or "") .. " · est. " .. ViewModel.SignedMoney(est)
             .. (pct and (" · " .. (pct >= 0 and "+" or "") .. pct .. "% on committed capital") or "") .. leftOut }
end

-- The empty block's second line, by its reason (portfolio_view.EMPTY_TEXTS, SPEC §3.4D.7).
PortfolioView.EMPTY_TEXTS = {
  ["no gold in hand"] = "No character had gold at the last snapshot.",
  ["market data too old"] = "The newest prices are more than 24 h old; run AHSync.",
  ["nothing pays"] = "No opportunity nets above 0 after fees, deposits and the chance it sells.",
  ["every candidate is excluded"] = "Each one has a reason: none fits your gold, professions or the market right now.",
}

--- Why nothing is funded, the first that applies (selection.empty_state, SPEC §3.4D.7, PF14, UI19).
---@param result table    Reselect.Run's result
---@param snapshot table  Plan.Portfolio
---@param ctx table       { liveGold, dataStale }
---@return table|nil      { title, text }; nil when something is funded
function PortfolioView.EmptyBlock(result, snapshot, ctx)
  if #result.funded > 0 then return nil end
  local reason
  local limit = PortfolioView.RiskLimit(snapshot)
  if (ctx.liveGold or 0) <= 0 then
    reason = "no gold in hand"
  elseif snapshot.committed >= limit then
    return { title = "Nothing funded: risk budget exhausted", text = ViewModel.Money(snapshot.committed)
             .. " committed of a " .. ViewModel.Money(limit) .. " limit. New rows come back as positions sell." }
  elseif ctx.dataStale then
    reason = "market data too old"
  elseif #result.excluded == 0 then
    reason = "nothing pays"
  else
    reason = "every candidate is excluded"
  end
  return { title = "Nothing funded: " .. reason, text = PortfolioView.EMPTY_TEXTS[reason] }
end

--- The Portfolio tab's data (SPEC §4.3; A77-A80, A84, UI19): the top line, Positions, Today and Next 7 days with
--- their funded rows first, then the excluded ones muted with their reasons, each section's totals, the footer and,
--- when nothing is funded, the empty block.
---@param result table     Reselect.Run's result (this character's live selection)
---@param snapshot table   Plan.Portfolio for the market
---@param positions table[] Plan.LivePositions(charId)
---@param ctx table        { nowMs, offsetMs, nameOf, lookOf (key -> look; default from nameOf), itemOf (key ->
---                        Market.Item; default none), totalGold, liveGold, charId, skills, dataStale, windowHoursOf,
---                        problem (status line or nil) }
---@return table           { top, problem, empty, positions = { rows, foot }, today = { rows, foot },
---                        week = { rows, foot }, footer = { funded, capacity (the right side: what stays unallocated,
---                        or the positions count) } }
function PortfolioView.Build(result, snapshot, positions, ctx)
  local lookOf = ctx.lookOf or function(key) return { name = ctx.nameOf(key), quality = 1 } end
  local rowCtx = { snapshot = snapshot, nowMs = ctx.nowMs, offsetMs = ctx.offsetMs, lookOf = lookOf,
                   itemOf = ctx.itemOf or function() return nil end, totalGold = ctx.totalGold,
                   windowHoursOf = ctx.windowHoursOf }
  local sections = { today = { funded = {}, excluded = {} }, week = { funded = {}, excluded = {} } }
  for _, rc in ipairs(result.funded) do
    local pick = result.picks[rc.c.id]
    table.insert(sections[pick.lst].funded, Row(rc, pick, nil, rowCtx))
  end
  local detailCtx = { itemOf = rowCtx.itemOf, skills = ctx.skills, charId = ctx.charId or "", capacity = result.capacity or 0,
                      left = result.capacityLeft or 0, offsetMs = ctx.offsetMs }
  for _, e in ipairs(result.excluded) do
    local pick = result.picks[e.rc.c.id]
    local row = Row(e.rc, pick, e.reason, rowCtx)
    row.detail = PortfolioView.Detail(e.rc, row.status, detailCtx)
    table.insert(sections[pick.lst].excluded, row)
  end
  local empty = PortfolioView.EmptyBlock(result, snapshot, ctx)
  local lead = empty and (ViewModel.ShortName(ctx.charId or "") .. " " .. ViewModel.Money(ctx.liveGold or 0)) or nil
  local out = { top = PortfolioView.TopLine(snapshot, ctx.nowMs, ctx.offsetMs, lead), problem = ctx.problem, empty = empty,
                positions = Positions(positions, { lookOf = lookOf, nowMs = ctx.nowMs, offsetMs = ctx.offsetMs }) }
  local all = {}
  for _, name in ipairs({ "today", "week" }) do
    local sec = sections[name]
    SortRows(sec.funded)
    SortExcluded(sec.excluded)
    local rows = {}
    for _, r in ipairs(sec.funded) do rows[#rows + 1] = r; all[#all + 1] = { net = r.netCopper, cash = r.cash } end
    for _, r in ipairs(sec.excluded) do rows[#rows + 1] = r end
    out[name] = { rows = rows, foot = PortfolioView.SectionFoot(sec.funded) }
  end
  local pct = PortfolioView.PoolPct(all)
  -- The capacity is in the top line; the footer keeps what is left of it (the user, 2026-11-07, 0.1.6).
  out.footer = { funded = "funded profit " .. ViewModel.SignedMoney(result.net) .. " · cost " .. ViewModel.Money(result.gold)
                   .. (pct and (" · " .. pct .. "% on cash") or ""),
                 capacity = ViewModel.Money(result.capacityLeft) .. " unallocated" }
  if empty then
    local n = #out.positions.rows
    out.footer = { funded = "funded profit " .. ViewModel.Money(0), capacity = n .. (n == 1 and " position" or " positions") }
  end
  return out
end

--- A status as the excluded-row tooltip writes it: "Exceeds risk tolerance: needs 45s, 24s 40c left" (UI19).
---@param status string  The full status text
---@return string
function PortfolioView.ReasonLine(status)
  local words, numbers = status:match("^(.-) %((.*)%)$")
  if words then return words .. ": " .. numbers end
  return status
end

--- The tooltip of excluded rows (SPEC §4.3, UI19): hovering one shows it; right-clicking lists every excluded row of
--- its section. Each row with its reason and detail and expected net, then the snapshot's risk limit and what was
--- funded before it.
---@param rows table[]      Excluded rows (Build's), in section order
---@param snapshot table    Plan.Portfolio
---@param charName string   "Brugan"
---@param section string    "today" | "week"
---@return table            { title, lines = { { text, colour, wrap } | { left, right } } }
function PortfolioView.ExcludedTooltip(rows, snapshot, charName, section)
  local lines = {}
  for _, r in ipairs(rows) do
    lines[#lines + 1] = { text = r.item .. " · " .. r.action .. " " .. r.qty, colour = "title" }
    lines[#lines + 1] = { text = PortfolioView.ReasonLine(r.status), colour = r.statusKind }
    if r.detail and r.detail ~= "" then lines[#lines + 1] = { text = r.detail, colour = "muted", wrap = true } end
    lines[#lines + 1] = { left = "Expected net", right = r.net }
  end
  lines[#lines + 1] = { left = "Risk limit", right = ViewModel.Money(PortfolioView.RiskLimit(snapshot)) .. " · committed "
                        .. ViewModel.Money(snapshot.committed) }
  lines[#lines + 1] = { left = "Funded before it", right = ViewModel.Money(snapshot.allocated) .. " of "
                        .. ViewModel.Money(snapshot.capacity) }
  return { title = (section == "today" and "Not funded today" or "Not funded this week") .. " (" .. charName .. ")",
           lines = lines }
end

--- One section's rows as list lines: each row (Today's below Min chance left out and counted, A84), its plan line
--- when funded and its sell record line (A52), and the excluded rows' tooltips.
---@param rows table[]     Build's rows of the section
---@param set string       "today" | "week"
---@param opts table       Lines' opts
---@param add function     Appends a line
---@return number          Rows hidden by Min chance
local function SectionRows(rows, set, opts, add)
  local hidden, excluded = 0, {}
  for _, r in ipairs(rows) do if not r.funded then excluded[#excluded + 1] = r end end
  for _, r in ipairs(rows) do
    if set == "today" and r.chanceValue and r.chanceValue < (opts.minChance or 0) then
      hidden = hidden + 1
    else
      r.lineKind, r.set = "row", set == "today" and "pfToday" or "pfWeek"
      r.height = (set == "week" or r.tall) and PortfolioView.TALL_HEIGHT or PortfolioView.ROW_HEIGHT
      if not r.funded then
        r.tip = PortfolioView.ExcludedTooltip({ r }, opts.snapshot, opts.charName, set)
        r.menuTip = PortfolioView.ExcludedTooltip(excluded, opts.snapshot, opts.charName, set)
      end
      add(r)
      if r.funded and set == "today" then add({ lineKind = "note", set = "pfToday", text = r.plan }) end
      local record = opts.recordOf and opts.recordOf(r.key)
      if record then add({ lineKind = "note", set = r.set, text = record.line }) end
    end
  end
  return hidden
end

--- Today's Needs a decision section (A52, A84): ViewModel.Decisions' lines as Portfolio lines.
---@param decisions table[]  ViewModel.Decisions' lines
---@param add function
local function DecisionLines(decisions, add)
  for _, d in ipairs(decisions or {}) do
    if d.header then
      add({ lineKind = "section", label = d.label, right = "expired 3× in a row while cheapest · rules apply to every character" })
    else
      d.lineKind, d.set, d.status, d.statusKind = "row", "pfToday", "NOT SELLING", "neg"
      add(d)
      for _, text in ipairs(d.notes or {}) do add({ lineKind = "note", set = "pfToday", text = text }) end
    end
  end
end

--- The Portfolio tab as the window's list lines (SPEC §4.3, UI16): Positions, Today (funded, then unfunded, its
--- totals line, then Needs a decision) and Next 7 days, each section with its column headers and totals; with
--- nothing funded the empty block replaces Today and Next 7 days (UI19). Min chance only hides rows (A84).
---@param tab table   Build's
---@param opts table  { minChance (percent), recordOf (key -> { line } or nil), decisions (ViewModel.Decisions'),
---                   snapshot, charName }
---@return table      { lines, hidden (Today rows hidden by Min chance), footer (with the hidden count) }
function PortfolioView.Lines(tab, opts)
  local lines = {}
  --- Append one line.
  ---@param line table
  local function Add(line) lines[#lines + 1] = line end
  local n = #tab.positions.rows
  Add({ lineKind = "section", label = "Positions", right = "this character · " .. n })
  if n > 0 then Add({ lineKind = "colhead", set = "pfPositions" }) end
  for _, r in ipairs(tab.positions.rows) do
    r.lineKind, r.set = "row", "pfPositions"
    Add(r)
  end
  Add({ lineKind = "foot", text = n > 0 and tab.positions.foot or "no open positions" })
  local hidden = 0
  if tab.empty then
    Add({ lineKind = "empty", title = tab.empty.title, text = tab.empty.text, height = PortfolioView.EMPTY_HEIGHT })
    DecisionLines(opts.decisions, Add)
  else
    Add({ lineKind = "section", label = "Today", right = "realized today · funded, then unfunded" })
    Add({ lineKind = "colhead", set = "pfToday" })
    hidden = SectionRows(tab.today.rows, "today", opts, Add)
    Add({ lineKind = "foot", text = tab.today.foot .. (hidden > 0 and (" · " .. hidden .. " hidden by Min chance") or "") })
    DecisionLines(opts.decisions, Add)
    Add({ lineKind = "section", label = "Next 7 days", right = "net → adjusted · wait · premium" })
    Add({ lineKind = "colhead", set = "pfWeek" })
    SectionRows(tab.week.rows, "week", opts, Add)
    Add({ lineKind = "foot", text = tab.week.foot })
  end
  local footer = tab.footer.funded
  if hidden > 0 then footer = footer .. " · " .. hidden .. " hidden (chance < " .. (opts.minChance or 0) .. "%)" end
  return { lines = lines, hidden = hidden, footer = footer }
end
