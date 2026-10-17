-- Core/Events.lua: one event frame, register/unregister, debounce and the combat queue (SPEC §5.1).
-- No OnUpdate: delays are one-shot timers through the Adapter. Handler errors
-- are caught and kept for /ahp status instead of raising Lua error popups.

local AHProfit = AHProfit
local Adapter = AHProfit.Adapter

local Events = {}
AHProfit.Events = Events

Events.DEBOUNCE_S = 0.5   -- SPEC §5.1: coalesce bursty events
local MAX_ERRORS = 10

local handlers = {}         -- event -> array of handler functions (replaced, never mutated)
local pending = {}          -- debounce key -> latest function waiting for its timer
local timers = {}           -- debounce key -> the one closure passed to the timer
local combatQueue = {}      -- key -> function to run when combat ends
local combatOrder = {}      -- keys in the order they were first queued
local errors = {}           -- most recent handler errors, oldest first
local inCombat = Adapter.InCombat()
local frame = Adapter.CreateEventFrame()

--- Run a function in a protected call and keep any error.
---@param fn function  Function to run
---@return boolean     true when it ran without error
local function SafeCall(fn, ...)
  local ok, err = pcall(fn, ...)
  if not ok then
    if #errors >= MAX_ERRORS then table.remove(errors, 1) end
    errors[#errors + 1] = tostring(err)
  end
  return ok
end

--- Run every queued function in the order it was first queued, then clear the queue.
local function FlushCombatQueue()
  local order = combatOrder
  combatOrder = {}
  for _, key in ipairs(order) do
    local fn = combatQueue[key]
    combatQueue[key] = nil
    if fn then SafeCall(fn) end
  end
end

--- The event frame's OnEvent script: tracks combat, then calls the event's handlers.
---@param _ table      The event frame
---@param event string WoW event name
local function Dispatch(_, event, ...)
  if event == "PLAYER_REGEN_DISABLED" then
    inCombat = true
  elseif event == "PLAYER_REGEN_ENABLED" then
    inCombat = false
    FlushCombatQueue()
  end
  local list = handlers[event]
  if not list then return end
  for i = 1, #list do SafeCall(list[i], event, ...) end
end

--- Add a handler for an event; the frame registers the event on its first handler.
---@param event string    WoW event name
---@param handler function Called as handler(event, ...); added once even if registered twice
function Events.Register(event, handler)
  local old = handlers[event] or {}
  local list = {}
  for i = 1, #old do
    if old[i] == handler then return end
    list[i] = old[i]
  end
  list[#list + 1] = handler
  handlers[event] = list
  -- An event this client doesn't know raises; keep the error instead (Forever lacks some
  -- Classic events, e.g. TRADE_SKILL_UPDATE), so one bad name can't break the addon.
  SafeCall(frame.RegisterEvent, frame, event)
end

--- Remove a handler; the frame unregisters the event when none is left (SPEC §5.1).
---@param event string     WoW event name
---@param handler function The handler passed to Register
function Events.Unregister(event, handler)
  local old = handlers[event]
  if not old then return end
  local list = {}
  for i = 1, #old do
    if old[i] ~= handler then list[#list + 1] = old[i] end
  end
  if #list > 0 then
    handlers[event] = list
  else
    handlers[event] = nil
    if event ~= "PLAYER_REGEN_DISABLED" and event ~= "PLAYER_REGEN_ENABLED" then
      SafeCall(frame.UnregisterEvent, frame, event)
    end
  end
end

--- Run now, or once after combat ends when in combat (SPEC §5.1: nothing in combat).
--- Queuing the same key again replaces the queued function, so work runs once.
---@param key string   Identifies the work, e.g. "inventory.bags"
---@param fn function  Work to run with no arguments
function Events.RunWhenSafe(key, fn)
  if not inCombat then
    SafeCall(fn)
    return
  end
  if not combatQueue[key] then combatOrder[#combatOrder + 1] = key end
  combatQueue[key] = fn
end

--- Timer callback for one debounce key: runs its latest function when safe.
---@param key string  The debounce key
local function FireDebounced(key)
  local fn = pending[key]
  pending[key] = nil
  if fn then Events.RunWhenSafe(key, fn) end
end

--- The timer closure for a key, created once per key and reused.
---@param key string  The debounce key
---@return function   Calls FireDebounced(key)
local function TimerFor(key)
  if not timers[key] then
    timers[key] = function() FireDebounced(key) end
  end
  return timers[key]
end

--- Run fn once, DEBOUNCE_S after the first of a burst of calls (SPEC §5.1).
--- Calls during the wait only replace the function; no new timer is started.
---@param key string   Identifies the work, e.g. "inventory.bags"
---@param fn function  Work to run with no arguments
function Events.Debounce(key, fn)
  local waiting = pending[key] ~= nil
  pending[key] = fn
  if not waiting then Adapter.After(Events.DEBOUNCE_S, TimerFor(key)) end
end

--- Whether the addon currently treats the player as in combat.
---@return boolean  true between PLAYER_REGEN_DISABLED and PLAYER_REGEN_ENABLED
function Events.InCombat()
  return inCombat
end

--- Errors caught from handlers and queued work, oldest first (at most 10).
---@return string[]  Error messages; empty when nothing failed
function Events.Errors()
  return errors
end

frame:SetScript("OnEvent", Dispatch)
frame:RegisterEvent("PLAYER_REGEN_DISABLED")
frame:RegisterEvent("PLAYER_REGEN_ENABLED")
