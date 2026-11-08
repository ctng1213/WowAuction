--- The tooltip's 14 variants (SPEC §4.3, docs/mockups/addon.html sections 3 and 3b, sell-through.html; TT8-TT21,
--- TT27-TT31, TT34-TT41, UI17) as the facts
--- UI/Tooltip.lua gathers, for TooltipView's specs and the UI17 layout check. Monday 10:38 UTC, local time UTC,
--- the market file 2 h old. Each variant's Do comes from TooltipView.DoOf over plain plan actions, so the specs
--- check which action applies as well as its words.

local TW = {}

TW.HOUR, TW.DAY = 3600000, 86400000
TW.MON = 1728259200000                         -- Mon 2024-10-07 00:00 UTC
TW.NOW = TW.MON + 10 * TW.HOUR + 38 * 60000
TW.ROD, TW.BAR, TW.ORE = 6217, 2840, 2770
TW.NAMES = { [6217] = "Copper Rod", [2840] = "Copper Bar", [2770] = "Copper Ore" }

--- A moment in the mockup's week.
---@param day number   Days after Monday
---@param hour number
---@param minute number|nil
---@return number      UTC ms
function TW.At(day, hour, minute)
  return TW.MON + day * TW.DAY + hour * TW.HOUR + (minute or 0) * 60000
end

--- The Do context with its defaults: nothing held, kept or listed, no actions.
---@param more table  Fields to set
---@return table      TooltipView.DoOf's context
function TW.Ctx(more)
  local ctx = { key = 0, held = 0, actions = {}, allActions = nil, nowMs = TW.NOW, offsetMs = 0,
                nameOf = function(key) return TW.NAMES[key] or ("Item " .. key) end,
                usedBy = function(product, key) return product == TW.ROD and key == TW.BAR end }
  for k, v in pairs(more) do ctx[k] = v end
  return ctx
end

--- A window at a weekday and hour (UTC = local here), as UI/Tooltip.lua gathers it.
---@param day number    0 = Monday
---@param hour number
---@param price number  Copper
---@param weeks number
---@param now boolean|nil
---@param pct1000 number|nil  The window's percentage in thousandths (TT38)
---@return table        { slot, price, weeks, now, pct1000, est }
---@param est boolean|nil     A selling window of an item that is no commodity (TT38)
local function Win(day, hour, price, weeks, now, pct1000, est)
  return { slot = day * 24 + hour, price = price, weeks = weeks, now = now, pct1000 = pct1000, est = est }
end

--- Units held in bags.
---@param n number
---@return table  TooltipView's holdings
local function Bags(n)
  return { total = n, places = { { "bags", n } } }
end

--- One variant's facts.
---@param TV table        AHProfit.TooltipView
---@param doCtx table|nil TW.Ctx's, or nil for no Do line
---@param body table      { cur, ageText, med7, med14, med30, listed, supply ({ 7d, 14d, 30d } or nil), chance
---                       (thousandths), commodity, st ({ 7d, 14d, 30d } thousandths), stHow, sell, buy, mats,
---                       vendor (copper per unit), holdings }
---@param age table|nil   { text, amber }; default "data 2 h old"
---@return table          TooltipView.Lines' facts
local function Facts(TV, doCtx, body, age)
  local supply = body.supply or {}
  return { age = age or { text = "data 2 h old", amber = false }, coverage = 33, doing = doCtx and TV.DoOf(doCtx),
           offsetMs = 0,
           market = { cur = body.cur, ageText = body.ageText or "2 h", med7 = body.med7, med14 = body.med14,
                      med30 = body.med30, listed = body.listed, supply7 = supply[1], supply14 = supply[2],
                      supply30 = supply[3], chance1000 = body.chance, chancePrice = body.cur, commodity = body.commodity,
                      st = body.st or {}, stHow = body.stHow },
           sell = body.sell, buy = body.buy, mats = body.mats, vendor = body.vendor, holdings = body.holdings }
end

--- Copper Rod in Brugan's bags, posting 5 now at 41 s (variants 1, 12 and 14).
---@param TV table
---@param age table|nil
---@param friday boolean|nil  Fri 18:30, inside the best sell window (variant 14)
---@return table
local function CopperRod(TV, age, friday)
  local now = friday and TW.At(4, 18, 30) or TW.NOW
  local sell = { start = 4 * 24 + 18, hours = 3 }
  local post = { id = "p", kind = "post", key = TW.ROD, qty = 5, price = 4100, at = now, net = 6306, chance = 58 }
  local inside = TV.InWindow(sell, now)
  return Facts(TV, TW.Ctx({ key = TW.ROD, held = 5, actions = { post }, nowMs = now, sellNow = inside }),
    { cur = 4200, med7 = 4000, med14 = 4300, med30 = 4500, listed = 14, supply = { 12, 12, 11 }, chance = 744,
      commodity = 1, st = { 790, 785, 780 }, stHow = "mmm",
      sell = Win(4, 18, 4500, 4, inside, 810),
      buy = Win(0, 3, 3800, 2, nil, 640), vendor = 300, holdings = Bags(5) }, age)
end

--- Copper Bar's market lines (variants 4 and 5).
---@return table
local function CopperBarBody()
  return { cur = 1000, med7 = 960, med14 = 955, med30 = 950, listed = 150, sold = 40, sell = Win(5, 18, 1200, 7),
           buy = Win(2, 4, 920, 4), holdings = Bags(10) }
end

--- The 14 variants, in the mockup's order.
---@param TV table   AHProfit.TooltipView
---@param VM table   AHProfit.ViewModel (the age badge)
---@return table[]   { name, facts }
function TW.Variants(TV, VM)
  local craftChain = { { id = "b", kind = "buy", key = TW.ORE, qty = 4, price = 62, at = TW.NOW },
                       { id = "k", kind = "craft", key = TW.ROD, qty = 5, at = TW.NOW, net = 971, parent = "b" },
                       { id = "q", kind = "post", key = TW.ROD, qty = 5, price = 1200, at = TW.At(5, 10), parent = "b",
                         chance = 90, peak = TW.At(5, 18) } }
  local mail = { id = "m", kind = "mail", key = TW.BAR, qty = 6, at = TW.NOW, to = "Brugan-Grommash" }
  local mailCraft = { id = "k2", kind = "craft", key = TW.ROD, qty = 5, at = TW.NOW + TW.HOUR, net = 971, parent = "m" }
  local spurMats = { { chance = 0.75, min = 2, max = 5, name = "Vision Dust", price = 2200 },
                     { chance = 0.2, min = 1, max = 2, name = "Greater Mystic Essence", price = 8000 },
                     { chance = 0.05, min = 1, max = 1, name = "Large Radiant Shard", price = 17000 } }
  local gloveMats = { { chance = 0.75, min = 2, max = 3, name = "Soul Dust", price = 600 },
                      { chance = 0.2, min = 1, max = 2, name = "Lesser Mystic Essence", price = 1000 } }
  return {
    { "1 bag item with a planned post", CopperRod(TV) },
    { "2 bag item planned for a later peak", Facts(TV, TW.Ctx({ key = 7910, held = 1, actions = { { id = "s", kind = "post",
        key = 7910, qty = 1, price = 45000, at = TW.At(5, 10), net = 15000, chance = 85, peak = TW.At(5, 18) } } }),
      { cur = 29500, med7 = 31000, med14 = 33000, med30 = 38000, listed = 4, sold = 3, sell = Win(5, 18, 45000, 4),
        buy = Win(1, 20, 28000, 4), mats = spurMats, holdings = Bags(1) }) },
    { "3 hold", Facts(TV, TW.Ctx({ key = 4338, held = 30, actions = { { id = "h", kind = "hold", key = 4338, qty = 30,
        at = TW.At(8, 20) } } }),
      { cur = 110, med7 = 112, med14 = 114, med30 = 115, listed = 120, sold = 55, sell = Win(1, 20, 120, 4), buy = Win(6, 3, 102, 2),
        holdings = Bags(30) }) },
    { "4 craft mat in bags (on Brugan, who knows the recipe)",
      Facts(TV, TW.Ctx({ key = TW.BAR, held = 10, actions = craftChain }), CopperBarBody()) },
    { "5 mail step (on Amaya, who holds the bars)", Facts(TV, TW.Ctx({ key = TW.BAR, held = 6, actions = { mail },
        allActions = { mail, mailCraft } }), CopperBarBody()) },
    { "6 disenchant", Facts(TV, TW.Ctx({ key = 7005, held = 1, deValue = 1425, actions = { { id = "d", kind = "de",
        key = 7005, qty = 1, at = TW.NOW, net = 625, chance = 100 } } }),
      { cur = 800, med7 = 1000, med14 = 1050, med30 = 1100, listed = 400, supply = { 420, 430, 450 }, chance = 50,
        commodity = 0, st = { 62, 62, 62 }, stHow = "eee", sell = Win(5, 17, 1230, 2, nil, 90, true),
        buy = Win(0, 3, 710, 2), mats = gloveMats, vendor = 24, holdings = Bags(1) }) },
    { "7 AH listing on your Shopping list", Facts(TV, TW.Ctx({ key = TW.BAR, listing = true,
        shopping = { qty = 6, price = 1000, purpose = "for Copper Rod" } }),
      { cur = 980, ageText = "now", med7 = 960, med14 = 955, med30 = 950, listed = 150, sold = 40, sell = Win(5, 18, 1200, 7),
        buy = Win(2, 4, 920, 4) }) },
    { "8 unfunded alternative (on Brugan)", Facts(TV, TW.Ctx({ key = 3385, held = 0, unfunded = { strategy = "craft",
        net = 80, reason = "Exceeds risk tolerance (needs 45s, 24s 40c left)" } }),
      { cur = 520, med7 = 530, med14 = 535, med30 = 540, listed = 25, sold = 10, sell = Win(5, 19, 580, 4), buy = Win(3, 2, 490, 2) }) },
    { "9 kept item", Facts(TV, TW.Ctx({ key = 159, held = 13, reserve = 10 }),
      { cur = 25, med7 = 24, med14 = 24, med30 = 24, listed = 200, sold = 30, sell = Win(6, 19, 27, 2), buy = Win(1, 3, 26, 2),
        holdings = Bags(13) }) },
    { "10 vendor", Facts(TV, TW.Ctx({ key = 3300, held = 4, own = { kind = "vendor", price = 12 } }),
      { med30 = 10, listed = 0, vendor = 3, holdings = Bags(4) }) },
    { "11 no market data", { age = { text = "data 2 h old", amber = false }, noData = true } },
    { "12 stale data (more than 24 h old)", CopperRod(TV, VM.Age(TW.NOW - 2 * TW.DAY, TW.NOW)) },
    { "13 AH listing on none of your lists", Facts(TV, TW.Ctx({ key = 2996, listing = true }),
      { cur = 1150, ageText = "now", med7 = 1180, med14 = 1190, med30 = 1200, listed = 45, sold = 26, sell = Win(5, 17, 1300, 6),
        buy = Win(1, 8, 1080, 4) }) },
    { "14 bag item inside its best sell window (Fri 18:30)", CopperRod(TV, nil, true) },
  }
end

--- A block's lines as "label | value" texts, the way the specs and the UI17 golden compare them.
---@param lines table[]  TooltipView.Lines'
---@return string[]
function TW.Texts(lines)
  local out = {}
  for i, l in ipairs(lines) do out[i] = l.label .. " | " .. l.value end
  return out
end

return TW
