-- UI/TooltipView.lua: the item tooltip's AHProfit block as view-model lines (SPEC §4.3 Tooltip; TT8-TT31, UI17).
-- Pure: the facts UI/Tooltip.lua gathers (the market file and a newer scan, this character's plan actions, its
-- reservations, the live reselection's unfunded rows, the addon's own option) come in; lines of text with colour
-- names come out, every price per unit. The header and the Do lines are two-column; the market lines are a title in
-- its section's colour with its value right after it, the lines under it indented, and a divider between sections
-- (TT42). Deltas keep views.delta_percent's rule (TT1-TT5); the text rules are the addon's own (the dashboard has the
-- item page). UI/Tooltip.lua only draws the lines.

local AHProfit = AHProfit
local ViewModel = AHProfit.ViewModel

local TooltipView = {}
AHProfit.TooltipView = TooltipView

local HOUR_MS, DAY_MS = 3600000, 86400000
TooltipView.WIDTH = 320          -- px the block may take (SPEC §4.3)
TooltipView.FONT = 10            -- px: the block's lines use the small tooltip font
TooltipView.GAP = 16             -- px between a double line's two columns
TooltipView.JOIN = "  "          -- between a joined line's title and its value (TT42)
TooltipView.INDENT = "   "       -- before a line under a title (TT42)
-- UNVERIFIED: that a tooltip font string draws leading spaces rather than trimming them, as on Classic.
TooltipView.WEEKDAYS = { "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun" }
TooltipView.DAYS = { "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday" }
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

--- A market line's title (TT42): its value right after the title, the title in its section's colour.
---@param line table      A line
---@param section string  "price" | "stock" | "chance" | "time" | "de" | "own"
---@return table          The same line
local function Section(line, section)
  line.joined, line.section = true, section
  return line
end

--- A line under a title (TT42): indented from the left edge.
---@param line table  A line
---@return table      The same line
local function Under(line)
  line.indent = true
  return line
end

--- A joined or indented line's text without colours, as drawn: indent, title, joiner, value (TT42).
---@param line table  A line
---@return string
function TooltipView.JoinedText(line)
  local text = line.indent and TooltipView.INDENT or ""
  if line.label ~= "" then text = text .. line.label .. (line.joiner or TooltipView.JOIN) end
  return text .. line.value
end

--- A line's estimated width in px (SPEC §4.3: at most 320): a joined or indented line as one text, a divider none,
--- else the double line's two columns.
---@param line table  A line
---@return number
function TooltipView.LineWidth(line)
  if line.divider then return 0 end
  if line.joined or line.indent then
    return ViewModel.TextWidth(ViewModel.WithCoins(TooltipView.JoinedText(line)), TooltipView.FONT)
  end
  return TooltipView.Width(ViewModel.WithCoins(line.label), ViewModel.WithCoins(line.value))
end

--- The qualifier word for Market Price and Market Inventory (SPEC §4.3, TT34): rising when now > 7d > 14d > 30d
--- with each step at least 5% up, falling the mirror; otherwise, against the 7-day value, high at 10% or more above,
--- low at 10% or more below, else steady. Integers only, so no float rounding at a boundary.
---@param now number|nil
---@param v7 number|nil
---@param v14 number|nil
---@param v30 number|nil
---@return string|nil, string|nil  The word and its colour ("pos", "neg", "amber" or nil); nil without now and 7d
function TooltipView.Qualifier(now, v7, v14, v30)
  if not (now and v7) then return nil, nil end
  if v14 and v30 then
    if now * 100 >= v7 * 105 and v7 * 100 >= v14 * 105 and v14 * 100 >= v30 * 105 then return "rising", "pos" end
    if now * 100 <= v7 * 95 and v7 * 100 <= v14 * 95 and v14 * 100 <= v30 * 95 then return "falling", "neg" end
  end
  if now * 10 >= v7 * 11 then return "high", "amber" end
  if now * 10 <= v7 * 9 then return "low", "amber" end
  return "steady", nil
end

--- A title line with its right text and a qualifier at its end, coloured as the line's tail (never found by text).
---@param label string
---@param value string
---@param word string|nil
---@param colour string|nil
---@return table  A line
local function Titled(label, value, word, colour)
  if not word then return Line(label, value) end
  local line = Line(label, value ~= "" and (value .. " · " .. word) or word)
  if colour then line.tail = { word, colour } end
  return line
end

--- The 7, 14 and 30 day values under a title, indented: "(7d) 21c · (14d) 21c · (30d) 21c" (TT35-TT37, TT42).
---@param show function  One value as text; called only with a value
---@param v7 any
---@param v14 any
---@param v30 any
---@return table  A value-only line
local function Lookbacks(show, v7, v14, v30)
  --- One lookback as shown, "—" when it has no value.
  ---@param span string
  ---@param v any
  ---@return string
  local function One(span, v) return "(" .. span .. ") " .. (v ~= nil and show(v) or "—") end
  return Under(Line("", One("7d", v7) .. " · " .. One("14d", v14) .. " · " .. One("30d", v30)))
end

--- A share in thousandths as a whole percent, half up (SPEC §3.3): 744 -> "74%", 62 -> "6%".
---@param milli number
---@return string
function TooltipView.Percent(milli)
  return math.floor((milli + 5) / 10) .. "%"
end

--- Market Price (TT35): the lowest listing with its age and the qualifier, then the 7, 14 and 30-day medians.
---@param m table  { cur (copper or nil), ageText ("now" at the AH, else "2 h"), med7, med14, med30 }
---@return table[] Lines
function TooltipView.MarketPriceLines(m)
  local values = Lookbacks(ViewModel.Money, m.med7, m.med14, m.med30)
  if not m.cur then return { Section(Line("Market Price", "none listed"), "price"), values } end
  local head = ViewModel.Money(m.cur) .. (m.ageText == "now" and " now" or (" (" .. m.ageText .. ")"))
  local word, colour = TooltipView.Qualifier(m.cur, m.med7, m.med14, m.med30)
  return { Section(Titled("Market Price", head, word, colour), "price"), values }
end

--- Market Inventory (TT36): a stock, the units listed now and the qualifier, then the typical units listed over 7,
--- 14 and 30 days.
---@param m table  { listed (units or nil), supply7, supply14, supply30 }
---@return table[] Lines
function TooltipView.MarketInventoryLines(m)
  local listed = m.listed or 0
  local word, colour = TooltipView.Qualifier(listed, m.supply7, m.supply14, m.supply30)
  return { Section(Titled("Market Inventory", ViewModel.Thousands(listed) .. " listed now", word, colour), "stock"),
           Lookbacks(ViewModel.Thousands, m.supply7, m.supply14, m.supply30) }
end

--- Sell Chance Now (TT37): the chance a 24 h post at the market price sells before it expires, at the price it was
--- made for, "est." when the item is no commodity or a lookback borrowed from its class; then the measured
--- sell-through at the lowest price over 7, 14 and 30 days.
---@param m table  { chance1000 or nil, price (copper or nil), commodity (1, 0 or nil), st = { st7, st14, st30 }, stHow }
---@return table[] Lines
function TooltipView.SellChanceLines(m)
  local at = m.price and (" at " .. ViewModel.Money(m.price)) or ""
  local head = (m.chance1000 and TooltipView.Percent(m.chance1000) or "—") .. at
  local est = m.stHow and m.stHow:find("c", 1, true) and "class est." or (m.commodity == 0 and "est." or nil)
  local st = m.st or {}
  return { Section(Titled("Sell Chance Now", head, est, est and "amber" or nil), "chance"),
           Lookbacks(TooltipView.Percent, st[1], st[2], st[3]) }
end


--- A local hour of the week in words (SPEC §4.3): Morning 06-12, Afternoon 12-18, Evening 18-23, Night 23-06, an
--- hour after midnight belonging to the evening before (Tue 02:00 is "Monday Night").
---@param slot number      UTC hour of the week, 0 = Monday 00:00
---@param offsetMs number  Local time minus UTC
---@return string          e.g. "Saturday Evening"
function TooltipView.DayPart(slot, offsetMs)
  local local_ = (slot + math.floor(offsetMs / HOUR_MS)) % 168
  local day, hour = math.floor(local_ / 24), local_ % 24
  if hour < 6 then return TooltipView.DAYS[(day + 6) % 7 + 1] .. " Night" end
  local part = hour < 12 and "Morning" or hour < 18 and "Afternoon" or hour < 23 and "Evening" or "Night"
  return TooltipView.DAYS[day + 1] .. " " .. part
end

--- Best Buy / Best Sell Time (TT38): the window's strength after the title (early in orange), then its expected price,
--- its best hour in words ("now" while it holds) and its percentage ("—" without one), "est." for a selling window of
--- an item that is no commodity.
---@param label string     "Best Buy Time" | "Best Sell Time"
---@param w table          { price, slot (the best hour), weeks, now, pct1000 or nil, est }
---@param offsetMs number
---@return table[]         Two lines
function TooltipView.BestLines(label, w, offsetMs)
  local strength = TooltipView.Strength(w.weeks)
  local title = Section(Line(label, strength), "time")
  if strength == "early" then title.tail = { strength, "amber" } end
  local pct = w.pct1000 and TooltipView.Percent(w.pct1000) or "—"
  local value = ViewModel.Money(w.price) .. " · " .. (w.now and "now" or TooltipView.DayPart(w.slot, offsetMs)) .. " · "
    .. pct .. (w.est and " est." or "")
  return { title, Under(Line("", value)) }
end

--- An average quantity: whole when it is, else one decimal ("1.5", "3.5", "1").
---@param lo number
---@param hi number
---@return string
local function Quantity(lo, hi)
  local avg = (lo + hi) / 2
  if avg == math.floor(avg) then return tostring(avg) end
  return string.format("%.1f", avg)
end

--- Disenchant Value (TT39): a title with the comparison against the AH price first, then the expected value of one
--- disenchant at the mats' current prices (the sum of the unrounded shares); a line per possible mat with its share
--- (chance x average quantity x the mat's latest price), indented: "80% · 1.5 Strange Dust @ 25c = 30c" (TT42).
---@param mats table[]   { chance (0-1), min, max, name, price (copper, the mat's latest; nil = no price) }
---@param cur number|nil The item's price now
---@return table[]       Lines
function TooltipView.DisenchantLines(mats, cur)
  local total, lines = 0, {}
  for _, m in ipairs(mats) do
    local label = ViewModel.RoundHalfUp(m.chance * 100) .. "% · " .. Quantity(m.min, m.max) .. " " .. m.name
    local line
    if m.price then
      local share = m.chance * (m.min + m.max) / 2 * m.price
      total = total + share
      line = Line(label .. " @ " .. ViewModel.Money(m.price), ViewModel.Money(ViewModel.RoundHalfUp(share)))
      line.joiner = " = "
    else
      line = Line(label, "no price")
      line.valueColor, line.joiner = "amber", " · "
    end
    line.labelMuted, line.joined = true, true
    Under(line)
    lines[#lines + 1] = line
  end
  local value = ViewModel.Money(ViewModel.RoundHalfUp(total))
  local delta = TooltipView.DeltaPercent(total, cur)
  local title = Line("Disenchant Value", value)
  if delta then
    local text = delta == 0 and "same as AH" or (math.abs(delta) .. "% " .. (delta > 0 and "above" or "below") .. " AH")
    title = Line("Disenchant Value", text .. " · " .. value, delta ~= 0 and { { text, delta > 0 and "pos" or "neg" } } or nil)
  end
  table.insert(lines, 1, Section(title, "de"))
  return lines
end

--- Vendor Price (TT41): what a vendor pays for one unit, and for all you hold when that is more than one; nil when
--- the price is unknown or 0 (the item can't be sold to a vendor).
---@param price number|nil  Copper per unit
---@param held number       Units you hold across your characters (the Inventory line's total)
---@return table|nil        A line
function TooltipView.VendorLine(price, held)
  if not price or price <= 0 then return nil end
  if held > 1 then
    return Section(Line("Vendor Price", ViewModel.Money(price) .. " each · " .. ViewModel.Money(price * held) .. " for "
      .. held), "own")
  end
  return Section(Line("Vendor Price", ViewModel.Money(price)), "own")
end

--- The header's coverage (TT40): the hours the market was observed in the last 30 days ÷ 720, half up to a whole
--- percent (integers only); "—" without observed hours.
---@param hours number|nil  Observed hours in the last 720
---@return string           "5% coverage"
function TooltipView.CoverageText(hours)
  if not hours or hours <= 0 then return "— coverage" end
  return math.floor((hours * 200 + 720) / 1440) .. "% coverage"
end

local PLACE_LABEL = { bags = "Bags", bank = "Bank", mail = "Mail", AH = "AH" }

--- Inventory (TT30, M40): "Total: 29 · Bags: 4 · Bank: 1 · Mail: 24", places in the order bags, bank, mail, AH.
---@param h table  { total, places = { { where, qty } } }, only the places that hold some
---@return table   A line
function TooltipView.InventoryLine(h)
  local parts = { "Total: " .. h.total }
  for _, p in ipairs(h.places) do parts[#parts + 1] = (PLACE_LABEL[p[1]] or p[1]) .. ": " .. p[2] end
  return Section(Line("Inventory", table.concat(parts, " · ")), "own")
end

--- What the units bring in if they all sell (SPEC §4.3 Sells for): qty × price less the AH cut, float copper.
---@param qty number    Units
---@param price number  Copper each
---@return number
function TooltipView.SellsFor(qty, price)
  return AHProfit.Engine.Fees.SaleNet(price, qty)
end

--- The money on a Do's line 2 (SPEC §4.3, the profit mode): Paid (or Mats) and Profit in full, Paid and the plan's
--- Expected Profit in expected; your own stock has nothing in full and "Expected Profit" in expected.
---@param d table             { mode "full" | "expected", paid (copper, 0 = none), paidLabel ("Paid" | "Mats"), net }
---@param full number|nil     The full profit's base (Sells for, or Mats worth), float copper; nil = only the expected
---@param always boolean|nil  Show the full profit in either mode (a disenchant always happens)
---@return string|nil, table  The text (nil = nothing) and its coloured spans
local function MoneyText(d, full, always)
  local paid = d.paid or 0
  local parts, spans = {}, {}
  if paid > 0 then parts[1] = (d.paidLabel or "Paid") .. " " .. ViewModel.Money(paid) end
  local profit, label
  if full and paid > 0 and (always or d.mode ~= "expected") then
    profit, label = ViewModel.RoundHalfUp(full - paid), "Profit"
  elseif d.net and d.net ~= 0 and (d.mode == "expected" or not full) then   -- the plan's; 0 = its net sits elsewhere
    profit, label = ViewModel.RoundHalfUp(d.net), "Expected Profit"
  end
  if profit then
    local amount = ViewModel.SignedMoney(profit)
    parts[#parts + 1] = label .. " " .. amount
    spans[1] = { amount, profit < 0 and "neg" or "pos" }
  end
  if #parts == 0 then return nil, spans end
  return table.concat(parts, " · "), spans
end

--- A post's when, line 1 (SPEC §4.3): "now", or the time frame from the post time to the window's best hour
--- ("Sat 10:00–18:00"; the day again when the best hour falls on another day).
---@param d table  { now, atDay, atClock, peakDay, peakClock }
---@return string
local function PostWhen(d)
  if d.now then return "now" end
  if not d.peakClock then return d.atDay .. " " .. d.atClock end
  local peak = (d.peakDay ~= d.atDay) and (d.peakDay .. " " .. d.peakClock) or d.peakClock
  return d.atDay .. " " .. d.atClock .. "–" .. peak
end

--- A post's Do (SPEC §4.3, TT45-TT47): when and Sells for, then quantity and price (a later one compared with now),
--- and the money.
---@param d table         From TooltipView.DoOf
---@param cur number|nil  The price now
---@return table          DoShape's shape
local function PostShape(d, cur)
  local M, sells = ViewModel.Money, TooltipView.SellsFor(d.qty, d.price)
  local subLeft, subSpans = d.qty .. "× at " .. M(d.price), {}
  if not d.now then
    local delta, colour = TooltipView.Delta(d.price, cur)
    if delta then
      subLeft = subLeft .. " · " .. delta .. " vs now"
      subSpans[1] = { delta, colour }
    end
  end
  local money, spans = MoneyText(d, sells)
  return { verb = "POST", rest = " " .. PostWhen(d),
           value = "Sells for " .. M(ViewModel.RoundHalfUp(sells)) .. " · " .. (d.chance or "—") .. "%", spans = {},
           sub = { left = subLeft, leftSpans = subSpans, right = money, spans = spans }, seconds = { d.next } }
end

--- A craft's Do (TT48): the product and Sells for (from its chain's posts), then its next step and Mats with the
--- profit.
---@param d table  From TooltipView.DoOf
---@return table   DoShape's shape
local function CraftShape(d)
  local M = ViewModel.Money
  local value = d.sells and ("Sells for " .. M(ViewModel.RoundHalfUp(d.sells)) .. " · " .. (d.chance or "—") .. "%") or ""
  local money, spans = MoneyText({ mode = d.mode, paid = d.mats, paidLabel = "Mats", net = d.net }, d.sells)
  return { verb = "CRAFT", rest = " " .. d.qty .. "× " .. d.product, value = value, spans = {},
           sub = { left = d.next or "", right = money, spans = spans }, seconds = {} }
end

--- A disenchant's Do (TT49): Mats worth, then the units and the money; a bought one's profit shows in both modes.
---@param d table  From TooltipView.DoOf
---@return table   DoShape's shape
local function DeShape(d)
  local worth = d.mats and ViewModel.RoundHalfUp(d.mats) or nil
  local money, spans = MoneyText(d, worth, true)
  return { verb = "DISENCHANT", rest = " now", value = worth and ("Mats worth " .. ViewModel.Money(worth)) or "",
           spans = {}, sub = { left = d.qty .. "×", right = money, spans = spans }, seconds = {} }
end

--- A Do's parts (SPEC §4.3 Do): verb, the rest of line 1's left text, its value, spans and colour; line 2 (sub,
--- indented: left text and money, or nil); and the value-only lines under them.
---@param d table         From TooltipView.DoOf
---@param cur number|nil  The price now
---@return table          { verb, rest, value, spans, colour, sub = { left, leftSpans, right, spans } | nil, seconds }
local function DoShape(d, cur)
  local M = ViewModel.Money
  if d.kind == "post" then return PostShape(d, cur) end
  if d.kind == "craft" then return CraftShape(d) end
  if d.kind == "de" then return DeShape(d) end
  local verb, rest, value, colour, seconds = "VENDOR", " " .. M(d.price or 0) .. " each", "nothing better", nil, {}
  if d.kind == "hold" then
    verb, rest, value = "HOLD", " until " .. d["until"], "no window pays this week"
  elseif d.kind == "mail" then
    verb, rest = "MAIL", " " .. d.qty .. "× to " .. d.to
    value = d.product and ("for crafting " .. d.product) or "for its plan"
  elseif d.kind == "buy" then
    verb, rest = "BUY", " ≤ " .. M(d.max) .. (d.purpose and (" · " .. d.purpose) or "")
    value = "funded · " .. d.qty .. " needed"
  elseif d.kind == "unfunded" then
    verb, rest = "UNFUNDED", " " .. d.strategy .. " · Expected Profit " .. ViewModel.SignedMoney(d.net)
    value, colour, seconds = d.short, "amber", { d.detail }
  elseif d.kind == "kept" then
    verb, rest, value = "KEPT", " for yourself", d.all and "keep all" or ("keep at least " .. d.keep)
    seconds = { "right-click to change" }
  end
  return { verb = verb, rest = rest, value = value, spans = {}, colour = colour, seconds = seconds }
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

--- Whether a two-column line fits the block's width, coins counted as drawn (TT31).
---@param left string
---@param right string
---@return boolean
local function Fits(left, right)
  return TooltipView.Width(ViewModel.WithCoins(left), ViewModel.WithCoins(right)) <= TooltipView.WIDTH
end

--- Line 2 of a Do (SPEC §4.3): indented, its left part plain; its money moves under it when both would pass 320 px.
---@param sub table|nil  DoShape's sub
---@param lines table[]  Changed: line 2 and any moved money appended
local function AddSub(sub, lines)
  if not sub or (sub.left == "" and not sub.right) then return end
  local line2 = Line(TooltipView.INDENT .. sub.left, sub.right or "", sub.spans)
  line2.leftSpans, line2.plainLeft = sub.leftSpans, true
  lines[#lines + 1] = line2
  if sub.right and not Fits(line2.label, sub.right) then
    line2.value, line2.spans = "", {}
    lines[#lines + 1] = Line("", sub.right, sub.spans)
  end
end

--- The Do lines (SPEC §4.3): line 1, line 2 indented under it, then the value-only lines; a right part that would
--- pass 320 px goes on a value-only line under its line (TT31).
---@param d table          From TooltipView.DoOf
---@param cur number|nil   The price now
---@return table[]         Lines
function TooltipView.DoLines(d, cur)
  local shape = DoShape(d, cur)
  local seconds = {}
  for i = 1, table.maxn(shape.seconds) do if shape.seconds[i] then seconds[#seconds + 1] = shape.seconds[i] end end
  if d.sellNow then AddNote(seconds, "best time to sell is now") end
  if d.buyNow then AddNote(seconds, "best time to buy is now") end
  local first = Line("Do: " .. shape.verb .. shape.rest, shape.value, shape.spans)
  first.verb, first.rest, first.verbColor, first.valueColor, first.isDo = shape.verb, shape.rest,
    TooltipView.VERB_COLOR[shape.verb], shape.colour, true
  local lines = { first }
  if shape.value ~= "" and not Fits(first.label, shape.value) then
    first.value, first.spans = "", {}
    local moved = Line("", shape.value, shape.spans)
    moved.valueColor = shape.colour
    lines[2] = moved
  end
  AddSub(shape.sub, lines)
  for _, text in ipairs(seconds) do lines[#lines + 1] = Line("", text) end
  return lines
end

--- The whole block (SPEC §4.3 Tooltip, TT31, TT34-TT42): the header with the data age and the market's coverage, the
--- Do line first, then Market Price, Market Inventory, Sell Chance Now, Best Buy and Best Sell Time (none without
--- windows), Disenchant Value (when one of your characters can, or the item is Bind on Equip and not soulbound,
--- TT43; Tooltip.lua decides), Vendor Price (when known) and Inventory (only
--- when you hold some), with a divider before each section: Do, the market lines, the best times, Disenchant Value,
--- and Vendor Price with Inventory (TT42). Without market data: the header and "no market data yet" (TT18), then
--- Disenchant Value when it would show (TT44).
---@param f table  { age = { text, amber }, coverage (hours or nil), noData, doing (DoOf's), market = { cur, ageText,
---                med7, med14, med30, listed, supply7, supply14, supply30, chance1000, chancePrice, commodity, st,
---                stHow }, offsetMs, buy = { price, slot, weeks, now, pct1000 } or nil, sell = (the same, est) or
---                nil, mats = DisenchantLines' mats or nil, vendor (copper or nil), holdings = { total, places } or nil }
---@return table[] Lines { label, value, spans, tail, muted, header, amber, coverage, isDo, verb, rest, verbColor,
---                valueColor, labelMuted, joined, joiner, indent, section } and dividers { divider = true }
function TooltipView.Lines(f)
  local header = Line("AHProfit", f.age.text)
  header.header, header.amber = true, f.age.amber
  local lines = { header }
  if f.noData then
    local only = Line("no market data yet", "")
    only.labelMuted = true
    lines[2] = only
    if f.mats then                     -- TT44: no AH price, so no comparison in the title
      lines[3] = { label = "", value = "", spans = {}, divider = true }
      for _, line in ipairs(TooltipView.DisenchantLines(f.mats, nil)) do lines[#lines + 1] = line end
    end
    return lines
  end
  header.coverage = TooltipView.CoverageText(f.coverage)
  --- Append a section to the block after a divider (TT42); an empty section adds nothing.
  ---@param ... table[]  The section's line lists, in order
  local function Add(...)
    local first = true
    for _, list in ipairs({ ... }) do
      for _, line in ipairs(list) do
        if first then lines[#lines + 1] = { label = "", value = "", spans = {}, divider = true } end
        first = false
        lines[#lines + 1] = line
      end
    end
  end
  local m = f.market
  if f.doing then Add(TooltipView.DoLines(f.doing, m.cur)) end
  Add(TooltipView.MarketPriceLines(m), TooltipView.MarketInventoryLines(m),
      TooltipView.SellChanceLines({ chance1000 = m.chance1000, price = m.chancePrice, commodity = m.commodity,
                                    st = m.st, stHow = m.stHow }))
  Add(f.buy and TooltipView.BestLines("Best Buy Time", f.buy, f.offsetMs or 0) or {},
      f.sell and TooltipView.BestLines("Best Sell Time", f.sell, f.offsetMs or 0) or {})
  if f.mats then Add(TooltipView.DisenchantLines(f.mats, m.cur)) end
  local held = f.holdings and f.holdings.total or 0
  local vendor = TooltipView.VendorLine(f.vendor, held)
  Add(vendor and { vendor } or {}, held > 0 and { TooltipView.InventoryLine(f.holdings) } or {})
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

--- Whether an action's chain makes something: its post's paid is then the mats the craft used (SPEC §4.3, DL3).
---@param actions table[]
---@param a table
---@return boolean
local function InCraftChain(actions, a)
  if a.chainCraft ~= nil then return a.chainCraft end            -- noted before done steps went (Plan.DoneLocally)
  for _, b in ipairs(ChainOf(actions, a)) do
    if b.kind == "craft" then return true end
  end
  return false
end

--- A post's Do, now or later (SPEC §4.3, TT45-TT47): units, price, chance, the plan's net and what it paid, and a
--- later one's post time and best hour.
---@param a table    The post
---@param ctx table
---@return table
local function PostDo(a, ctx)
  local d = { kind = "post", now = a.at <= ctx.nowMs + HOUR_MS, qty = a.qty, price = a.price, net = a.net or 0,
              chance = a.chance, paid = a.paid or 0, paidLabel = InCraftChain(ctx.actions, a) and "Mats" or "Paid" }
  if not d.now then
    d.atDay, d.atClock = ViewModel.Clock(a.at, ctx.offsetMs)
    if a.peak and a.peak > a.at then d.peakDay, d.peakClock = ViewModel.Clock(a.peak, ctx.offsetMs) end
  end
  return d
end

--- A craft's Do for one of its mats (TT11, TT48): the product, what its chain's posts bring in and the first one's
--- chance and time frame, the mats as the plan values them (its paid; 0 from an older file: no profit shown) and the
--- chain's net.
---@param a table    The craft
---@param ctx table
---@return table
local function CraftDo(a, ctx)
  local sells, chance, nextStep = nil, nil, nil
  for _, b in ipairs(ChainOf(ctx.actions, a)) do
    if b.kind == "post" and b.key == a.key then
      sells = (sells or 0) + TooltipView.SellsFor(b.qty, b.price)
      if not nextStep then
        chance = b.chance
        nextStep = "then post " .. PostWhen(PostDo(b, ctx))
      end
    end
  end
  return { kind = "craft", qty = a.qty, product = ctx.nameOf(a.key), net = a.net or 0, chance = chance, sells = sells,
           mats = a.paid or 0, next = nextStep }   -- the mats as the plan values them, owned ones too (DL3)
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

--- A disenchant's Do (TT49): its units, the mats' worth for all of them, what the plan paid (its own, else its
--- chain's buy) and the plan's net.
---@param a table    The disenchant
---@param ctx table
---@return table
local function DeDo(a, ctx)
  local paid = a.paid or 0
  if paid == 0 and a.chainBuyPaid then paid = a.chainBuyPaid end   -- noted before the buy went (Plan.DoneLocally)
  if paid == 0 then
    for _, b in ipairs(ChainOf(ctx.actions, a)) do
      if b.kind == "buy" and b.key == a.key then paid = paid + (b.paid or 0) end
    end
  end
  return { kind = "de", qty = a.qty or 1, net = a.net or 0, paid = paid,
           mats = ctx.deValue and ctx.deValue * (a.qty or 1) or nil }
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
  if a.kind == "de" then return DeDo(a, ctx) end
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
  if d then d.mode = ctx.profitMode or "full" end
  return d
end
