-- Specs for addon/AHProfit/Core/Events.lua: registration, debounce, combat queue, no OnUpdate.

local Stubs = require("wow_stubs")

--- Fresh stubs with only the Core files loaded.
---@return table  AHProfit.Events
local function Boot()
  Stubs.Reset()
  Stubs.LoadAddon({ "Core/Adapter.lua", "Core/Events.lua" })
  return AHProfit.Events
end

describe("Events", function()
  it("calls every handler of an event with its arguments, in registration order", function()
    local Events = Boot()
    local calls = {}
    Events.Register("BAG_UPDATE", function(event, bag) calls[#calls + 1] = "a:" .. event .. ":" .. bag end)
    Events.Register("BAG_UPDATE", function(_, bag) calls[#calls + 1] = "b:" .. bag end)
    Stubs.FireEvent("BAG_UPDATE", 2)
    assert_eq(calls, { "a:BAG_UPDATE:2", "b:2" })
  end)

  it("registers a handler only once", function()
    local Events = Boot()
    local count = 0
    local function handler() count = count + 1 end
    Events.Register("PLAYER_MONEY", handler)
    Events.Register("PLAYER_MONEY", handler)
    Stubs.FireEvent("PLAYER_MONEY")
    assert_eq(count, 1)
  end)

  it("unregisters the frame's event when its last handler is removed", function()
    local Events = Boot()
    local function a() end
    local function b() end
    Events.Register("MAIL_INBOX_UPDATE", a)
    Events.Register("MAIL_INBOX_UPDATE", b)
    Events.Unregister("MAIL_INBOX_UPDATE", a)
    assert_eq(Stubs.IsRegistered("MAIL_INBOX_UPDATE"), true)
    Events.Unregister("MAIL_INBOX_UPDATE", b)
    assert_eq(Stubs.IsRegistered("MAIL_INBOX_UPDATE"), false)
    Events.Unregister("MAIL_INBOX_UPDATE", b)
  end)

  it("a handler may unregister itself while the event is dispatched", function()
    local Events = Boot()
    local calls = {}
    local function first() calls[#calls + 1] = "first"; Events.Unregister("X", first) end
    Events.Register("X", first)
    Events.Register("X", function() calls[#calls + 1] = "second" end)
    Stubs.FireEvent("X")
    Stubs.FireEvent("X")
    assert_eq(calls, { "first", "second", "second" })
  end)

  it("debounce runs a burst once, 0.5 s after its first call, with the latest function", function()
    local Events = Boot()
    local ran = {}
    Events.Debounce("bags", function() ran[#ran + 1] = 1 end)
    Stubs.Advance(0.2)
    Events.Debounce("bags", function() ran[#ran + 1] = 2 end)
    assert_eq(Stubs.PendingTimers(), 1)
    Stubs.Advance(0.2)
    assert_eq(ran, {})
    Stubs.Advance(0.1)
    assert_eq(ran, { 2 })
    Events.Debounce("bags", function() ran[#ran + 1] = 3 end)
    Stubs.Advance(0.5)
    assert_eq(ran, { 2, 3 })
  end)

  it("queues work in combat and runs it once, in first-queued order, when combat ends", function()
    local Events = Boot()
    local ran = {}
    Stubs.EnterCombat()
    assert_eq(Events.InCombat(), true)
    Events.RunWhenSafe("bags", function() ran[#ran + 1] = "bags1" end)
    Events.RunWhenSafe("mail", function() ran[#ran + 1] = "mail" end)
    Events.RunWhenSafe("bags", function() ran[#ran + 1] = "bags2" end)
    assert_eq(ran, {})
    Stubs.LeaveCombat()
    assert_eq(ran, { "bags2", "mail" })
    Events.RunWhenSafe("bags", function() ran[#ran + 1] = "now" end)
    assert_eq(ran, { "bags2", "mail", "now" })
  end)

  it("a debounce that fires in combat waits for combat to end", function()
    local Events = Boot()
    local ran = 0
    Events.Debounce("bags", function() ran = ran + 1 end)
    Stubs.EnterCombat()
    Stubs.Advance(1)
    assert_eq(ran, 0)
    Stubs.LeaveCombat()
    assert_eq(ran, 1)
  end)

  it("starts in combat when loaded during combat lockdown", function()
    Stubs.Reset()
    Stubs.world.inCombat = true
    Stubs.LoadAddon({ "Core/Adapter.lua", "Core/Events.lua" })
    assert_eq(AHProfit.Events.InCombat(), true)
  end)

  it("catches handler errors and keeps the last 10", function()
    local Events = Boot()
    local after = false
    Events.Register("X", function() error("broken", 0) end)
    Events.Register("X", function() after = true end)
    for _ = 1, 12 do Stubs.FireEvent("X") end
    assert_eq(after, true)
    assert_eq(#Events.Errors(), 10)
    assert_eq(Events.Errors()[1], "broken")
  end)

  it("setting an OnUpdate script raises (SPEC §5.1)", function()
    Boot()
    local frame = CreateFrame("Frame")
    local ok = pcall(frame.SetScript, frame, "OnUpdate", function() end)
    assert_eq(ok, false)
  end)

  it("the whole addon loads and logs in without any OnUpdate script", function()
    Stubs.Reset()
    Stubs.Login()
    assert_eq(AHProfit.Events.Errors(), {})
  end)
end)
