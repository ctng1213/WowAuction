-- UI/TooltipView.lua: the item tooltip's AHProfit block as view-model lines (SPEC §4.3 Tooltip; TT8-TT21, UI17).
-- Pure: the facts UI/Tooltip.lua gathers (the market file and a newer scan, this character's plan actions, its
-- reservations, the live reselection's unfunded rows, the addon's own option) come in; two-column lines of text with
-- colour names come out, every price per unit. Deltas keep views.delta_percent's rule (TT1-TT5); the two-column text
-- rules are the addon's own (the dashboard has the item page). UI/Tooltip.lua only draws the lines.

local AHProfit = AHProfit
local ViewModel = AHProfit.ViewModel

local TooltipView = {}
AHProfit.TooltipView = TooltipView

local HOUR_MS, DAY_MS = 3600000, 86400000
TooltipView.WIDTH = 320          -- px the block may take (SPEC §4.3)
TooltipView.FONT = 10            -- px: the block's lines use the small tooltip font
TooltipView.GAP = 16             -- px between a double line's two columns
TooltipView.WEEKDAYS = { "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun" }
-- The Do verb's badge colour (SPEC §4.3): POST gold, BUY blue, CRAFT orange, DISENCHANT purple, VENDOR and HOLD grey,
-- MAIL light blue, KEPT and UNFUNDED muted.
TooltipView.VERB_COLOR = { POST = "post", BUY = "buy", CRAFT = "craft", DISENCHANT = "de", VENDOR = "grey",
                           HOLD = "grey", MAIL = "mail", KEPT = "muted", UNFUNDED = "muted" }
-- A strategy's words in an UNFUNDED Do line.
TooltipView.STRATEGY_WORDS = { flip = "flip", buy_later = "buy for later", craft = "craft", multistage = "craft",
                               disenchant = "disenchant", sell_stock = "sale" }

--- Delta against the current price: (value − cur) ÷ cur × 100, half up (views.delta_percent, TT1-TT5).
---@param value number|nil
---@param current number|nil
---@return number|nil  Whole percent; nil when either is missing or cur is 0
function TooltipView.DeltaPercent(value, current)
  if value == nil or not current or current == 0 then return nil end
  return ViewModel.RoundHalfUp((value - current) / current * 100)
end

--- A delta as shown, "(+7%)", and its colour: green above the price now, red below (SPEC §4.3).
---@param value number|nil
---@param current number|nil
---@return string|nil   "+7%" without brackets; nil when there is no delta
---@return string|nil   "pos" | "neg" | nil
function TooltipView.Delta(value, current)
  local delta = TooltipView.DeltaPercent(value, current)
  if delta == nil then return nil, nil end
  local sign = delta > 0 and "+" or (delta < 0 and "−" or "")
  return sign .. math.abs(delta) .. "%", delta > 0 and "pos" or (delta < 0 and "neg" or nil)
end

--- A window's hours in local time, "Sat 17–21" (SPEC §4.3: hour ranges, so a line fits 320 px).
---@param w table          { start (UTC slot 0-167, 0 = Mon 00:00), hours }
---@param offsetMs number  Local time minus UTC
---@return string
function TooltipView.Hours(w, offsetMs)
  local start = (w.start + math.floor(offsetMs / HOUR_MS)) % 168
  return string.format("%s %02d–%02d", TooltipView.WEEKDAYS[math.floor(start / 24) + 1], start % 24,
                       (start + w.hours) % 24)
end

--- A window's strength from its weeks of data (SPEC §3.3.4).
---@param weeks number
---@return string  "early" (under 3), "pattern" (3-5) or "established" (6 or more)
function TooltipView.Strength(weeks)
  if weeks < 3 then return "early" end
  if weeks < 6 then return "pattern" end
  return "established"
end

--- Whether now falls inside a window (TT21).
---@param w table        { start, hours }
---@param nowMs number
---@return boolean
function TooltipView.InWindow(w, nowMs)
  local hour = math.floor(nowMs / HOUR_MS)
  local current = (math.floor(hour / 24) + 3) % 7 * 24 + hour % 24       -- Prices.DaysUntilSlot's slot of now
  return (current - w.start) % 168 < w.hours
end

--- How old a price is, for the Market line: "now", "25 min", "2 h", "3 d".
---@param ageMs number|nil
---@return string
function TooltipView.AgeText(ageMs)
  local minutes = math.floor(math.max(0, ageMs or 0) / 60000)
  if minutes < 1 then return "now" end
  if minutes < 60 then return minutes .. " min" end
  if minutes < 1440 then return math.floor(minutes / 60) .. " h" end
  return math.floor(minutes / 1440) .. " d"
end

--- A double line's estimated width in px (SPEC §4.3: at most 320).
---@param label string
---@param value string
---@return number
function TooltipView.Width(label, value)
  local w = ViewModel.TextWidth(label, TooltipView.FONT)
  if value ~= "" then w = w + TooltipView.GAP + ViewModel.TextWidth(value, TooltipView.FONT) end
  return w
end

--- One line.
---@param label string
---@param value string
---@param spans table|nil  { { text, colour } } coloured parts of the value
---@return table
local function Line(label, value, spans)
  return { label = label, value = value, spans = spans or {} }
end

--- The Market line: the price now with its age, then the 30-day median and its delta.
---@param m table  { cur (copper or nil), ageText ("now" at the AH, else "2 h"), med30 }
---@return table   A line
function TooltipView.MarketLine(m)
  local head = "none listed"
  if m.cur then head = ViewModel.Money(m.cur) .. (m.ageText == "now" and " now" or (" (" .. m.ageText .. ")")) end
  if not m.med30 then return Line("Market", head) end
  local delta, colour = TooltipView.Delta(m.med30, m.cur)
  local value = head .. " · " .. ViewModel.Money(m.med30) .. " 30d" .. (delta and (" (" .. delta .. ")") or "")
  return Line("Market", value, delta and { { "(" .. delta .. ")", colour } } or nil)
end

--- A value with its delta: "14s 25c (+78%)".
---@param label string
---@param price number    Copper
---@param cur number|nil
---@return table          A line
local function PriceLine(label, price, cur)
  local delta, colour = TooltipView.Delta(price, cur)
  return Line(label, ViewModel.Money(price) .. (delta and (" (" .. delta .. ")") or ""),
              delta and { { "(" .. delta .. ")", colour } } or nil)
end

--- Best time to sell: "Fri 18–21 · 45s (+7%) · pattern", or "now (Fri 18–21) · 45s · pattern" inside it (TT21).
---@param s table        { label, price, weeks, now }
---@param cur number|nil
---@return table
function TooltipView.SellLine(s, cur)
  local strength = TooltipView.Strength(s.weeks)
  if s.now then
    return Line("Best time to sell", "now (" .. s.label .. ") · " .. ViewModel.Money(s.price) .. " · " .. strength)
  end
  local delta, colour = TooltipView.Delta(s.price, cur)
  local value = s.label .. " · " .. ViewModel.Money(s.price) .. (delta and (" (" .. delta .. ")") or "") .. " · " .. strength
  return Line("Best time to sell", value, delta and { { "(" .. delta .. ")", colour } } or nil)
end

--- Best time to buy: the top window, or "now · cheaper than Tue 20–23 (7s 10c)" when the price now is below every
--- buy window's expected price, shortened to "now · under …" when that would pass 320 px (TT14, TT16).
---@param b table        { label, price, weeks, cheaperNow }
---@param cur number|nil
---@return table
function TooltipView.BuyLine(b, cur)
  if b.cheaperNow then
    local tail = b.label .. " (" .. ViewModel.Money(b.price) .. ")"
    local value = "now · cheaper than " .. tail
    if TooltipView.Width("Best time to buy", ViewModel.WithCoins(value)) > TooltipView.WIDTH then
      value = "now · under " .. tail
    end
    return Line("Best time to buy", value)
  end
  local delta, colour = TooltipView.Delta(b.price, cur)
  local value = b.label .. " · " .. ViewModel.Money(b.price) .. (delta and (" (" .. delta .. ")") or "") .. " · "
    .. TooltipView.Strength(b.weeks)
  return Line("Best time to buy", value, delta and { { "(" .. delta .. ")", colour } } or nil)
end

--- Activity, muted: "6/day · 14 listed · 2.3 d supply"; "0/day · 0 listed" when nothing sells (SPEC §3.3.6).
---@param a table  { soldPerDay (units, nil unknown), listed (units now) }
---@return table
function TooltipView.ActivityLine(a)
  local listed = a.listed or 0
  local parts = { (a.soldPerDay and ViewModel.RoundHalfUp(a.soldPerDay) or "—") .. "/day", listed .. " listed" }
  if a.soldPerDay and a.soldPerDay > 0 then
    local tenths = ViewModel.RoundHalfUp(listed * 10 / a.soldPerDay)
    parts[3] = math.floor(tenths / 10) .. "." .. tenths % 10 .. " d supply"
  end
  local line = Line("Activity", table.concat(parts, " · "))
  line.muted = true
  return line
end

--- A Do line's verb, the rest of its left text, its value and the value-only lines under it (SPEC §4.3 Do).
---@param d table  From TooltipView.DoOf
---@param cur number|nil
---@return string, string, string, table, string|nil, string[]  verb, rest, value, spans, value colour, second lines
local function DoParts(d, cur)
  local M, S = ViewModel.Money, ViewModel.SignedMoney
  if d.kind == "post" and d.now then
    local net = S(d.net)
    return "POST", " " .. d.qty .. " now at " .. M(d.price), net .. " · " .. (d.chance or "—") .. "% chance",
           { { net, d.net < 0 and "neg" or "pos" } }, nil, { d.next or "then nothing" }
  elseif d.kind == "post" then
    local delta, colour = TooltipView.Delta(d.price, cur)
    local value = M(d.price) .. (delta and (" · " .. delta .. " vs now") or "") .. (d.chance and (" · " .. d.chance .. "%") or "")
    return "POST", " " .. d.at, value, delta and { { delta, colour } } or {}, nil,
           d.peak and { "for the " .. d.peak .. " peak" } or {}
  elseif d.kind == "hold" then
    return "HOLD", " until " .. d["until"], "no window pays this week", {}, nil, {}
  elseif d.kind == "craft" then
    local net, steps = S(d.net), {}
    if d.mats and d.mats > 0 then steps[#steps + 1] = "mats " .. M(d.mats) end
    if d.next then steps[#steps + 1] = d.next end
    return "CRAFT", " " .. d.qty .. " " .. d.product, net .. (d.chance and (" · " .. d.chance .. "%") or ""),
           { { net, d.net < 0 and "neg" or "pos" } }, nil, #steps > 0 and { table.concat(steps, " · ") } or {}
  elseif d.kind == "mail" then
    return "MAIL", " " .. d.qty .. " to " .. d.to, d.product and ("for crafting " .. d.product) or "for its plan", {}, nil, {}
  elseif d.kind == "de" then
    local net = S(d.net)
    return "DISENCHANT", "", net .. (d.mats and (" · mats " .. M(d.mats)) or "") .. " · " .. (d.chance or 100) .. "%",
           { { net, d.net < 0 and "neg" or "pos" } }, nil, {}
  elseif d.kind == "buy" then
    return "BUY", " ≤ " .. M(d.max) .. (d.purpose and (" · " .. d.purpose) or ""), "funded · " .. d.qty .. " needed",
           {}, nil, {}
  elseif d.kind == "unfunded" then
    return "UNFUNDED", " " .. d.strategy .. " · " .. S(d.net), d.short, {}, "amber", d.detail and { d.detail } or {}
  elseif d.kind == "kept" then
    return "KEPT", " for yourself", d.all and "keep all" or ("keep at least " .. d.keep), {}, nil, { "right-click to change" }
  end
  return "VENDOR", " " .. M(d.price) .. " each", "nothing better", {}, nil, {}
end

--- Append a note to the Do line's last value-only line when it fits, else on a line of its own (TT14, TT21).
---@param seconds string[]  Changed
---@param text string
local function AddNote(seconds, text)
  local last = seconds[#seconds]
  if last and TooltipView.Width("", ViewModel.WithCoins(last .. " · " .. text)) <= TooltipView.WIDTH then
    seconds[#seconds] = last .. " · " .. text
  else
    seconds[#seconds + 1] = text
  end
end

--- The Do line and the value-only lines under it (SPEC §4.3): a long value continues on a line with an empty label.
---@param d table          From TooltipView.DoOf
---@param cur number|nil   The price now
---@return table[]         Lines; the first has verb, verbColor and rest (its label is "Do: " .. verb .. rest)
function TooltipView.DoLines(d, cur)
  local verb, rest, value, spans, colour, seconds = DoParts(d, cur)
  if d.sellNow then AddNote(seconds, "best time to sell is now") end
  if d.buyNow then AddNote(seconds, "best time to buy is now") end
  local first = Line("Do: " .. verb .. rest, value, spans)
  first.verb, first.rest, first.verbColor, first.valueColor, first.isDo = verb, rest, TooltipView.VERB_COLOR[verb], colour, true
  local lines = { first }
  if TooltipView.Width(ViewModel.WithCoins(first.label), ViewModel.WithCoins(value)) > TooltipView.WIDTH then
    first.value, first.spans = "", {}
    local moved = Line("", value, spans)
    moved.valueColor = colour
    lines[2] = moved
  end
  for _, text in ipairs(seconds) do lines[#lines + 1] = Line("", text) end
  return lines
end

--- The whole block (SPEC §4.3 Tooltip, TT8-TT21): the header with the data age, the Do line first, then Market,
--- Disenchant (only when one of your characters can), Best time to sell and to buy (never dropped; none without
--- windows, W7) and Activity. Without market data: the header and "no market data yet" (TT18).
---@param f table  { age = { text, amber }, noData, doing (DoOf's), market = { cur, ageText, med30 }, canDisenchant,
---                deValue, sell = { label, price, weeks, now } or nil, buy = { label, price, weeks, cheaperNow } or nil,
---                activity = { soldPerDay, listed } }
---@return table[] Lines { label, value, spans, muted, header, amber, isDo, verb, rest, verbColor, valueColor }
function TooltipView.Lines(f)
  local header = Line("AHProfit", f.age.text)
  header.header, header.amber = true, f.age.amber
  local lines = { header }
  if f.noData then
    local only = Line("no market data yet", "")
    only.labelMuted = true
    lines[2] = only
    return lines
  end
  local cur = f.market.cur
  if f.doing then
    for _, line in ipairs(TooltipView.DoLines(f.doing, cur)) do lines[#lines + 1] = line end
  end
  lines[#lines + 1] = TooltipView.MarketLine(f.market)
  if f.canDisenchant and f.deValue then lines[#lines + 1] = PriceLine("Disenchant", ViewModel.RoundHalfUp(f.deValue), cur) end
  if f.sell then lines[#lines + 1] = TooltipView.SellLine(f.sell, cur) end
  if f.buy then lines[#lines + 1] = TooltipView.BuyLine(f.buy, cur) end
  lines[#lines + 1] = TooltipView.ActivityLine(f.activity or {})
  return lines
end

-- ------------------------------------------------------------------ what to do with the item

--- A reason split into its words and its numbers (SPEC §3.4D.6): "Exceeds risk tolerance (needs 45s, 24s 40c left)"
--- -> "exceeds risk tolerance", "needs 45s, 24s 40c left"; "Insufficient liquidity: 3/day, 40 planned" ->
--- "insufficient liquidity", "3/day, 40 planned".
---@param reason string
---@return string        The words, first letter lower case
---@return string|nil    The numbers; nil when the reason has none
function TooltipView.SplitReason(reason)
  local words, detail = reason:match("^(.-) %((.*)%)$")
  if not words then words, detail = reason:match("^(.-): (.*)$") end
  words = words or reason
  return words:sub(1, 1):lower() .. words:sub(2), detail
end

--- An action's chain: every action with the same chain id, in time order.
---@param actions table[]
---@param a table
---@return table[]
local function ChainOf(actions, a)
  local id, out = a.parent or a.id, {}
  for _, b in ipairs(actions) do
    if (b.parent or b.id) == id then out[#out + 1] = b end
  end
  table.sort(out, ViewModel.ByTime)
  return out
end

--- This character's first open plan action on the item: a post, hold, vendor sale, disenchant or mail of it, or a
--- craft whose recipe uses it (a mat in the bags, TT11).
---@param ctx table  DoOf's
---@return table|nil
local function FirstAction(ctx)
  local best = nil
  for _, a in ipairs(ctx.actions or {}) do
    local own = a.key == ctx.key and (a.kind == "post" or a.kind == "hold" or a.kind == "vendor" or a.kind == "de"
                                      or a.kind == "mail")
    local mat = a.kind == "craft" and a.key ~= ctx.key and ctx.usedBy and ctx.usedBy(a.key, ctx.key)
    if (own or mat) and (best == nil or ViewModel.ByTime(a, best)) then best = a end
  end
  return best
end

--- A post's Do, now or later.
---@param a table    The post
---@param ctx table
---@return table
local function PostDo(a, ctx)
  if a.at <= ctx.nowMs + HOUR_MS then
    return { kind = "post", now = true, qty = a.qty, price = a.price, net = a.net or 0, chance = a.chance }
  end
  local day, clock = ViewModel.Clock(a.at, ctx.offsetMs)
  local peak = a.peak and select(2, ViewModel.Clock(a.peak, ctx.offsetMs)) or nil
  return { kind = "post", now = false, at = day .. " " .. clock, price = a.price, chance = a.chance, peak = peak }
end

--- A craft's Do for one of its mats: the product, the chain's net and its first post's chance, the mats it buys
--- and when it posts (TT11).
---@param a table    The craft
---@param ctx table
---@return table
local function CraftDo(a, ctx)
  local mats, chance, nextStep = 0, nil, nil
  for _, b in ipairs(ChainOf(ctx.actions, a)) do
    if b.kind == "buy" then mats = mats + (b.price or 0) * b.qty end
    if b.kind == "post" and not nextStep then
      chance = b.chance
      local day, clock = ViewModel.Clock(b.at, ctx.offsetMs)
      nextStep = "then post " .. day .. " " .. clock
    end
  end
  return { kind = "craft", qty = a.qty, product = ctx.nameOf(a.key), net = a.net or 0, chance = chance, mats = mats,
           next = nextStep }
end

--- A hold's Do: until the weekday it moves on, "next" when that is more than six days away (TT10).
---@param a table
---@param ctx table
---@return table
local function HoldDo(a, ctx)
  local at = a.peak or a.at
  local day = ViewModel.Clock(at, ctx.offsetMs)
  return { kind = "hold", ["until"] = ((at - ctx.nowMs > 6 * DAY_MS) and "next " or "") .. day }
end

--- A mail's Do: to whom, and what the recipient crafts with it (TT12).
---@param a table
---@param ctx table
---@return table
local function MailDo(a, ctx)
  local product, all = nil, ctx.allActions
  if type(all) == "function" then all = all() end           -- read only for a mail: every character's actions
  for _, b in ipairs(ChainOf(all or ctx.actions, a)) do
    if b.kind == "craft" and not product then product = ctx.nameOf(b.key) end
  end
  return { kind = "mail", qty = a.qty, to = ViewModel.ShortName(a.to or "?"), product = product }
end

--- The Do of a plan action.
---@param a table
---@param ctx table
---@return table
local function FromAction(a, ctx)
  if a.kind == "post" then return PostDo(a, ctx) end
  if a.kind == "craft" then return CraftDo(a, ctx) end
  if a.kind == "hold" then return HoldDo(a, ctx) end
  if a.kind == "mail" then return MailDo(a, ctx) end
  if a.kind == "de" then return { kind = "de", net = a.net or 0, mats = ctx.deValue, chance = a.chance } end
  return { kind = "vendor", price = a.price }
end

--- An unfunded alternative's Do (TT15): its strategy and net, then why the portfolio left it out.
---@param u table  { strategy, net, reason }
---@return table
local function UnfundedDo(u)
  local short, detail = TooltipView.SplitReason(u.reason)
  return { kind = "unfunded", strategy = TooltipView.STRATEGY_WORDS[u.strategy] or u.strategy,
           net = ViewModel.RoundHalfUp(u.net), short = short, detail = detail }
end

--- What to do with the item, the first that applies (SPEC §4.3 Do): kept for yourself; on an AH listing the
--- Shopping list's buy (else an unfunded buy, else nothing); this character's plan action; its unfunded alternative;
--- the addon's own option for what it holds.
---@param ctx table  { key, held (units in bags, bank and mail), reserve (units kept, Reserves.ALL = all; nil), listing
---                  (an AH listing, not a bag item), shopping = { qty, price, purpose } (the Shopping list's buy) or nil,
---                  actions (this character's open plan actions), allActions (every character's, or a function
---                  returning them, called only for a mail), unfunded = {
---                  strategy, net, reason } or nil, own (Options.SellOne's action) or nil, deValue, usedBy (product,
---                  key -> whether its recipe uses key), nameOf, nowMs, offsetMs, sellNow, buyNow }
---@return table|nil The Do for DoLines; nil for an AH listing on none of your lists, or nothing to do
function TooltipView.DoOf(ctx)
  local d = nil
  if ctx.reserve and (ctx.held or 0) > 0 then
    d = { kind = "kept", all = ctx.reserve < 0, keep = ctx.reserve }
  elseif ctx.listing then
    if ctx.shopping then
      d = { kind = "buy", max = ctx.shopping.price, qty = ctx.shopping.qty, purpose = ctx.shopping.purpose,
            buyNow = ctx.buyNow }
    elseif ctx.unfunded then
      d = UnfundedDo(ctx.unfunded)
    end
  else
    local a = FirstAction(ctx)
    if a then d = FromAction(a, ctx)
    elseif ctx.unfunded then d = UnfundedDo(ctx.unfunded)
    elseif ctx.own then
      local o = ctx.own
      d = o.kind == "post" and { kind = "post", now = true, qty = o.qty, price = o.price, net = o.net, chance = o.chance }
          or { kind = "vendor", price = o.price }
    end
  end
  if d and d.kind == "post" and d.now and ctx.sellNow then d.sellNow = true end
  return d
end
