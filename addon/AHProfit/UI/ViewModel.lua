-- UI/ViewModel.lua: everything the addon window shows, as text and flags (SPEC §4.3, §8.5).
-- Lua twin of the parts of ahsync/engine/views.py and engine/plan_views.py the window uses:
-- money, risk, the chain rule that counts a chain's profit once (VW1), and the Today and
-- Week tabs. Frame code only draws what these functions return and does no arithmetic.
-- Keep them formula-for-formula identical to the Python, rounding included (SPEC §3.3).

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter

local ViewModel = {}
AHProfit.ViewModel = ViewModel

local HOUR_MS, DAY_MS = 3600000, 86400000
ViewModel.HIGH_RISK_PCT = 25
ViewModel.STALE_MS = 24 * HOUR_MS               -- data older than this: the age badge turns amber (SPEC §4.3)
ViewModel.ACTION_LABELS = { buy = "BUY", post = "POST", de = "DE", craft = "CRAFT", vendor = "VENDOR", mail = "MAIL",
                            hold = "HOLD" }

-- The ready-check tick as an inline texture: WoW's fonts have no ✓ glyph (A36).
ViewModel.CHECK = "|TInterface\\RaidFrame\\ReadyCheck-Ready:12:12|t"

-- Column header tooltips (SPEC §4.3): the dashboard's texts where the column is the same.
ViewModel.COLUMN_HELP = {
  Item = "The item, in its quality colour",
  Qty = "Units (or crafts) this row is about",
  Each = "Per unit: the price you pay, or post at",
  Total = "Each × quantity",
  Net = "What this action adds on average, after fees, deposits and the chance it sells. A chain counts it once, "
        .. "on its first action",
  Chance = "Chance it sells in its planned window. The dot is the confidence in that chance: green 75% or more, "
           .. "amber 45% or more, grey below",
  Volume = "Units the market sells per day (last 7 days)",
  Risk = "Gold this action spends ÷ all your gold. Amber above 25%",
  ilvl = "Item level: sets the disenchant results",
  Skill = "Enchanting skill needed to disenchant it",
  Buy = "Cheapest buyout on the AH: your latest scan's, else AHSync's",
  EV = "Expected value of the mats: average results × their sell value",
  Profit = "EV − buy price",
  Where = "Bags, bank or mail",
  Worth = "What it is expected to bring under its plan, else its sell value",
  Time = "When to do it, local time",
  Price = "Cheapest listing seen now (your latest scan or search). \"gone\": none at or below Max; \"checking\": not seen yet",
  Max = "The most to pay each: above this the buy no longer pays",
  For = "Why it's bought: CRAFT (a mat for a planned craft), FLIP (to resell later) or DE (to disenchant)",
  ["List price"] = "The price to list at, each",
  ["Est. profit"] = "Expected profit of posting all of them: price × chance it sells, after the AH cut and deposit, "
                    .. "less what the units are worth otherwise",
  Keeping = "Units this character keeps for itself; \"All\" keeps every one. Click the row to change it",
  Held = "Units held in bags, bank and mail",
  Free = "Units above what you keep: the only ones suggested for selling or crafting",
  Source = "Where the mats come from: Owned (your bags), AH, Vendor, or Mixed (more than one)",
  ["×"] = "Crafts that each still pay, given the mats you have and what the AH lists",
  Mats = "Gold for the mats you need to buy, at the AH's current prices. \"—\": you own them all",
  Sell = "The price to post at, each, for the selling window",
  When = "The peak the post is for, local time: post 8 hours before it",
}

--- Round half up, as every display does (SPEC §3.3).
---@param x number
---@return number  floor(x + 0.5)
function ViewModel.RoundHalfUp(x)
  return math.floor(x + 0.5)
end

--- Copper as WoW money, e.g. "1g 18s 10c"; zero parts left out (views.money).
---@param copper number|nil  Copper (rounded half up), or nil
---@return string            "0c" for zero, "—" for nil, a leading "−" for negative amounts
function ViewModel.Money(copper)
  if copper == nil then return "—" end
  local amount = ViewModel.RoundHalfUp(copper)
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

--- Money with a sign for expected net, e.g. "+73s 42c" (views.signed_money).
---@param copper number
---@return string
function ViewModel.SignedMoney(copper)
  local text = ViewModel.Money(copper)
  if ViewModel.RoundHalfUp(copper) > 0 then return "+" .. text end
  return text
end

--- Risk % = gold the action spends ÷ all your gold, one decimal (views.risk).
---@param spend number      Copper at stake
---@param totalGold number  All your characters' gold, copper
---@return string           e.g. "9.6%"; "—" with no gold recorded
---@return boolean          true above HIGH_RISK_PCT (amber)
function ViewModel.Risk(spend, totalGold)
  if totalGold <= 0 then return "—", false end
  local tenths = ViewModel.RoundHalfUp(spend / totalGold * 1000)
  return math.floor(tenths / 10) .. "." .. tenths % 10 .. "%", tenths / 10 > ViewModel.HIGH_RISK_PCT
end

--- The confidence dot's level (confidence.level).
---@param conf number|nil  Confidence %
---@return string|nil      "high" (≥ 75), "medium" (≥ 45), "low"; nil when unknown
function ViewModel.Level(conf)
  if conf == nil then return nil end
  if conf >= 75 then return "high" end
  if conf >= 45 then return "medium" end
  return "low"
end

--- Local weekday and time of a moment (plan_views.clock).
---@param ms number        UTC ms
---@param offsetMs number  Local time minus UTC, ms
---@return string, string  "Fri", "14:10"
function ViewModel.Clock(ms, offsetMs)
  local seconds = math.floor((ms + offsetMs) / 1000)
  return Adapter.UtcDate("%a", seconds), Adapter.UtcDate("%H:%M", seconds)
end

--- Mark which actions are part of a chain: those with a parent, or that are one (plan_views).
---@param actions table[]  Plan actions (Plan.DecodeAction); inChain is set on each
function ViewModel.MarkChains(actions)
  local parents = {}
  for _, a in ipairs(actions) do
    if a.parent then parents[a.parent] = true end
  end
  for _, a in ipairs(actions) do a.inChain = a.parent ~= nil or parents[a.id] == true end
end

--- How an action counts toward totals (plan_views.role).
---@param a table
---@return string  "sale", "step", "profit" or "follow"
local function Role(a)
  if not a.inChain then return "sale" end
  if a.kind == "post" then return "follow" end
  if a.net and a.net ~= 0 then return "profit" end
  return "step"
end

--- The chain an action belongs to (plan_views.chain_id).
---@param a table
---@return string
local function ChainId(a)
  return a.parent or a.id
end

--- Sort actions by time, then id.
---@param a table
---@param b table
---@return boolean
function ViewModel.ByTime(a, b)
  if a.at ~= b.at then return a.at < b.at end
  return a.id < b.id
end

--- What each action in a view counts, and the note that explains it (plan_views.counted, VW1).
---@param actions table[]  Marked actions
---@param startMs number   View start, inclusive
---@param endMs number     View end, exclusive
---@param offsetMs number  Local time minus UTC
---@return table           id -> { net = copper or nil ("no net"), note = text }
function ViewModel.Counted(actions, startMs, endMs, offsetMs)
  local shown = {}
  for _, a in ipairs(actions) do
    if a.at >= startMs and a.at < endMs then shown[#shown + 1] = a end
  end
  table.sort(shown, ViewModel.ByTime)
  local carriers = {}
  for _, a in ipairs(shown) do
    if Role(a) == "profit" and not carriers[ChainId(a)] then carriers[ChainId(a)] = a end
  end
  local out = {}
  for _, a in ipairs(shown) do
    local role, carrier = Role(a), carriers[ChainId(a)]
    if role == "step" or a.kind == "hold" then         -- nothing sold now: no net (D39)
      out[a.id] = { net = nil, note = "" }
    elseif role == "follow" and carrier then
      local day, hour = ViewModel.Clock(carrier.at, offsetMs)
      out[a.id] = { net = 0, note = "profit counted on the " .. day .. " " .. hour .. " " .. carrier.kind }
    else
      out[a.id] = { net = a.net or 0, note = "" }
    end
  end
  return out
end

--- The chance a row shows: its own for a post, its chain's first post for a craft or buy (plan_views.chance_of).
---@param a table        A marked action
---@param chains table   chainId -> chance of its first post
---@return number|nil    Percent; nil when nothing is sold
local function ChanceOf(a, chains)
  if a.kind == "post" then return a.chance end
  if a.kind == "de" or a.kind == "vendor" or a.kind == "hold" or Role(a) == "step" then return nil end
  return chains[ChainId(a)]
end

--- Each chain's chance: its first post's (plan_views.chain_chances).
---@param actions table[]  Marked actions
---@return table           chainId -> percent
local function ChainChances(actions)
  local sorted, out = {}, {}
  for i, a in ipairs(actions) do sorted[i] = a end
  table.sort(sorted, ViewModel.ByTime)
  for _, a in ipairs(sorted) do
    if a.kind == "post" and a.chance and a.inChain and out[ChainId(a)] == nil then out[ChainId(a)] = a.chance end
  end
  return out
end

--- Each chain's first post that is for a peak (plan_views.chain_posts).
---@param actions table[]  Marked actions
---@return table           chainId -> that post
local function ChainPosts(actions)
  local sorted, out = {}, {}
  for i, a in ipairs(actions) do sorted[i] = a end
  table.sort(sorted, ViewModel.ByTime)
  for _, a in ipairs(sorted) do
    if a.kind == "post" and a.inChain and a.peak and out[ChainId(a)] == nil then out[ChainId(a)] = a end
  end
  return out
end

--- The note under one of the addon's own craft rows (A9): where its mats come from.
---@param a table  An action
---@return string|nil  nil when it isn't one of the addon's own crafts
function ViewModel.OwnCraftNote(a)
  if not (a.own and a.kind == "craft") then return nil end
  local tail = " · AHSync has no plan for it"
  if a.ownSource == "Owned" then return "from your own mats" .. tail end
  if a.ownSource == "AH" then return "mats from the AH: " .. ViewModel.Money(a.matGold) .. tail end
  if a.ownSource == "Vendor" then return "mats from a vendor: " .. ViewModel.Money(a.matGold) .. tail end
  return "your mats topped up from the AH: " .. ViewModel.Money(a.matGold) .. tail
end

--- What each crafting chain makes: its last craft's product name (plan_views.chain_crafts, D39).
---@param actions table[]  Marked actions
---@param lookOf function  key -> look
---@return table           chainId -> product name
local function ChainCrafts(actions, lookOf)
  local sorted, out = {}, {}
  for i, a in ipairs(actions) do sorted[i] = a end
  table.sort(sorted, ViewModel.ByTime)
  for _, a in ipairs(sorted) do
    if a.kind == "craft" and a.inChain then out[ChainId(a)] = lookOf(a.key).name end
  end
  return out
end

--- A row's note (plan_views.note_of, D37-D39): the peak a post is for, what a chain's mat buy
--- is for, why a hold waits, where a chain's profit step will post, and why it counts what it
--- counts, joined by " · ".
---@param a table           The action
---@param countedNote string From Counted
---@param posts table       From ChainPosts
---@param crafts table      From ChainCrafts
---@param offsetMs number
---@return string
function ViewModel.NoteOf(a, countedNote, posts, crafts, offsetMs)
  local parts = {}
  if a.kind == "buy" and a.vendor then parts[1] = "from a vendor" end      -- D41, A32
  if a.kind == "post" and a.peak then
    local _, peak = ViewModel.Clock(a.peak, offsetMs)
    parts[#parts + 1] = "for the " .. peak .. " peak"
  end
  if Role(a) == "step" and a.kind ~= "craft" and crafts[ChainId(a)] then
    parts[#parts + 1] = "for crafting " .. crafts[ChainId(a)]
  end
  if a.kind == "hold" then parts[#parts + 1] = "hold for a later week" end
  local post = (a.kind == "buy" or a.kind == "craft") and Role(a) == "profit" and posts[ChainId(a)] or nil
  if post then
    local day, hour = ViewModel.Clock(post.at, offsetMs)
    local _, peak = ViewModel.Clock(post.peak, offsetMs)
    parts[#parts + 1] = (a.kind == "buy" and "Flip" or "Then") .. ": post " .. day .. " " .. hour .. ", for the "
                        .. peak .. " peak"
  end
  if countedNote ~= "" then parts[#parts + 1] = countedNote end
  return table.concat(parts, " · ")
end

--- One row's display cells, shared by Today and Week.
---@param a table          The action
---@param counted table    { net, note } from Counted
---@param chance number|nil Percent shown
---@param look table       { name, quality, conf, soldPerDay } for its item
---@param totalGold number All your gold, copper
---@param offsetMs number
---@return table           Row of text and flags
local function Row(a, counted, chance, look, totalGold, offsetMs)
  local spend = a.kind == "buy" and (a.price or 0) * a.qty or 0
  local risk, amber = ViewModel.Risk(spend, totalGold)
  local day, hour = ViewModel.Clock(a.at, offsetMs)
  local net = counted.net
  return {
    id = a.id, kind = a.kind, action = ViewModel.ACTION_LABELS[a.kind] or a.kind, key = a.key, item = look.name,
    quality = look.quality, qty = "×" .. a.qty, each = ViewModel.Money(a.price),
    total = a.price and ViewModel.Money(a.price * a.qty) or "—",
    net = net == nil and "—" or ViewModel.SignedMoney(net),
    -- The action's own expected profit, even when a chain counts it elsewhere: the AH panel's Est. profit.
    postNet = ViewModel.SignedMoney(a.net or 0), postNetColor = (a.net or 0) < 0 and "neg" or "pos",
    netColor = (net or 0) > 0 and "pos" or ((net or 0) < 0 and "neg" or "muted"),
    chance = chance and (chance .. "%") or "—", dot = chance and ViewModel.Level(look.conf) or nil,
    confidence = chance and look.conf or nil,
    volume = (chance and look.soldPerDay) and (ViewModel.RoundHalfUp(look.soldPerDay) .. "/day") or "—",
    risk = risk, riskAmber = amber, who = a.char:match("^([^-]+)") or a.char, day = day, time = hour,
    price = a.price, units = a.qty, postNetCopper = a.net or 0,   -- numbers, for the AH panel (A29, A31)
    status = a.status, note = counted.note, netCopper = ViewModel.RoundHalfUp(net or 0),
  }
end

--- The Today tab: open actions due within the next hour, best expected net first (D1, D2, D16).
--- Ties go to the higher chance, then the item name, then the character. Rows whose chance is
--- below minChance are hidden and counted; rows with nothing to sell never are.
---@param actions table[]  Plan actions, each with char (the character id)
---@param nowMs number
---@param offsetMs number  Local time minus UTC
---@param minChance number Percent, 0-100
---@param lookOf function  key -> { name, quality, conf, soldPerDay }
---@param totalGold number All your gold, copper
---@return table           { rows, hidden, total (signed money), count }
function ViewModel.Today(actions, nowMs, offsetMs, minChance, lookOf, totalGold)
  ViewModel.MarkChains(actions)
  local due, earliest = {}, nowMs
  for _, a in ipairs(actions) do
    if (a.status == "pending" or a.status == "late") and a.at < nowMs + HOUR_MS then
      due[#due + 1] = a
      earliest = math.min(earliest, a.at)
    end
  end
  local nets, chains = ViewModel.Counted(due, earliest, nowMs + HOUR_MS, offsetMs), ChainChances(actions)
  local posts, crafts = ChainPosts(actions), ChainCrafts(actions, lookOf)
  local rows, hidden, total = {}, 0, 0
  for _, a in ipairs(due) do
    local chance = ChanceOf(a, chains)
    if chance ~= nil and chance < minChance then
      hidden = hidden + 1
    else
      local counted = nets[a.id]
      local note = a.ownNote or ViewModel.OwnCraftNote(a) or ViewModel.NoteOf(a, counted.note, posts, crafts, offsetMs)
      local row = Row(a, { net = counted.net, note = note }, chance,
                      lookOf(a.key), totalGold, offsetMs)
      row.sortChance, row.sortChar = chance or -1, a.char
      rows[#rows + 1] = row
      total = total + row.netCopper
    end
  end
  table.sort(rows, function(x, y)
    if x.netCopper ~= y.netCopper then return x.netCopper > y.netCopper end
    if x.sortChance ~= y.sortChance then return x.sortChance > y.sortChance end
    if x.item ~= y.item then return x.item < y.item end
    return x.sortChar < y.sortChar
  end)
  return { rows = rows, hidden = hidden, total = ViewModel.SignedMoney(total), count = #rows }
end

--- The Week tab: a rolling 7 days from today's local midnight, by day (D11, D17).
---@param actions table[]  Plan actions
---@param nowMs number
---@param offsetMs number
---@param lookOf function  key -> look
---@param totalGold number
---@return table           { days = { { label, today, total, rows } × 7 }, total }
function ViewModel.Week(actions, nowMs, offsetMs, lookOf, totalGold)
  ViewModel.MarkChains(actions)
  local start = math.floor((nowMs + offsetMs) / DAY_MS) * DAY_MS - offsetMs
  local finish = start + 7 * DAY_MS
  local shown = {}
  for _, a in ipairs(actions) do
    if a.at >= start and a.at < finish then shown[#shown + 1] = a end
  end
  table.sort(shown, ViewModel.ByTime)
  local nets, chains = ViewModel.Counted(shown, start, finish, offsetMs), ChainChances(actions)
  local posts, crafts = ChainPosts(actions), ChainCrafts(actions, lookOf)
  local days, total = {}, 0
  for d = 0, 6 do
    local midnight = start + d * DAY_MS
    days[d + 1] = { label = Adapter.UtcDate("%a ", math.floor((midnight + offsetMs) / 1000))
                      .. tonumber(Adapter.UtcDate("%d", math.floor((midnight + offsetMs) / 1000))),
                    today = d == 0, cents = 0, rows = {} }
  end
  for _, a in ipairs(shown) do
    local day = days[math.floor((a.at - start) / DAY_MS) + 1]
    local counted = nets[a.id]
    local row = Row(a, { net = counted.net, note = ViewModel.NoteOf(a, counted.note, posts, crafts, offsetMs) },
                    ChanceOf(a, chains), lookOf(a.key), totalGold, offsetMs)
    day.rows[#day.rows + 1] = row
    day.cents = day.cents + row.netCopper
    total = total + row.netCopper
  end
  for _, day in ipairs(days) do day.total = ViewModel.SignedMoney(day.cents) end
  return { days = days, total = ViewModel.SignedMoney(total) }
end

--- The title bar's age badge (SPEC §4.3, UI5).
---@param generatedAt number|nil  When AHSync wrote the market data, ms
---@param nowMs number
---@return table                  { text = "data 2 h old", amber = boolean }; "no data" without a file
function ViewModel.Age(generatedAt, nowMs)
  if generatedAt == nil then return { text = "no data", amber = true } end
  local minutes = math.floor(math.max(0, nowMs - generatedAt) / 60000)
  local text
  if minutes < 1 then text = "now"
  elseif minutes < 60 then text = minutes .. " min"
  elseif minutes < 1440 then text = math.floor(minutes / 60) .. " h"
  else text = math.floor(minutes / 1440) .. " d" end
  return { text = "data " .. text .. (minutes < 1 and "" or " old"), amber = nowMs - generatedAt > ViewModel.STALE_MS }
end

--- The footer under Today (SPEC §4.3 wireframe, A2).
---@param today table      From ViewModel.Today
---@param minChance number Percent
---@param gone number|nil  Planned buys a live scan showed are gone
---@return string          e.g. "7 actions · expected +1g 54s 15c · 1 hidden (chance < 50%) · 2 gone"
function ViewModel.TodayFooter(today, minChance, gone)
  local text = today.count .. (today.count == 1 and " action" or " actions") .. " · expected " .. today.total
  if today.hidden > 0 then text = text .. " · " .. today.hidden .. " hidden (chance < " .. minChance .. "%)" end
  if gone and gone > 0 then text = text .. " · " .. gone .. " gone" end
  return text
end

--- What the window shows about an item besides the plan: its name, quality and market activity.
--- The name the addon saved from a link wins (it carries a random suffix, "… of Frost"), then
--- the client's item cache, then the key itself.
---@param key number           Item key
---@param item table|nil       Market.Item(key)
---@param saved string|nil     AHProfitDB.itemNames[key], "quality,name"
---@param cachedName string|nil   Adapter.ItemInfo name
---@param cachedQuality number|nil Adapter.ItemInfo quality
---@return table               { name, quality, conf, soldPerDay }
function ViewModel.Look(key, item, saved, cachedName, cachedQuality)
  local quality, name = (saved or ""):match("^(%d),(.+)$")
  name = name or cachedName or ("Item " .. key)
  quality = tonumber(quality) or cachedQuality or 1
  return { name = name, quality = quality, conf = item and item.conf, soldPerDay = item and item.soldPerDay }
end

--- The lines a list shows from a scroll offset, and where its scroll bar's thumb sits (UI2).
---@param lines table[]     Every line
---@param offset number     Lines scrolled past (clamped)
---@param visible number    Lines that fit
---@return table            { lines, offset, scrollable, thumbTop, thumbSize } (thumb as shares of the track)
function ViewModel.Page(lines, offset, visible)
  local total = #lines
  local maxOffset = math.max(0, total - visible)
  offset = math.max(0, math.min(offset, maxOffset))
  local page = {}
  for i = 1, math.min(visible, total - offset) do page[i] = lines[offset + i] end
  if total <= visible then return { lines = page, offset = offset, scrollable = false, thumbTop = 0, thumbSize = 1 } end
  return { lines = page, offset = offset, scrollable = true, thumbTop = offset / total, thumbSize = visible / total }
end

--- The Week tab as one list: a header line per day with its total, then its rows.
---@param week table  From ViewModel.Week
---@return table[]    { header = true, label, total, today } or a row
function ViewModel.WeekLines(week)
  local lines = {}
  for _, day in ipairs(week.days) do
    lines[#lines + 1] = { header = true, label = day.label .. (day.today and " (today)" or ""), total = day.total,
                          netColor = day.cents > 0 and "pos" or (day.cents < 0 and "neg" or "muted") }
    for _, row in ipairs(day.rows) do lines[#lines + 1] = row end
  end
  return lines
end

--- The base item id of an item key (SPEC §2.1), for the client's item cache.
---@param key number  Item key, or -itemId for an unknown random suffix
---@return number
function ViewModel.BaseItemId(key)
  if key < 0 then return -key end
  return key % 2147483648
end

--- All your characters' gold, for risk %.
---@param characters table  AHProfitDB.characters: charId -> { gold }
---@return number           Copper
function ViewModel.TotalGold(characters)
  local total = 0
  for _, c in pairs(characters or {}) do total = total + (tonumber(c.gold) or 0) end
  return total
end

-- Coin icons for money text (SPEC §4.3: money uses gold, silver and copper coin icons).
ViewModel.COINS = { g = "|TInterface\\MoneyFrame\\UI-GoldIcon:0:0:1:0|t",
                    s = "|TInterface\\MoneyFrame\\UI-SilverIcon:0:0:1:0|t",
                    c = "|TInterface\\MoneyFrame\\UI-CopperIcon:0:0:1:0|t" }

--- Money text with coin icons in place of the g, s and c letters: "1g 18s" -> "1<gold> 18<silver>".
---@param text string  From Money or SignedMoney; "—" and other text pass unchanged
---@return string
function ViewModel.WithCoins(text)
  return (tostring(text):gsub("(%d+)([gsc])", function(n, unit) return n .. ViewModel.COINS[unit] end))
end

--- The list's lines: each row, followed by its note as a line of its own when it has one.
---@param rows table[]  View rows (or Week lines)
---@return table[]      Rows, day headers and { noteLine = true, text } lines
function ViewModel.WithNotes(rows)
  local lines = {}
  for _, row in ipairs(rows) do
    lines[#lines + 1] = row
    if row.note and row.note ~= "" then lines[#lines + 1] = { noteLine = true, text = row.note } end
    for _, text in ipairs(row.notes or {}) do lines[#lines + 1] = { noteLine = true, text = text } end   -- A34
  end
  return lines
end

-- ------------------------------------------------------------------ item tooltip (SPEC §4.3)

ViewModel.WEEKDAYS = { "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun" }

--- Tooltip delta against the current AH price: (value − cur) ÷ cur × 100, half up (views.delta_percent).
---@param value number|nil
---@param current number|nil
---@return number|nil  Whole percent; nil when either is missing
function ViewModel.DeltaPercent(value, current)
  if value == nil or not current or current == 0 then return nil end
  return ViewModel.RoundHalfUp((value - current) / current * 100)
end

--- A value with its delta and the delta's colour (views._delta_text).
---@param value number|nil
---@param current number|nil
---@return string, string|nil  e.g. "11s (+38%)", "green"
local function DeltaText(value, current)
  local delta = ViewModel.DeltaPercent(value, current)
  if value == nil then return "—", nil end
  if delta == nil then return ViewModel.Money(value), nil end
  local sign = delta > 0 and "+" or (delta < 0 and "−" or "")
  return ViewModel.Money(value) .. " (" .. sign .. math.abs(delta) .. "%)", delta > 0 and "green" or (delta < 0 and "red" or nil)
end

--- The tooltip block, one fact per line, prices per unit (views.tooltip, TT1-TT5).
---@param t table  { cur, curAgeText, med30, deValue, canDisenchant, bestWindow, bestPrice, chance }
---@return table[] { label, value, colour } in spec order; the Disenchant line is left out without an enchanter
function ViewModel.Tooltip(t)
  local lines = { { label = "Current AH price", value = t.cur and (ViewModel.Money(t.cur) .. " (" .. t.curAgeText .. ")") or "—" } }
  local hist, colour = DeltaText(t.med30, t.cur)
  if t.med30 and t.cur and t.cur ~= 0 then hist = hist:gsub(" %(", " (30d, ", 1) end
  lines[#lines + 1] = { label = "Historical price", value = hist, colour = colour }
  if t.canDisenchant then
    local v, c = DeltaText(t.deValue, t.cur)
    lines[#lines + 1] = { label = "Disenchant value", value = v, colour = c }
  end
  lines[#lines + 1] = { label = "Best time to sell", value = t.bestWindow or "—" }
  local v, c = DeltaText(t.bestPrice, t.cur)
  lines[#lines + 1] = { label = "Price at best time", value = v, colour = c }
  lines[#lines + 1] = { label = "Chance to sell", value = t.chance and (ViewModel.RoundHalfUp(t.chance * 100) .. "%") or "—" }
  return lines
end

--- A selling window in local time, e.g. "Sat 17:00–21:00", with its strength when not yet established
--- (item_detail.window_long; SPEC §4.3 "Best time to sell").
---@param w table          { start, hours, weeks } (UTC slots, Market.Decode)
---@param offsetMs number  Local time minus UTC
---@return string
function ViewModel.WindowLabel(w, offsetMs)
  local start = (w.start + math.floor(offsetMs / HOUR_MS)) % 168
  local finish = (start + w.hours) % 168
  local startDay, endDay = math.floor(start / 24), math.floor(finish / 24)
  local text = string.format("%s %02d:00–", ViewModel.WEEKDAYS[startDay + 1], start % 24)
  if endDay ~= startDay then text = text .. ViewModel.WEEKDAYS[endDay + 1] .. " " end
  text = text .. string.format("%02d:00", finish % 24)
  if w.weeks < 3 then return text .. " (early)" end         -- short: the line must fit 320 px (UI8)
  if w.weeks < 6 then return text .. " (pattern)" end
  return text
end

--- How long ago a price was seen, for the tooltip ("2 h ago", "now").
---@param ageMs number|nil
---@return string
function ViewModel.SeenAgo(ageMs)
  local minutes = math.floor(math.max(0, ageMs or 0) / 60000)
  if minutes < 1 then return "now" end
  if minutes < 60 then return minutes .. " min ago" end
  if minutes < 1440 then return math.floor(minutes / 60) .. " h ago" end
  return math.floor(minutes / 1440) .. " d ago"
end

-- ------------------------------------------------------------------ Inventory tab (SPEC §4.3, A22)

ViewModel.PLACES = { "bags", "bank", "mail" }

--- What the plan (AHSync's or the addon's own) does with an item, as the Inventory note.
---@param a table|nil       The item's first sale action, or nil
---@param craftedInto string|nil  The product a craft uses it for
---@param offsetMs number
---@return string
local function PlanText(a, craftedInto, offsetMs)
  if craftedInto then return "For crafting " .. craftedInto end
  if not a then return "Not planned" end
  local who = a.own and "" or " (AHSync)"
  if a.kind == "post" then
    if a.own then return "Post now at " .. ViewModel.Money(a.price) end
    local day, hour = ViewModel.Clock(a.at, offsetMs)
    return "Post " .. day .. " " .. hour .. " at " .. ViewModel.Money(a.price) .. who
  end
  if a.kind == "vendor" then return "Vendor at " .. ViewModel.Money(a.price) .. " each" .. who end
  if a.kind == "de" then return "Disenchant" .. who end
  return "Hold for a later week" .. who
end

--- An Inventory note with what you keep in front of the plan for the rest (A25).
---@param kept number|nil  The reserve (-1: all)
---@param plan string      The plan's note
---@return string
function ViewModel.KeptNote(kept, plan)
  if kept == nil then return plan end
  if kept == -1 then return "Kept for yourself" end
  return "Keeping " .. kept .. " for yourself · " .. plan
end

--- The Inventory tab: what this character holds, best worth first (A22). Worth is what its plan
--- expects (the action's net per unit), else its sell value; bound items have none.
---@param holdings table[]  { where, key, qty, bound }
---@param actions table[]   This character's plan actions and the addon's own options
---@param lookOf function   key -> look
---@param itemOf function   key -> market item or nil
---@param offsetMs number
---@param reserves table|nil key -> units you keep (A25)
---@return table[]          Rows { item, quality, where, qty, worth, chance, dot, volume, note, sortWorth }
function ViewModel.Inventory(holdings, actions, lookOf, itemOf, offsetMs, reserves)
  local sale, craftedInto = {}, {}
  local sorted = {}
  for i, a in ipairs(actions) do sorted[i] = a end
  table.sort(sorted, ViewModel.ByTime)
  for _, a in ipairs(sorted) do
    if (a.kind == "post" or a.kind == "vendor" or a.kind == "de" or a.kind == "hold") and not a.parent
      and sale[a.key] == nil then
      sale[a.key] = a
    end
    for key in pairs(a.uses or {}) do craftedInto[key] = craftedInto[key] or lookOf(a.key).name end
  end
  local rows = {}
  for _, h in ipairs(holdings) do
    local look, a, item = lookOf(h.key), sale[h.key], itemOf(h.key)
    local each
    if not h.bound and not craftedInto[h.key] then
      if a and a.net and a.qty > 0 then each = a.net / a.qty else each = item and item.sellValue end
    end
    local worth = each and each * h.qty
    local chance = (not h.bound and a and a.kind == "post") and a.chance or nil
    rows[#rows + 1] = {
      key = h.key, item = look.name, quality = look.quality, where = h.where, qty = "×" .. h.qty, kind = "inventory",
      worth = worth and ViewModel.Money(worth) or "—", chance = chance and (chance .. "%") or "—",
      dot = chance and ViewModel.Level(look.conf) or nil,
      volume = (look.soldPerDay and not h.bound) and (ViewModel.RoundHalfUp(look.soldPerDay) .. "/day") or "—",
      note = h.bound and "Bound · not planned" or ViewModel.KeptNote((reserves or {})[h.key], PlanText(a, craftedInto[h.key], offsetMs)),
      sortWorth = worth and ViewModel.RoundHalfUp(worth) or -1, netColor = "muted", riskAmber = false,
    }
  end
  table.sort(rows, function(x, y)
    if x.sortWorth ~= y.sortWorth then return x.sortWorth > y.sortWorth end
    if x.item ~= y.item then return x.item < y.item end
    return x.where < y.where
  end)
  return rows
end

--- The footer under Inventory.
---@param rows table[]  From ViewModel.Inventory
---@return string       e.g. "5 items · worth 10s 74c"
function ViewModel.InventoryFooter(rows)
  local total = 0
  for _, r in ipairs(rows) do if r.sortWorth > 0 then total = total + r.sortWorth end end
  return #rows .. (#rows == 1 and " item" or " items") .. " · worth " .. ViewModel.Money(total)
end

-- ------------------------------------------------------------------ Reservations tab (A26)

--- The Reservations tab: every item this character keeps, held or not, by name (A26).
---@param reserves table    key -> units kept (-1: all), from Reserves.Get
---@param holdings table[]  { where, key, qty, bound }, every place
---@param lookOf function   key -> look
---@return table[]          Rows { key, item, quality, keeping, held, free }
function ViewModel.Reservations(reserves, holdings, lookOf)
  local held = {}
  for _, h in ipairs(holdings) do
    if not h.bound then held[h.key] = (held[h.key] or 0) + h.qty end
  end
  local rows = {}
  for key, kept in pairs(reserves) do
    local look, units = lookOf(key), held[key] or 0
    local free = kept == -1 and 0 or math.max(0, units - kept)
    rows[#rows + 1] = { key = key, item = look.name, quality = look.quality, kind = "reservation",
                        keeping = kept == -1 and "All" or tostring(kept), held = "×" .. units,
                        free = free > 0 and ("×" .. free) or "—", netColor = "muted", riskAmber = false }
  end
  table.sort(rows, function(x, y)
    if x.item ~= y.item then return x.item < y.item end
    return x.key < y.key
  end)
  return rows
end

--- The footer under Reservations.
---@param rows table[]  From ViewModel.Reservations
---@return string       e.g. "3 items kept · click a row to change it"
function ViewModel.ReservationsFooter(rows)
  return #rows .. (#rows == 1 and " item" or " items") .. " kept · click a row to change it"
end

-- ------------------------------------------------------------------ Flip tab (A28, UI13)

--- The Flip tab's rows: AHSync's flips for this character, a buy whose chain ends in a post (A28).
---@param actions table[]  This character's plan actions, gone buys already dropped
---@param lookOf function  key -> look
---@param levelsOf function key -> live levels or nil
---@param totalGold number All your gold, copper, for risk
---@param offsetMs number
---@return table[]         Rows of text, best profit first (ties: id); each with spend (copper)
function ViewModel.FlipRows(actions, lookOf, levelsOf, totalGold, offsetMs)
  local posts, crafts = {}, {}
  local sorted = {}
  for i, a in ipairs(actions) do sorted[i] = a end
  table.sort(sorted, ViewModel.ByTime)
  for _, a in ipairs(sorted) do
    if a.parent and a.kind == "post" and not posts[a.parent] then posts[a.parent] = a end
    if a.parent and a.kind == "craft" then crafts[a.parent] = true end
  end
  local rows = {}
  for _, a in ipairs(sorted) do
    local post = a.kind == "buy" and not crafts[a.id] and posts[a.id] or nil
    if post then
      local look, levels = lookOf(a.key), levelsOf(a.key)
      local spend = (a.price or 0) * a.qty
      local risk, amber = ViewModel.Risk(spend, totalGold)
      local day, peak = ViewModel.Clock(post.peak or post.at, offsetMs)
      local postDay, postHour = ViewModel.Clock(post.at, offsetMs)
      local note = { "post " .. postDay .. " " .. postHour .. ", for the " .. peak .. " peak" }
      if levels and levels[1] then note[2] = "on the AH now from " .. ViewModel.Money(levels[1].price) end
      rows[#rows + 1] = {
        id = a.id, key = a.key, item = look.name, quality = look.quality, kind = "buy", qty = "×" .. a.qty,
        buy = ViewModel.Money(a.price), sell = ViewModel.Money(post.price), when = day .. " " .. peak,
        profit = ViewModel.SignedMoney(a.net or 0), netColor = (a.net or 0) > 0 and "pos" or "neg",
        chance = post.chance and (post.chance .. "%") or "—", dot = post.chance and ViewModel.Level(look.conf) or nil,
        risk = risk, riskAmber = amber, note = table.concat(note, " · "), spend = spend, netCopper = a.net or 0,
      }
    end
  end
  table.sort(rows, function(x, y)
    if x.netCopper ~= y.netCopper then return x.netCopper > y.netCopper end
    return x.id < y.id
  end)
  return rows
end

--- The footer under Flip.
---@param rows table[]     From ViewModel.FlipRows, gone buys dropped
---@param planned table[]  From ViewModel.FlipRows over every planned action, so the difference is the gone flips
---@return string          e.g. "1 flip · capital needed 8s · 1 gone"
function ViewModel.FlipFooter(rows, planned)
  local capital, gone = 0, #planned - #rows
  for _, r in ipairs(rows) do capital = capital + r.spend end
  local text = #rows .. (#rows == 1 and " flip" or " flips") .. " · capital needed " .. ViewModel.Money(capital)
  if gone > 0 then text = text .. " · " .. gone .. " gone" end
  return text
end

-- ------------------------------------------------------------------ Disenchant tab (A23)

--- The Disenchant tab's rows: always shown, each with the skill it needs (A23).
---@param targets table[]   Options.Disenchant
---@param lookOf function   key -> look
---@param bestSkill number|nil  Your best Enchanting skill; nil without an enchanter
---@return table[]          Rows of text
function ViewModel.DisenchantRows(targets, lookOf, bestSkill)
  local rows = {}
  for _, t in ipairs(targets) do
    local look, mat = lookOf(t.key), lookOf(t.mainMat)
    local parts = {}
    for _, m in ipairs(t.mats) do
      local tenths = ViewModel.RoundHalfUp(m.units * 10)
      if tenths > 0 then parts[#parts + 1] = math.floor(tenths / 10) .. "." .. tenths % 10 .. " " .. lookOf(m.key).name end
    end
    if bestSkill == nil then
      parts[#parts + 1] = "no enchanter yet"
    elseif t.skill > bestSkill then
      parts[#parts + 1] = "needs Enchanting " .. t.skill .. " (best " .. bestSkill .. ")"
    end
    rows[#rows + 1] = {
      key = t.key, item = look.name, quality = look.quality, ilvl = tostring(t.ilvl), skill = tostring(t.skill),
      buy = ViewModel.Money(t.buy), ev = ViewModel.Money(t.ev), profit = ViewModel.SignedMoney(t.profit),
      netColor = "pos", kind = "de",
      chance = t.matChance and (ViewModel.RoundHalfUp(t.matChance * 100) .. "%") or "—",   -- the mats sell (SPEC §3.5)
      dot = t.matChance and ViewModel.Level(mat.conf) or nil,
      volume = mat.soldPerDay and (ViewModel.RoundHalfUp(mat.soldPerDay) .. "/day") or "—",
      note = table.concat(parts, " · "), muted = bestSkill ~= nil and t.skill > bestSkill,
    }
  end
  return rows
end

--- The footer under Disenchant.
---@param rows table[]
---@param bestSkill number|nil
---@return string
function ViewModel.DisenchantFooter(rows, bestSkill)
  local who = bestSkill and ("best Enchanting " .. bestSkill) or "no enchanter yet: what one could disenchant"
  return #rows .. (#rows == 1 and " target" or " targets") .. " · " .. who
end

-- ------------------------------------------------------------------ AH side panel (SPEC §4.3, UI7, A24)

--- A copy of a post row for fewer units, its Est. profit scaled to match (A31).
---@param row table    A Today post row
---@param units number Units, fewer than row.units
---@return table
local function PostFor(row, units)
  local copy = {}
  for k, v in pairs(row) do copy[k] = v end
  local net = row.postNetCopper * units / row.units
  copy.units, copy.qty, copy.postNetCopper = units, "×" .. units, net
  copy.postNet, copy.postNetColor = ViewModel.SignedMoney(net), net < 0 and "neg" or "pos"
  return copy
end

--- The Post list's rows: Today's posts for items you hold, each capped at the units you still hold
--- (shared in order across rows of the same item). A post for an item you don't hold yet is a later
--- step of a chain and waits (A30, A31).
---@param rows table[]  Today rows
---@param held table    key -> units in your bags
---@param limit number  Most rows
---@return table[]
function ViewModel.PostableRows(rows, held, limit)
  local out, left = {}, {}
  for key, units in pairs(held) do left[key] = units end
  for _, row in ipairs(rows) do
    local units = math.min(row.units or 0, left[row.key] or 0)
    if row.kind == "post" and units > 0 and #out < limit then
      out[#out + 1] = units < row.units and PostFor(row, units) or row
      left[row.key] = left[row.key] - units
    end
  end
  return out
end

ViewModel.WHY_LABELS = { craft = "CRAFT", flip = "FLIP", de = "DE" }
ViewModel.READ_SHARE = 0.85            -- A43: reading fills this much of the scan bar; saving the rest

--- A count with thousands separators.
---@param n number  A whole number
---@return string   e.g. "47,667"
function ViewModel.Thousands(n)
  local text = tostring(math.floor(n))
  local out = text:reverse():gsub("(%d%d%d)", "%1,"):reverse()
  return (out:gsub("^,", ""))
end

--- The panel's Full scan status and bar (A43).
---@param p table|nil   Replicate.Progress()
---@param width number  The bar's full width, px
---@return table        { text (nil: leave the status line alone), shown (the bar), px (its fill, 0 = none) }
function ViewModel.ScanStatus(p, width)
  if not p then return { shown = false, px = 0 } end
  local read = math.floor(width * ViewModel.READ_SHARE)
  if p.state == "waiting" then return { text = "full scan: waiting for the server…", shown = true, px = 0 } end
  if p.state == "read" then
    return { text = "full scan: reading " .. ViewModel.Thousands(p.done) .. " of " .. ViewModel.Thousands(p.total),
             shown = true, px = p.total > 0 and math.floor(read * p.done / p.total) or 0 }
  end
  if p.state == "sort" then return { text = "full scan: saving", shown = true, px = read } end
  if p.state == "encode" then
    local share = p.total > 0 and p.done / p.total or 1
    return { text = "full scan: saving", shown = true, px = read + math.floor((width - read) * share) }
  end
  if p.state == "save" then return { text = "full scan: saving", shown = true, px = width } end
  if p.state == "timeout" then return { text = "no answer: another addon may have scanned recently", shown = false, px = 0 } end
  return { text = "full scan done · " .. ViewModel.Thousands(p.auctions) .. " auctions" .. (p.complete and "" or " (partial)"),
           shown = false, px = 0 }
end

--- Why a planned buy is made, from what follows it in its chain (A40).
---@param a table         The buy
---@param actions table[] This character's plan actions
---@return string|nil     "craft" (a craft follows), "de" (a disenchant), "flip" (only a post); nil when alone
function ViewModel.BuyReason(a, actions)
  local chain, kinds = a.parent or a.id, {}
  for _, b in ipairs(actions) do
    if b ~= a and (b.parent == chain or b.id == chain) then kinds[b.kind] = true end
  end
  if kinds.craft then return "craft" end
  if kinds.de then return "de" end
  if kinds.post then return "flip" end
  return nil
end

--- The panel's shopping and post lists (UI7).
---@param targets table[]  { key, qty, price (the most to pay each), why ("craft" | "flip" | "de" | nil) } in list order
---@param posts table[]    Today rows of posts { item, quality, qty, each, chance, dot }
---@param levelsOf function key -> live levels, or nil when not checked yet
---@param lookOf function  key -> look
---@param gold number      All your gold, copper
---@return table           { shopping = rows, checked = "3 of 5", posts = rows, footer }
function ViewModel.AHPanel(targets, posts, levelsOf, lookOf, gold)
  local shopping, checked, found, spend = {}, 0, 0, 0
  for _, t in ipairs(targets) do
    local look, levels = lookOf(t.key), levelsOf(t.key)
    local row = { key = t.key, item = look.name, quality = look.quality, qty = "×" .. t.qty, max = ViewModel.Money(t.price),
                  why = ViewModel.WHY_LABELS[t.why] or "", whyKind = t.why }
    if not levels then
      row.state, row.text, row.price = "checking", "checking", "checking"
    else
      checked = checked + 1
      local units = 0
      for _, level in ipairs(levels) do if level.price <= t.price then units = units + level.units end end
      if units >= t.qty then
        found, spend = found + 1, spend + levels[1].price * t.qty
        row.state, row.text = "found", ViewModel.Money(levels[1].price) .. " ≤ " .. ViewModel.Money(t.price)
        row.price = ViewModel.Money(levels[1].price)
      else
        row.state, row.text, row.price = "gone", "gone", "gone"
      end
    end
    shopping[#shopping + 1] = row
  end
  return { shopping = shopping, checked = checked .. " of " .. #targets, posts = posts,
           footer = found .. " found · budget left " .. ViewModel.Money(math.max(0, gold - spend)) }
end
