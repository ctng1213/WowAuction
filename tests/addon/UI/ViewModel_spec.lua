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

  it("D39 a chain's mat buy says what it is for, and a hold shows no net", function()
    local VM = Boot()
    LOOKS[5000] = { name = "Basic Campfire Kit", quality = 1 }
    local wood = Act("w", "buy", 0, NOW, nil, { key = 2840, qty = 7 })
    local kit = Act("k", "craft", 732, NOW + MINUTE, nil, { key = 5000, qty = 7, price = nil, parent = "w" })
    local eggs = Act("e", "hold", 0, NOW, nil, { qty = 2, price = nil })
    local week = VM.Week({ wood, kit, eggs }, NOW, 0, Look, GOLD)
    local rows = {}
    for _, r in ipairs(week.days[1].rows) do rows[r.id] = r end
    assert_eq({ rows.w.net, rows.w.note }, { "—", "for crafting Basic Campfire Kit" })
    assert_eq({ rows.e.net, rows.e.note }, { "—", "hold for a later week" })
    assert_eq(week.total, "+7s 32c")
    LOOKS[5000] = nil
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

  it("A22 the Inventory tab: what you hold, its worth and its plan, best worth first", function()
    local VM = Boot()
    LOOKS[6948], LOOKS[2592], LOOKS[4306], LOOKS[6217] = { name = "Hearthstone", quality = 1 },
      { name = "Wool Cloth", quality = 1 }, { name = "Silk Cloth", quality = 1 }, { name = "Copper Rod", quality = 1 }
    local holdings = { { where = "bags", key = 2589, qty = 20, bound = false }, { where = "bags", key = 2840, qty = 6, bound = false },
                       { where = "bags", key = 6948, qty = 1, bound = true }, { where = "bank", key = 2592, qty = 4, bound = false },
                       { where = "mail", key = 4306, qty = 2, bound = false } }
    local actions = { Act("own:2589", "post", 840, NOW, 90, { qty = 20, price = 45, own = true }),
                      Act("own:craft:2663", "craft", 5700, NOW, 100, { key = 6217, qty = 3, own = true, uses = { [2840] = 6 } }),
                      Act("w", "post", 114, NOW + DAY + 19 * HOUR + 30 * MINUTE, 80, { key = 2592, qty = 4, price = 30 }) }
    local items = { [4306] = { sellValue = 60 } }
    local rows = VM.Inventory(holdings, actions, Look, function(key) return items[key] end, 0)
    local got = {}
    for i, r in ipairs(rows) do got[i] = { r.item, r.where, r.worth, r.note } end
    assert_eq(got, { { "Linen Cloth", "bags", "8s 40c", "Post now at 45c" }, { "Silk Cloth", "mail", "1s 20c", "Not planned" },
                     { "Wool Cloth", "bank", "1s 14c", "Post Sat 10:00 at 30c (AHSync)" },
                     { "Copper Bar", "bags", "—", "For crafting Copper Rod" }, { "Hearthstone", "bags", "—", "Bound · not planned" } })
    assert_eq({ rows[1].chance, rows[1].volume, rows[5].volume }, { "90%", "48/day", "—" })
    local others = VM.Inventory({ { where = "bags", key = 3300, qty = 1, bound = false } },
      { Act("v", "vendor", 12, NOW, nil, { key = 3300, qty = 1, price = 12 }), Act("h", "hold", 0, NOW, nil, { key = 2592, qty = 1 }) },
      function() return { name = "x", quality = 0 } end, function() return nil end, 0)
    assert_eq(others[1].note, "Vendor at 12c each (AHSync)")
    LOOKS[6948], LOOKS[2592], LOOKS[4306], LOOKS[6217] = nil, nil, nil, nil
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
              { "18", "1", "8s", "14s 25c", "+6s 25c", "80%", "high", "210/day" })
    assert_eq(row.note, "1.9 Strange Dust · 0.3 Greater Magic Essence · no enchanter yet")
    assert_eq(VM.DisenchantRows({ target }, Look, 150)[1].note, "1.9 Strange Dust · 0.3 Greater Magic Essence")
    target.skill = 225
    local high = VM.DisenchantRows({ target }, Look, 150)[1]
    assert_eq({ high.muted, high.note:match("needs.*$") }, { true, "needs Enchanting 225 (best 150)" })
    assert_eq({ VM.DisenchantFooter({ row }, nil), VM.DisenchantFooter({}, 150) },
              { "1 target · no enchanter yet: what one could disenchant", "0 targets · best Enchanting 150" })
    LOOKS[6585], LOOKS[10940], LOOKS[10939], LOOKS[10978] = nil, nil, nil, nil
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

  it("counts held units across places, leaves bound ones out and ties on the key", function()
    local VM = Boot()
    local holdings = { { where = "bags", key = 7, qty = 4 }, { where = "bank", key = 7, qty = 3 },
                       { where = "bags", key = 8, qty = 1, bound = true } }
    local rows = VM.Reservations({ [7] = 2, [8] = -1, [9] = 1 }, holdings, function() return { name = "Same", quality = 1 } end)
    local out = {}
    for i, r in ipairs(rows) do out[i] = r.key .. " " .. r.keeping .. " " .. r.held .. " " .. r.free end
    assert_eq(out, { "7 2 ×7 ×5", "8 All ×0 —", "9 1 ×0 —" })
    assert_eq(VM.ReservationsFooter({ rows[1] }), "1 item kept · click a row to change it")
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

  it("labels a window in local time, with its strength when it isn't established", function()
    local VM = Boot()
    assert_eq(VM.WindowLabel({ start = 138, hours = 4, weeks = 7 }, 0), "Sat 18:00–22:00")
    assert_eq(VM.WindowLabel({ start = 138, hours = 4, weeks = 4 }, -HOUR), "Sat 17:00–21:00 (pattern)")
    assert_eq(VM.WindowLabel({ start = 167, hours = 3, weeks = 2 }, 0), "Sun 23:00–Mon 02:00 (early)")
    assert_eq({ VM.SeenAgo(20000), VM.SeenAgo(25 * MINUTE), VM.SeenAgo(2 * HOUR), VM.SeenAgo(3 * DAY) },
              { "now", "25 min ago", "2 h ago", "3 d ago" })
  end)
end)

--- A JSON null from the generated fixtures as nil.
---@param v any
---@return any
local function Value(v)
  if v == JSON_NULL then return nil end
  return v
end

describe("ViewModel tooltip shared scenarios (tests/scenarios/tooltip.json)", function()
  for _, s in ipairs(dofile("tests/addon/generated/tooltip.lua")) do
    it(s.id .. " " .. s.name, function()
      local VM = Boot()
      local a = s.args
      if s.call == "delta_percent" then
        assert_eq(VM.DeltaPercent(a.value, a.current), s.expect, s.why)
        return
      end
      local lines = VM.Tooltip({ cur = Value(a.cur), curAgeText = a.cur_age_text, med30 = Value(a.med30), deValue = Value(a.de_value),
                                 canDisenchant = a.can_disenchant, bestWindow = Value(a.best_window),
                                 bestPrice = Value(a.best_price), chance = Value(a.chance) })
      local got = {}
      for i, line in ipairs(lines) do got[i] = { line.label, line.value, line.colour } end
      local want = {}
      for i, line in ipairs(s.expect) do want[i] = { line[1], line[2], Value(line[3]) } end
      assert_eq(got, want, s.why)
    end)
  end
end)
