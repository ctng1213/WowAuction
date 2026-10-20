-- UI/ViewModel.lua: everything the addon window shows, as text and flags (SPEC §4.3, §8.5).
-- Lua twin of the parts of ahsync/engine/views.py and engine/plan_views.py the window uses:
-- money, risk and the chain rule that counts a chain's profit once (VW1); the plan's Today rows (the Portfolio tab
-- without a snapshot, and the AH panel's Post list) are in UI/PlanView.lua, the Portfolio tab in UI/PortfolioView.lua
-- and the tooltip in UI/TooltipView.lua. Frame code only draws what these functions return and does no arithmetic.
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
  -- the Portfolio tab (SPEC §4.3), the dashboard's texts
  Status = "Funded, or why it was left out. For a position: Acquiring, Processing, Holding, Ready to sell, Listed, "
           .. "Partially sold or Needs reconciliation",
  Next = "The next thing to do for this position",
  Plan = "When it buys or makes, and when it sells",
  Adjusted = "Net ÷ (1 + waiting premium): 15% + 5% × the wait, in fractional days from now to the expected sale hour; "
             .. "0% for what is realized today. The plan ranks Today and the week together by this",
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
function ViewModel.Role(a)
  if not a.inChain then return "sale" end
  if a.kind == "post" then return "follow" end
  if a.net and a.net ~= 0 then return "profit" end
  return "step"
end

--- The chain an action belongs to (plan_views.chain_id).
---@param a table
---@return string
function ViewModel.ChainId(a)
  return a.parent or a.id
end

local Role, ChainId = ViewModel.Role, ViewModel.ChainId

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
    if role == "step" then                             -- nothing sold now: no net (D39); holds are no rows (D64)
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
function ViewModel.ChanceOf(a, chains)
  if a.kind == "post" then return a.chance end
  if a.kind == "de" or Role(a) == "step" then return nil end
  return chains[ChainId(a)]
end

--- Each chain's chance: its first post's (plan_views.chain_chances).
---@param actions table[]  Marked actions
---@return table           chainId -> percent
function ViewModel.ChainChances(actions)
  local sorted, out = {}, {}
  for i, a in ipairs(actions) do sorted[i] = a end
  table.sort(sorted, ViewModel.ByTime)
  for _, a in ipairs(sorted) do
    if a.kind == "post" and a.chance and a.inChain and out[ChainId(a)] == nil then out[ChainId(a)] = a.chance end
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
function ViewModel.ChainCrafts(actions, lookOf)
  local sorted, out = {}, {}
  for i, a in ipairs(actions) do sorted[i] = a end
  table.sort(sorted, ViewModel.ByTime)
  for _, a in ipairs(sorted) do
    if a.kind == "craft" and a.inChain then out[ChainId(a)] = lookOf(a.key).name end
  end
  return out
end

--- A row's note (plan_views.note_of, D37-D39, D66): the peak a post is for, whom a mail goes to, what a chain's
--- step is for, and why it counts what it counts, joined by " · ". When a chain posts is its row's last line (D62).
---@param a table           The action
---@param countedNote string From Counted
---@param crafts table      From ChainCrafts
---@param offsetMs number   Local time minus UTC
---@return string
function ViewModel.NoteOf(a, countedNote, crafts, offsetMs)
  local parts = {}
  if a.kind == "buy" and a.vendor then parts[1] = "from a vendor" end      -- D41, A32
  if a.kind == "post" and a.peak then
    local _, peak = ViewModel.Clock(a.peak, offsetMs)
    parts[#parts + 1] = "for the " .. peak .. " peak"
  end
  if a.kind == "mail" and a.to then parts[#parts + 1] = "to " .. ViewModel.ShortName(a.to) end   -- C22, A74
  if Role(a) == "step" and a.kind ~= "craft" and crafts[ChainId(a)] then
    parts[#parts + 1] = "for crafting " .. crafts[ChainId(a)]
  end
  if countedNote ~= "" then parts[#parts + 1] = countedNote end
  return table.concat(parts, " · ")
end

--- The footer's provisional label when your newer events were applied to an older plan (FM18).
---@param applied number  Events applied locally
---@return string         "provisional: 3 done in game since AHSync's plan · ", or "" when none
function ViewModel.Provisional(applied)
  if applied <= 0 then return "" end
  return "provisional: " .. applied .. " done in game since AHSync's plan · "
end

--- Add each row's sell record line as a note line of its own (A52).
---@param rows table[]       Today or Week rows (each with key)
---@param recordOf function  key -> { line } or nil (Plan.Record)
---@return table[]           The same rows
function ViewModel.AddRecords(rows, recordOf)
  for _, row in ipairs(rows) do
    local record = row.key and recordOf(row.key)
    if record then
      row.notes = row.notes or {}
      row.notes[#row.notes + 1] = record.line
    end
  end
  return rows
end

--- Today's Needs a decision section (A52, UI10): a header, then a DECIDE row per item AHSync
--- flagged not selling and you haven't decided on in game since.
---@param keys number[]      Plan.NotSelling()
---@param recordOf function  key -> { line }
---@param lookOf function    key -> look
---@param decided table      key -> true when you set a rule for it in game (A53)
---@return table[]           Lines; empty without a flagged item
function ViewModel.Decisions(keys, recordOf, lookOf, decided)
  local lines = {}
  for _, key in ipairs(keys) do
    if not decided[key] then
      local look = lookOf(key)
      lines[#lines + 1] = { key = key, kind = "decide", action = "DECIDE", item = look.name, quality = look.quality,
                            qty = "", each = "—", total = "—", net = "—", netColor = "muted", chance = "—", volume = "—",
                            risk = "", riskAmber = false,
                            notes = { recordOf(key).line, "right-click: Blacklist or Keep" } }
    end
  end
  if #lines == 0 then return lines end
  table.insert(lines, 1, { header = true, label = "Needs a decision", total = "", netColor = "muted" })
  return lines
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

--- What the window shows about an item besides the plan: its name, quality and market activity.
--- The name the addon saved from a link wins (it carries a random suffix, "… of Frost"), then
--- the client's item cache, then the key itself.
---@param key number           Item key
---@param item table|nil       Market.Item(key)
---@param saved string|nil     AHProfitDB.itemNames[key], "quality,name"
---@param cachedName string|nil   Adapter.ItemInfo name
---@param cachedQuality number|nil Adapter.ItemInfo quality
---@param vendorSell number|nil    Adapter.ItemInfo sell price, copper (a post's deposit)
---@return table               { name, quality, conf, soldPerDay, volumeUnknown, vendorSell }
function ViewModel.Look(key, item, saved, cachedName, cachedQuality, vendorSell)
  local quality, name = (saved or ""):match("^(%d),(.+)$")
  name = name or cachedName or ("Item " .. key)
  quality = tonumber(quality) or cachedQuality or 1
  return { name = name, quality = quality, conf = item and item.conf, soldPerDay = item and item.soldPerDay,
           volumeUnknown = (item and item.volumeUnknown) or false, vendorSell = vendorSell }
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

--- The footer's text: the most urgent problem first, so a blank tab is never silent (FM23, FM34, FM35, EX4).
---@param setupError string|nil     Why capture is off (DB.SetupError), or nil
---@param marketProblem string|nil  Market.Problem(), or nil
---@param planProblem string|nil    Plan.Problem(), or nil
---@param footer string             The tab's own footer, money as text
---@return string                   "not recording: …", else the market's problem, else the plan's, else the footer
---                                 with coin icons
function ViewModel.FooterText(setupError, marketProblem, planProblem, footer)
  if setupError then return "not recording: " .. setupError end
  return marketProblem or planProblem or ViewModel.WithCoins(footer)
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

-- ------------------------------------------------------------------ text size and paging

ViewModel.GLYPH = 0.55             -- average glyph width as a share of the font size (tests/support/lua/layout.lua's)

--- Text as the player sees it: colour codes and links reduced to their visible characters; an inline texture
--- (a coin, a dot) counts as two characters.
---@param text string|nil
---@return string
function ViewModel.Visible(text)
  text = tostring(text or "")
  text = text:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|cnIQ%d:", ""):gsub("|r", "")
  return (text:gsub("|H.-|h(.-)|h", "%1"):gsub("|T.-|t", "  "))
end

--- Estimated width of a text in px, as the layout snapshots estimate it (SPEC §8.4).
---@param text string|nil
---@param size number  Font size, px
---@return number      px
function ViewModel.TextWidth(text, size)
  local _, chars = ViewModel.Visible(text):gsub("[^\128-\191]", "")     -- characters, not bytes: "…" is one glyph
  return chars * size * ViewModel.GLYPH
end

--- How many lines a text takes when word-wrapped into a width.
---@param text string
---@param width number  px
---@param size number   Font size, px
---@return number       Lines, at least 1
function ViewModel.WrapCount(text, width, size)
  local lines, line = 1, ""
  for word in tostring(text):gmatch("%S+") do
    local candidate = line == "" and word or (line .. " " .. word)
    if line ~= "" and ViewModel.TextWidth(candidate, size) > width then
      lines, line = lines + 1, word
    else
      line = candidate
    end
  end
  return lines
end

--- The lines a list of mixed heights shows from a scroll offset (the Portfolio tab's tall rows, UI16).
---@param lines table[]   Every line; line.height px (default unitPx)
---@param offset number   Lines scrolled past (clamped)
---@param maxPx number    The list's height, px
---@param pool number     Pooled rows available
---@param unitPx number   A line's default height, px
---@return table          { lines, offset, scrollable, thumbTop, thumbSize } (thumb as shares of the track)
function ViewModel.PageByHeight(lines, offset, maxPx, pool, unitPx)
  --- A line's height, px.
  ---@param line table
  ---@return number
  local function H(line) return line.height or unitPx end
  local total, used, maxOffset = #lines, 0, #lines
  while maxOffset > 0 and used + H(lines[maxOffset]) <= maxPx and total - maxOffset + 1 <= pool do
    used, maxOffset = used + H(lines[maxOffset]), maxOffset - 1
  end
  offset = math.max(0, math.min(offset, maxOffset))
  local page, px = {}, 0
  for i = offset + 1, total do
    if #page >= pool or px + H(lines[i]) > maxPx then break end
    page[#page + 1], px = lines[i], px + H(lines[i])
  end
  if maxOffset == 0 then return { lines = page, offset = 0, scrollable = false, thumbTop = 0, thumbSize = 1 } end
  return { lines = page, offset = offset, scrollable = true, thumbTop = offset / total, thumbSize = #page / total }
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

--- One Shopping row with its state from the live data: found at or below its price, gone, or still checking (UI7).
---@param t table          { key, qty, price, why }
---@param look table
---@param levels table[]|nil
---@return table           The row
---@return number|nil      What buying it costs now, copper, when found
local function ShopRow(t, look, levels)
  local row = { key = t.key, item = look.name, quality = look.quality, qty = "×" .. t.qty, max = ViewModel.Money(t.price),
                why = ViewModel.WHY_LABELS[t.why] or "", whyKind = t.why }
  if not levels then
    row.state, row.text, row.price = "checking", "checking", "checking"
    return row, nil
  end
  local units = 0
  for _, level in ipairs(levels) do if level.price <= t.price then units = units + level.units end end
  if units < t.qty then
    row.state, row.text, row.price = "gone", "gone", "gone"
    return row, nil
  end
  row.state, row.text = "found", ViewModel.Money(levels[1].price) .. " ≤ " .. ViewModel.Money(t.price)
  row.price = ViewModel.Money(levels[1].price)
  return row, levels[1].price * t.qty
end

--- An unfunded Post row (A82): the portfolio left it out, so it is muted and says why.
---@param p table     { key, units, price (copper), net (float copper), chance (0-1), conf, reason }
---@param look table
---@return table      A post row as Today's, plus unfunded and hover
local function UnfundedPost(p, look)
  local chance = p.chance and ViewModel.RoundHalfUp(p.chance * 100) or nil
  return { key = p.key, item = look.name, quality = look.quality, kind = "post", qty = "×" .. p.units, units = p.units,
           price = p.price, each = ViewModel.Money(p.price), postNet = ViewModel.SignedMoney(p.net),
           postNetColor = p.net < 0 and "neg" or "pos", postNetCopper = p.net,
           chance = chance and (chance .. "%") or "—", dot = chance and ViewModel.Level(p.conf) or nil, unfunded = true,
           hover = "Unfunded: " .. p.reason .. ". Click to fill the Sell tab anyway" }
end

--- The panel's shopping and post lists (UI7, A82): the funded rows first, then the rows the portfolio left out, muted
--- and tagged, each with its reason on hover; the section counts say how many of each.
---@param targets table[]  { key, qty, price (the most to pay each), why ("craft" | "flip" | "de" | nil) } in list order
---@param posts table[]    Today rows of posts { item, quality, qty, each, chance, dot }
---@param levelsOf function key -> live levels, or nil when not checked yet
---@param lookOf function  key -> look
---@param gold number      All your gold, copper
---@param unfunded table|nil { shopping = targets plus reason, posts = UnfundedPost's p } (Recommend/Reselect's
---                        exclusions); nil without a portfolio snapshot
---@return table           { shopping = rows, checked = "3 of 5 + 1 unfunded", posts = rows, postCount = "1 + 1 unfunded",
---                        footer }
function ViewModel.AHPanel(targets, posts, levelsOf, lookOf, gold, unfunded)
  local shopping, checked, found, spend = {}, 0, 0, 0
  for _, t in ipairs(targets) do
    local row, cost = ShopRow(t, lookOf(t.key), levelsOf(t.key))
    if row.state ~= "checking" then checked = checked + 1 end
    if cost then found, spend = found + 1, spend + cost end
    shopping[#shopping + 1] = row
  end
  local extraShop, extraPost = (unfunded or {}).shopping or {}, (unfunded or {}).posts or {}
  for _, t in ipairs(extraShop) do
    local row = ShopRow(t, lookOf(t.key), levelsOf(t.key))
    row.unfunded, row.hover = true, "Unfunded: " .. t.reason .. ". Click to search anyway"
    shopping[#shopping + 1] = row
  end
  local postRows = {}
  for _, row in ipairs(posts) do postRows[#postRows + 1] = row end
  for _, p in ipairs(extraPost) do postRows[#postRows + 1] = UnfundedPost(p, lookOf(p.key)) end
  --- The count's unfunded part: " + 1 unfunded", or "" for none.
  ---@param n number
  ---@return string
  local function Unf(n) return n > 0 and (" + " .. n .. " unfunded") or "" end
  return { shopping = shopping, checked = checked .. " of " .. #targets .. Unf(#extraShop), posts = postRows,
           postCount = #posts .. Unf(#extraPost),
           footer = found .. " found · budget left " .. ViewModel.Money(math.max(0, gold - spend)) }
end
