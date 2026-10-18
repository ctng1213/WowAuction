-- Scan/Replicate.lua: saves the whole auction house as an order book whenever a
-- ReplicateItems answer arrives, usually one AuctionHouseFYI asked for (SPEC §4.2).
-- Listening costs no request of our own; we call ReplicateItems only as a fallback.
-- Reading and encoding are chunked at 4 ms per frame and wait out combat (SPEC §5.1).

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local DB = AHProfit.DB
local Characters = AHProfit.Capture.Characters

local Replicate = {}
AHProfit.Scan = AHProfit.Scan or {}
AHProfit.Scan.Replicate = Replicate

Replicate.BUDGET_MS = 4              -- SPEC §5.1: work per frame, the ceiling
Replicate.TARGET_MS = 3              -- where a step aims to stop: 1 ms left for a collector step or a slow row
Replicate.FALLBACK_WAIT_S = 30       -- SPEC §4.2: no answer this long after opening → call it ourselves
Replicate.MIN_CALL_GAP_S = 1200      -- SPEC §4.2: at most one own call per 20 min
Replicate.MAX_CALL_GAP_S = 7200      -- an unanswered call doubles the gap, up to 2 h
Replicate.MIN_RUN_GAP_S = 60         -- a repeated update for the same answer starts no second scan
Replicate.MAX_FULL_SCANS = 4         -- SPEC §5.1, FM22: un-ingested full scans kept

local ahOpen = false
local openId = 0               -- counts AH openings, so a timer from an earlier one does nothing
local answeredOpenId = nil     -- the opening that got an answer
local lastCallMs = nil         -- our own last ReplicateItems call this session
local callGapS = Replicate.MIN_CALL_GAP_S
local callUnanswered = false
local lastRunStartMs = nil
local run = nil                -- the scan being read or encoded
local stats = { calls = 0 }    -- last run's numbers, for diagnostics and /ahp perf
local warning = nil            -- status line text after dropping scans (FM22)
local lastBook = nil           -- the last complete scan's book, kept for live prices (SPEC §4.0)
local listeners = {}           -- called when a complete book arrives

-- ------------------------------------------------------------------ order book

--- Count one auction into the run's book: key -> unit price -> stack * 5 + band -> count.
---@param book table      The run's book; changed in place
---@param key number      Item key
---@param price number    Unit price, copper
---@param stack number    Stack size
---@param band number     Time-left band 1-4
local function AddToBook(book, key, price, stack, band)
  local prices = book[key]
  if not prices then prices = {}; book[key] = prices end
  local slots = prices[price]
  if not slots then slots = {}; prices[price] = slots end
  local slot = stack * 5 + band
  slots[slot] = (slots[slot] or 0) + 1
end

--- Sorted keys of a table.
---@param t table  Keys are numbers
---@return number[] Ascending
local function SortedKeys(t)
  local keys = {}
  for k in pairs(t) do keys[#keys + 1] = k end
  table.sort(keys)
  return keys
end

--- Encode one item's part of the book as "key,price,stack,band,count" rows (SPEC §2.2.1).
---@param parts string[]  Output rows, appended to
---@param key number      Item key
---@param prices table    price -> stack * 5 + band -> count
local function EncodeItem(parts, key, prices)
  local keyText = DB.FormatNumber(key)
  for _, price in ipairs(SortedKeys(prices)) do
    local slots = prices[price]
    local priceText = DB.FormatNumber(price)
    for _, slot in ipairs(SortedKeys(slots)) do
      local band = slot % 5
      parts[#parts + 1] = keyText .. "," .. priceText .. "," .. DB.FormatNumber((slot - band) / 5) .. ","
        .. band .. "," .. slots[slot]
    end
  end
end

-- ------------------------------------------------------------------ saving

--- Drop the oldest full scans beyond the cap; events are never touched (SPEC §5.2, FM22).
---@param db table  The database; db.scans changes in place
---@return number   Scans dropped
function Replicate.CapFullScans(db)
  local full = 0
  for _, scan in ipairs(db.scans) do
    if scan.kind == "full" then full = full + 1 end
  end
  local dropped, kept = 0, {}
  for _, scan in ipairs(db.scans) do
    if scan.kind == "full" and full - dropped > Replicate.MAX_FULL_SCANS then
      dropped = dropped + 1
    else
      kept[#kept + 1] = scan
    end
  end
  for i = 1, #db.scans do db.scans[i] = kept[i] end
  return dropped
end

--- Last phase, in a frame of its own: append the run to AHProfitDB.scans, then apply the cap.
--- Each encode step already joined its rows, so only one chunk per step is joined here.
local function SavePhase()
  local finished = run
  run = nil
  local db = DB.Get()
  if not db then return end
  db.scans[#db.scans + 1] = {
    id = DB.NewEventId(finished.charId, finished.at), char = finished.charId, at = finished.at,
    kind = finished.complete and "full" or "partial", coverage = finished.coverage,
    rows = table.concat(finished.chunks, ";"),
  }
  local dropped = Replicate.CapFullScans(db)
  stats.dropped = dropped
  if dropped > 0 then
    warning = "Dropped " .. dropped .. " old full scan(s): run AHSync to sync"
  end
  stats.kind, stats.rows = finished.complete and "full" or "partial", finished.rows
  if finished.complete then
    lastBook = { at = finished.at, book = finished.book }
    for _, fn in ipairs(listeners) do fn() end
  end
end

--- The last complete scan's order book this session (SPEC §4.0 live prices).
---@return table|nil  { at = ms, book = key -> unit price -> stack * 5 + band -> count }; nil before one
function Replicate.LastBook()
  return lastBook
end

--- Call a function whenever a complete scan's book arrives (the window redraws).
---@param fn function
function Replicate.OnBook(fn)
  listeners[#listeners + 1] = fn
end

-- ------------------------------------------------------------------ reading and encoding

--- Stop reading: what was read so far is complete or partial (FM19). Sorting starts next frame.
---@param complete boolean  true when every auction was read
local function EndReading(complete)
  run.complete = complete
  run.coverage = complete and 1 or math.floor(run.i * 1000 / run.n) / 1000
  run.phase = "sort"
end

--- Whether one more unit of work would likely cross the step's target. The next unit is
--- assumed to cost twice this step's average so far, and the target leaves 1 ms below the
--- 4 ms ceiling for what averages can't foresee: a collector step, a new link to parse, an
--- item with many price levels (measured 2026-10-17: 4.18 ms aiming at 4 with 1× the
--- average, 4.50 ms with 2×).
---@param start number  Step start, ProfileMs
---@param done number   Units of work done this step
---@return boolean      true to stop this step now
local function BudgetSpent(start, done)
  if done == 0 then return false end
  local elapsed = Adapter.ProfileMs() - start
  return elapsed + 2 * elapsed / done > Replicate.TARGET_MS
end

--- Count one auction's raw time left for the diagnostics histogram (A11, A12).
---@param value string|number  The band (1-4) or the raw value that wasn't one, as text
local function CountTimeLeft(value)
  local slot = tostring(value)
  stats.left[slot] = (stats.left[slot] or 0) + 1
end

--- Read auctions until the frame budget is spent (see BudgetSpent).
---@param start number  Step start, ProfileMs
local function ReadPhase(start)
  local rows = 0
  while run.i < run.n do
    if BudgetSpent(start, rows) then return end
    local key, price, stack, band, byId = Adapter.ReadReplicateItem(run.i, run.keyCache)
    if key then
      AddToBook(run.book, key, price, stack, band)
      CountTimeLeft(band)
      if byId then stats.byId = stats.byId + 1 end
    else
      stats.skipped = stats.skipped + 1
      stats.skip[price] = (stats.skip[price] or 0) + 1   -- price holds the reason
      if price == "band" then CountTimeLeft(stack) end  -- stack holds the raw time left
    end
    run.i, rows = run.i + 1, rows + 1
  end
  EndReading(true)
end

--- Sort the item keys, in a frame of its own, so the scan is saved in key order.
local function SortPhase()
  run.items = SortedKeys(run.book)
  run.next, run.chunks, run.rows = 1, {}, 0
  run.phase = "encode"
end

--- Encode items until the frame budget is spent (see BudgetSpent), then join this step's rows into one chunk.
---@param start number|nil  Step start, ProfileMs; nil = no budget (logout)
local function EncodePhase(start)
  local parts, items = {}, 0
  while run.next <= #run.items do
    if start and BudgetSpent(start, items) then break end
    local key = run.items[run.next]
    EncodeItem(parts, key, run.book[key])
    run.next, items = run.next + 1, items + 1
  end
  if #parts > 0 then run.chunks[#run.chunks + 1] = table.concat(parts, ";") end
  run.rows = run.rows + #parts
  if run.next > #run.items then run.phase = "save" end
end

local PHASES = { read = ReadPhase, sort = SortPhase, encode = EncodePhase, save = SavePhase }
local ScheduleStep

--- One frame's work on the current run (one phase), then the next frame is scheduled.
local function Step()
  if not run then return end
  local phase = run.phase
  local start = Adapter.ProfileMs()
  PHASES[phase](start)
  local spent = Adapter.ProfileMs() - start
  stats.steps = stats.steps + 1
  if spent > stats.maxStepMs then stats.maxStepMs = spent end
  if spent > stats.phaseMs[phase] then stats.phaseMs[phase] = spent end
  if run then Adapter.After(0, ScheduleStep) end
end

--- Timer callback: run the next step now, or once combat ends (SPEC §5.1).
ScheduleStep = function()
  Events.RunWhenSafe("scan.replicate", Step)
end

-- ------------------------------------------------------------------ events

--- REPLICATE_ITEM_LIST_UPDATE: an answer to anyone's ReplicateItems call. Start reading it.
local function OnReplicateUpdate()
  if not ahOpen or run or not DB.Get() then return end
  local now = Adapter.NowMs()
  if lastRunStartMs and now - lastRunStartMs < Replicate.MIN_RUN_GAP_S * 1000 then return end
  local n = Adapter.NumReplicateItems()
  if n < 1 then return end   -- not yet; a later update carries the auctions
  answeredOpenId, callUnanswered, callGapS = openId, false, Replicate.MIN_CALL_GAP_S
  lastRunStartMs = now
  run = { n = n, i = 0, at = now, charId = Characters.CurrentId(), phase = "read", book = {}, keyCache = {} }
  stats = { calls = stats.calls, n = n, skipped = 0, skip = {}, left = {}, byId = 0, steps = 0, maxStepMs = 0, dropped = 0,
            phaseMs = { read = 0, sort = 0, encode = 0, save = 0 } }
  Events.RunWhenSafe("scan.replicate", Step)
end

--- Our fallback: call ReplicateItems when this opening has had no answer (SPEC §4.2, A7).
---@param id number  The opening the timer was set for
function Replicate.FallbackCheck(id)
  if id ~= openId or not ahOpen or answeredOpenId == id or run or not Adapter.HasReplicate() then return end
  local now = Adapter.NowMs()
  if callUnanswered then
    callGapS = math.min(callGapS * 2, Replicate.MAX_CALL_GAP_S)
    callUnanswered = false
  end
  if lastCallMs and now - lastCallMs < callGapS * 1000 then return end
  -- Marked before the call: the answer can arrive before it returns.
  lastCallMs, callUnanswered = now, true
  stats.calls = stats.calls + 1
  if not Adapter.RequestReplicate() then callUnanswered = false end
end

--- The panel's Full scan button: ask for a ReplicateItems scan now, within the same 20-minute
--- gap as the fallback (SPEC §4.2, A24).
---@return boolean, number|nil  true when asked; else the minutes to wait (nil: the AH isn't open or a scan runs)
function Replicate.RequestScan()
  if not ahOpen or run or not Adapter.HasReplicate() then return false, nil end
  local now = Adapter.NowMs()
  if lastCallMs and now - lastCallMs < callGapS * 1000 then
    return false, math.ceil((callGapS * 1000 - (now - lastCallMs)) / 60000)
  end
  lastCallMs, callUnanswered = now, true
  stats.calls = stats.calls + 1
  if not Adapter.RequestReplicate() then callUnanswered = false end
  return true, nil
end

--- Whether the AH window is open right now.
---@return boolean
function Replicate.AHOpen()
  return ahOpen
end

--- AUCTION_HOUSE_SHOW: listen for answers; check for one after the fallback wait.
local function OnShow()
  ahOpen, openId = true, openId + 1
  local id = openId
  Adapter.After(Replicate.FALLBACK_WAIT_S, function() Replicate.FallbackCheck(id) end)
end

--- AUCTION_HOUSE_CLOSED: a read in progress is kept as partial (FM19).
local function OnClosed()
  ahOpen = false
  if run and run.phase == "read" then EndReading(false) end
end

--- PLAYER_LOGOUT (also /reload): finish every phase now, without a budget; SavedVariables is written next.
local function OnLogout()
  if run and run.phase == "read" then EndReading(false) end
  while run do PHASES[run.phase](nil) end
end

--- Keys of a table with string keys, sorted.
---@param t table  string -> any
---@return string[] Ascending
local function SortedTextKeys(t)
  local keys = {}
  for k in pairs(t) do keys[#keys + 1] = k end
  table.sort(keys)
  return keys
end

-- Skip reasons in diagnostics order (Adapter.ReadReplicateItem).
local SKIP_REASONS = { "pet", "count", "buyout", "band", "link" }

--- The last scan's numbers for diagnostics (SPEC §2.2.1).
---@return string  "calls=..|n=..|skipped=..|pet=..|count=..|buyout=..|band=..|link=..|byId=..|left=1:..,2:..|steps=..|maxStepMs=..
---                |readMs=..|sortMs=..|encodeMs=..|saveMs=..|kind=..|rows=..|dropped=.."; ms are the slowest step per phase
function Replicate.Describe()
  local s = stats
  local parts = { "calls=" .. s.calls, "n=" .. tostring(s.n), "skipped=" .. tostring(s.skipped) }
  for _, reason in ipairs(SKIP_REASONS) do parts[#parts + 1] = reason .. "=" .. ((s.skip or {})[reason] or 0) end
  parts[#parts + 1] = "byId=" .. tostring(s.byId or 0)
  local left = {}
  for _, value in ipairs(SortedTextKeys(s.left or {})) do left[#left + 1] = value .. ":" .. s.left[value] end
  parts[#parts + 1] = "left=" .. table.concat(left, ",")
  parts[#parts + 1] = "steps=" .. tostring(s.steps)
  parts[#parts + 1] = "maxStepMs=" .. string.format("%.2f", s.maxStepMs or 0)
  for _, phase in ipairs({ "read", "sort", "encode", "save" }) do
    parts[#parts + 1] = phase .. "Ms=" .. string.format("%.2f", (s.phaseMs or {})[phase] or 0)
  end
  parts[#parts + 1] = "kind=" .. tostring(s.kind)
  parts[#parts + 1] = "rows=" .. tostring(s.rows)
  parts[#parts + 1] = "dropped=" .. tostring(s.dropped)
  return table.concat(parts, "|")
end

--- The last scan's numbers as a table, for tests and /ahp perf.
---@return table  { calls, n, skipped, skip = reason -> count, steps, maxStepMs, phaseMs = phase -> ms, kind, rows, dropped }
function Replicate.Stats()
  return stats
end

--- Status line after scans were dropped for space (FM22); nil when nothing was dropped.
---@return string|nil
function Replicate.Warning()
  return warning
end

Events.Register("AUCTION_HOUSE_SHOW", OnShow)
Events.Register("AUCTION_HOUSE_CLOSED", OnClosed)
Events.Register("REPLICATE_ITEM_LIST_UPDATE", OnReplicateUpdate)
Events.Register("PLAYER_LOGOUT", OnLogout)
