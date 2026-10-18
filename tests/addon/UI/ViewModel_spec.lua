-- Specs for addon/AHProfit/UI/ViewModel.lua: the window's text and flags (SPEC §4.3, §8.4 layer 1).
-- Expected values match ahsync/engine/views.py and engine/plan_views.py for the same inputs.

local Stubs = require("wow_stubs")

local HOUR, DAY, MINUTE = 3600000, 86400000, 60000
local T = 1760000000000                      -- Thu 9 Oct 2025 08:53:20 UTC, the stubs' clock
local NOW = T - (8 * 60 + 53) * MINUTE - 20000 + 14 * HOUR + 30 * MINUTE   -- Thu 14:30 UTC
local GOLD = 314000
local LOOKS = { [2589] = { name = "Linen Cloth", quality = 1, conf = 86, soldPerDay = 48 },
                [2840] = { name = "Copper Bar", quality = 1, conf = 50, soldPerDay = 40.4 } }

--- Boot the addon and return its view model.
---@return table
local function Boot()
  Stubs.Reset()
  Stubs.Login()
  return AHProfit.ViewModel
end

--- An item's look.
---@param key number
---@return table
local function Look(key) return LOOKS[key] end

--- A pending plan action of 20 Linen at 38c by Amaya, standing alone unless changed.
---@param id string
---@param kind string
---@param net number|nil
---@param at number
---@param chance number|nil  Percent
---@param more table|nil     Fields to change
---@return table
local function Act(id, kind, net, at, chance, more)
  local a = { id = id, kind = kind, char = "Amaya-Grommash", key = 2589, qty = 20, price = 38, at = at, net = net,
              chance = chance, status = "pending" }
  for k, v in pairs(more or {}) do a[k] = v end
  return a
end

describe("ViewModel", function()
  it("formats money, signed money and risk exactly as the dashboard does", function()
    local VM = Boot()
    assert_eq({ VM.Money(11810), VM.Money(-20), VM.Money(0), VM.Money(nil), VM.Money(213.75), VM.Money(10000) },
              { "1g 18s 10c", "−20c", "0c", "—", "2s 14c", "1g" })
    assert_eq({ VM.SignedMoney(1045.5), VM.SignedMoney(-20), VM.SignedMoney(0.4) }, { "+10s 46c", "−20c", "0c" })
    assert_eq({ VM.Risk(30000, GOLD) }, { "9.6%", false })          -- 30000 / 314000 = 9.55% -> 96 tenths
    assert_eq({ VM.Risk(100000, GOLD) }, { "31.8%", true })
    assert_eq({ VM.Risk(5, 0) }, { "—", false })
    assert_eq({ VM.Level(75), VM.Level(45), VM.Level(44), VM.Level(nil) }, { "high", "medium", "low", nil })
  end)

  it("D16 Today: best net first, ties to the higher chance; the footer adds up", function()
    local VM = Boot()
    local actions = { Act("a", "post", 1000, NOW, 70), Act("b", "post", 500, NOW, 80), Act("c", "post", 500, NOW, 90) }
    local today = VM.Today(actions, NOW, 0, 50, Look, GOLD)
    local chances = {}
    for i, r in ipairs(today.rows) do chances[i] = r.chance end
    assert_eq(chances, { "70%", "90%", "80%" })
    assert_eq({ today.total, today.hidden, today.rows[1].net, today.rows[1].netColor }, { "+20s", 0, "+10s", "pos" })
    assert_eq({ today.rows[1].dot, today.rows[1].volume, today.rows[1].who, today.rows[1].action }, { "high", "48/day", "Amaya", "POST" })
  end)

  it("D1 rows below the chance filter are hidden and counted in the footer; later actions wait", function()
    local VM = Boot()
    local actions = { Act("a", "post", 500, NOW, 90), Act("b", "post", 2000, NOW, 30), Act("c", "post", 1000, NOW, 60),
                      Act("later", "post", 900, NOW + 2 * HOUR, 90), Act("done", "post", 9, NOW, 90, { status = "done" }) }
    local today = VM.Today(actions, NOW, 0, 50, Look, GOLD)
    assert_eq(VM.TodayFooter(today, 50), "2 actions · expected +15s · 1 hidden (chance < 50%)")
    assert_eq(VM.TodayFooter(VM.Today({ Act("a", "post", 500, NOW, 90) }, NOW, 0, 50, Look, GOLD), 50), "1 action · expected +5s")
  end)

  it("VW1 a flip's profit counts once: on its buy, not on its follow-up post", function()
    local VM = Boot()
    local buy = Act("buy", "buy", 290, NOW, nil, { qty = 20, price = 38 })
    local post = Act("post", "post", 1050, NOW + 10 * MINUTE, 90, { parent = "buy", price = 55 })
    local hold = Act("mats", "buy", 0, NOW, nil, { key = 2840, parent = "buy" })
    local today = VM.Today({ buy, post, hold }, NOW, 0, 0, Look, GOLD)
    local by = {}
    for _, r in ipairs(today.rows) do by[r.id] = r end
    assert_eq({ by.buy.net, by.buy.chance, by.buy.risk }, { "+2s 90c", "90%", "0.2%" })   -- 760c / 314000c
    assert_eq({ by.post.net, by.post.note }, { "0c", "profit counted on the Thu 14:30 buy" })
    assert_eq({ by.mats.net, by.mats.chance, by.mats.volume }, { "—", "—", "—" })
    assert_eq(today.total, "+2s 90c")
  end)

  it("D37 a chain's post names its peak and where its profit was counted", function()
    local VM = Boot()
    local buy = Act("f", "buy", 210, NOW - 20 * MINUTE)
    local post = Act("g", "post", 95, NOW + 20 * HOUR - 30 * MINUTE, 100, { parent = "f", price = 45, peak = NOW + DAY + 3 * HOUR + 30 * MINUTE })
    local week = VM.Week({ buy, post }, NOW, 0, Look, GOLD)
    assert_eq(week.days[2].rows[1].note, "for the 18:00 peak · profit counted on the Thu 14:10 buy")
  end)

  it("D38 Today says which peak each post is for and when a chain will post", function()
    local VM = Boot()
    local buy = Act("f", "buy", 210, NOW)
    local flipPost = Act("g", "post", 95, NOW + DAY - 4 * HOUR - 30 * MINUTE, 100,
                         { parent = "f", peak = NOW + DAY + 3 * HOUR + 30 * MINUTE })
    local craft = Act("k", "craft", 300, NOW, nil, { key = 2840, qty = 1, price = nil })
    local craftPost = Act("q", "post", 300, NOW + DAY - 3 * HOUR - 30 * MINUTE, 80,
                          { parent = "k", key = 2840, peak = NOW + DAY + 4 * HOUR + 30 * MINUTE })
    local wool = Act("w", "post", 100, NOW, 90, { peak = NOW + 5 * HOUR + 30 * MINUTE })
    local today = VM.Today({ buy, flipPost, craft, craftPost, wool }, NOW, 0, 0, Look, GOLD)
    local notes = {}
    for _, r in ipairs(today.rows) do notes[r.id] = r.note end
    assert_eq(notes, { f = "Flip: post Fri 10:00, for the 18:00 peak", k = "Then: post Fri 11:00, for the 19:00 peak",
                       w = "for the 20:00 peak" })
  end)

  it("D17 Week: seven local days from today, each with its total, and the week total", function()
    local VM = Boot()
    local actions = { Act("a", "post", 830, NOW + HOUR), Act("b", "post", 11000, NOW + 2 * HOUR),
                      Act("c", "vendor", -20, NOW + DAY, nil, { char = "Brugan-Grommash" }),
                      Act("far", "post", 5, NOW + 8 * DAY) }
    local week = VM.Week(actions, NOW, 0, Look, GOLD)
    assert_eq({ #week.days, week.days[1].label, week.days[1].today, week.days[2].label }, { 7, "Thu 9", true, "Fri 10" })
    assert_eq({ week.days[1].total, week.days[2].total, week.days[3].total, week.total }, { "+1g 18s 30c", "−20c", "0c", "+1g 18s 10c" })
    assert_eq({ week.days[1].rows[1].time, week.days[2].rows[1].who }, { "15:30", "Brugan" })
  end)

  it("times are local: the client's time zone moves the week's first day and the clock", function()
    local VM = Boot()
    Stubs.world.localOffsetS = -15 * 3600                 -- UTC-15: Thu 14:30 UTC is Wed 23:30 local
    local offset = AHProfit.Adapter.LocalOffsetMs(NOW)
    assert_eq(offset, -15 * HOUR)
    assert_eq({ VM.Clock(NOW, offset) }, { "Wed", "23:30" })
    assert_eq(VM.Week({}, NOW, offset, Look, GOLD).days[1].label, "Wed 8")
    Stubs.world.localOffsetS = 9 * 3600 + 1800
    assert_eq(AHProfit.Adapter.LocalOffsetMs(NOW), 9 * HOUR + 30 * MINUTE)
  end)

  it("money shows coin icons, and each note becomes a line under its row", function()
    local VM = Boot()
    local g, s, c = VM.COINS.g, VM.COINS.s, VM.COINS.c
    assert_eq(VM.WithCoins("+1g 18s 10c"), "+1" .. g .. " 18" .. s .. " 10" .. c)
    assert_eq(VM.WithCoins("3 actions · expected −20c"), "3 actions · expected −20" .. c)
    assert_eq({ VM.WithCoins("—"), g:find("MoneyFrame", 1, true) ~= nil }, { "—", true })
    local lines = VM.WithNotes({ { id = "a", note = "for the 18:00 peak" }, { id = "b", note = "" }, { header = true } })
    assert_eq(lines, { { id = "a", note = "for the 18:00 peak" }, { noteLine = true, text = "for the 18:00 peak" },
                       { id = "b", note = "" }, { header = true } })
  end)

  it("UI5 the age badge turns amber once the data is more than 24 h old", function()
    local VM = Boot()
    assert_eq(VM.Age(NOW - 30 * HOUR, NOW), { text = "data 1 d old", amber = true })
    assert_eq(VM.Age(NOW - 2 * HOUR, NOW), { text = "data 2 h old", amber = false })
    assert_eq(VM.Age(NOW - 20 * MINUTE, NOW), { text = "data 20 min old", amber = false })
    assert_eq(VM.Age(NOW - 1000, NOW), { text = "data now", amber = false })
    assert_eq(VM.Age(nil, NOW), { text = "no data", amber = true })
  end)
end)
