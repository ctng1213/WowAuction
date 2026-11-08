-- Specs for addon/AHProfit/UI/TooltipView.lua: the tooltip's lines (SPEC §4.3 Tooltip; TT1-TT5's deltas, TT8-TT21,
-- TT27, TT28, TT30, TT31, TT34-TT41).
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
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: POST 5 now at 41s | +63s 6c · 58% chance", " | then nothing", "Market Price | 42s (2 h) · steady", " | (7d) 40s · (14d) 43s · (30d) 45s", "Market Inventory | 14 listed now · high", " | (7d) 12 · (14d) 12 · (30d) 11", "Sell Chance Now | 74% at 42s", " | (7d) 79% · (14d) 79% · (30d) 78%", "Best Buy Time | early", " | 38s · Sunday Night · 64%", "Best Sell Time | pattern", " | 45s · Friday Evening · 81%", "Vendor Price | 3s each · 15s for 5", "Inventory | Total: 5 · Bags: 5" })
    assert_eq({ lines[1].amber, lines[2].verb, lines[2].verbColor, lines[2].spans[1] },
              { false, "POST", "post", { "+63s 6c", "pos" } })
    assert_eq({ lines[1].coverage, lines[4].tail, lines[6].tail, lines[10].tail, lines[12].tail },
              { "5% coverage", nil, { "high", "amber" }, { "early", "amber" }, nil })   -- steady and pattern: plain
  end)

  it("TT9 a post for a later peak, and the disenchant breakdown when a character can", function()
    assert_eq(Variant(2), { "AHProfit | data 2 h old", "Do: POST Sat 10:00 | 4g 50s · +53% vs now · 85%", " | for the 18:00 peak", "Market Price | 2g 95s (2 h) · steady", " | (7d) 3g 10s · (14d) 3g 30s · (30d) 3g 80s", "Market Inventory | 4 listed now", " | (7d) — · (14d) — · (30d) —", "Sell Chance Now | — at 2g 95s", " | (7d) — · (14d) — · (30d) —", "Best Buy Time | pattern", " | 2g 80s · Tuesday Evening · —", "Best Sell Time | pattern", " | 4g 50s · Saturday Evening · —", "Disenchant Value | 69% below AH · 90s 25c", "  75% · 3.5 Vision Dust @ 22s | 57s 75c", "  20% · 1.5 Greater Mystic Essence @ 80s | 24s", "  5% · 1 Large Radiant Shard @ 1g 70s | 8s 50c", "Inventory | Total: 1 · Bags: 1" })
  end)

  it("TT10 a hold until next week", function()
    assert_eq(Variant(3), { "AHProfit | data 2 h old", "Do: HOLD until next Tue | no window pays this week", "Market Price | 1s 10c (2 h) · steady", " | (7d) 1s 12c · (14d) 1s 14c · (30d) 1s 15c", "Market Inventory | 120 listed now", " | (7d) — · (14d) — · (30d) —", "Sell Chance Now | — at 1s 10c", " | (7d) — · (14d) — · (30d) —", "Best Buy Time | early", " | 1s 2c · Saturday Night · —", "Best Sell Time | pattern", " | 1s 20c · Tuesday Evening · —", "Inventory | Total: 30 · Bags: 30" })
  end)

  it("TT11 a mat in the crafter's bags: the craft it goes into, its mats and when it posts", function()
    local texts, lines = Variant(4)
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: CRAFT 5 Copper Rod | +9s 71c · 90%", " | mats 2s 48c · then post Sat 10:00", "Market Price | 10s (2 h) · steady", " | (7d) 9s 60c · (14d) 9s 55c · (30d) 9s 50c", "Market Inventory | 150 listed now", " | (7d) — · (14d) — · (30d) —", "Sell Chance Now | — at 10s", " | (7d) — · (14d) — · (30d) —", "Best Buy Time | pattern", " | 9s 20c · Tuesday Night · —", "Best Sell Time | established", " | 12s · Saturday Evening · —", "Inventory | Total: 10 · Bags: 10" })
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
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: DISENCHANT | +6s 25c · mats 14s 25c · 100%", "Market Price | 8s (2 h) · low", " | (7d) 10s · (14d) 10s 50c · (30d) 11s", "Market Inventory | 400 listed now · steady", " | (7d) 420 · (14d) 430 · (30d) 450", "Sell Chance Now | 5% at 8s · est.", " | (7d) 6% · (14d) 6% · (30d) 6%", "Best Buy Time | early", " | 7s 10c · Sunday Night · —", "Best Sell Time | early", " | 12s 30c · Saturday Afternoon · 9% est.", "Disenchant Value | 78% above AH · 14s 25c", "  75% · 2.5 Soul Dust @ 6s | 11s 25c", "  20% · 1.5 Lesser Mystic Essence @ 10s | 3s", "Vendor Price | 24c", "Inventory | Total: 1 · Bags: 1" })
    assert_eq({ lines[2].verbColor, lines[13].spans[1], lines[14].labelMuted, lines[3].tail, lines[7].tail },
              { "de", { "78% above AH", "pos" }, true, { "low", "amber" }, { "est.", "amber" } })
  end)

  it("TT14 an AH listing on the Shopping list; then cheaper now than every buy window", function()
    local texts, lines = Variant(7)
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: BUY ≤ 10s · for Copper Rod | funded · 6 needed", "Market Price | 9s 80c now · steady", " | (7d) 9s 60c · (14d) 9s 55c · (30d) 9s 50c", "Market Inventory | 150 listed now", " | (7d) — · (14d) — · (30d) —", "Sell Chance Now | — at 9s 80c", " | (7d) — · (14d) — · (30d) —", "Best Buy Time | pattern", " | 9s 20c · Tuesday Night · —", "Best Sell Time | established", " | 12s · Saturday Evening · —" })
    assert_eq(lines[2].verbColor, "buy")
    local TV = AHProfit.TooltipView
    local now = TV.Lines({ age = { text = "data 2 h old" }, market = { cur = 900, ageText = "now", med7 = 960, listed = 150 },
      doing = TV.DoOf(TW.Ctx({ key = TW.BAR, listing = true, shopping = { qty = 6, price = 1000, purpose = "for Copper Rod" },
                               buyNow = true })) })
    assert_eq(TW.Texts(now)[3], " | best time to buy is now")
  end)

  it("TT15 an unfunded alternative: why the portfolio left it out, in amber", function()
    local texts, lines = Variant(8)
    assert_eq(texts, { "AHProfit | data 2 h old", "Do: UNFUNDED craft · +80c | exceeds risk tolerance", " | needs 45s, 24s 40c left", "Market Price | 5s 20c (2 h) · steady", " | (7d) 5s 30c · (14d) 5s 35c · (30d) 5s 40c", "Market Inventory | 25 listed now", " | (7d) — · (14d) — · (30d) —", "Sell Chance Now | — at 5s 20c", " | (7d) — · (14d) — · (30d) —", "Best Buy Time | early", " | 4s 90c · Wednesday Night · —", "Best Sell Time | pattern", " | 5s 80c · Saturday Evening · —" })
    assert_eq({ lines[2].verbColor, lines[2].valueColor }, { "muted", "amber" })
    assert_eq({ AHProfit.TooltipView.SplitReason("Insufficient liquidity: 3/day, 40 planned") },
              { "insufficient liquidity", "3/day, 40 planned" })
  end)

  it("TT16 a kept item: keep at least or keep all, and what you hold", function()
    assert_eq(Variant(9), { "AHProfit | data 2 h old", "Do: KEPT for yourself | keep at least 10", " | right-click to change", "Market Price | 25c (2 h) · steady", " | (7d) 24c · (14d) 24c · (30d) 24c", "Market Inventory | 200 listed now", " | (7d) — · (14d) — · (30d) —", "Sell Chance Now | — at 25c", " | (7d) — · (14d) — · (30d) —", "Best Buy Time | early", " | 26c · Monday Night · —", "Best Sell Time | early", " | 27c · Sunday Evening · —", "Inventory | Total: 13 · Bags: 13" })
    assert_eq(AHProfit.TooltipView.DoOf(TW.Ctx({ key = 159, held = 13, reserve = -1 })).all, true)
  end)

  it("TT17 a vendor item with nothing listed and no windows: no best price lines", function()
    assert_eq(Variant(10), { "AHProfit | data 2 h old", "Do: VENDOR 12c each | nothing better", "Market Price | none listed", " | (7d) — · (14d) — · (30d) 10c", "Market Inventory | 0 listed now", " | (7d) — · (14d) — · (30d) —", "Sell Chance Now | —", " | (7d) — · (14d) — · (30d) —", "Vendor Price | 3c each · 12c for 4", "Inventory | Total: 4 · Bags: 4" })
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
    assert_eq(Variant(13), { "AHProfit | data 2 h old", "Market Price | 11s 50c now · steady", " | (7d) 11s 80c · (14d) 11s 90c · (30d) 12s", "Market Inventory | 45 listed now", " | (7d) — · (14d) — · (30d) —", "Sell Chance Now | — at 11s 50c", " | (7d) — · (14d) — · (30d) —", "Best Buy Time | pattern", " | 10s 80c · Tuesday Morning · —", "Best Sell Time | established", " | 13s · Saturday Afternoon · —" })
  end)

  it("TT21 inside the best sell window: Best Sell Price says now, and the Do line says so", function()
    local texts = Variant(14)
    assert_eq({ texts[3], texts[13] }, { " | then nothing · best time to sell is now", " | 45s · now · 81%" })
    local TV = AHProfit.TooltipView
    local window = { start = 4 * 24 + 18, hours = 3 }
    assert_eq({ TV.InWindow(window, TW.At(4, 18, 30)), TV.InWindow(window, TW.At(4, 21)), TV.InWindow(window, TW.At(4, 17, 59)) },
              { true, false, false })
    assert_eq({ TV.Hours(window, 0), TV.Hours({ start = 167, hours = 3 }, 0), TV.Hours(window, -TW.HOUR) },
              { "Fri 18–21", "Sun 23–02", "Fri 17–20" })
  end)

  it("TT27 Market Inventory: the units listed now, with thousands", function()
    local TV = Boot()
    local function Inv(listed)
      for _, l in ipairs(TV.Lines({ age = { text = "data 2 h old" }, market = { cur = 40, ageText = "2 h", listed = listed } })) do
        if l.label == "Market Inventory" then return l.value end
      end
    end
    assert_eq({ Inv(433), Inv(3243), Inv(nil) }, { "433 listed now", "3,243 listed now", "0 listed now" })
  end)
  it("TT28 Best Buy and Best Sell Time: the day and time of day, and the strength, early in orange", function()
    local TV = Boot()
    assert_eq({ TV.DayPart(5 * 24 + 19, 0), TV.DayPart(24 + 2, 0), TV.DayPart(4 * 24 + 6, 0), TV.DayPart(4 * 24 + 12, 0),
                TV.DayPart(4 * 24 + 18, 0), TV.DayPart(4 * 24 + 23, 0), TV.DayPart(4 * 24 + 5, 0), TV.DayPart(0, 0) },
              { "Saturday Evening", "Monday Night", "Friday Morning", "Friday Afternoon", "Friday Evening", "Friday Night",
                "Thursday Night", "Sunday Night" })
    assert_eq({ TV.DayPart(4 * 24 + 4, 2 * TW.HOUR), TV.DayPart(0, -TW.HOUR) }, { "Friday Morning", "Sunday Night" })
    local early = TV.BestLines("Best Sell Time", { price = 58, slot = 5 * 24 + 19, weeks = 1 }, 0)
    assert_eq({ TW.Texts(early), early[1].tail }, { { "Best Sell Time | early", " | 58c · Saturday Evening · —" }, { "early", "amber" } })
    assert_eq(TW.Texts(TV.BestLines("Best Buy Time", { price = 38, slot = 24 + 2, weeks = 4 }, 0)),
              { "Best Buy Time | pattern", " | 38c · Monday Night · —" })
  end)
  it("TT30 Inventory: what you hold and where, none without holdings", function()
    local TV = Boot()
    assert_eq(TV.InventoryLine({ total = 29, places = { { "bags", 4 }, { "bank", 1 }, { "mail", 24 } } }).value,
              "Total: 29 · Bags: 4 · Bank: 1 · Mail: 24")
    assert_eq(TV.InventoryLine({ total = 3, places = { { "bags", 3 } } }).value, "Total: 3 · Bags: 3")
    assert_eq(TV.InventoryLine({ total = 7, places = { { "bags", 2 }, { "AH", 5 } } }).value, "Total: 7 · Bags: 2 · AH: 5")
    local none = TV.Lines({ age = { text = "data 2 h old" }, market = { cur = 40, ageText = "2 h" }, holdings = { total = 0, places = {} } })
    assert_eq(none[#none - 1].label, "Sell Chance Now")      -- its values line is last: no Inventory line
  end)

  it("TT34 the qualifier word: rising or falling only when consistent across lookbacks, else high, low or steady", function()
    local TV = Boot()
    local function Word(...) return (TV.Qualifier(...)) end
    assert_eq({ Word(130, 120, 110, 100), Word(100, 104, 108, 112), Word(70, 100, 105, 110), Word(24808, 14859, 14859, 14859),
                Word(1713, 2751, 2751, 2751), Word(105, 100, 98, 96) },
              { "rising", "steady", "low", "high", "low", "steady" })
    assert_eq({ Word(90, 100, 110, 120), Word(110, 100, nil, nil), Word(40, nil, 50, 60), select(2, TV.Qualifier(130, 120, 110, 100)) },
              { "falling", "high", nil, "pos" })
  end)

  it("TT35 Market Price: the price now and its word, then the 7, 14 and 30-day medians; no Price history line", function()
    local TV = Boot()
    assert_eq(TW.Texts(TV.MarketPriceLines({ cur = 19, ageText = "now", med7 = 21, med14 = 21, med30 = 21 })),
              { "Market Price | 19c now · steady", " | (7d) 21c · (14d) 21c · (30d) 21c" })      -- 19 / 21 = -9.5%
    assert_eq(TW.Texts(TV.MarketPriceLines({ cur = 19, ageText = "2 h", med7 = 21, med14 = 21, med30 = 21 }))[1],
              "Market Price | 19c (2 h) · steady")
    assert_eq(TW.Texts(TV.MarketPriceLines({ ageText = "2 h", med30 = 10 })), { "Market Price | none listed", " | (7d) — · (14d) — · (30d) 10c" })
  end)

  it("TT36 Market Inventory: a stock against its typical over 7, 14 and 30 days", function()
    local TV = Boot()
    local linen = TV.MarketInventoryLines({ listed = 24808, supply7 = 14859, supply14 = 14859, supply30 = 14859 })
    assert_eq({ TW.Texts(linen), linen[1].tail },
              { { "Market Inventory | 24,808 listed now · high", " | (7d) 14,859 · (14d) 14,859 · (30d) 14,859" }, { "high", "amber" } })
    assert_eq(TW.Texts(TV.MarketInventoryLines({ listed = 1713, supply7 = 2751, supply14 = 2751, supply30 = 2751 }))[1],
              "Market Inventory | 1,713 listed now · low")
  end)

  it("TT37 Sell Chance Now: a 24 h post at the market price, then the measured sell-through; no Sales Rate line", function()
    local TV = Boot()
    assert_eq(TW.Texts(TV.SellChanceLines({ chance1000 = 744, price = 19, commodity = 1, st = { 790, 790, 790 }, stHow = "mmm" })),
              { "Sell Chance Now | 74% at 19c", " | (7d) 79% · (14d) 79% · (30d) 79%" })
    local gloves = TV.SellChanceLines({ chance1000 = 50, price = 100, commodity = 0, st = { 62, 62, 62 }, stHow = "eee" })
    assert_eq({ TW.Texts(gloves), gloves[1].tail },
              { { "Sell Chance Now | 5% at 1s · est.", " | (7d) 6% · (14d) 6% · (30d) 6%" }, { "est.", "amber" } })
    assert_eq(TW.Texts(TV.SellChanceLines({ price = 19, commodity = 1, st = {} })),
              { "Sell Chance Now | — at 19c", " | (7d) — · (14d) — · (30d) —" })
    assert_eq(TV.SellChanceLines({ chance1000 = 300, price = 19, commodity = 1, st = { 410 }, stHow = "c--" })[1].value,
              "30% at 19c · class est.")
    assert_eq({ TV.Percent(744), TV.Percent(745), TV.Percent(62), TV.Percent(0) }, { "74%", "75%", "6%", "0%" })
  end)

  it("TT38 Best Buy and Best Sell Time: the strength word, then the price, the hour and the percentage", function()
    local TV = Boot()
    local buy = TV.BestLines("Best Buy Time", { price = 17, slot = 24 + 7, weeks = 1, pct1000 = 640 }, 0)
    assert_eq({ TW.Texts(buy), buy[1].tail }, { { "Best Buy Time | early", " | 17c · Tuesday Morning · 64%" }, { "early", "amber" } })
    assert_eq(TW.Texts(TV.BestLines("Best Sell Time", { price = 24, slot = 4 * 24 + 18, weeks = 1, pct1000 = 810 }, 0)),
              { "Best Sell Time | early", " | 24c · Friday Evening · 81%" })
    local established = TV.BestLines("Best Sell Time", { price = 24, slot = 4 * 24 + 18, weeks = 7, pct1000 = 90, est = true }, 0)
    assert_eq({ TW.Texts(established), established[1].tail },
              { { "Best Sell Time | established", " | 24c · Friday Evening · 9% est." }, nil })
  end)

  it("TT39 Disenchant Value: the comparison first, then the value; a line per mat and no total line", function()
    local TV = Boot()
    local dust = { chance = 0.8, min = 1, max = 2, name = "Strange Dust", price = 25 }
    local essence = { chance = 0.2, min = 1, max = 2, name = "Lesser Magic Essence", price = 110 }
    local lines = TV.DisenchantLines({ dust, essence }, 100)
    assert_eq(TW.Texts(lines), { "Disenchant Value | 37% below AH · 63c", "  80% · 1.5 Strange Dust @ 25c | 30c",
                                 "  20% · 1.5 Lesser Magic Essence @ 1s 10c | 33c" })
    assert_eq({ lines[1].spans[1], lines[2].labelMuted }, { { "37% below AH", "neg" }, true })
    local above = TV.DisenchantLines({ dust, essence }, 56)
    assert_eq({ above[1].value, above[1].spans[1] }, { "13% above AH · 63c", { "13% above AH", "pos" } })   -- 12.5%, half up
    local unpriced = TV.DisenchantLines({ dust, { chance = 0.2, min = 1, max = 2, name = "Lesser Magic Essence" } }, 100)
    assert_eq({ unpriced[3].value, unpriced[3].valueColor, #unpriced }, { "no price", "amber", 3 })
    assert_eq(TV.DisenchantLines({ dust, essence }, nil)[1].value, "63c")
  end)

  it("TT40 the header's coverage: hours observed in the last 30 days of 720, half up", function()
    local TV = Boot()
    assert_eq({ TV.CoverageText(33), TV.CoverageText(720), TV.CoverageText(719), TV.CoverageText(0), TV.CoverageText(nil) },
              { "5% coverage", "100% coverage", "100% coverage", "— coverage", "— coverage" })
    local header = TV.Lines({ age = { text = "data 12 min old" }, coverage = 33, market = { cur = 19, ageText = "now" } })[1]
    assert_eq({ header.value, header.coverage }, { "data 12 min old", "5% coverage" })
    assert_eq(TV.Lines({ age = { text = "data 2 h old" }, noData = true })[1].coverage, nil)   -- no market data: no coverage
  end)

  it("TT41 Vendor Price: per unit, and for all you hold above Inventory; none when unknown or 0", function()
    local TV = Boot()
    assert_eq({ TV.VendorLine(2, 20).value, TV.VendorLine(24, 1).value, TV.VendorLine(5, 0).value, TV.VendorLine(0, 3),
                TV.VendorLine(nil, 3) }, { "2c each · 40c for 20", "24c", "5c", nil, nil })
    local lines = TV.Lines({ age = { text = "data 2 h old" }, market = { cur = 19, ageText = "now" }, vendor = 2,
                             holdings = { total = 20, places = { { "bags", 20 } } } })
    assert_eq({ lines[#lines - 1].label, lines[#lines].label }, { "Vendor Price", "Inventory" })
  end)

  it("TT31 the whole block keeps its order, has no Activity line, and every line fits 320 px", function()
    local TV = Boot()
    local labels = {}
    for _, l in ipairs(TV.Lines(TW.Variants(TV, AHProfit.ViewModel)[2][2])) do
      if l.label ~= "" and l.label:sub(1, 2) ~= "  " then labels[#labels + 1] = l.label:match("^Do:") and "Do" or l.label end
    end
    assert_eq(labels, { "AHProfit", "Do", "Market Price", "Market Inventory", "Sell Chance Now", "Best Buy Time",
                        "Best Sell Time", "Disenchant Value", "Inventory" })
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
      for _, line in ipairs(block) do hasDE = hasDE or line.label == "Disenchant Value" end
      assert_eq(hasDE, a.can_disenchant == true, s.why)               -- TT4: only when a character can
    end)
  end
end)
