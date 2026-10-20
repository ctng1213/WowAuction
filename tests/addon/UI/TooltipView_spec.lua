-- Specs for addon/AHProfit/UI/TooltipView.lua: the tooltip's lines (SPEC §4.3 Tooltip; TT1-TT5's deltas, TT8-TT21).
-- The variants' facts are in tests/support/lua/tooltip_world.lua; tests/visual/Tooltip_spec.lua checks they fit (UI17).

local Stubs = require("wow_stubs")
local TW = require("tooltip_world")

--- Boot the addon and return its tooltip view model.
---@return table
local function Boot()
  Stubs.Reset()
  Stubs.Login()
  return AHProfit.TooltipView
end

--- One variant's lines as "label | value" texts.
---@param n number  The variant, 1-14
---@return string[], table[]  Texts, and the lines
local function Variant(n)
  local TV = Boot()
  local lines = TV.Lines(TW.Variants(TV, AHProfit.ViewModel)[n][2])
  return TW.Texts(lines), lines
end

--- A JSON null from the generated fixtures as nil.
---@param v any
---@return any
local function Value(v)
  if v == JSON_NULL then return nil end
  return v
end

describe("TooltipView", function()
  it("TT8 a bag item with a planned post: Do first, then Market, both best times and Activity", function()
    local texts, lines = Variant(1)
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: POST 5 now at 41s | +63s 6c · 58% chance", " | then nothing",
      "Market | 42s (2 h) · 45s 30-day (+7%)", "Best time to sell | Fri 18–21 · 45s (+7%) · pattern",
      "Best time to buy | Mon 03–07 · 38s (−10%) · early", "Activity | 6/day · 14 listed · 2.3 d supply" })
    assert_eq({ lines[1].amber, lines[2].verb, lines[2].verbColor, lines[2].spans[1], lines[7].muted },
              { false, "POST", "post", { "+63s 6c", "pos" }, true })
    assert_eq({ lines[4].spans[1], lines[6].spans[1] }, { { "(+7%)", "pos" }, { "(−10%)", "neg" } })
  end)

  it("TT9 a post for a later peak, and the disenchant line when a character can", function()
    assert_eq(Variant(2), { "AHProfit | data 2 h old", "Do: POST Sat 10:00 | 4g 50s · +53% vs now · 85%",
      " | for the 18:00 peak", "Market | 2g 95s (2 h) · 3g 80s 30-day (+29%)", "Disenchant | 1g 80s (−39%)",
      "Best time to sell | Sat 18–21 · 4g 50s (+53%) · pattern", "Best time to buy | Tue 20–23 · 2g 80s (−5%) · pattern",
      "Activity | 3/day · 4 listed · 1.3 d supply" })
  end)

  it("TT10 a hold until next week", function()
    assert_eq(Variant(3), { "AHProfit | data 2 h old", "Do: HOLD until next Tue | no window pays this week",
      "Market | 1s 10c (2 h) · 1s 15c 30-day (+5%)", "Best time to sell | Tue 20–22 · 1s 20c (+9%) · pattern",
      "Best time to buy | Sun 03–07 · 1s 2c (−7%) · early", "Activity | 55/day · 120 listed · 2.2 d supply" })
  end)

  it("TT11 a mat in the crafter's bags: the craft it goes into, its mats and when it posts", function()
    local texts, lines = Variant(4)
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: CRAFT 5 Copper Rod | +9s 71c · 90%",
      " | mats 2s 48c · then post Sat 10:00", "Market | 10s (2 h) · 9s 50c 30-day (−5%)",
      "Best time to sell | Sat 18–21 · 12s (+20%) · established", "Best time to buy | Wed 04–08 · 9s 20c (−8%) · pattern",
      "Activity | 40/day · 150 listed · 3.8 d supply" })
    assert_eq(lines[2].verbColor, "craft")
  end)

  it("TT12 the same mat on the character who mails it", function()
    local texts, lines = Variant(5)
    local craft = Variant(4)
    assert_eq({ texts[2], lines[2].verbColor }, { "Do: MAIL 6 to Brugan | for crafting Copper Rod", "mail" })
    assert_eq({ texts[3], texts[4], texts[5], texts[6] }, { craft[4], craft[5], craft[6], craft[7] })
  end)

  it("TT13 a disenchant: what it nets, what the mats are worth and the chance", function()
    local texts, lines = Variant(6)
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: DISENCHANT | +6s 25c · mats 14s 25c · 100%",
      "Market | 8s (2 h) · 11s 30-day (+38%)", "Disenchant | 14s 25c (+78%)",
      "Best time to sell | Sat 17–21 · 12s 30c (+54%) · early", "Best time to buy | Mon 03–07 · 7s 10c (−11%) · early",
      "Activity | 210/day · 400 listed · 1.9 d supply" })
    assert_eq(lines[2].verbColor, "de")
  end)

  it("TT14 an AH listing on the Shopping list; then cheaper now than every buy window", function()
    local texts, lines = Variant(7)
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: BUY ≤ 10s · for Copper Rod | funded · 6 needed",
      "Market | 9s 80c now · 9s 50c 30-day (−3%)", "Best time to sell | Sat 18–21 · 12s (+22%) · established",
      "Best time to buy | Wed 04–08 · 9s 20c (−6%) · pattern", "Activity | 40/day · 150 listed · 3.8 d supply" })
    assert_eq(lines[2].verbColor, "buy")
    local TV = AHProfit.TooltipView
    local now = TV.Lines({ age = { text = "data 2 h old" }, market = { cur = 900, ageText = "now", med30 = 950 },
      doing = TV.DoOf(TW.Ctx({ key = TW.BAR, listing = true, shopping = { qty = 6, price = 1000, purpose = "for Copper Rod" },
                               buyNow = true })),
      buy = { label = "Wed 04–08", price = 920, weeks = 4, cheaperNow = true }, activity = { soldPerDay = 40, listed = 150 } })
    local got = TW.Texts(now)
    assert_eq({ got[3], got[5] }, { " | best time to buy is now", "Best time to buy | now · cheaper than Wed 04–08 (9s 20c)" })
  end)

  it("TT15 an unfunded alternative: why the portfolio left it out, in amber", function()
    local texts, lines = Variant(8)
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: UNFUNDED craft · +80c | exceeds risk tolerance",
      " | needs 45s, 24s 40c left", "Market | 5s 20c (2 h) · 5s 40c 30-day (+4%)",
      "Best time to sell | Sat 19–22 · 5s 80c (+12%) · pattern", "Best time to buy | Thu 02–06 · 4s 90c (−6%) · early",
      "Activity | 10/day · 25 listed · 2.5 d supply" })
    assert_eq({ lines[2].verbColor, lines[2].valueColor }, { "muted", "amber" })
    assert_eq({ AHProfit.TooltipView.SplitReason("Insufficient liquidity: 3/day, 40 planned") },
              { "insufficient liquidity", "3/day, 40 planned" })
  end)

  it("TT16 a kept item; best time to buy is now, shortened when it would pass 320 px", function()
    assert_eq(Variant(9), { "AHProfit | data 2 h old", "Do: KEPT for yourself | keep at least 10", " | right-click to change",
      "Market | 25c (2 h) · 24c 30-day (−4%)", "Best time to sell | Sun 19–22 · 27c (+8%) · early",
      "Best time to buy | now · cheaper than Tue 03–07 (26c)", "Activity | 30/day · 200 listed · 6.7 d supply" })
    local TV = AHProfit.TooltipView
    assert_eq(TV.BuyLine({ label = "Tue 03–07", price = 1234567, weeks = 2, cheaperNow = true }).value,
              "now · under Tue 03–07 (123g 45s 67c)")
    assert_eq(TV.DoOf(TW.Ctx({ key = 159, held = 13, reserve = -1 })).all, true)
  end)

  it("TT17 a vendor item with nothing listed and no windows: no best-time lines", function()
    assert_eq(Variant(10), { "AHProfit | data 2 h old", "Do: VENDOR 12c each | nothing better",
      "Market | none listed · 10c 30-day", "Activity | 0/day · 0 listed" })
  end)

  it("TT18 no market data: the header and one line", function()
    local texts, lines = Variant(11)
    assert_eq({ texts, lines[2].labelMuted }, { { "AHProfit | data 2 h old", "no market data yet | " }, true })
  end)

  it("TT19 a market file 2 days old: the age and its dot turn amber", function()
    local texts, lines = Variant(12)
    local fresh = Variant(1)
    assert_eq({ texts[1], lines[1].amber }, { "AHProfit | data 2 d old", true })
    for i = 2, #fresh do assert_eq(texts[i], fresh[i]) end
  end)

  it("TT20 an AH listing on none of your lists has no Do line", function()
    assert_eq(Variant(13), { "AHProfit | data 2 h old", "Market | 11s 50c now · 12s 30-day (+4%)",
      "Best time to sell | Sat 17–21 · 13s (+13%) · established", "Best time to buy | Tue 08–12 · 10s 80c (−6%) · pattern",
      "Activity | 26/day · 45 listed · 1.7 d supply" })
  end)

  it("TT21 inside the best sell window: best time to sell is now, and the Do line says so", function()
    local texts = Variant(14)
    assert_eq({ texts[3], texts[5] }, { " | then nothing · best time to sell is now",
                                        "Best time to sell | now (Fri 18–21) · 45s · pattern" })
    local TV = AHProfit.TooltipView
    local window = { start = 4 * 24 + 18, hours = 3 }
    assert_eq({ TV.InWindow(window, TW.At(4, 18, 30)), TV.InWindow(window, TW.At(4, 21)), TV.InWindow(window, TW.At(4, 17, 59)) },
              { true, false, false })
    assert_eq({ TV.Hours(window, 0), TV.Hours({ start = 167, hours = 3 }, 0), TV.Hours(window, -TW.HOUR) },
              { "Fri 18–21", "Sun 23–02", "Fri 17–20" })
  end)

  it("strengths, ages and a Do value too long for its line moving under it", function()
    local TV = Boot()
    assert_eq({ TV.Strength(2), TV.Strength(3), TV.Strength(5), TV.Strength(6) }, { "early", "pattern", "pattern", "established" })
    assert_eq({ TV.AgeText(20000), TV.AgeText(25 * 60000), TV.AgeText(2 * TW.HOUR), TV.AgeText(3 * TW.DAY), TV.AgeText(nil) },
              { "now", "25 min", "2 h", "3 d", "now" })
    local lines = TV.DoLines({ kind = "craft", qty = 20, product = "Runecloth Bandage of the Long Name", net = 123456789,
                               chance = 90 }, nil)
    assert_eq({ lines[1].value, lines[2].label, lines[2].value }, { "", "", "+12345g 67s 89c · 90%" })
    assert_eq(TV.ActivityLine({ listed = 3 }).value, "—/day · 3 listed")
    assert_eq(TV.MarketLine({ ageText = "2 h" }).value, "none listed")
    assert_eq(TV.SellLine({ label = "Sat 17–21", price = 100, weeks = 7 }, nil).value, "Sat 17–21 · 1s · established")
  end)
end)

describe("TooltipView shared delta scenarios (tests/scenarios/tooltip.json)", function()
  for _, s in ipairs(dofile("tests/addon/generated/tooltip.lua")) do
    it(s.id .. " " .. s.name, function()
      local TV = Boot()
      local a = s.args
      if s.call == "delta_percent" then
        assert_eq(TV.DeltaPercent(a.value, a.current), s.expect, s.why)
        return
      end
      -- views.tooltip's deltas as the addon shows them: the same percent and colour for the same value and price.
      local values = { ["Historical price"] = Value(a.med30), ["Disenchant value"] = Value(a.de_value),
                       ["Price at best time"] = Value(a.best_price) }
      local colours = { green = "pos", red = "neg" }
      for _, line in ipairs(s.expect) do
        local v = values[line[1]]
        if v ~= nil then
          local delta, colour = TV.Delta(v, Value(a.cur))
          local shown = (line[2]:match("%(([^()]*)%)$") or ""):match("([^%s,]+%%)$")
          assert_eq({ delta, colour }, { shown, colours[Value(line[3])] }, s.why)
        end
      end
      local block = TV.Lines({ age = { text = "data 2 h old" }, market = { cur = Value(a.cur), ageText = "2 h", med30 = Value(a.med30) },
                               canDisenchant = a.can_disenchant, deValue = Value(a.de_value) or 1, activity = {} })
      local hasDE = false
      for _, line in ipairs(block) do hasDE = hasDE or line.label == "Disenchant" end
      assert_eq(hasDE, a.can_disenchant == true, s.why)               -- TT4: only when a character can
    end)
  end
end)
