-- Specs for addon/AHProfit/UI/TooltipView.lua: the tooltip's lines (SPEC §4.3 Tooltip; TT1-TT5's deltas, TT8-TT21,
-- TT25-TT31).
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
  it("TT8 a bag item with a planned post: Do first, then the market lines, both best prices and what you hold", function()
    local texts, lines = Variant(1)
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: POST 5 now at 41s | +63s 6c · 58% chance", " | then nothing",
      "Market Price | 42s (2 h) · 5% higher than 7d median", "Price history | falling", " | (30d) 45s · (14d) 43s · (7d) 40s",
      "Market Inventory | 14 listed", "Sales Rate | 6 sold/day · 2.3 days of supply", "Best Buy Price | 38s · Sunday Night · early",
      "Best Sell Price | 45s · Friday Evening · pattern", "Inventory | 5 · bags 5" })
    assert_eq({ lines[1].amber, lines[2].verb, lines[2].verbColor, lines[2].spans[1] },
              { false, "POST", "post", { "+63s 6c", "pos" } })
    assert_eq({ lines[4].spans[1], lines[5].spans[1], lines[9].spans[1], lines[10].spans[1] },
              { { "5% higher than 7d median", "pos" }, { "falling", "neg" }, { "early", "amber" }, nil })
  end)

  it("TT9 a post for a later peak, and the disenchant breakdown when a character can", function()
    assert_eq(Variant(2), { "AHProfit | data 2 h old", "Do: POST Sat 10:00 | 4g 50s · +53% vs now · 85%",
      " | for the 18:00 peak", "Market Price | 2g 95s (2 h) · 5% lower than 7d median", "Price history | falling",
      " | (30d) 3g 80s · (14d) 3g 30s · (7d) 3g 10s", "Market Inventory | 4 listed", "Sales Rate | 3 sold/day · 1.3 days of supply",
      "Best Buy Price | 2g 80s · Tuesday Evening · pattern", "Best Sell Price | 4g 50s · Saturday Evening · pattern",
      "Disenchant | 69% lower than AH price", "  75% · 3.5 Vision Dust @ 22s | 57s 75c",
      "  20% · 1.5 Greater Mystic Essence @ 80s | 24s", "  5% · 1 Large Radiant Shard @ 1g 70s | 8s 50c", " | 90s 25c",
      "Inventory | 1 · bags 1" })
  end)

  it("TT10 a hold until next week", function()
    assert_eq(Variant(3), { "AHProfit | data 2 h old", "Do: HOLD until next Tue | no window pays this week",
      "Market Price | 1s 10c (2 h) · 2% lower than 7d median", "Price history | steady",
      " | (30d) 1s 15c · (14d) 1s 14c · (7d) 1s 12c", "Market Inventory | 120 listed", "Sales Rate | 55 sold/day · 2.2 days of supply",
      "Best Buy Price | 1s 2c · Saturday Night · early", "Best Sell Price | 1s 20c · Tuesday Evening · pattern",
      "Inventory | 30 · bags 30" })
  end)

  it("TT11 a mat in the crafter's bags: the craft it goes into, its mats and when it posts", function()
    local texts, lines = Variant(4)
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: CRAFT 5 Copper Rod | +9s 71c · 90%",
      " | mats 2s 48c · then post Sat 10:00", "Market Price | 10s (2 h) · 4% higher than 7d median", "Price history | steady",
      " | (30d) 9s 50c · (14d) 9s 55c · (7d) 9s 60c", "Market Inventory | 150 listed", "Sales Rate | 40 sold/day · 3.8 days of supply",
      "Best Buy Price | 9s 20c · Tuesday Night · pattern", "Best Sell Price | 12s · Saturday Evening · established",
      "Inventory | 10 · bags 10" })
    assert_eq(lines[2].verbColor, "craft")
  end)

  it("TT12 the same mat on the character who mails it", function()
    local texts, lines = Variant(5)
    local craft = Variant(4)
    assert_eq({ texts[2], lines[2].verbColor }, { "Do: MAIL 6 to Brugan | for crafting Copper Rod", "mail" })
    for i = 3, #texts do assert_eq(texts[i], craft[i + 1]) end
  end)

  it("TT13 a disenchant: what it nets at the mats' sale values, and the breakdown at their latest prices", function()
    local texts, lines = Variant(6)
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: DISENCHANT | +6s 25c · mats 14s 25c · 100%",
      "Market Price | 8s (2 h) · 20% lower than 7d median", "Price history | steady",
      " | (30d) 11s · (14d) 10s 50c · (7d) 10s", "Market Inventory | 400 listed", "Sales Rate | 210 sold/day · 1.9 days of supply",
      "Best Buy Price | 7s 10c · Sunday Night · early", "Best Sell Price | 12s 30c · Saturday Afternoon · early",
      "Disenchant | 78% higher than AH price", "  75% · 2.5 Soul Dust @ 6s | 11s 25c",
      "  20% · 1.5 Lesser Mystic Essence @ 10s | 3s", " | 14s 25c", "Inventory | 1 · bags 1" })
    assert_eq({ lines[2].verbColor, lines[10].spans[1], lines[11].labelMuted }, { "de", { "78% higher than AH price", "pos" }, true })
  end)

  it("TT14 an AH listing on the Shopping list; then cheaper now than every buy window", function()
    local texts, lines = Variant(7)
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: BUY ≤ 10s · for Copper Rod | funded · 6 needed",
      "Market Price | 9s 80c now · 2% higher than 7d median", "Price history | steady",
      " | (30d) 9s 50c · (14d) 9s 55c · (7d) 9s 60c", "Market Inventory | 150 listed", "Sales Rate | 40 sold/day · 3.8 days of supply",
      "Best Buy Price | 9s 20c · Tuesday Night · pattern", "Best Sell Price | 12s · Saturday Evening · established" })
    assert_eq(lines[2].verbColor, "buy")
    local TV = AHProfit.TooltipView
    local now = TV.Lines({ age = { text = "data 2 h old" }, market = { cur = 900, ageText = "now", med7 = 960, listed = 150 },
      doing = TV.DoOf(TW.Ctx({ key = TW.BAR, listing = true, shopping = { qty = 6, price = 1000, purpose = "for Copper Rod" },
                               buyNow = true })) })
    assert_eq(TW.Texts(now)[3], " | best time to buy is now")
  end)

  it("TT15 an unfunded alternative: why the portfolio left it out, in amber", function()
    local texts, lines = Variant(8)
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: UNFUNDED craft · +80c | exceeds risk tolerance",
      " | needs 45s, 24s 40c left", "Market Price | 5s 20c (2 h) · 2% lower than 7d median", "Price history | steady",
      " | (30d) 5s 40c · (14d) 5s 35c · (7d) 5s 30c", "Market Inventory | 25 listed", "Sales Rate | 10 sold/day · 2.5 days of supply",
      "Best Buy Price | 4s 90c · Wednesday Night · early", "Best Sell Price | 5s 80c · Saturday Evening · pattern" })
    assert_eq({ lines[2].verbColor, lines[2].valueColor }, { "muted", "amber" })
    assert_eq({ AHProfit.TooltipView.SplitReason("Insufficient liquidity: 3/day, 40 planned") },
              { "insufficient liquidity", "3/day, 40 planned" })
  end)

  it("TT16 a kept item: keep at least or keep all, and what you hold", function()
    assert_eq(Variant(9), { "AHProfit | data 2 h old", "Do: KEPT for yourself | keep at least 10", " | right-click to change",
      "Market Price | 25c (2 h) · 4% higher than 7d median", "Price history | steady", " | (30d) 24c · (14d) 24c · (7d) 24c",
      "Market Inventory | 200 listed", "Sales Rate | 30 sold/day · 6.7 days of supply", "Best Buy Price | 26c · Monday Night · early",
      "Best Sell Price | 27c · Sunday Evening · early", "Inventory | 13 · bags 13" })
    assert_eq(AHProfit.TooltipView.DoOf(TW.Ctx({ key = 159, held = 13, reserve = -1 })).all, true)
  end)

  it("TT17 a vendor item with nothing listed and no windows: no best price lines", function()
    assert_eq(Variant(10), { "AHProfit | data 2 h old", "Do: VENDOR 12c each | nothing better",
      "Market Price | none listed", "Price history | ", " | (30d) 10c · (14d) — · (7d) —", "Market Inventory | 0 listed", "Sales Rate | 0 sold/day",
      "Inventory | 4 · bags 4" })
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
    assert_eq(Variant(13), { "AHProfit | data 2 h old", "Market Price | 11s 50c now · 3% lower than 7d median",
      "Price history | steady", " | (30d) 12s · (14d) 11s 90c · (7d) 11s 80c", "Market Inventory | 45 listed", "Sales Rate | 26 sold/day · 1.7 days of supply",
      "Best Buy Price | 10s 80c · Tuesday Morning · pattern", "Best Sell Price | 13s · Saturday Afternoon · established" })
  end)

  it("TT21 inside the best sell window: Best Sell Price says now, and the Do line says so", function()
    local texts = Variant(14)
    assert_eq({ texts[3], texts[10] }, { " | then nothing · best time to sell is now", "Best Sell Price | 45s · now · pattern" })
    local TV = AHProfit.TooltipView
    local window = { start = 4 * 24 + 18, hours = 3 }
    assert_eq({ TV.InWindow(window, TW.At(4, 18, 30)), TV.InWindow(window, TW.At(4, 21)), TV.InWindow(window, TW.At(4, 17, 59)) },
              { true, false, false })
    assert_eq({ TV.Hours(window, 0), TV.Hours({ start = 167, hours = 3 }, 0), TV.Hours(window, -TW.HOUR) },
              { "Fri 18–21", "Sun 23–02", "Fri 17–20" })
  end)

  it("TT25 Market Price against the 7-day median, continuing below when the line would pass 320 px", function()
    local TV = Boot()
    local function Texts(m) return TW.Texts(TV.MarketPriceLines(m)) end
    local lower = TV.MarketPriceLines({ cur = 40, ageText = "50 min", med7 = 45 })
    assert_eq({ TW.Texts(lower), lower[1].spans[1] },
              { { "Market Price | 40c (50 min) · 11% lower than 7d median" }, { "11% lower than 7d median", "neg" } })
    assert_eq(Texts({ cur = 29567, ageText = "50 min", med7 = 31000 }),
              { "Market Price | 2g 95s 67c (50 min)", " | 5% lower than 7d median" })
    assert_eq(TV.MarketPriceLines({ cur = 100, ageText = "1 h", med7 = 98 })[1].spans[1], { "2% higher than 7d median", "pos" })
    assert_eq({ Texts({ cur = 45, ageText = "2 h", med7 = 45 }), Texts({ cur = 40, ageText = "50 min" }), Texts({ ageText = "2 h" }),
                Texts({ cur = 980, ageText = "now", med7 = 960 }) },
              { { "Market Price | 45c (2 h) · at 7d median" }, { "Market Price | 40c (50 min)" }, { "Market Price | none listed" },
                { "Market Price | 9s 80c now · 2% higher than 7d median" } })
  end)

  it("TT26 Price history: the shift from the 30-day to the 7-day median, and the medians oldest first", function()
    local TV = Boot()
    local falling = TV.PriceHistoryLines({ med7 = 45, med14 = 52, med30 = 55 })
    assert_eq({ TW.Texts(falling), falling[1].spans[1] },
              { { "Price history | falling", " | (30d) 55c · (14d) 52c · (7d) 45c" }, { "falling", "neg" } })
    local rising = TV.PriceHistoryLines({ med7 = 31000, med14 = 26300, med30 = 24800 })
    assert_eq({ rising[1].value, rising[1].spans[1] }, { "rising", { "rising", "pos" } })
    assert_eq({ TV.Shift(98, 101), TV.Shift(110, 100), TV.Shift(90, 100), TV.Shift(109, 100), TV.Shift(91, 100) },
              { "steady", "rising", "falling", "steady", "steady" })
    assert_eq(TW.Texts(TV.PriceHistoryLines({ med7 = 45, med14 = 52 })), { "Price history | ", " | (30d) — · (14d) 52c · (7d) 45c" })
  end)

  it("TT27 Market Inventory: the units listed now, with thousands", function()
    local TV = Boot()
    local function Inv(listed)
      for _, l in ipairs(TV.Lines({ age = { text = "data 2 h old" }, market = { cur = 40, ageText = "2 h", listed = listed } })) do
        if l.label == "Market Inventory" then return l.value end
      end
    end
    assert_eq({ Inv(433), Inv(3243), Inv(nil) }, { "433 listed", "3,243 listed", "0 listed" })
  end)

  it("TT28 Best Buy and Best Sell Price: the day and time of day, and the strength, early in orange", function()
    local TV = Boot()
    assert_eq({ TV.DayPart(5 * 24 + 19, 0), TV.DayPart(24 + 2, 0), TV.DayPart(4 * 24 + 6, 0), TV.DayPart(4 * 24 + 12, 0),
                TV.DayPart(4 * 24 + 18, 0), TV.DayPart(4 * 24 + 23, 0), TV.DayPart(4 * 24 + 5, 0), TV.DayPart(0, 0) },
              { "Saturday Evening", "Monday Night", "Friday Morning", "Friday Afternoon", "Friday Evening", "Friday Night",
                "Thursday Night", "Sunday Night" })
    assert_eq({ TV.DayPart(4 * 24 + 4, 2 * TW.HOUR), TV.DayPart(0, -TW.HOUR) }, { "Friday Morning", "Sunday Night" })
    local early = TV.BestLines("Best Sell Price", { price = 58, slot = 5 * 24 + 19, weeks = 1 }, 0)
    assert_eq({ TW.Texts(early), early[1].spans[1] },
              { { "Best Sell Price | 58c · Saturday Evening · early" }, { "early", "amber" } })
    assert_eq(TW.Texts(TV.BestLines("Best Buy Price", { price = 38, slot = 24 + 2, weeks = 4 }, 0)),
              { "Best Buy Price | 38c · Monday Night · pattern" })
    assert_eq(TW.Texts(TV.BestLines("Best Sell Price", { price = 45000, slot = 5 * 24 + 19, weeks = 7 }, 0)),
              { "Best Sell Price | 4g 50s · Saturday Evening", " | established" })
  end)

  it("TT29 Disenchant: the title against the AH price, a line per mat at its latest price, and the total", function()
    local TV = Boot()
    local dust = { chance = 0.8, min = 1, max = 2, name = "Strange Dust", price = 25 }
    local essence = { chance = 0.2, min = 1, max = 2, name = "Lesser Magic Essence", price = 110 }
    local lines = TV.DisenchantLines({ dust, essence }, 100)
    assert_eq(TW.Texts(lines), { "Disenchant | 37% lower than AH price", "  80% · 1.5 Strange Dust @ 25c | 30c",
                                 "  20% · 1.5 Lesser Magic Essence @ 1s 10c | 33c", " | 63c" })
    assert_eq({ lines[1].spans[1], lines[2].labelMuted }, { { "37% lower than AH price", "neg" }, true })
    local unpriced = TV.DisenchantLines({ dust, { chance = 0.2, min = 1, max = 2, name = "Lesser Magic Essence" } }, 100)
    assert_eq({ unpriced[3].value, unpriced[3].valueColor, unpriced[4].value }, { "no price", "amber", "30c" })
    assert_eq(TV.DisenchantLines({ dust, essence }, nil)[1].value, "")
    assert_eq(TV.DisenchantLines({ { chance = 0.05, min = 1, max = 1, name = "Large Radiant Shard", price = 17000 } }, 100)[2].label,
              "  5% · 1 Large Radiant Shard @ 1g 70s")
  end)

  it("TT30 Inventory: what you hold and where, none without holdings", function()
    local TV = Boot()
    assert_eq(TV.InventoryLine({ total = 29, places = { { "bags", 4 }, { "bank", 1 }, { "mail", 24 } } }).value,
              "29 · bags 4, bank 1, mail 24")
    local none = TV.Lines({ age = { text = "data 2 h old" }, market = { cur = 40, ageText = "2 h" }, holdings = { total = 0, places = {} } })
    assert_eq(none[#none].label, "Sales Rate")
  end)

  it("TT32 Sales Rate: units sold a day, then days of supply, one decimal under 10 days", function()
    local TV = Boot()
    local function Rate(sold, listed) return TV.SalesRateLine(sold, listed).value end
    assert_eq({ Rate(55, 433), Rate(120, 3243), Rate(3, 18), Rate(6, 14), Rate(nil, 40), Rate(0, 0), Rate(0.4, 10) },
              { "55 sold/day · 7.9 days of supply", "120 sold/day · 27 days of supply", "3 sold/day · 6.0 days of supply",
                "6 sold/day · 2.3 days of supply", "— sold/day", "0 sold/day", "0 sold/day · 25 days of supply" })
  end)

  it("TT31 the whole block keeps its order, has no Activity line, and every line fits 320 px", function()
    local TV = Boot()
    local labels = {}
    for _, l in ipairs(TV.Lines(TW.Variants(TV, AHProfit.ViewModel)[2][2])) do
      if l.label ~= "" and l.label:sub(1, 2) ~= "  " then labels[#labels + 1] = l.label:match("^Do:") and "Do" or l.label end
    end
    assert_eq(labels, { "AHProfit", "Do", "Market Price", "Price history", "Market Inventory", "Sales Rate", "Best Buy Price",
                        "Best Sell Price", "Disenchant", "Inventory" })
    for n, v in ipairs(TW.Variants(TV, AHProfit.ViewModel)) do
      for _, l in ipairs(TV.Lines(v[2])) do
        assert_eq(TV.Width(AHProfit.ViewModel.WithCoins(l.label), AHProfit.ViewModel.WithCoins(l.value)) <= TV.WIDTH, true,
                  "variant " .. n .. ": " .. l.label .. " | " .. l.value)
      end
    end
  end)

  it("strengths, ages and a Do value too long for its line moving under it", function()
    local TV = Boot()
    assert_eq({ TV.Strength(2), TV.Strength(3), TV.Strength(5), TV.Strength(6) }, { "early", "pattern", "pattern", "established" })
    assert_eq({ TV.AgeText(20000), TV.AgeText(25 * 60000), TV.AgeText(2 * TW.HOUR), TV.AgeText(3 * TW.DAY), TV.AgeText(nil) },
              { "now", "25 min", "2 h", "3 d", "now" })
    local lines = TV.DoLines({ kind = "craft", qty = 20, product = "Runecloth Bandage of the Long Name", net = 123456789,
                               chance = 90 }, nil)
    assert_eq({ lines[1].value, lines[2].label, lines[2].value }, { "", "", "+12345g 67s 89c · 90%" })
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
      local mats = a.can_disenchant and { { chance = 1, min = 1, max = 1, name = "Dust", price = Value(a.de_value) or 1 } } or nil
      local block = TV.Lines({ age = { text = "data 2 h old" }, market = { cur = Value(a.cur), ageText = "2 h", med30 = Value(a.med30) },
                               mats = mats })
      local hasDE = false
      for _, line in ipairs(block) do hasDE = hasDE or line.label == "Disenchant" end
      assert_eq(hasDE, a.can_disenchant == true, s.why)               -- TT4: only when a character can
    end)
  end
end)
