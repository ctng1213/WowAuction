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

  it("A68 rows with volume unknown are shown with the marker, never hidden by Min chance", function()
    local VM = Boot()
    local looks = { [2589] = LOOKS[2589], [2840] = { name = "Copper Bar", quality = 1, conf = 30, volumeUnknown = true } }
    local actions = { Act("a", "post", 500, NOW, 80), Act("b", "post", 2000, NOW, 30),
                      Act("c", "post", 1000, NOW, 30, { key = 2840 }) }
    local today = VM.Today(actions, NOW, 0, 50, function(key) return looks[key] end, GOLD)
    assert_eq({ today.rows[1].id, today.rows[1].chance, today.rows[1].volume, today.rows[1].volumeUnknown,
                today.rows[2].id, today.rows[2].volumeUnknown }, { "c", "30%", "—", true, "a", nil })
    assert_eq(VM.TodayFooter(today, 50), "2 actions · expected +15s · 1 hidden (chance < 50%)")
    assert_eq(VM.Look(2840, { volumeUnknown = true }).volumeUnknown, true)
    assert_eq(VM.Look(2840, nil).volumeUnknown, false)
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
    assert_eq({ by.mats, by.buy.notes }, { nil, { "buy 20 Copper Bar on the AH · 7s 60c", "then: relist now" } })  -- D62
    assert_eq(today.total, "+2s 90c")
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
    for _, r in ipairs(today.rows) do notes[r.id] = { r.note, r.notes } end
    assert_eq(notes, { f = { "", { "then: post Fri 10:00, for the 18:00 peak" } },     -- the chain's last line (D62)
                       k = { "", { "then: post Fri 11:00, for the 19:00 peak" } }, w = { "for the 20:00 peak", {} } })
  end)

  it("D39 another character's mat buy says what it is for, and a hold is no row", function()
    local VM = Boot()
    LOOKS[5000] = { name = "Basic Campfire Kit", quality = 1 }
    local wood = Act("w", "buy", 0, NOW, nil, { key = 2840, qty = 7, char = "Brugan-Grommash" })
    local kit = Act("k", "craft", 732, NOW + MINUTE, nil, { key = 5000, qty = 7, price = nil, parent = "w" })
    local eggs = Act("e", "hold", 0, NOW, nil, { qty = 2, price = nil })
    local today = VM.Today({ wood, kit, eggs }, NOW, 0, 0, Look, GOLD)
    local rows = {}
    for _, r in ipairs(today.rows) do rows[r.id] = r end
    assert_eq({ rows.w.net, rows.w.note, rows.e }, { "—", "for crafting Basic Campfire Kit", nil })
    assert_eq(today.total, "+7s 32c")
    LOOKS[5000] = nil
  end)

  it("times are local: the client's time zone moves the clock", function()
    local VM = Boot()
    Stubs.world.localOffsetS = -15 * 3600                 -- UTC-15: Thu 14:30 UTC is Wed 23:30 local
    local offset = AHProfit.Adapter.LocalOffsetMs(NOW)
    assert_eq(offset, -15 * HOUR)
    assert_eq({ VM.Clock(NOW, offset) }, { "Wed", "23:30" })
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

  it("the footer shows capture off first, then the market file's problem, then the plan's, else the tab's own", function()
    local VM = Boot()
    assert_eq(VM.FooterText("broken", "market", "plan", "1g"), "not recording: broken")
    assert_eq({ VM.FooterText(nil, "market", "plan", "1g"), VM.FooterText(nil, nil, "plan", "1g") }, { "market", "plan" })
    assert_eq(VM.FooterText(nil, nil, nil, "1g"), VM.WithCoins("1g"))
  end)

  it("UI5 the age badge turns amber once the data is more than 24 h old", function()
    local VM = Boot()
    assert_eq(VM.Age(NOW - 30 * HOUR, NOW), { text = "data 1 d old", amber = true })
    assert_eq(VM.Age(NOW - 2 * HOUR, NOW), { text = "data 2 h old", amber = false })
    assert_eq(VM.Age(NOW - 20 * MINUTE, NOW), { text = "data 20 min old", amber = false })
    assert_eq(VM.Age(NOW - 1000, NOW), { text = "data now", amber = false })
    assert_eq(VM.Age(nil, NOW), { text = "no data", amber = true })
  end)

  it("UI30 coins replace money's letters only: whole words of digits then g, s or c", function()
    local VM = Boot()
    local G, S, C = VM.COINS.g, VM.COINS.s, VM.COINS.c
    assert_eq({ VM.WithCoins("4g 5s 6c"), VM.WithCoins("+1s 70c"), VM.WithCoins("20× at 18c") },
              { "4" .. G .. " 5" .. S .. " 6" .. C, "+1" .. S .. " 70" .. C, "20× at 18" .. C })
    assert_eq({ VM.WithCoins("1st"), VM.WithCoins("Sat 10:00"), VM.WithCoins("3 stacks"), VM.WithCoins("|cff9c0000x|r") },
              { "1st", "Sat 10:00", "3 stacks", "|cff9c0000x|r" })          -- a colour's hex digits are not money
    assert_eq({ VM.WithCoins(VM.WithCoins("2s 66c")), VM.WithCoins(nil) }, { VM.WithCoins("2s 66c"), nil })   -- idempotent
  end)

  it("A23 Disenchant rows say what the mats are and whether an enchanter on the account can do it", function()
    local VM = Boot()
    LOOKS[6585], LOOKS[10940], LOOKS[10939], LOOKS[10978] = { name = "Scouting Gloves", quality = 2 },
      { name = "Strange Dust", quality = 1, conf = 80, soldPerDay = 210 }, { name = "Greater Magic Essence", quality = 2 },
      { name = "Small Glimmering Shard", quality = 3 }
    local target = { key = 6585, ilvl = 18, skill = 1, buy = 800, ev = 1425, profit = 625, mainMat = 10940, matChance = 0.8,
                     mats = { { key = 10940, units = 1.875 }, { key = 10939, units = 0.3 }, { key = 10978, units = 0.04 } } }
    local row = VM.DisenchantRows({ target }, Look, nil)[1]
    assert_eq({ row.ilvl, row.skill, row.buy, row.ev, row.profit, row.chance, row.dot, row.volume },
              { "18", nil, "8s", "14s 25c", "+6s 25c", "80%", "high", "210/day" })
    assert_eq(row.note, "1.9 Strange Dust · 0.3 Greater Magic Essence · no enchanter yet")
    assert_eq(VM.DisenchantRows({ target }, Look, 150)[1].note, "1.9 Strange Dust · 0.3 Greater Magic Essence")
    target.skill = 225                                   -- a skill in the data never gates or greys a row (R34)
    local high = VM.DisenchantRows({ target }, Look, 1)[1]
    assert_eq({ high.muted, high.note }, { nil, "1.9 Strange Dust · 0.3 Greater Magic Essence" })
    assert_eq({ VM.DisenchantFooter({ row }, nil), VM.DisenchantFooter({}, 150) },
              { "1 target · no enchanter yet: what one could disenchant", "0 targets" })
    LOOKS[6585], LOOKS[10940], LOOKS[10939], LOOKS[10978] = nil, nil, nil, nil
  end)

  it("A43 the Full scan status and bar for each step of a scan", function()
    local VM = Boot()
    local function S(p) local s = VM.ScanStatus(p, 200); return (s.text or "-") .. " " .. tostring(s.shown) .. " " .. s.px end
    assert_eq({ S(nil), S({ state = "read", done = 23400, total = 47667 }), S({ state = "read", done = 0, total = 0 }),
                S({ state = "sort" }), S({ state = "encode", done = 1, total = 2 }), S({ state = "encode", done = 0, total = 0 }),
                S({ state = "save" }), S({ state = "done", auctions = 47667, complete = false }) },
              { "- false 0", "full scan: reading 23,400 of 47,667 true 83", "full scan: reading 0 of 0 true 0",
                "full scan: saving true 170", "full scan: saving true 185", "full scan: saving true 200",
                "full scan: saving true 200", "full scan done · 47,667 auctions (partial) false 0" })
    assert_eq({ VM.Thousands(7), VM.Thousands(1234567) }, { "7", "1,234,567" })
  end)

  it("flip rows: best profit first (ties: id), no live note without a scan, a post without a peak uses its own time", function()
    local VM = Boot()
    local lookOf = function() return { name = "Linen Cloth", quality = 1 } end
    local actions = { { id = "b", kind = "buy", key = 1, qty = 2, price = 10, at = 0, net = 50 },
                      { id = "b2", kind = "post", key = 1, qty = 2, price = 30, at = 3600000, parent = "b" },
                      { id = "a", kind = "buy", key = 1, qty = 1, price = 10, at = 0, net = 50 },
                      { id = "a2", kind = "post", key = 1, qty = 1, price = 30, at = 7200000, parent = "a" },
                      { id = "c", kind = "buy", key = 1, qty = 1, price = 10, at = 0 },
                      { id = "c2", kind = "post", key = 1, qty = 1, price = 5, at = 7200000, parent = "c" } }
    local rows = VM.FlipRows(actions, lookOf, function() return nil end, 0, 0)
    assert_eq({ rows[1].id, rows[2].id, rows[3].id }, { "a", "b", "c" })
    assert_eq({ rows[1].when, rows[1].chance, rows[1].dot, rows[1].risk, rows[1].note },
              { "Thu 02:00", "—", nil, "—", "post Thu 02:00, for the 02:00 peak" })
    assert_eq({ rows[3].profit, rows[3].netColor }, { "0c", "neg" })
    assert_eq({ VM.FlipFooter({}, {}), VM.FlipFooter({ rows[1] }, rows) },
              { "0 flips · capital needed 0c", "1 flip · capital needed 10c · 2 gone" })
  end)

  it("UI7 the AH panel's states: found at or below the target, gone, or still checking; 3 of 5 checked", function()
    local VM = Boot()
    local levels = { [1] = { { price = 38, units = 20 } }, [2] = { { price = 800, units = 1 } }, [3] = { { price = 60, units = 2 } } }
    local targets = { { key = 1, qty = 20, price = 45 }, { key = 2, qty = 1, price = 900 }, { key = 3, qty = 6, price = 70 },
                      { key = 4, qty = 1, price = 10 }, { key = 5, qty = 2, price = 10 } }
    local panel = VM.AHPanel(targets, {}, function(k) return levels[k] end, function(k) return { name = "I" .. k, quality = 1 } end, 314000)
    local states = {}
    for i, r in ipairs(panel.shopping) do states[i] = r.state .. " " .. r.text end
    assert_eq(states, { "found 38c ≤ 45c", "found 8s ≤ 9s", "gone gone", "checking checking", "checking checking" })
    assert_eq({ panel.checked, panel.footer }, { "3 of 5", "2 found · budget left 31g 24s 40c" })    -- 31g 40s - 7s 60c - 8s
    assert_eq({ panel.shopping[1].price, panel.shopping[1].max, panel.shopping[3].price, panel.shopping[4].price },
              { "38c", "45c", "gone", "checking" })
  end)

  it("estimates text widths, wrapped lines and pages of mixed heights as the layout snapshots do", function()
    local VM = Boot()
    assert_eq({ VM.TextWidth("abcd", 10), VM.TextWidth("|cffffffff…|r", 10), VM.TextWidth("1|Tx|t", 10) }, { 22, 5.5, 16.5 })
    assert_eq({ VM.WrapCount("Required profession unavailable: Enchanting 150", 150, 10),
                VM.WrapCount("Funded", 150, 10), VM.WrapCount("", 150, 10) }, { 2, 1, 1 })
    local lines = { { height = 20 }, { height = 30 }, { height = 30 }, {}, { height = 50 } }
    local page = VM.PageByHeight(lines, 0, 80, 12, 20)
    assert_eq({ #page.lines, page.scrollable, page.offset }, { 3, true, 0 })     -- 20 + 30 + 30; the 4th won't fit
    page = VM.PageByHeight(lines, 99, 80, 12, 20)
    assert_eq({ #page.lines, page.offset }, { 2, 3 })                          -- the last two: 20 + 50
    assert_eq(VM.PageByHeight({ {}, {} }, 5, 80, 12, 20), { lines = { {}, {} }, offset = 0, scrollable = false,
                                                            thumbTop = 0, thumbSize = 1 })
  end)

  it("UI24 how far a Flip row's When time is: whole days, else whole hours, else within the hour", function()
    local VM = Boot()
    local H, D = 3600000, 86400000
    assert_eq({ VM.InTime(2 * D + 9 * H), VM.InTime(D), VM.InTime(6 * D + 4 * H), VM.InTime(D - 60000),
                VM.InTime(3 * H + 30 * 60000), VM.InTime(40 * 60000) },
              { "in 2 days", "in 1 day", "in 6 days", "in 23 h", "in 3 h", "within the hour" })
  end)

  it("UI25 a Flip cell's money from 1g rounds to the silver; under 1g keeps its copper", function()
    local VM = Boot()
    assert_eq({ VM.CellMoney(151201), VM.CellMoney(65501, true), VM.CellMoney(65550, true), VM.CellMoney(10000),
                VM.CellMoney(9999), VM.CellMoney(30, true), VM.CellMoney(-12345, true), VM.CellMoney(nil) },
              { "15g 12s", "+6g 55s", "+6g 56s", "1g", "99s 99c", "+30c", "−1g 23s", "—" })
  end)
end)
