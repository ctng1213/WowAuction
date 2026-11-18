-- Recommend/DeTargets.lua: the AH panel's disenchant targets (Options.DisenchantOne for every disenchantable item),
-- worked out a few ms per frame and kept until a price they read changes (SPEC §5.1, A96-A98). Opening the AH, a
-- search result and a finished scan used to work all of them out in the frame that drew the panel: 6,177 items,
-- 0.1-0.9 s each time on the 2026-11-17 market file.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter
local Events = AHProfit.Events
local Market = AHProfit.Market
local Live = AHProfit.Live
local Options = AHProfit.Options

local DeTargets = {}
AHProfit.DeTargets = DeTargets

DeTargets.TARGET_MS = 3      -- a step stops here, inside SPEC §5.1's 4 ms per frame
DeTargets.STEP_ITEMS = 500   -- and after this many items, so a step stays short where the clock can't tell (tests)

local job = nil              -- { book, cursor, rows = key -> row | false, done, steps } for the current prices
local lastOut = nil          -- the last complete targets, best profit first: shown while a new job runs (SPEC §5.2)
local listeners = {}         -- called when a job finishes
local perf = { steps = 0, maxStepMs = 0, jobs = 0 }

--- The live levels the targets read: a scan newer than the market file, else nil.
---@param key number
---@return table[]|nil
local function LevelsOf(key)
  local _, newest = Market.Times()
  return Live.Item(key, newest)
end

local CTX = { itemOf = function(key) return Market.Item(key) end, levelsOf = LevelsOf }

--- The book the targets are priced from: the live scan when it is newer than the market file.
---@return table|boolean  The book; false without one
local function CurrentBook()
  local _, newest = Market.Times()
  local last = Live.Book(newest)
  return last and last.book or false
end

--- The finished job's rows, best profit first (Options.SortDisenchant).
---@param j table  The job
---@return table[]
local function Collect(j)
  local out = {}
  for _, row in pairs(j.rows) do
    if row then out[#out + 1] = row end
  end
  return Options.SortDisenchant(out)
end

--- Finish a job: keep its targets and tell the listeners (the panel redraws once).
---@param j table  The job
local function Finish(j)
  j.done, lastOut = true, Collect(j)
  perf.jobs = perf.jobs + 1
  for _, fn in ipairs(listeners) do fn() end
end

local Schedule

--- One frame's work on a job: items until TARGET_MS or STEP_ITEMS, then the next frame (SPEC §5.1).
---@param j table  The job; a step of a replaced job does nothing
local function Step(j)
  if job ~= j or j.done then return end
  local start, done = Adapter.ProfileMs(), 0
  local candidates, tbl = Market.Disenchantable(), AHProfit.Engine.Disenchant.Table()
  while true do
    local key, c = next(candidates, j.cursor)    -- the cached table never changes, so next() can resume
    if key == nil then break end
    j.cursor = key
    j.rows[key] = Options.DisenchantOne(tbl, key, c, CTX) or false
    done = done + 1
    if done >= DeTargets.STEP_ITEMS or Adapter.ProfileMs() - start >= DeTargets.TARGET_MS then break end
  end
  j.steps, perf.steps = j.steps + 1, perf.steps + 1
  perf.maxStepMs = math.max(perf.maxStepMs, Adapter.ProfileMs() - start)
  if next(candidates, j.cursor) == nil then Finish(j) else Schedule(j) end
end

--- Run a job's next step in the next frame, after combat (SPEC §5.1).
---@param j table
Schedule = function(j)
  Adapter.After(0, function() Events.RunWhenSafe("detargets.step", function() Step(j) end) end)
end

--- The disenchant targets for the current prices: the last complete ones while new ones are worked out (A96, A98).
--- Starts a job when there is none for these prices; never works one out in this frame.
---@return table[], boolean  Options.DisenchantOne's rows, best profit first (empty before the first job ends);
---                          whether they are for the current prices
function DeTargets.Rows()
  local book = CurrentBook()
  if not job or job.book ~= book then
    job = { book = book, cursor = nil, rows = {}, done = false, steps = 0 }
    Schedule(job)
  end
  return lastOut or {}, job.done
end

--- Base item id of a key (a random-suffix key's item, A38).
---@param key number
---@return number
local function BaseId(key)
  return key < 0 and -key or key % 2147483648
end

--- A search result for an item: re-evaluate the targets of that item already worked out, so its row follows the
--- search in the redraw that comes next (A97). Items not reached yet read it when their step comes.
---@param itemId number
function DeTargets.Searched(itemId)
  if not job then return end
  local tbl, candidates, changed = AHProfit.Engine.Disenchant.Table(), Market.Disenchantable(), false
  for key, row in pairs(job.rows) do
    if BaseId(key) == itemId then
      local new = Options.DisenchantOne(tbl, key, candidates[key], CTX) or false
      if new or row then job.rows[key], changed = new, true end
    end
  end
  if changed and job.done then lastOut = Collect(job) end
end

--- Call a function when a job finishes (the panel redraws with the new targets).
---@param fn function
function DeTargets.OnReady(fn)
  listeners[#listeners + 1] = fn
end

--- The steps' numbers for /ahp perf and the diagnostics.
---@return table  { steps, maxStepMs, jobs }
function DeTargets.Perf()
  return perf
end

--- Forget every job and target (tests).
function DeTargets.Reset()
  job, lastOut = nil, nil
  perf = { steps = 0, maxStepMs = 0, jobs = 0 }
end

Live.OnSearch(function(itemId) DeTargets.Searched(itemId) end)
