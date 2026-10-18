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
local function ByTime(a, b)
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
  table.sort(shown, ByTime)
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
  table.sort(sorted, ByTime)
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
  table.sort(sorted, ByTime)
  for _, a in ipairs(sorted) do
    if a.kind == "post" and a.inChain and a.peak and out[ChainId(a)] == nil then out[ChainId(a)] = a end
  end
  return out
end

--- What each crafting chain makes: its last craft's product name (plan_views.chain_crafts, D39).
---@param actions table[]  Marked actions
---@param lookOf function  key -> look
---@return table           chainId -> product name
local function ChainCrafts(actions, lookOf)
  local sorted, out = {}, {}
  for i, a in ipairs(actions) do sorted[i] = a end
  table.sort(sorted, ByTime)
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
    netColor = (net or 0) > 0 and "pos" or ((net or 0) < 0 and "neg" or "muted"),
    chance = chance and (chance .. "%") or "—", dot = chance and ViewModel.Level(look.conf) or nil,
    confidence = chance and look.conf or nil,
    volume = (chance and look.soldPerDay) and (ViewModel.RoundHalfUp(look.soldPerDay) .. "/day") or "—",
    risk = risk, riskAmber = amber, who = a.char:match("^([^-]+)") or a.char, day = day, time = hour,
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
      local note = a.ownNote or ViewModel.NoteOf(a, counted.note, posts, crafts, offsetMs)
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
  table.sort(shown, ByTime)
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

--- The footer under Today (SPEC §4.3 wireframe).
---@param today table      From ViewModel.Today
---@param minChance number Percent
---@return string          e.g. "7 actions · expected +1g 54s 15c · 1 hidden (chance < 50%)"
function ViewModel.TodayFooter(today, minChance)
  local text = today.count .. (today.count == 1 and " action" or " actions") .. " · expected " .. today.total
  if today.hidden > 0 then text = text .. " · " .. today.hidden .. " hidden (chance < " .. minChance .. "%)" end
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
  end
  return lines
end
