--- The tooltip's 14 variants (SPEC §4.3, docs/mockups/addon.html section 3; TT8-TT21, UI17) as the facts
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

--- One variant's facts.
---@param TV table        AHProfit.TooltipView
---@param doCtx table|nil TW.Ctx's, or nil for no Do line
---@param body table      { cur, ageText, med30, deValue, sell, buy, sold, listed }
---@param age table|nil   { text, amber }; default "data 2 h old"
---@return table          TooltipView.Lines' facts
local function Facts(TV, doCtx, body, age)
  return { age = age or { text = "data 2 h old", amber = false }, doing = doCtx and TV.DoOf(doCtx),
           market = { cur = body.cur, ageText = body.ageText or "2 h", med30 = body.med30 },
           canDisenchant = body.deValue ~= nil, deValue = body.deValue, sell = body.sell, buy = body.buy,
           activity = { soldPerDay = body.sold, listed = body.listed } }
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
    { cur = 4200, med30 = 4500, sell = { label = TV.Hours(sell, 0), price = 4500, weeks = 4, now = inside },
      buy = { label = "Mon 03–07", price = 3800, weeks = 2 }, sold = 6, listed = 14 }, age)
end

--- Copper Bar's market lines (variants 4 and 5).
---@return table
local function CopperBarBody()
  return { cur = 1000, med30 = 950, sell = { label = "Sat 18–21", price = 1200, weeks = 7 },
           buy = { label = "Wed 04–08", price = 920, weeks = 4 }, sold = 40, listed = 150 }
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
  return {
    { "1 bag item with a planned post", CopperRod(TV) },
    { "2 bag item planned for a later peak", Facts(TV, TW.Ctx({ key = 7910, held = 1, actions = { { id = "s", kind = "post",
        key = 7910, qty = 1, price = 45000, at = TW.At(5, 10), net = 15000, chance = 85, peak = TW.At(5, 18) } } }),
      { cur = 29500, med30 = 38000, deValue = 18000, sell = { label = "Sat 18–21", price = 45000, weeks = 4 },
        buy = { label = "Tue 20–23", price = 28000, weeks = 4 }, sold = 3, listed = 4 }) },
    { "3 hold", Facts(TV, TW.Ctx({ key = 4338, held = 30, actions = { { id = "h", kind = "hold", key = 4338, qty = 30,
        at = TW.At(8, 20) } } }),
      { cur = 110, med30 = 115, sell = { label = "Tue 20–22", price = 120, weeks = 4 },
        buy = { label = "Sun 03–07", price = 102, weeks = 2 }, sold = 55, listed = 120 }) },
    { "4 craft mat in bags (on Brugan, who knows the recipe)",
      Facts(TV, TW.Ctx({ key = TW.BAR, held = 10, actions = craftChain }), CopperBarBody()) },
    { "5 mail step (on Amaya, who holds the bars)", Facts(TV, TW.Ctx({ key = TW.BAR, held = 6, actions = { mail },
        allActions = { mail, mailCraft } }), CopperBarBody()) },
    { "6 disenchant", Facts(TV, TW.Ctx({ key = 7005, held = 1, deValue = 1425, actions = { { id = "d", kind = "de",
        key = 7005, qty = 1, at = TW.NOW, net = 625, chance = 100 } } }),
      { cur = 800, med30 = 1100, deValue = 1425, sell = { label = "Sat 17–21", price = 1230, weeks = 2 },
        buy = { label = "Mon 03–07", price = 710, weeks = 2 }, sold = 210, listed = 400 }) },
    { "7 AH listing on your Shopping list", Facts(TV, TW.Ctx({ key = TW.BAR, listing = true,
        shopping = { qty = 6, price = 1000, purpose = "for Copper Rod" } }),
      { cur = 980, ageText = "now", med30 = 950, sell = { label = "Sat 18–21", price = 1200, weeks = 7 },
        buy = { label = "Wed 04–08", price = 920, weeks = 4 }, sold = 40, listed = 150 }) },
    { "8 unfunded alternative (on Brugan)", Facts(TV, TW.Ctx({ key = 3385, held = 0, unfunded = { strategy = "craft",
        net = 80, reason = "Exceeds risk tolerance (needs 45s, 24s 40c left)" } }),
      { cur = 520, med30 = 540, sell = { label = "Sat 19–22", price = 580, weeks = 4 },
        buy = { label = "Thu 02–06", price = 490, weeks = 2 }, sold = 10, listed = 25 }) },
    { "9 kept item", Facts(TV, TW.Ctx({ key = 159, held = 13, reserve = 10 }),
      { cur = 25, med30 = 24, sell = { label = "Sun 19–22", price = 27, weeks = 2 },
        buy = { label = "Tue 03–07", price = 26, weeks = 2, cheaperNow = true }, sold = 30, listed = 200 }) },
    { "10 vendor", Facts(TV, TW.Ctx({ key = 3300, held = 4, own = { kind = "vendor", price = 12 } }),
      { med30 = 10, sold = 0, listed = 0 }) },
    { "11 no market data", { age = { text = "data 2 h old", amber = false }, noData = true } },
    { "12 stale data (more than 24 h old)", CopperRod(TV, VM.Age(TW.NOW - 2 * TW.DAY, TW.NOW)) },
    { "13 AH listing on none of your lists", Facts(TV, TW.Ctx({ key = 2996, listing = true }),
      { cur = 1150, ageText = "now", med30 = 1200, sell = { label = "Sat 17–21", price = 1300, weeks = 6 },
        buy = { label = "Tue 08–12", price = 1080, weeks = 4 }, sold = 26, listed = 45 }) },
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
